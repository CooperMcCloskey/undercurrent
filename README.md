# Undercurrent

**See the story before it breaks.** Undercurrent groups news (GDELT), social posts (Bluesky, Reddit) and Wikipedia activity into *stories*, puts them on a 3D globe, and forecasts how big each will be over the coming days, so you can scrub the globe back through time and forward into the future.

Built at Hack the Burgh for the HRT (*Best Use of Data for Predictions & Decision Making*) and G-Research (*Make the invisible visible*) challenges.

## Quickstart

Prerequisites: Node 22.12 or newer, and [uv](https://docs.astral.sh/uv/getting-started/installation/). uv installs the right Python (3.13) by itself.

```bash
npm install            # web app + tooling
uv sync                # Python pipeline + API (first run downloads ~1.2 GB, mostly PyTorch)
cp .env.example .env   # optional
npm run dev:mock       # web only, on mock data: http://localhost:5173
npm run dev            # API on :8000 + web on :5173
```

## What you see

- **The globe:** every story is a marker coloured by its category (politics, conflict, economy...), over a heatmap of the places it is happening. Click a category in the legend to hide or show it.
- **Hover a story** to outline its area and burst out a small dot for every place each of its articles mentions, with each place named. That is why the heat is where it is. The big dots are stories; the small ones are their articles (circles) and posts (diamonds), all one size.
- **Click a story** to fly to it and open a panel with every article, post and Wikipedia page in it, linked. Filter by source, sort by importance or date. Hovering a row lights its dots on the globe, and clicking a dot jumps to its row.

Until the real pipeline exports, the globe shows the mock data (`npm run dev:mock`) or the hand-written demo (`undercurrent.demo`). Neither is real news.

## Commands (from the repo root)

| Command | What it does |
|---|---|
| `npm run dev` | API + web together |
| `npm run dev:mock` | Web only, reading `web/public/mock/` (no Python needed) |
| `npm run gen:types` | Regenerate `web/src/api/types.gen.ts` from `pipeline/undercurrent/contracts.py` |
| `npm run gen:mock` | Regenerate `web/public/mock/` from `pipeline/undercurrent/mock.py` |
| `npm run check` | Typecheck + ruff + pytest |
| `npm run format` | Prettier + ruff format |
| `uv run python -m undercurrent.<area>.<module>` | Run a pipeline step |
| `uv run python -m undercurrent.demo` | Embed + cluster the hand-written demo items, score them, export to `data/export/` (then `npm run dev` shows them) |

## Scaling up: the plan to maximise performance

We have roughly $50–200 of compute, and we are **no longer limited to 60 days of data**. This section is the current plan. Where it disagrees with `PLAN.md`, `docs/WORKPLAN.md` or `docs/ARCHITECTURE.md` (which still describe a 60-day, hourly-sampled backfill), this section wins until those are updated.

**Why this plan:** the forecast improves mostly with more distinct stories to learn from and with accurately tracked stories, not with a bigger GPU. GPUs are cheap (an L4 is roughly $0.30–0.80 an hour, an A100 $1–5). The slow and risky parts are wall-clock time, clustering that doesn't scale yet, and Wikipedia's rate limits.

### Data to collect

- **GDELT GKG, both feeds, full resolution (no hourly sampling): 12 months first, then 2–3 years if time allows.** Sampling makes small-story counts noisy. Older years help less because GDELT's mix of sources changes over time. Keep the latest 3–6 months as the test set.
- **Size:** about 1 GB a day zipped for both feeds. That comes from only three file sizes, so measure one full day before committing.
- **Collect compact data, not zips.** On Google Cloud, use BigQuery (`gdelt-bq.gdeltv2.gkg_partitioned`): select only the columns we need, dedupe by title and day in SQL (keep `dup_count`), and export Parquet. Translated articles should be in the same table, with a `T` in their record id (check on one day). The table is at least 3.6 TB, so always filter by date and **dry-run first** (free) to see the bytes scanned. After the first free TB each month it costs about $6.25 per TB. On other clouds, download the zips on a big VM instead.
- **Also keep:**
  - daily totals before any filtering, per feed and per country, to normalise volume and spot outages;
  - Events + Mentions (event type and intensity), joined to articles by URL. On the one sample we saw they were only 2–3% of the GKG size;
  - the GKG dates field (dates mentioned in the text). Check whether it reveals scheduled events.
- **Drop at parse time:** the emotion (GCAM) column, images, video, quotes, amounts, and any article text.

### Vectors: don't store one per item

At this scale a vector for every article is too big: 200 million × 384 numbers is about 150 GB. So:

- Embed each day on the GPU machine, assign its items to stories, then **throw the item vectors away.** Keep only the story assignments (`article_id, story_id, day, similarity`), the daily story centroids and the aggregated features.
- Keep vectors only where something needs them: the labelled evaluation pairs, a small random sample for debugging, and the latest days if the live matcher needs them.
- Cache embeddings by text hash within a run, so a wire headline repeated on many sites is embedded once.
- Fit anything corpus-wide (PCA, UMAP) on training days only.
- **This changes one workplan rule.** C used to count a story's future articles by nearest frozen centroid, which needs future item vectors. With an online tracker whose assignments are never revised (below), C can count them from `story_assignments` instead. Stories that merge or split must keep a lineage table, so counts follow the right id. C to confirm.

### Clustering: needs a rewrite to scale

`stories/graph.py` is a prototype tested on 57 hand-written items. It will not survive hundreds of thousands of articles a day: `knn_edges` builds a 4096 × N similarity block (about 5 GB at N = 300,000) and loops in Python per row, and networkx Louvain is slow on millions of edges. The plan:

1. **Dedupe** headlines within a day (keep `dup_count`).
2. **Assign** each new item to the nearest active story centroid with FAISS (on the GPU if we have one), using `vectors.threshold("news", "news")` and the existing place rule (`far_apart`).
3. **Cluster only the leftovers** (items that matched no story) over a trailing window, to start new stories. That is small enough for Louvain; if it isn't, add Leiden with `uv add`.
4. **Update the centroids** and retire stories that have been idle for N days.
5. **Process days in order**, so everything is point-in-time. Embedding each day is independent, so run it on several machines at once; only this tracking step is sequential, and it is cheap.
6. **Measure before every long run.** Build a labelled set of 1–2k "same story?" pairs (an LLM can draft them if we have API credit) and check precision, recall and adjusted Rand index. Recalibrate `min_sim` for the chosen embedding model. Badly formed stories make the forecast target noisy, so this matters more than the choice of forecast model.
7. **Export only the top N stories per day and the top items per story**, so the web app stays fast.

### Wikipedia: what the data agent (A) should do now

`pipeline/undercurrent/ingest/WIKIPEDIA.md` is right for a few thousand entities, but it won't scale to years of stories as written:

- **Rate-limit maths.** Pageviews and edits in 8 languages are about 16 requests per entity, at 190 requests a minute. 1,000 entities take about 1.5 hours, 10,000 take about 14 hours and 30,000 take about 2 days. So **choose entities in tiers**, with the same rule for training and test days:
  1. **Tier 1:** the top ~5 entities of the biggest stories each week (people, organisations and places, but not countries or capitals): all 8 languages, pageviews and edits.
  2. **Tier 2:** the next-biggest stories: English pageviews only.
  3. **Everything else:** missing (NaN), never 0. The choice of entities must never depend on how the story later did.
- **Bulk pageviews instead of per-entity calls.** Wikimedia publishes daily pageview dump files (the existing doc estimates about 700 MB a day). On a cloud VM that is fine for a year or two. First check the dump layout and whether a user-only (no bots) version exists, then filter to the tier 1 and 2 titles and keep daily totals. This is unverified, so try one day first.
- **Top-1000 lists for the whole history.** 8 languages × 2 lists × 365 days is about 5,800 requests (about 30 minutes) per year. Start this early; it needs no entity names.
- **Edits:** English only, tier 1 only. They are a weaker signal than pageviews.
- **Timing rule.** Pageviews arrive about a day late, so a forecast made on day t may only use pageviews up to t − 1. Edits are known straight away. Write the rule in Notes in `PLAN.md` and apply it everywhere.
- **Stay cached and resumable**, as the doc says; a long run will be interrupted.
- **Stretch: scheduled events.** Scrape Wikipedia pages that list dated upcoming events (elections, summits, finals) into `wiki_scheduled.parquet` (`date, title, kind`). Scheduled events are the main thing that can make a 14+ day forecast better than "it fades".

### Forecast model

- **Models:** gradient boosting (one per horizon, quantile loss), then a sequence model (GRU or a small transformer) over each story's last ~30 days. Ensemble them. Calibrate the ranges on a held-out recent block, so "80%" really covers about 80%.
- **Features**, on top of the current list:
  - story age and position relative to its peak, probably the strongest signal for fade-out;
  - a PCA-reduced story centroid, so the model learns that disasters fade fast and wars persist;
  - category, the day's total GDELT volume and the calendar;
  - the Events mix and the Wikipedia series above.
- **Evaluation:** time-ordered folds by quarter, with a gap between train and test; put each story in a fold by its `first_seen`. Beat three baselines: persistence, damped momentum, and the median trajectory for the story's age and category. Report per horizon, per category and for big vs small stories, with confidence intervals bootstrapped over stories.
- **30 days** is only offered if its backtest passes.

### Compute and cost (estimates, not measured)

| Item | Ballpark |
|---|---|
| BigQuery scans | $10–40 |
| Embedding (~100M titles: a MiniLM-class model takes a few hours; a large model $15–60) | $5–50 |
| VMs, storage, egress | $10–30 |
| Training and sweeps | $10–40 |
| Reserve for reruns | about 30% |

- **Request GPU quota now.** New cloud accounts often start with zero GPUs, and approval can take hours or days.
- **Make every stage resumable per day**, because cheap spot machines get preempted.
- **Wall-clock time is the real limit.** Start extraction in the first hour.
- Run the heavy jobs on the cloud machine. `data/` stays out of git, so share results through a bucket or drive.

### Build order (stop at any rung)

1. **Core:** 12 months of full-resolution GDELT, scalable story tracking, tier 1 Wikipedia pageviews, gradient boosting with calibrated ranges, a quarterly backtest.
2. **Better:** 2–3 years, Events/Mentions and dates features, bulk Wikipedia, the sequence model and ensemble, a stronger embedding model.
3. **Future globe:** a model of new-story volume by region and category (stories that don't exist yet; needs one optional contract field), a 30-day horizon if it passes, an LLM-labelled gold set.

## Repo map

```
pipeline/undercurrent/
  contracts.py     shared data contract (source of truth for every JSON shape)
  config.py        paths + shared constants
  mock.py          generates web/public/mock/
  ingest/          A: GDELT, Bluesky, Wikipedia, Reddit -> data/raw, data/processed
  vectors/         V: text cleaning + embeddings for every source -> data/processed/vectors
  stories/         B: clustering, story tracking, footprints -> data/export
  forecast/        C: labels, features, model, backtest
  api/             D: FastAPI over data/export
  demo/            hand-written items -> embed -> cluster -> export (python -m undercurrent.demo)
pipeline/tests/    pytest
web/               D: Vite + React + react-globe.gl
  src/globe/       the globe: land, heatmap, category colours, article dots
  src/story/       story panel, article ranking and dot layout
data/              gitignored; see data/README.md
notebooks/         exploration, one folder per person
docs/ARCHITECTURE.md   design, data sources, gotchas
docs/WORKPLAN.md       who builds what, in what order, and the hand-off files
PLAN.md            plan, owners, status
AGENTS.md          rules for coding agents (Claude Code reads it via CLAUDE.md)
```
