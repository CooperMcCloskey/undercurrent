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
| `uv run python -m undercurrent.forecast.run --source synthetic` | Train, backtest and forecast on made-up stories (no data needed). `--source proxy` uses A's `articles.parquet`; `--source stories` uses B's stories. Add `--model hgb gru` for the neural net. See "Future prediction" below |
| `uv run python -m undercurrent.vectors.store --source news --start D --end D` | Embed each day's headlines into the vector store (resumable; `--shard i/n` splits days across machines) |
| `uv run python -m undercurrent.stories.track --start D --end D` | Run the story tracker over a range of days (resumable) |
| `uv run python -m undercurrent.vectors.wiki_events --days 30` | Download Wikipedia's daily "Current events" bullets → `data/processed/wiki_events.parquet` (one request a day; resumable). Then embed them with `undercurrent.vectors.store --source wiki_events`, on days at least 4 days old |
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

The commands, in order (each is resumable, and every one skips work that is already done):

```bash
uv run python -m undercurrent.vectors.store --source news --start 2026-01-01 --end 2026-03-31   # embed (GPU); add --shard 0/4 on each of 4 machines
uv run python -m undercurrent.stories.track --start 2026-01-01 --end 2026-03-31                  # track (CPU, its own process)
uv run python -m undercurrent.forecast.run --source stories                                      # forecast
```

### Vectors: encode once, keep them in cloud storage

The encoder is a pretrained model; we don't train it. It turns each headline into a vector, so similar headlines sit close together. Those vectors assign articles to stories, attach posts and Wikipedia items, pick categories and match live posts.

**Built** (`pipeline/undercurrent/vectors/store.py`, tested with a fake model and run once with the real one on the demo headlines):

- `python -m undercurrent.vectors.store` embeds one day at a time and writes `vectors/<source>/<day>.npy` (float16, unit length) and `.parquet` (id and the exact text embedded). It reads A's `articles` for `news`, and `bluesky/<day>.parquet`, `wiki_events.parquet`, `wiki_new.parquet` for the others.
- **Resumable:** files are written under a temporary name and renamed, so a killed run never leaves half a day; finished days are skipped. `--shard i/n` splits days across machines.
- **Cleans, then drops junk** (`vectors/clean.py`: "Home", "404 Not Found", too-short headlines; the word count handles Chinese, Japanese and Korean). Identical cleaned texts are embedded once, and texts from the previous 3 days are reused.
- **Refuses to mix models:** `manifest.json` records the model, dimension and text version, and writing with a different one stops with an explanation.
- `vectors.load(source, days)` returns ids and unit-length float32 vectors, and refuses days that haven't been embedded (pass `missing="skip"` if gaps are expected). `vectors.threshold(a, b)` returns the "same story" cutoff for the current model. **Those cutoffs are placeholders** that warn when used; `vectors/sample_pairs.py` samples about 150 pairs of real headlines to label (the same pairs the tracker would link, spread across similarity, with weights), and `python -m undercurrent.vectors.calibrate --pairs pairs_unlabelled.csv --labels "1011 0010 ..."` turns the labels into a cutoff with a range. On the cloud VM: `scripts/gcp/vm.sh pairs`, then `vm.sh calibrate`.
- **Cleaning is not safe to run twice** (it can strip real words from "A - B debate - CNN"), so the store embeds the already-cleaned text with `embed_cleaned()`. Use `embed()` only on raw text.
- **Device:** CUDA, then Apple's MPS, then the CPU, automatically (`UC_DEVICE` or `--device` overrides). `UC_HALF=1` runs a CUDA model in half precision, which is about twice as fast.

- **Encode everything, per day.** Days are independent, so run them in parallel. Dedupe by text hash first, so a wire headline repeated on many sites is embedded once.
- **Store one file per day:** fp16, L2-normalised, with an id file and a manifest (model, dimension, text version), as in `docs/WORKPLAN.md` section 3.4. Expect roughly 80–150 GB for a few years at 384 dimensions (a guess; measure on one day). In object storage that costs a few dollars.
- **Keep them in the bucket only:** not in git, not on laptops.
- **Why keep them.** We will retune the clustering. With stored vectors that is a rerun of the tracker; without them it means re-embedding everything, which takes hours and $5–50 each time. Stored vectors also let us run several tracker settings in parallel.
- **Don't keep item vectors in the exports.** The web app and the `Item.similarity` field only need the similarity to the story centroid, which the tracker records.
- **How C counts a story's future articles** is still open. The original rule (nearest frozen day-t centroid) needs future vectors for every (story, day), which is expensive at this scale. Counting from the tracker's assignments is much cheaper, and is valid if assignments are never revised once written. Stories that merge or split must keep a lineage table, so counts follow the right id. C to decide and write it in Notes.
- **Fit anything corpus-wide** (PCA, UMAP) on training days only.
- **Idea, untested:** fine-tune the encoder on "same event" pairs. The GDELT Mentions table links many articles to one event id, which could give free training pairs. It has to be done before the bulk encode.

### Clustering: the story tracker (built, not yet run on real data)

`stories/graph.py` is the small prototype (tested on 153 hand-written items); it can't handle a real day. Its replacement is built, and tested on synthetic stories and the demo headlines:

| File | What it does |
|---|---|
| `stories/cluster.py` | Clusters one batch of items: nearest neighbours with FAISS, then connected components, then Louvain inside each component. A split is kept only if it makes the groups clearly tighter, because Louvain also cuts one coherent story into pieces |
| `stories/tracker.py` | The day-by-day state: match items to story centroids, start new stories from the leftovers, merge duplicates, retire idle stories |
| `stories/track.py` | The command that runs it over a range of days, resumably |

Run it once the day's vectors exist, in its own process:

```bash
uv run python -m undercurrent.stories.track --start 2026-01-01 --end 2026-03-31
```

It writes `story_assignments/<day>.parquet`, `stories_daily/<day>.parquet`, `story_lineage/<day>.parquet` (only on days with a merge) and `tracker_state/state.pkl`, which is rewritten every day so a killed run resumes where it stopped. Every day has to be processed, in order.

Each day, in order:

1. **Fade** every story's centroid (half-life 7 days), so it follows how the story is worded now.
2. **Match** each item to the nearest story centroid above `assign_sim`, unless it is clearly about somewhere else (the place rule from `graph.py`).
3. **Start new stories** from the items that matched nothing: they are clustered among themselves, and each group of at least `min_size` becomes a story.
4. **Merge** stories whose centroids are now nearly the same. The oldest id survives and the merge is written to `story_lineage`.
5. **Retire** stories with no new item for 14 days.

What we measured (made-up and demo data only):

- **Speed:** about 200,000 items a day at 384 dimensions takes 13–37 seconds (the first day is slowest) and peaks at 2.3 GB. A year is roughly 1.5–2 hours.
- **Accuracy on synthetic stories:** every story assignment was correct, and no noise item was put in a story. On the 102 hand-labelled demo headlines, `--min-size 2` finds 16 of the 27 stories with 92% purity; the default of 3 finds 2, because the demo has only about 2 headlines per story per day.
- **Tests** check that results for earlier days don't change when later days are added, that input order doesn't matter, that resuming equals an uninterrupted run, that merges are recorded, and that retired stories don't come back under the old id.

Things to know:

- **A story starts when at least `min_size` items about it arrive on the same day.** A slow trickle (one headline a day) is missed until it reaches that. This keeps every decision point-in-time, and at full resolution it costs little: stories too small to start are far below the 20 articles a week the forecast needs. Carrying leftovers over for a few days is the obvious extension; it would need an `assigned_on` column to stay point-in-time.
- **The cutoffs are placeholders.** `vectors/thresholds.py` has 0.42 for news vs news (tuned on the demo), `assign_sim` is that + 0.05, and `merge_sim` is 0.85. Read 20 real stories and calibrate before trusting any of it.
- **PyTorch and FAISS crash each other on macOS** if both run in one process (PyTorch must load first, and FAISS then runs on one thread). Embedding and tracking are separate processes, so this doesn't arise. The code guards the mixed case, but keep them apart. It was not seen on Linux, but that is untested.
- **Merged stories:** rows already written keep the old id, and `story_lineage` maps old to surviving id. The forecast code doesn't apply the lineage yet.
- **Still to do for B:** `story_wiki_entities.parquet`, and attaching posts and Wikipedia items (`post_count_7d` is 0 until then). Location, footprint, entities and the exporter are done (below).

#### Export: `python -m undercurrent.stories.export`

Reads `story_assignments`, `stories_daily`, `articles` and, if they exist, `forecasts.parquet` and `model_metrics.json`, and writes `data/export/` in the `contracts.py` shapes: `meta.json`, `snapshots/<day>.json` with each day's `--top` (300) biggest stories, and `stories/<id>.json` for every story in any snapshot, with its `--items` (25) articles closest to the centroid.

- **Point-in-time:** a snapshot for day t uses only assignments up to day t. Story details are one file per story, with everything up to `--end`.
- **`article_count_7d`** counts each story id's assignments over the 7 days ending that day, without applying `story_lineage`, exactly as `forecast/panel.py` counts. Outcomes are read from the later snapshots, so they always match what the globe showed on that day.
- **Footprint and location** (`stories/footprint.py`): each article splits one unit of attention over its places. A country is dropped from an article that also names a city or region in it, so countries only get heat from articles that name nothing more specific. Radius by GDELT type: city 75 km, region 250 km, country 500 km. The marker goes on the most-mentioned place, or on that country's most-mentioned city. `Item.locations` are the same places, so the dots and the heat line up.
- **Entities** are GDELT's persons and organisations named in at least 2 of the story's articles, with names only (no Wikipedia titles yet).
- Stories with fewer than `--min-articles` (3) articles in the week, or with no places, are left out.
- It writes into `data/export.tmp` and swaps it in at the end. It replaces only an export it or the demo wrote (`--force` otherwise).
- **Before the first long run,** label 1–2k "same story?" pairs (an LLM can draft them) and run `python -m undercurrent.vectors.calibrate`. Badly formed stories make the forecast target noisy, so this matters more than the choice of forecast model.

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

- **Models:** gradient boosting (one per horizon, quantile loss) and a sequence model (a GRU over each story's last 28 days) are both built; average them. Calibrate the ranges on a held-out recent block, so "80%" really covers about 80%. The plan for using them is under "Future prediction".
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

## Future prediction: the plan

The part of the project that looks ahead. For every story and day it gives the story's expected size (`article_count_7d`) 1, 3, 7 and 14 days later, with an 80% range. Scrub the globe forward and it redraws with those projections; scrub back and it shows what the model said then, next to what actually happened. The data shape is `Forecast` and `Outcome` in `pipeline/undercurrent/contracts.py`; the code and its guide are in `pipeline/undercurrent/forecast/`.

### What we predict, and how it's shown

**The relevance metric** is `article_count_7d`: how many articles a story had in the 7 days ending that day. The exporter already computes it for every story and day; it sizes the story's heat on the globe. Plotted over the story's whole life, it shows how relevant the story was over time.

**The prediction:** for each story on each day, how many articles it will have in the 7 days ending 1, 3, 7 and 14 days from now (`article_count_7d` at t+1, t+3, t+7, t+14). Each comes as a middle estimate, an 80% range (low–high) and the main reasons (`factors`). It's the number that sizes a story's heat on the globe, so a forecast is "how big this story will be on the globe in a few days". The model card compares it with "it stays the size it is" (persistence).

**Shown in four places:**

| Where | What | Data |
|---|---|---|
| **Globe, future view** | The time bar continues past `meta.latest` by `meta.horizons`: each story's heat is drawn at its forecast size | `StorySummary.forecast` in the latest snapshot |
| **Story panel: the forecast graph** | x: dates; y: articles in the past 7 days. The history is a solid line up to the selected day; the forecast is a dashed line through the 1/3/7/14-day estimates, with a shaded band from low to high. In replay (a past day), what actually happened is drawn on top | history: `StoryDetail.timeline` (articles per day) as a **rolling 7-day sum**; forecast: `StorySummary.forecast.horizons` of the selected day's snapshot; actual: the rest of `timeline`, or `StorySummary.outcome` |
| **Emerging stories page** | The stories projected to grow most, ranked by projected growth: the 7-day forecast's middle estimate against today's size, `log2((articles_7d + 1) / (article_count_7d + 1))`. Each row has the title, place, category, today's size, the projection with its range, the top factor, and a small version of the forecast graph. Clicking a row opens the story on the globe. In replay, the list for a past day sits next to what those stories actually did | the selected day's snapshot: `StorySummary.forecast`, `article_count_7d`, `growth_7d` |
| **Model card** | Per horizon, the typical miss next to persistence's, and how often the range held the outcome. Horizons that don't beat persistence are greyed out | `meta.model` |

**Units:** `timeline` counts articles **per day**, but forecasts are **7-day totals**. Plot the history as a rolling 7-day sum so both are on the same scale; the rolling sum on day t equals that day's `article_count_7d` in the snapshot.

**The demo moment (replay):** go back to before a story blew up, show the band predicting it, then reveal the real line landing inside the band.

### What exists today

| Piece | What it is | Where |
|---|---|---|
| Rows | One per (story, day): 23 features, the last 28 days of history, and the targets | `dataset.py`, `features.py` |
| Trees (`hgb`) | Gradient boosted quantile models, one set per horizon. Fast; the baseline | `models.py` |
| Neural net (`gru`) | A GRU over the last 28 days plus the same features, predicting every horizon at once. Can be pretrained | `neural.py` |
| Blend and calibration | Averages the models, then widens or narrows the range so "80%" covers about 80% | `backtest.py` |
| Backtest | Walk-forward windows that never overlap, three baselines, confidence intervals | `backtest.py` |
| Inputs | Made-up stories, theme × country proxy stories, or B's real stories | `synthetic.py`, `proxy.py`, `panel.py` |
| Output | `forecasts.parquet`, `model_metrics.json`, and contract objects for B's exporter | `export.py` |

64 tests pass, including checks that no feature, no 28-day window and no pretraining data can see the future. **None of it has run on real data yet.**

### Run it

```bash
uv run python -m undercurrent.forecast.run --source synthetic                      # no data needed
uv run python -m undercurrent.forecast.run --source proxy                          # needs A's articles
uv run python -m undercurrent.forecast.run --source stories                        # needs B's stories
uv run python -m undercurrent.forecast.run --source stories --model hgb gru        # trees + net, averaged
uv run python -m undercurrent.forecast.run --source stories --model gru --pretrain proxy   # pretrain first
```

Trial runs (synthetic, proxy) write to `data/processed/forecast/<source>/`, so they can't overwrite what B's exporter reads. The output shows each model's error on its own next to the blend and the baselines.

### The plan, in order

Each stage has a rule for when to move on. The trees come first because they are fast and dependable, and they put real forecasts on the globe soonest.

| Stage | What | Move on when |
|---|---|---|
| 0. Dry run on proxy series | `--source proxy` as soon as A's first articles land. No stories needed | It runs on real articles, and the trees beat "it stays the same" at 1 and 3 days. If not, fix the features or the target before building more |
| 1. Real stories, trees only | `--source stories --model hgb`. First check that B's assignments are never revised (re-run the tracker twice on a sample and compare) | The report shows which horizons beat persistence on the final window. Check coverage for small, medium and large stories separately; if it is off by more than ~5 points, calibrate per size band |
| 2. Onto the globe | B's exporter calls `export.load_forecasts()`; D builds the future view, the story forecast graph and the model card | The globe scrubs past today using real forecasts, and the model card hides horizons that don't beat persistence |
| 3. Add the neural net | `--model hgb gru`, compare the columns | Keep it in the blend only if the blend beats the trees alone with the 95% interval below zero on the earlier windows. Then confirm once on the final window |
| 4. Pretrain the net | `--pretrain proxy` on years of theme × country series (or aggregates pulled from BigQuery). This is where the net should start to win | It beats the un-pretrained net on the earlier windows |
| 5. More inputs | Story centroid (reduced, fit on training days only), Events mix, scheduled dates, Wikipedia at scale. One at a time | Each is kept only if the earlier windows improve |
| 6. Longer reach | A 30-day horizon, and a model of new-story volume by region and category (needs one optional contract field) | The backtest passes. Until then the globe only shows stories that already exist |

### How we decide what ships

- **A horizon is offered as a forecast only if** its calibrated blend beats persistence with the 95% interval below zero on the final window (`beats_persistence` in `forecast_report.json`). Otherwise the model card shows it greyed out.
- **Tune on the earlier windows.** Look at the final window once per decision. If we tune on it, its numbers are no longer honest.
- **Judge each model on its own column,** not just the blend.
- **Beat all three baselines:** persistence, damped momentum, and the typical change for a story of that age and category.

### Compute (rough estimates)

The trees take minutes on a CPU. The net trained at about 34,000 rows a second on a small test on a laptop-class CPU, so a year of rows (1–3 million, a guess) is 1–2 minutes per epoch. A full backtest is several windows × ~20 epochs, so roughly 1–3 hours on a CPU and minutes on a GPU. Pretraining adds a few epochs on the auxiliary data. Measure on the real data before planning around these.

### Risks and open decisions

- **Counting from `story_assignments` is only honest if B never revises an assignment.** The notes in `PLAN.md` ask for this; verify it in stage 1.
- **The net may not beat the trees.** Rows from one story are near-copies, so there are fewer independent examples than rows. Pretraining is the bet; the trees stay as the baseline either way.
- **Only stories that already exist are forecast,** and only those with at least 20 articles in the last 7 days. Others are drawn unchanged.
- **Never run on real data.** Expect the first real run to break something in the loaders. The error messages name the file to fix.
- **Open:** one model for all horizons in the trees too (probably a small gain), and what the minimum story size should be once the data is full resolution.

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
