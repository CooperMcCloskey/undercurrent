# Forecast (workstream C)

Predicts how big each story will be in the coming days, so the globe can look into the future. For
every story and every day it gives, for each horizon (1, 3, 7 and 14 days), a median and an 80%
range of the story's `article_count_7d` on that later day. `article_count_7d` is the number the
globe draws the heat from, so a projected day is drawn by the same code as a real snapshot.

Why this shape (a size, not a "will it double" probability) is in `PLAN.md` (Decisions) and
`docs/ARCHITECTURE.md` (Forecast).

## Try it now

```bash
uv run python -m undercurrent.forecast.run --source synthetic   # made-up stories, ~1 minute
uv run python -m undercurrent.forecast.run --source proxy       # theme x country series, needs A's articles.parquet
uv run python -m undercurrent.forecast.run --source stories     # the real thing, needs B's output
uv run pytest pipeline/tests/test_forecast_*.py                 # ~15 seconds
```

`synthetic` and `proxy` write to `data/processed/forecast/<source>/`, so a trial run can never
overwrite what B's exporter reads. Add `--help` for the options (horizons, test window, model).

## What it reads (all under `data/processed/`)

| File | From | Columns used | Needed? |
|---|---|---|---|
| `story_assignments` | B | `article_id, story_id, day` | yes, and **append-only** |
| `articles` | A | `id, domain, tone, locations[].country` | yes |
| `stories_daily` | B | `story_id, day, category, first_seen` | optional (features) |
| `daily_totals` | A | `day, total_articles` | optional (else counted from `articles`) |
| `story_wiki_entities` | B | `story_id, qid` | optional (Wikipedia features) |
| `wiki_entity_daily` | A | `qid, day, views, edits, views_en` | optional (Wikipedia features) |

Each is one file `name.parquet` or a folder `name/` of partitions. **Everything that knows these
names is in two places: `schema.py` and `panel.py`.** Nothing else reads another workstream's files.
A missing optional file just turns its features into NaN (gradient boosting handles that).

## What it writes

`forecasts.parquet`, one row per (story, day, horizon):

| Column | Meaning |
|---|---|
| `story_id`, `day`, `days` | the story, the day the forecast was made, the horizon |
| `articles_7d`, `low`, `high` | median and 80% range of `article_count_7d` on `day + days` |
| `factors` | the features that moved the median most (log2 units); only for the latest 14 days |
| `actual_articles_7d` | what really happened, empty until `day + days` has passed |
| `source` | `walkforward` = made by a model that hadn't seen those days (safe for a replay); `live` = the latest day |
| `model_version`, `fold` | which model, which test window |

plus `model_metrics.json` (exactly `contracts.ModelMetrics`) and `forecast_report.json` (everything
else: each baseline, confidence intervals, results by category, size and age).

**B's exporter should not regroup these rows.** `export.load_forecasts(path)` returns, per
(story_id, day), a validated `contracts.Forecast` and `contracts.Outcome`, and
`export.meta_horizons(path)` gives `Meta.horizons`.

## How it works

1. **`panel.py`** joins B's assignments with A's articles (DuckDB, so a year doesn't need to fit in
   memory) into the **story-day panel** described at the top of `schema.py`: one row per story and
   day with the article count, distinct domains and countries, and mean tone.
2. **`dataset.py` + `features.py`** turn the panel into one row per (story, day): the features at
   that day, and the targets `y_h = log2((a7(t+h) + 1) / (a7(t) + 1))`. Every feature at day t
   uses only data up to t (Wikipedia pageviews up to t − 1); `tests/test_forecast_core.py` checks
   this by rewriting the future and asserting nothing at t moves.
3. **`backtest.py`** cuts time into windows and trains a model per window and horizon on rows whose
   outcomes were known before the window started. The ranges are then calibrated on the 28 days
   just before the window (`models.conformal_offset`), so "80%" really covers about 80%. The last
   90 days are the final test window: that's what `model_metrics.json` reports. Don't tune on it.
4. **`export.py`** turns predictions into `forecasts.parquet` and the contract objects.

## How to change things

| To… | Change… |
|---|---|
| follow a renamed or moved column from A or B | `schema.py` and the SQL in `panel.py` (the error message says which) |
| add a feature | write a function with `@feature("name")` in `features.py` (above `FEATURES`); the point-in-time test covers it automatically |
| add a model (ridge, a sequence model…) | a class with `fit(X, y, weight)` and `predict(X) -> (n, 3)` (low, median, high), decorated with `@register("name")` in `models.py`; run with `--model name` |
| change horizons or the range | `HORIZONS` and `FORECAST_INTERVAL` in `config.py` (shared: tell everyone, and the contract's `Meta.horizons` follows) |
| change who gets a forecast, the test window, the pageview lag… | `settings.py`; each knob has a comment |
| add a baseline | `baselines.py`, then list it in `backtest.baseline_table` |
| use different data (Events, centroid PCA…) | add columns to the panel in `panel.py`, then features that read them |

If you need something from this module that isn't here, add it under "Needs from others" in your
section of `PLAN.md`, or just add it: the tests will say if you broke the no-lookahead guarantee.

## Not done yet

- **Never run on real data.** The synthetic and proxy paths work; the `stories` path is tested on
  small fake files that follow the documented schemas, not on B's real output.
- **Ridge regression and a sequence model.** Only gradient boosting exists (the `@register`
  hook is there).
- **More features:** a PCA-reduced story centroid (fit on training days only), GDELT Events/CAMEO
  mix, and the dates-mentioned field for scheduled events.
- **Speed at 12 months.** Designed for it (DuckDB for the join, story chunks for the features,
  no per-row Python), but untested beyond a few hundred stories. Measure first.
- **Counting from `story_assignments`** is only honest if B never revises an assignment once made.
  The notes in `PLAN.md` say so; verify it when B's tracker lands.
