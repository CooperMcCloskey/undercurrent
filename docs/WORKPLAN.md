# Work plan: division of labour and timeline

This is the step-by-step plan for the four agents (A: Ingest, B: Stories, C: Forecast, D: Web/API). It adds detail to `PLAN.md` without replacing it.

- `PLAN.md` is the status board. Tick boxes there, in your own section only.
- This file says **what to build, in what order, in what file, and what to hand to whom**.
- `docs/ARCHITECTURE.md` explains how each data source works and its pitfalls. Read your section before starting.
- `AGENTS.md` rules still apply: stay in your own folders, only change the contract the documented way, and commit only when your human says so.

**How agents should use this file:** find your workstream letter, read "Your job" and "Hand-off files" for it, then work through the phases in order. When you finish a hand-off, do three things: commit it, tick the box in `PLAN.md`, and tell your human the exact message under "Announce", so they can tell the others.

---

## 1. Ground rules for working in parallel

1. **The pipeline hand-off files below are internal contracts.** `contracts.py` only covers `data/export/`. The parquet files that pass between A, B and C are defined in section 3 of this file. Don't change a column's name or type without telling the team. Adding a column is fine.
2. **Only B writes `data/export/`.** C writes forecasts to `data/processed/forecasts.parquet`, and B's exporter merges them into the snapshots. This stops two people writing the same JSON files.
3. **Days are UTC calendar days**, as `datetime.date`. "As of day t" means everything published up to 23:59:59 UTC on day t.
4. **Point-in-time rule:** anything computed as of day t uses only data from day t or earlier. Every function that takes an `as_of` argument must filter its input to that date first.
5. **Never block on someone else.** If an input doesn't exist yet, build against a stub, a fake or a proxy with the same schema, and keep going. Each section below names the stub to use.
6. **Reading another workstream's code is fine; editing it isn't.** C may `from undercurrent.stories.embed import load_embeddings`. If you need a change there, put it under "Needs from others" in `PLAN.md`.
7. Every module runs as `uv run python -m undercurrent.<area>.<module>` with `argparse`. Fetch scripts take `--start/--end` or `--days`, skip files already on disk, and default to small date ranges.
8. Run `npm run check` before every commit. Run `git pull --rebase` before every push.

---

## 2. Who owns what

| Agent | Owns | Main outputs | Needs |
|---|---|---|---|
| **A: Ingest** | `pipeline/undercurrent/ingest/` | `articles.parquet`, Wikipedia tables, `resolve()`, Bluesky parquet and live generator | nothing (always unblocked) |
| **B: Stories** | `pipeline/undercurrent/stories/` | embeddings, `stories_daily.parquet`, `story_assignments.parquet`, **all of `data/export/`**, `nearest_story()` | A's `articles.parquet`; C's `forecasts.parquet` (optional, merged if present) |
| **C: Forecast** | `pipeline/undercurrent/forecast/` | `forecasts.parquet`, `model_metrics.json`, pitch examples, the `ModelMetrics.calibration` contract change | B's daily centroids, B's embeddings, A's Wikipedia tables |
| **D: Web/API** | `pipeline/undercurrent/api/`, `web/` | the globe app, `/api/live`, offline mode, **demo script and Devpost draft** | mock data until B's first export; A's and B's live hooks |

D has the least blocking work early on, so D also owns the demo and submission material. That keeps A, B and C free for fixes in the last hours.

---

## 3. Hand-off files (internal contracts)

All paths are relative to `data/`. Lists are parquet list columns, and structs are parquet structs.

### 3.1 `processed/articles.parquet` (A → B, C)

One row per **deduplicated** article. A may also write it partitioned (`processed/articles/day=YYYY-MM-DD/*.parquet`), as long as `duckdb.read_parquet("data/processed/articles/**/*.parquet", hive_partitioning=true)` returns these columns.

| Column | Type | Notes |
|---|---|---|
| `id` | str | First 16 hex characters of sha1(url). Stable across reruns. |
| `url` | str | `DocumentIdentifier` |
| `domain` | str | `SourceCommonName` |
| `published_at` | timestamp (UTC) | from `DATE` |
| `day` | date | UTC date of `published_at` |
| `title` | str | from `<PAGE_TITLE>`, HTML-unescaped and stripped. **Drop rows with no title.** |
| `locations` | list<struct{name: str, country: str?, lat: float, lon: float, type: int}> | country is **ISO alpha-2**, converted from FIPS |
| `persons` | list<str> | |
| `orgs` | list<str> | |
| `themes` | list<str> | V1Themes, without offsets |
| `tone` | float | first value of V1.5Tone |
| `dup_count` | int | how many syndicated copies were folded into this row (1 = unique). Volume = row count; this column is for reference. |
| `sampled` | bool | true if the article came from a 1-file-per-hour backfill day, false if from a full day |

**Volume convention (everyone uses this):** a story's volume for a day is the number of deduplicated articles that day. **Don't mix sampled and full days in one growth ratio.** For backfill days, every day uses the same sampling rate, so ratios still hold. For the most recent full-data days, C should multiply sampled-day counts by 4 or compare only like with like. C decides which and writes the rule in `PLAN.md` Notes.

### 3.2 Wikipedia tables (A → B, C)

Full guide: `pipeline/undercurrent/ingest/WIKIPEDIA.md`. Covers 8 languages (en ja de fr ru es it zh), linked through Wikidata IDs.

| File | Columns |
|---|---|
| `processed/wiki_entity_daily.parquet` | `qid, title_en, day, views, edits, n_langs, views_<lang>…, edits_<lang>…`: **one row per entity and day, languages summed. Start here.** |
| `processed/wiki_pageviews.parquet` | `lang, title, day, views, complete` |
| `processed/wiki_edits.parquet` | `lang, title, day, edits, complete` |
| `processed/wiki_top.parquet` | `lang, day, rank, title, views, desktop_share, suspect_bot` (daily top-1000 per language) |
| `cache/wiki_resolve.json` | `{ "<normalised name>": "<English title>" or null }` |
| `cache/wiki_links.json` | `{ "<English title>": {"qid", "titles": {lang: title}, "checked"} }` |

Python API, in `undercurrent.ingest.wiki_resolve`:

```python
def resolve(name: str) -> str | None: ...                     # English title; cached
def resolve_many(names: list[str]) -> dict[str, str | None]: ...
def resolve_entities(names: list[str]) -> dict[str, dict | None]: ...  # {"qid", "title", "titles": {lang: title}}
```

Wikipedia fetch CLI, which B (or A on B's behalf) runs with the stories' resolved entity titles. Allow about 5 seconds per entity: Wikimedia allows 200 requests per minute.

```
uv run python -m undercurrent.ingest.wiki --titles-file data/processed/wiki_titles.txt --start ... --end ...
```

### 3.3 `processed/bluesky/<YYYY-MM-DD>.parquet` (A → B)

| Column | Type |
|---|---|
| `id` | str (at:// URI) |
| `url` | str (`https://bsky.app/profile/<did>/post/<rkey>`) |
| `author` | str (handle if known, otherwise did) |
| `text` | str |
| `langs` | list<str> |
| `created_at` | timestamp (UTC) |

Live hook, in `undercurrent.ingest.bluesky`:

```python
async def stream_posts(langs: set[str] = {"en"}) -> AsyncIterator[dict]: ...  # same keys as the columns above
```

### 3.4 Embeddings (B → C)

- `processed/embeddings/<YYYY-MM-DD>.npy`: float32 array of shape (n, 384), **L2-normalised**.
- `processed/embeddings/<YYYY-MM-DD>.ids.parquet`: one column, `id`, in the same row order.
- Loader, in `undercurrent.stories.embed`:

  ```python
  def load_embeddings(days: list[date]) -> tuple[list[str], np.ndarray]: ...
  ```

### 3.5 `processed/stories_daily.parquet` (B → C)

One row per (story, day): the state of the story **as of the end of that day**, computed only from data up to that day.

| Column | Type | Notes |
|---|---|---|
| `story_id` | str | matches `contracts.STORY_ID_PATTERN`, stable across days |
| `day` | date | as-of day |
| `centroid` | list<float32>[384] | L2-normalised mean of member embeddings |
| `label` | str | most central headline |
| `location` | struct{name, country, lat, lon} | most-mentioned location |
| `entities` | list<str> | top persons and orgs |
| `first_seen` | date | |
| `n_articles_day` | int | articles assigned on this day |

### 3.6 `processed/story_assignments.parquet` (B → C, B's exporter)

`article_id: str, story_id: str, day: date, similarity: float`. Each article is assigned once, on the day it arrives, to the story it matched **as of that day**.

### 3.7 `processed/forecasts.parquet` and `processed/model_metrics.json` (C → B's exporter)

| Column | Type |
|---|---|
| `story_id` | str |
| `day` | date |
| `p_grow` | float |
| `model_version` | str |
| `factors` | list<struct{name: str, contribution: float}> (top 3–5) |
| `growth_ratio` | float, null if the outcome isn't known yet |
| `grew` | bool, null if the outcome isn't known yet |

`model_metrics.json` is exactly the `ModelMetrics` shape in `contracts.py`. The exporter validates it with `ModelMetrics.model_validate_json` and puts it in `meta.json`.

### 3.8 Live matching (B → D)

In `undercurrent.stories.live_match`:

```python
def nearest_story(text: str) -> tuple[str, float] | None: ...  # (story_id, similarity); None if under the Bluesky threshold
```

It loads the latest day's centroids once and reuses them.

---

## 4. Timeline

H = hours since hacking starts. Every phase ends with a **sync**: a 5–10 minute stand-up where each human says what landed and what's blocked.

### Phase 0: before the event

| Who | Task |
|---|---|
| All | Fill in owner names in `PLAN.md`. Run `npm install && uv sync && npm run dev:mock`, and **open the globe in a browser**. Nobody has done that yet. |
| All | Read your section of `docs/ARCHITECTURE.md` and all of this file. |
| All | If two agents share a laptop, set up one `git worktree` per agent. |
| A | Apply for Reddit API access, or mark Reddit as cut in `PLAN.md`. |
| B | Download and cache the embedding model: `uv run python -c "import undercurrent.config; from sentence_transformers import SentenceTransformer as S; S(undercurrent.config.EMBEDDING_MODEL)"` |
| D | Check the hackathon rules on code written beforehand. Start the Devpost note about the pre-built scaffolding. |

### Phase 1: H+0 to H+2 → M0, everyone running

| Agent | Tasks, in order | Files |
|---|---|---|
| **A** | 1. `ingest/fips.py`: FIPS 10-4 → ISO alpha-2 table (watch for UK, GM, CH, SZ, AS, RS). <br>2. `ingest/gdelt_download.py`: read `masterfilelist.txt`; `--start/--end`, `--sample-per-hour N`, `--translation`; skip existing zips; download to `raw/gdelt/YYYYMMDD/`. <br>3. Download **one full day**, measure its size and download time, and write both in `PLAN.md` Notes. <br>4. **Start the backfill in the background right away**: 60 days, `--sample-per-hour 1` (or less if the measurement says so). <br>5. `ingest/gkg_parse.py` → `articles.parquet` for that one day, with dedup (same URL; same normalised title within a day). | `ingest/*.py` |
| **B** | 1. `stories/embed.py`: embed the titles for a day in batches (batch size 256), cache `.npy` + `.ids.parquet`, `load_embeddings()`. Test it on 500 made-up or sample titles until A's day lands. <br>2. Draft `stories/cluster.py` with HDBSCAN on normalised vectors (`min_cluster_size` about 5, metric euclidean on unit vectors, which is equivalent to cosine). | `stories/embed.py`, `stories/cluster.py` |
| **C** | 1. Write the label definition in `PLAN.md` Notes: `grew = vol(t+1..t+7) >= 2 × vol(t-6..t)`, only when `vol(t-6..t) >= 20`. <br>2. `forecast/proxy.py`: proxy stories = (top GKG theme × country) daily counts from `articles.parquet`, with the same columns that `forecast/labels.py` will need later. Until A's day lands, use a generated fake frame. <br>3. **Contract change (do it now, so D can build on it):** add an optional `calibration: list[CalibrationBin] = []` to `ModelMetrics`, where `CalibrationBin(p_pred: float, p_obs: float, n: int)`. Then `npm run gen:types`, update `mock.py`, `npm run gen:mock`, `uv run pytest`. One commit, `contract: add ModelMetrics.calibration`. Announce it. | `forecast/proxy.py`, `contracts.py`, `mock.py` |
| **D** | 1. Zustand store: `selectedStoryId`, `currentDate`, `replayMode`, `revealOutcome`. <br>2. Globe: `pointsData` (radius ∝ sqrt(article_count_7d), colour = `p_grow` on `d3-scale-chromatic` `interpolateYlOrRd`), country outlines from `world-atlas` 110m, hover labels. <br>3. Story panel skeleton, opened by clicking a point; fetches `/api/stories/{id}`. | `web/src/**` |

**Sync at H+2:** confirm the schemas in section 3 as written, or announce any changes. A reports the one-day GKG size and the backfill depth chosen.

**Announce:** A: "`articles.parquet` for <day> is in `data/processed/`; the backfill is running." C: "Contract changed: `ModelMetrics.calibration`. Pull and rerun gen:types if needed."

### Phase 2: H+2 to H+8 → M1, real stories on the globe

**Critical path:** A's 7 days → B's clustering and tracking → B's first export → D switches from mock data.

| Agent | Tasks, in order |
|---|---|
| **A** | 1. Parse **the last 7 days in full** (every 15-minute file, `sampled=false`). Target H+5. **Announce.** <br>2. `ingest/wiki_top.py`: daily top-1000 pageviews over the backfill range → `wiki_top.parquet`. Needs no titles. <br>3. `ingest/wiki_resolve.py`: `resolve()`/`resolve_many()` via the search API, cached, with `config.WIKIMEDIA_USER_AGENT`, max about 10 requests per second. **Announce.** <br>4. `ingest/wiki.py`: per-title pageviews and edits for a titles file. <br>5. Keep checking on the backfill and parse each day as it finishes (the parser must work one day at a time). |
| **B** | 1. Embed the 7 days. <br>2. Cluster a trailing 3-day window ending on each day. **Print 20 clusters with 5 titles each and read them** before tuning. Write the parameters you chose in Notes. <br>3. `stories/track.py`: for each day in order, match new clusters to the previous day's story centroids (cosine ≥ about 0.75, Hungarian or greedy best-first). Matched clusters keep their id; unmatched ones get a new slug (`slugify(label)[:40] + "-" + 4 hex characters`). Write `stories_daily.parquet` and `story_assignments.parquet`. <br>4. Labels (title closest to the centroid), location (most common location, ignoring country-level entries when a city is available), entities (`resolve_many` once A ships it; until then names only). <br>5. `stories/export.py`: build `Meta`, `Snapshot`, `StoryDetail` with the Pydantic models in `contracts.py`, write `data/export/`, run `uv run pytest`. Use `forecast=None` until `forecasts.parquet` exists. **Target H+8. Announce.** |
| **C** | 1. `forecast/labels.py` on proxy stories: for each (story, t), compute past volume, future volume, `growth_ratio` and `grew`, with the minimum-volume filter. <br>2. `forecast/features.py`: volume (log), `growth_7d`, acceleration (growth this week minus last week), distinct domains, distinct countries, tone and Δtone. Wikipedia features are stubbed as NaN for now (HGB handles NaN). <br>3. `forecast/backtest.py`: time split (last 14 days = test), momentum baseline (rank by `growth_7d`; Brier score via logistic regression on `growth_7d` alone), AUC, Brier, calibration bins. Run it on the proxies end to end, so the harness is finished before real stories arrive. |
| **D** | 1. Time slider over `meta.dates`; changing it fetches `/api/snapshots/{day}`. <br>2. Story panel: title, timeline chart (recharts: articles; posts and pageviews when present), items grouped by `kind`, entities, forecast with its factors as a signed bar list. <br>3. Model card component: AUC and Brier vs baseline, calibration chart (reliability diagram against the diagonal), reading `meta.model`. Use the mock first. <br>4. When B announces the first export, run `npm run dev` against the real data and fix whatever breaks (empty fields, long titles, too many points). |

**Sync at H+8 (M1):** real stories on the globe for 7 days. If clustering still looks bad, see "Fallbacks" in section 6.

### Phase 3: H+8 to H+16 → M2, forecast backtest

| Agent | Tasks, in order |
|---|---|
| **A** | 1. Finish parsing the backfill (about 60 days, sampled). **Announce** with the exact date range. <br>2. Per-entity pageviews and edits for the titles B and C send (B writes `data/processed/wiki_titles.txt`). <br>3. `ingest/bluesky.py`: Jetstream consumer that writes daily parquet (filtered to English plus a cheap keyword prefilter), and the `stream_posts()` async generator. **Announce.** |
| **B** | 1. Run embed → cluster → track over **the whole backfill, one day at a time in order**, so every `stories_daily` row is point-in-time. This takes a while, so start it as soon as A's backfill is parsed. **Announce** when `stories_daily.parquet` covers the backfill. <br>2. Write `wiki_titles.txt` (entities of the top stories → `resolve_many`). <br>3. `stories/attach.py`: Bluesky posts → nearest centroid. Pick the threshold by reading about 50 pairs at different similarities, and write it in Notes. Fill `post_count_7d` and `items`. <br>4. Exporter: merge `forecasts.parquet` (forecast + outcome per story-day) and `model_metrics.json` into the snapshots and `meta.json`. Add `wiki_pageviews` to the timelines. Re-export and run pytest. |
| **C** | 1. Switch from proxies to real stories. For each (story, t), take the frozen centroid from `stories_daily` at t, and count each future article (t+1..t+7) towards the story whose **day-t** centroid is nearest, above B's threshold. Don't use `story_assignments` for future volume, because it reflects later re-clustering. <br>2. Add the Wikipedia features (pageview and edit growth over the story's entities, via `wiki_resolve`). <br>3. Logistic regression (standardised features), then `HistGradientBoostingClassifier`. Pick the better one on the test window. Report honestly if neither beats momentum. <br>4. Factors: LR coefficient × standardised value; for HGB, a per-story approximation (difference from the mean prediction with each feature set to its median), top 3–5. <br>5. Write `forecasts.parquet` (every story-day in the backfill and the live days) and `model_metrics.json`, with calibration. **Announce.** <br>6. `forecast/examples.py`: list stories with high `p_grow` at t that grew 3× or more afterwards. Pick 2–3 for the pitch and write the ids and dates in Notes. |
| **D** | 1. Replay mode: choose a past date → show the predictions → press a key or button to reveal the outcome (ring colour or ✓/✗ for `grew`, actual growth ratio). <br>2. Rings layer for stories with `p_grow` above about 0.6 ("about to break"). <br>3. Offline fallback: check that `npm run dev` works with wifi off (no CDN globe textures; serve the globe image from `web/public/`). <br>4. Start `docs/DEMO.md`: a 2-minute script, using C's pitch examples when they land. |

**Sync at H+16 (M2):** `meta.json` has real model metrics; the replay works on at least one pitch example.

### Phase 4: H+16 to H+20 → M3, replay and live

| Agent | Tasks |
|---|---|
| **A** | Keep the Jetstream consumer stable (reconnect with backoff, cursor resume). Help D wire `stream_posts()`. Then: translated GKG feed, or Reddit, only if everything else is green. |
| **B** | `stories/live_match.py`: `nearest_story()`. Final full export for the demo dates. Check story titles and locations on the globe for embarrassing errors (wrong country, junk headlines) and fix the causes. |
| **C** | Freeze the model (`model_version` string, saved under `processed/models/`). Write a short "Methodology" paragraph in `PLAN.md` Notes for the Devpost: label, split, baseline, metrics, known limits. |
| **D** | `api/live.py`: `WS /api/live` that iterates `stream_posts()`, calls `nearest_story()`, and sends `LivePost` JSON. The web side gets a ticker plus a pulse ring on the story's location. Test with `fastapi.testclient.TestClient`. Then polish: loading and empty states, intro overlay, keyboard shortcuts (←/→ change the day, space reveals the outcome, `m` opens the model card). |

**Sync at H+20: feature freeze.**

### Phase 5: H+20 to the end, freeze

| Who | Task |
|---|---|
| All | Bug fixes only. No new features or contract changes. |
| D (lead), all | Final `docs/DEMO.md`; Devpost text (tracks: HRT and G-Research; methodology from C; note on pre-built scaffolding); screenshots; video. |
| All | Rehearse twice, once with wifi off. Commit the final export somewhere outside git (shared drive) as a backup. |

---

## 5. Hand-off checklist

Tick these in your own `PLAN.md` section as well. Times are targets.

| # | By | From → to | What |
|---|---|---|---|
| 1 | H+1 | C → all | Contract: `ModelMetrics.calibration` |
| 2 | H+2 | A → B, C | `articles.parquet`, 1 day |
| 3 | H+5 | A → B, C | `articles.parquet`, last 7 days in full |
| 4 | H+6 | A → B, C | `resolve()` and `wiki_top.parquet` |
| 5 | H+8 | B → D | first real `data/export/` (7 days) |
| 6 | H+10 | A → B, C | backfill parsed (about 60 days) |
| 7 | H+12 | B → C | `stories_daily.parquet` covering the backfill, plus embeddings |
| 8 | H+12 | A → B, D | Bluesky parquet and `stream_posts()` |
| 9 | H+15 | C → B | `forecasts.parquet` and `model_metrics.json` |
| 10 | H+16 | B → D | export with forecasts, outcomes and model metrics |
| 11 | H+17 | B → D | `nearest_story()` |

---

## 6. Fallbacks (decide at the sync, not alone)

| If | Then |
|---|---|
| The backfill is too slow or too big | Cut to 30 days, or sample 1 file every 2 hours. A test window of 14 days is still enough. |
| Clustering looks bad at H+6 | Switch to `AgglomerativeClustering(metric="cosine", linkage="average", distance_threshold≈0.35)` and export anyway, so D gets real data. Tune afterwards. |
| B's point-in-time stories aren't ready by H+12 | C trains and reports on proxy stories, and maps forecasts onto B's stories by the best overlap of articles. Say so in the Devpost. |
| The model doesn't beat momentum | Report it honestly on the model card. Show where it helps (for example on low-volume stories) if that's true. |
| Live Bluesky is flaky | Cut `/api/live`. The replay is the core of the pitch. |
| Wikipedia rate limits | Use only `wiki_top.parquet` (bulk), and restrict per-title fetches to entities of stories in the test window. |

---

## 7. Commit messages

Use these prefixes: `ingest:`, `stories:`, `forecast:`, `api:`, `web:`, `contract:`, `docs:`. For example: `stories: track story ids across days`.
