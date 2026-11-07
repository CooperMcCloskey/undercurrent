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
- **A story's forecast**, when the pipeline has one, shows in its hover tooltip and in the panel: how many articles a week it should have in 7 days, with an 80% range, such as "~340 articles a week in 7 days (80% range 220–510)". The model projects 1, 3, 7 and 14 days ahead (`pipeline/undercurrent/forecast/README.md`); the web app quotes the 7-day one.

**Not built yet:** the time slider, and with it scrubbing the globe into the past or the future. The exports already list the days (`Meta.dates`) and horizons (`Meta.horizons`) it will need; see the Web section of `PLAN.md`.

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
| `uv run python -m undercurrent.forecast.run --source synthetic` | Train, backtest and forecast on made-up stories (no data needed). `--source proxy` uses A's `articles.parquet`; `--source stories` uses B's stories. See `pipeline/undercurrent/forecast/README.md` |
| `uv run python -m undercurrent.demo` | Embed + cluster the hand-written demo items, score them, export to `data/export/` (then `npm run dev` shows them) |

## Scaling up: the plan to maximise performance

We have roughly $50–200 of compute, and we are **no longer limited to 60 days of data**. This section is the current plan. `PLAN.md`, `docs/WORKPLAN.md` and `docs/ARCHITECTURE.md` have been updated to match it (12 months of full-resolution data, not a 60-day hourly sample). If they ever disagree with this section, this section wins.

**Why this plan:** the forecast improves mostly with more distinct stories to learn from and with accurately tracked stories, not with a bigger GPU. GPUs are cheap (an L4 is roughly $0.30–0.80 an hour, an A100 $1–5). The slow and risky parts are wall-clock time, clustering that doesn't scale yet, and Wikipedia's rate limits.

### Before you start the VPS (checklist)

Do these first. Most are cheap now and expensive to fix later.

1. **Which cloud?** BigQuery only exists on Google Cloud. On AWS or elsewhere the same plan works with object storage (S3) and downloading the GDELT zips on the VM.
2. **Request GPU quota now.** New accounts often start with zero GPUs, and approval can take hours or days.
3. **Make one bucket, and put every machine in the same region as it.** Traffic inside a region is usually free; downloading to laptops is billed per GB (check your cloud's rates). Only the small exports should leave the cloud.
4. **Measure one full day before the big pull.** Dry-run the BigQuery query (free), then check the rows, unique titles and size. Write the numbers in `PLAN.md` Notes (A). Every size, time and cost below is an estimate until then.
5. **Lock these before year 1 is processed.** Changing any of them later means redoing it: the embedding model (`config.EMBEDDING_MODEL`), the text-cleaning version, the similarity thresholds, the tracker settings and the Wikipedia tier rule. If you want to fine-tune the encoder (see Vectors), do it before the bulk encode.
6. **Fix the test period now:** the latest 3–6 months. Never train or tune on it.
7. **Every stage writes one file per day and skips days that already exist**, so any machine can be restarted. Sync finished days to the bucket as you go.
8. **Switch GPU machines off when they aren't embedding.** An A100 left on for a day costs $25–100 of the budget.

### Data to collect

- **GDELT GKG, both feeds, full resolution (no hourly sampling): 12 months first, then older years if they help (see "One year first, then older years").** Sampling makes small-story counts noisy. Older years help less because GDELT's mix of sources changes over time. Keep the latest 3–6 months as the test set.
- **Size:** about 1 GB a day zipped for both feeds. That comes from only three file sizes, so measure one full day before committing.
- **Collect compact data, not zips.** On Google Cloud, use BigQuery (`gdelt-bq.gdeltv2.gkg_partitioned`): select only the columns we need, dedupe by title and day in SQL (keep `dup_count`), and export Parquet. Translated articles should be in the same table, with a `T` in their record id (check on one day). The table is at least 3.6 TB, so always filter by date and **dry-run first** (free) to see the bytes scanned. After the first free TB each month it costs about $6.25 per TB. On other clouds, download the zips on a big VM instead.
- **Also keep:**
  - daily totals before any filtering, per feed and per country, to normalise volume and spot outages;
  - Events + Mentions (event type and intensity), joined to articles by URL. On the one sample we saw they were only 2–3% of the GKG size;
  - the GKG dates field (dates mentioned in the text). Check whether it reveals scheduled events.
- **Drop at parse time:** the emotion (GCAM) column, images, video, quotes, amounts, and any article text.

### Where each stage runs

| Stage | Runs on | Reads → writes |
|---|---|---|
| Extract | BigQuery (or a VM) | GDELT → compact Parquet in the bucket |
| Embed | GPU machine (cheap spot), many days in parallel | titles → one vector file per day in the bucket |
| Track and cluster | CPU machine with plenty of RAM, days in order | vectors → assignments, centroids, per-story-per-day tables |
| Train | The same CPU machine for gradient boosting; a GPU only for the sequence model | small tables → models and forecasts |
| Export | CPU machine | → JSON for the web app. Only this goes to laptops |

- **Training doesn't read vectors.** It reads small tables plus a reduced story centroid, so only the embed and track stages touch the big vector files.
- **The bucket is the source of truth.** A machine's disk is a cache. Mirror the `data/` layout under the bucket root, so the same paths work locally and in the cloud (`gsutil rsync` or `aws s3 sync`). See `data/README.md`.
- **Keep the GPU off the critical path.** CPU FAISS should be enough for the daily matching (roughly 100k items against ~50k story centroids; my estimate, not measured). Prefetch the next day's vectors while the tracker works on today's.
- **If time is short,** run everything on one GPU machine and sync each finished day to the bucket. You waste some money on idle GPU time but avoid coordinating two machines.

### Vectors: encode once, keep them in cloud storage

The encoder is a pretrained model; we don't train it. It turns each headline into a vector, so similar headlines sit close together. Those vectors assign articles to stories, attach posts and Wikipedia items, pick categories and match live posts.

- **Encode everything, per day.** Days are independent, so run them in parallel. Dedupe by text hash first, so a wire headline repeated on many sites is embedded once.
- **Store one file per day:** fp16, L2-normalised, with an id file and a manifest (model, dimension, text version), as in `docs/WORKPLAN.md` section 3.4. Expect roughly 80–150 GB for a few years at 384 dimensions (a guess; measure on one day). In object storage that costs a few dollars.
- **Keep them in the bucket only:** not in git, not on laptops.
- **Why keep them.** We will retune the clustering. With stored vectors that is a rerun of the tracker; without them it means re-embedding everything, which takes hours and $5–50 each time. Stored vectors also let us run several tracker settings in parallel.
- **Don't keep item vectors in the exports.** The web app and the `Item.similarity` field only need the similarity to the story centroid, which the tracker records.
- **How C counts a story's future articles** is still open. The original rule (nearest frozen day-t centroid) needs future vectors for every (story, day), which is expensive at this scale. Counting from the tracker's assignments is much cheaper, and is valid if assignments are never revised once written. Stories that merge or split must keep a lineage table, so counts follow the right id. C to decide and write it in Notes.
- **Fit anything corpus-wide** (PCA, UMAP) on training days only.
- **Idea, untested:** fine-tune the encoder on "same event" pairs. The GDELT Mentions table links many articles to one event id, which could give free training pairs. It has to be done before the bulk encode.

### Clustering: needs a rewrite to scale

`stories/graph.py` is a prototype tested on 57 hand-written items. It will not survive hundreds of thousands of articles a day: `knn_edges` builds a 4096 × N similarity block (about 5 GB at N = 300,000) and loops in Python per row, and networkx Louvain is slow on millions of edges. The plan:

1. **Dedupe** headlines within a day (keep `dup_count`).
2. **Assign** each new item to the nearest active story centroid with FAISS (CPU should be enough; use a GPU if it isn't), using `vectors.threshold("news", "news")` and the existing place rule (`far_apart`).
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

### One year first, then older years

Start with the most recent 12 months, which are closest to what we forecast. Train, then look at the learning curve before adding more: train on the last 3, 6 and 12 months and test on the same held-out months. If the error is still falling at 12 months, add older years, going backwards. If it flattens, stop.

- **The lock list in the checklist is what makes this work.** If the model, thresholds or tracker change, year 1 has to be redone before the years can be compared.
- **Each added block is its own timeline for the tracker** (it starts with no stories). Drop its first 2–4 weeks from training, when ongoing stories look new.
- **Refit anything fitted on the data** (PCA, volume normalisation) when data is added.
- **Older data can hurt,** because GDELT's mix of sources drifts. Down-weight it if the learning curve says so.
- **Retraining is cheap** (minutes). Processing the data is the expensive part.
- **The month-ahead and seasonal view** is the one place where many more years matter. That can come from cheap daily GDELT counts back to 2015, outside the story pipeline.

### Forecast model

- **Models:** gradient boosting (one per horizon, quantile loss), then a sequence model (GRU or a small transformer) over each story's last ~30 days. Ensemble them. Calibrate the ranges on a held-out recent block, so "80%" really covers about 80%.
- **Features**, on top of the current list:
  - story age and position relative to its peak, probably the strongest signal for fade-out;
  - a PCA-reduced story centroid, so the model learns that disasters fade fast and wars persist;
  - category, the day's total GDELT volume and the calendar;
  - the Events mix and the Wikipedia series above.
- **Evaluation:** time-ordered folds by quarter, with a gap between train and test; put each story in a fold by its `first_seen`. Beat three baselines: persistence, damped momentum, and the median trajectory for the story's age and category. Report per horizon, per category and for big vs small stories, with confidence intervals bootstrapped over stories.
- **30 days** is only offered if its backtest passes.

### Training data: why it's quick, and how to get more

- **Why a fit takes minutes, not days.** The model never sees raw articles. The training table has one row per (story, active day) with 30–50 numeric features: roughly 1–3 million rows for a few years (a guess) and under 1 GB. The expensive work (embedding, clustering, tracking) happens earlier. One gradient boosting fit takes minutes on a CPU. The whole workflow (about 5 horizons × 3 quantiles, quarterly folds, sweeps) takes hours, and the sequence model takes hours on a GPU.
- **Rows from one story are near-copies of each other,** so what counts is the number of distinct stories, not rows.
- **More training data, cheapest first:**
  1. **Lower the minimum story size.** It is free, because the data is already processed. Weight rows by volume, since small stories are noisy.
  2. **Pretrain on cheap aggregate series.** GDELT daily counts by theme × country, back to 2015, follow the same rise-peak-fade pattern. Pretrain the sequence model on them, then fine-tune on stories. Wikipedia pageview series could work the same way (untested).
  3. **More years of real stories.** This costs about what year 1 did.
  4. **Augment the windows** (small time shifts, scaling, noise). The gain is modest.
- **What doesn't help:** duplicating rows or running several clusterings of the same articles (near-copies that can leak between train and test), using the test period, or adding old data blindly.
- **Is data the bottleneck?** Compare training and validation error. Training far below validation: more data (or a simpler model) helps. Both high and close together: more rows won't fix it, so improve the features or the stories. Some error is irreducible, because a sudden disaster can't be forecast from the past.

### Compute and cost (estimates, not measured)

| Item | Ballpark |
|---|---|
| BigQuery scans | $10–40 |
| Embedding (~100M titles: a MiniLM-class model takes a few hours; a large model $15–60) | $5–50 |
| VMs, storage, egress | $10–30 |
| Training and sweeps | $10–40 |
| Reserve for reruns | about 30% |

- **Use the GPU only for embedding and the sequence model.** Tracking and gradient boosting run on a cheaper CPU machine. Request GPU quota now (see the checklist).
- **Make every stage resumable per day**, because cheap spot machines get preempted.
- **Wall-clock time is the real limit.** Start extraction in the first hour.
- Run the heavy jobs in the cloud. `data/` stays out of git, and raw data and vectors stay in the bucket (`data/README.md`).

### Build order (stop at any rung)

1. **Core:** 12 months of full-resolution GDELT, scalable story tracking, tier 1 Wikipedia pageviews, gradient boosting with calibrated ranges, a quarterly backtest.
2. **Better:** older years (if the learning curve says they help), Events/Mentions and dates features, bulk Wikipedia, the sequence model and ensemble, a stronger embedding model.
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
  forecast/        C: story-size forecast: panel, features, quantile model, walk-forward backtest, export (see forecast/README.md)
  api/             D: FastAPI over data/export
  demo/            hand-written items -> embed -> cluster -> export (python -m undercurrent.demo)
pipeline/tests/    pytest
web/               D: Vite + React + react-globe.gl
  src/globe/       the globe: land, heatmap, category colours, article dots
  src/story/       story panel, article ranking, dot layout, forecast wording
data/              gitignored; see data/README.md
notebooks/         exploration, one folder per person
docs/ARCHITECTURE.md   design, data sources, gotchas
docs/WORKPLAN.md       who builds what, in what order, and the hand-off files
PLAN.md            plan, owners, status
AGENTS.md          rules for coding agents (Claude Code reads it via CLAUDE.md)
```
