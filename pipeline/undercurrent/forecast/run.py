"""Train, backtest and forecast.

    uv run python -m undercurrent.forecast.run --source synthetic          # try it, no data needed
    uv run python -m undercurrent.forecast.run --source proxy              # theme x country series
    uv run python -m undercurrent.forecast.run --source stories            # the real thing

Sources:
    synthetic  made-up stories with realistic fading (never quote its numbers)
    proxy      proxy stories from articles.parquet (A's output), before B's stories exist
    stories    B's tracked stories: story_assignments + articles (+ stories_daily, wiki)

Writes forecasts.parquet, model_metrics.json and forecast_report.json. With --source stories they
go to data/processed/ (where B's exporter reads them); otherwise to data/processed/forecast/<source>/
so a trial run can't overwrite real outputs.
"""

import argparse
import json
import logging
from pathlib import Path

from undercurrent import config
from undercurrent.forecast import settings
from undercurrent.forecast.backtest import BacktestResult, run_backtest
from undercurrent.forecast.dataset import build_rows
from undercurrent.forecast.export import forecasts_frame, write_outputs
from undercurrent.forecast.models import MODELS
from undercurrent.forecast.schema import DAY, STORY


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--source", choices=["synthetic", "proxy", "stories"], default="synthetic")
    p.add_argument("--processed", type=Path, default=config.PROCESSED, help="where A and B write")
    p.add_argument("--out", type=Path, default=None, help="where to write the outputs")
    p.add_argument("--horizons", type=int, nargs="+", default=list(config.HORIZONS))
    p.add_argument("--model", choices=sorted(MODELS), default="hgb")
    p.add_argument("--min-articles", type=int, default=settings.MIN_ARTICLES_7D)
    p.add_argument("--max-iter", type=int, default=settings.HGBParams.max_iter)
    p.add_argument("--folds", type=int, default=settings.N_FOLDS, help="extra earlier test windows")
    p.add_argument("--final-test-days", type=int, default=settings.FINAL_TEST_DAYS)
    p.add_argument("--fold-days", type=int, default=settings.FOLD_DAYS)
    p.add_argument("--cal-days", type=int, default=settings.CALIBRATION_DAYS)
    p.add_argument("--min-train-days", type=int, default=settings.MIN_TRAIN_DAYS)
    p.add_argument("--factor-days", type=int, default=settings.FACTOR_DAYS)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--synthetic-stories", type=int, default=300)
    p.add_argument("--synthetic-days", type=int, default=420)
    return p.parse_args(argv)


def load_inputs(args: argparse.Namespace):
    if args.source == "synthetic":
        from undercurrent.forecast.synthetic import make_panel

        return make_panel(args.synthetic_stories, args.synthetic_days, args.seed)
    if args.source == "proxy":
        from undercurrent.forecast.proxy import build_panel

        return build_panel(args.processed)
    from undercurrent.forecast.panel import build_panel

    return build_panel(args.processed)


def summary(result: BacktestResult) -> str:
    final = result.report.get("final")
    if not final:
        return "No scored rows in the final test window."
    lines = [
        f"{'horizon':>8} {'n':>8} {'model':>7} {'persist':>8} {'moment.':>8} {'age×cat':>8} "
        f"{'coverage':>9} {'vs persistence (95% CI)':>26}"
    ]
    for m in final["metrics"]:
        h = int(m["days"])
        by = final["mae_by_method"][h]
        boot = final["model_minus_persistence"][h]
        lines.append(
            f"{str(h) + 'd':>8} {m['n_test']:>8,} {by['model']:>7.3f} {by['persistence']:>8.3f} "
            f"{by['momentum']:>8.3f} {by['age_category']:>8.3f} {m['coverage']:>9.2f} "
            f"{boot['mean']:>+9.3f} [{boot['ci_low']:+.3f}, {boot['ci_high']:+.3f}]"
        )
    lines.append(
        "MAE is the typical miss in log2 units (0.5 = off by about 1.4x). Negative = better."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    panel, totals, wiki = load_inputs(args)
    rows = build_rows(
        panel, args.horizons, totals=totals, wiki=wiki, min_articles=args.min_articles
    )
    if rows.empty:
        raise SystemExit("No rows: no story had enough articles. Lower --min-articles?")
    print(
        f"{len(rows):,} rows from {rows[STORY].nunique():,} stories, "
        f"{rows[DAY].min().date()} to {rows[DAY].max().date()}"
    )
    result = run_backtest(
        rows,
        args.horizons,
        model_name=args.model,
        interval=config.FORECAST_INTERVAL,
        params=settings.HGBParams(max_iter=args.max_iter),
        n_folds=args.folds,
        final_test_days=args.final_test_days,
        fold_days=args.fold_days,
        cal_days=args.cal_days,
        min_train_days=args.min_train_days,
        factor_days=args.factor_days,
        seed=args.seed,
    )
    out = args.out or (
        args.processed if args.source == "stories" else args.processed / "forecast" / args.source
    )
    forecasts = forecasts_frame(result.walkforward, result.live, result.model_version)
    paths = write_outputs(
        out, forecasts, result.metrics, json.loads(json.dumps(result.report, default=str))
    )
    print(summary(result))
    print(f"{len(forecasts):,} forecasts -> {paths['forecasts']}")
    if args.source != "stories":
        print(f"(trial run on {args.source} data: written to {out}, not to {args.processed})")


if __name__ == "__main__":
    main()
