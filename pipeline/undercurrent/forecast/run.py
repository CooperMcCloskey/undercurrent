"""Train, backtest and forecast.

    uv run python -m undercurrent.forecast.run --source synthetic             # try it, no data needed
    uv run python -m undercurrent.forecast.run --source proxy                 # theme x country series
    uv run python -m undercurrent.forecast.run --source stories               # the real thing
    uv run python -m undercurrent.forecast.run --model hgb gru                # trees + neural net, averaged
    uv run python -m undercurrent.forecast.run --model gru --pretrain proxy   # pretrain the net first

Sources:
    synthetic  made-up stories with realistic fading (never quote its numbers)
    proxy      proxy stories from articles.parquet (A's output), before B's stories exist
    stories    B's tracked stories: story_assignments + articles (+ stories_daily, wiki)

Models (--model takes one or more; several are averaged and calibrated together):
    hgb        gradient boosted trees on the tabular features (fast, the baseline)
    gru        a GRU over each story's last 28 days plus the same features (needs PyTorch; slower)

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
from undercurrent.forecast.dataset import build_dataset
from undercurrent.forecast.export import forecasts_frame, write_outputs
from undercurrent.forecast.models import all_model_names, resolve
from undercurrent.forecast.schema import DAY, STORY


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--source", choices=["synthetic", "proxy", "stories"], default="synthetic")
    p.add_argument("--processed", type=Path, default=config.PROCESSED, help="where A and B write")
    p.add_argument("--out", type=Path, default=None, help="where to write the outputs")
    p.add_argument("--horizons", type=int, nargs="+", default=list(config.HORIZONS))
    p.add_argument("--model", nargs="+", choices=all_model_names(), default=["hgb"])
    p.add_argument("--blend", type=float, nargs="+", default=None, help="weights, one per --model")
    p.add_argument("--min-articles", type=int, default=settings.MIN_ARTICLES_7D)
    p.add_argument("--max-iter", type=int, default=settings.HGBParams.max_iter, help="trees")
    p.add_argument("--seq-days", type=int, default=settings.SEQ_DAYS, help="history the gru reads")
    p.add_argument("--nn-epochs", type=int, default=settings.NNParams.epochs)
    p.add_argument("--nn-hidden", type=int, default=settings.NNParams.hidden)
    p.add_argument("--nn-seeds", type=int, default=settings.NNParams.seeds, help="nets to average")
    p.add_argument("--device", default=settings.NNParams.device, help="auto, cpu, cuda or mps")
    p.add_argument(
        "--pretrain",
        choices=["none", "proxy", "synthetic"],
        default="none",
        help="data to pretrain the gru on before fine-tuning on --source",
    )
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


def load_panel(source: str, args: argparse.Namespace, seed: int):
    if source == "synthetic":
        from undercurrent.forecast.synthetic import make_panel

        return make_panel(args.synthetic_stories, args.synthetic_days, seed)
    if source == "proxy":
        from undercurrent.forecast.proxy import build_panel

        return build_panel(args.processed)
    from undercurrent.forecast.panel import build_panel

    return build_panel(args.processed)


BASELINES = ("model", "persistence", "momentum", "age_category")


def summary(result: BacktestResult) -> str:
    final = result.report.get("final")
    if not final:
        return "No scored rows in the final test window."
    first = final["mae_by_method"][int(final["metrics"][0]["days"])]
    parts = [name for name in first if name not in BASELINES]  # each model on its own
    header = (
        f"{'horizon':>8} {'n':>8} {'blend':>7} "
        + "".join(f"{p:>7} " for p in parts)
        + f"{'persist':>8} {'moment.':>8} {'age×cat':>8} {'coverage':>9} "
        + f"{'blend vs persistence (95% CI)':>31}"
    )
    lines = [header]
    for m in final["metrics"]:
        h = int(m["days"])
        by = final["mae_by_method"][h]
        boot = final["model_minus_persistence"][h]
        lines.append(
            f"{str(h) + 'd':>8} {m['n_test']:>8,} {by['model']:>7.3f} "
            + "".join(f"{by[p]:>7.3f} " for p in parts)
            + f"{by['persistence']:>8.3f} {by['momentum']:>8.3f} {by['age_category']:>8.3f} "
            f"{m['coverage']:>9.2f} {boot['mean']:>+14.3f} [{boot['ci_low']:+.3f}, {boot['ci_high']:+.3f}]"
        )
    lines.append(
        "MAE is the typical miss in log2 units (0.5 = off by about 1.4x). Negative = better. "
        "'blend' is the calibrated average of the models; each model's own column is uncalibrated."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    if args.blend is not None and len(args.blend) != len(args.model):
        raise SystemExit("--blend needs one weight per --model")
    needs_seq = any(resolve(name)[0] == "joint" for name in args.model)
    seq_len = args.seq_days if needs_seq else 0

    panel, totals, wiki = load_panel(args.source, args, args.seed)
    rows, seq = build_dataset(
        panel,
        args.horizons,
        totals=totals,
        wiki=wiki,
        min_articles=args.min_articles,
        seq_len=seq_len,
    )
    if rows.empty:
        raise SystemExit("No rows: no story had enough articles. Lower --min-articles?")
    print(
        f"{len(rows):,} rows from {rows[STORY].nunique():,} stories, "
        f"{rows[DAY].min().date()} to {rows[DAY].max().date()}"
    )

    aux = None
    if args.pretrain != "none":
        if not needs_seq:
            raise SystemExit("--pretrain is for models that read daily history, e.g. --model gru")
        a_panel, a_totals, a_wiki = load_panel(args.pretrain, args, args.seed + 1)
        a_rows, a_seq = build_dataset(
            a_panel,
            args.horizons,
            totals=a_totals,
            wiki=a_wiki,
            min_articles=args.min_articles,
            seq_len=seq_len,
        )
        print(f"pretraining data ({args.pretrain}): {len(a_rows):,} rows")
        aux = (a_rows, a_seq)

    result = run_backtest(
        rows,
        args.horizons,
        models=args.model,
        interval=config.FORECAST_INTERVAL,
        model_params={
            "hgb": settings.HGBParams(max_iter=args.max_iter),
            "gru": settings.NNParams(
                epochs=args.nn_epochs,
                hidden=args.nn_hidden,
                seeds=args.nn_seeds,
                device=args.device,
            ),
        },
        seq=seq,
        aux=aux,
        blend=args.blend,
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
