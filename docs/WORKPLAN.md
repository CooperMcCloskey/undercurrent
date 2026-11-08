# Work plan: division of labour and timeline

This is the step-by-step plan for the five workstreams (A: Ingest, V: Vectors, B: Stories, C: Forecast, D: Web/API), shared by four people. **Cooper owns V.** It adds detail to `PLAN.md` without replacing it.

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
6. **Reading another workstream's code is fine; editing it isn't.** C may `from undercurrent.vectors import load`. If you need a change there, put it under "Needs from others" in `PLAN.md`.
7. Every module runs as `uv run python -m undercurrent.<area>.<module>` with `argparse`. Fetch scripts take `--start/--end` or `--days`, skip files already on disk, and default to small date ranges.
8. Run `npm run check` before every commit. Run `git pull --rebase` before every push.

---

## 2. Who owns what

| Agent | Owns | Main outputs | Needs |
|---|---|---|---|
| **A: Ingest** | `pipeline/undercurrent/ingest/` | `articles.parquet`, Wikipedia tables, `resolve()`, Bluesky parquet and live generator | nothing (always unblocked) |
| **V: Vectors** (Cooper) | `pipeline/undercurrent/vectors/` | text cleaning per source, the vector store with `load()`, `embed()` (also used live), calibrated similarity thresholds, Wikipedia text tables (`wiki_events`, `wiki_new`) | A's `articles.parquet` and Bluesky parquet (V fetches the Wikipedia text itself) |
| **B: Stories** | `pipeline/undercurrent/stories/` | `stories_daily/`, `story_assignments/`, `story_lineage/`, **all of `data/export/`**, `nearest_story()` | V's vectors and thresholds; A's `articles.parquet`; C's `forecasts.parquet` (optional, merged if present) |
| **C: Forecast** | `pipeline/undercurrent/forecast/` | `forecasts.parquet`, `model_metrics.json`, pitch examples | B's story assignments (and lineage), A's articles and Wikipedia tables |
| **D: Web/API** | `pipeline/undercurrent/api/`, `web/` | the globe app, `/api/live`, offline mode, **demo script and Devpost draft** | mock data until B's first export; A's and B's live hooks |

D has the least blocking work early on, so D also owns the demo and submission material. That keeps A, V, B and C free for fixes in the last hours.

**Five workstreams, four people:** B still needs an owner. Either Cooper takes B as well (clustering and tracking are vector-space work), or A takes it once ingest is stable (A's work is front-loaded). Decide before the event and fill in `PLAN.md`.

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
| `sampled` | bool | true if the article came from a sampled day (1 file per hour; a fallback only, the plan is full resolution), false if from a full day |
| `translated` | bool | true if the article came from the machine-translated feed (`*.translation.gkg.csv.zip`), false if from the English feed |
| `lang` | str? | source language code from `TranslationInfo`; null for English-feed articles |

`translated` and `lang` were added when we decided to ingest both GDELT feeds. Adding columns is allowed (rule 1 in section 1); nothing else in this table changes.

**Volume convention (everyone uses this):** a story's volume for a day is the number of deduplicated articles that day. The plan is full resolution for every day, so mixing should not arise. If some days end up sampled (the fallback), **don't mix sampled and full days in one growth ratio**: every day in a ratio uses the same sampling rate, and on any given day the rate is identical across both feeds (English and translated). C can multiply sampled-day counts by 4 or compare only like with like, and writes the rule in `PLAN.md` Notes.

### 3.2 Wikipedia tables (A → B, C)

Full guide: `pipeline/undercurrent/ingest/WIKIPEDIA.md`. Covers 8 languages (en ja de fr ru es it zh), linked through Wikidata IDs.

| File | Columns |
|---|---|
| `processed/wiki_entity_daily.parquet` | `qid, title_en, day, views, edits, n_langs, views_<lang>…, edits_<lang>…`: **one row per entity and day, languages summed. Start here.** `views_en` exists for every entity. `views` covers 8 languages and `edits` exists only for tier-1 entities, over their whole history (see `full_from` in 3.6b) |
| `processed/wiki_selection.parquet` | `name, kind, tier, first_week, tier1_week, mentions, title_en, qid`: the GDELT names A fetched (each week's top 50 people and orgs), one row per name. `first_week` and `tier1_week` are the first weeks it was picked at all and as tier 1 |
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

Wikipedia fetch CLI for titles outside the selection (places, smaller names). Allow about 5 seconds per entity: Wikimedia allows 200 requests per minute. For the two-year run the entities came from the articles instead (`wiki_names` → `wiki_bulk`; `docs/DATA_COVERAGE.md`), and they're in `gs://undercurrent-data-cooper/wiki/`.

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

### 3.4 Vectors (V → B, C, D)

One store for every source. `source` is one of `news`, `bluesky`, `wiki_events`, `wiki_new`, `reddit`.

- `processed/vectors/<source>/<YYYY-MM-DD>.npy`: float16, shape (n, dim), **L2-normalised**. float16 halves the disk size; cosine precision is unaffected.
- `processed/vectors/<source>/<YYYY-MM-DD>.parquet`: `id`, `text` (exactly what was embedded), in the same row order.
- `processed/vectors/manifest.json`: `{model, dim, text_version}`. If the model or the text cleaning changes, every vector must be rebuilt, so **the model is locked at the H+2 sync**.
- Python API, in `undercurrent.vectors`:

  ```python
  Source = Literal["news", "bluesky", "wiki_events", "wiki_new", "reddit"]
  def embed(texts: list[str], source: Source) -> np.ndarray: ...   # cleans + embeds; (n, dim) float32, L2-normalised. Same model as the store; used live.
  def load(source: Source, days: list[date]) -> tuple[list[str], np.ndarray]: ...  # ids, float32 vectors
  def threshold(a: Source, b: Source) -> float: ...  # calibrated "same story" cosine cutoff for this model
  ```

- Thresholds live in git (`pipeline/undercurrent/vectors/thresholds.py`, keyed by model), not in `data/`, so every laptop gets the same ones. Similarity scales differ between models, so never hardcode a cutoff like 0.75 elsewhere: call `threshold()`. Until a pair is measured (`python -m undercurrent.vectors.calibrate`), its value is a placeholder and `threshold()` logs a warning.
- Built (`vectors/store.py`): `python -m undercurrent.vectors.store --source news --start D --end D`, resumable, `--shard i/n` to split days across machines. `load()` refuses days that haven't been embedded (`missing="skip"` to allow gaps). Junk headlines get no vector (`vectors/clean.py`), so a day's vector file can have fewer rows than the day's articles. The store embeds already-cleaned text (`embed_cleaned()`); `embed()` is for raw text.

### 3.5 `processed/stories_daily/<YYYY-MM-DD>.parquet` (B → C, B's exporter)

One file per day, one row per story that **had an article that day**, computed only from data up to that day. Written by `stories/track.py`.

| Column | Type | Notes |
|---|---|---|
| `story_id` | str | matches `contracts.STORY_ID_PATTERN`, stable across days |
| `day` | date | as-of day |
| `n_articles_day` | int | articles assigned on this day (after any merge today) |
| `first_seen` | date | |
| `label` | str | the most central headline on the day the story started; fixed afterwards |
| `category` | str | `vectors.categorize(centroid)` as of the day; one of `contracts.Category`; sets the story's colour on the globe |

Still to come from B's footprint step, as extra columns: `location` (struct{name, country, lat, lon}, the most-mentioned location), `footprint` (list<struct{lat, lon, weight, radius_km}>, same fields as `contracts.FootprintPoint`; see `docs/ARCHITECTURE.md`) and `entities` (top persons and orgs).

There is no per-day `centroid` column: for every active story that would be about 11 GB a year. The centroids of the stories alive after the last day are in `tracker_state/state.pkl` (`Tracker.nearest()` matches a live post against them).

### 3.6 `processed/story_assignments/<YYYY-MM-DD>.parquet` and `story_lineage/` (B → C, B's exporter)

`article_id: str, story_id: str, day: date, similarity: float`, one file per day. Each article is assigned once, on the day it arrives, to the story it matched **as of that day**, and **a row is never rewritten afterwards**. An article that belongs to no story (nobody else wrote about it) has no row. `similarity` is the cosine to the story's centroid when it was matched.

When two stories turn out to be the same, the older id survives, today's rows already use it, and `story_lineage/<YYYY-MM-DD>.parquet` (`old_story_id, new_story_id, day`; only on days with a merge) records it. Rows written on earlier days keep the old id: that is what keeps the data point-in-time. Anything that needs one story's whole history applies the lineage as of the day it cares about.

### 3.6b `processed/story_wiki_entities.parquet` (B → C)

Written by `stories/wiki_entities.py` (run by `scripts/merge.sh`) from 3.6, 3.1 and A's `wiki_selection.parquet`. At most 5 rows per story, one per (story, entity), and only for stories that ever get a forecast row. How it's chosen, and why it's point in time: `docs/ARCHITECTURE.md`, "Wikipedia features, end to end".

| Column | Type | Notes |
|---|---|---|
| `story_id` | str | The tracker's id, without `story_lineage` (as C counts) |
| `qid` | str | Wikidata id; C joins it to `wiki_entity_daily.qid` |
| `full_from` | date or null | From this day on, the entity's 8-language `views` and its `edits` may be used; before it, or if null, `views_en` only. C reads it |
| `title_en`, `name`, `articles`, `rank`, `chosen_on` | | For people: the English title, the GDELT name, the story's articles naming it (up to `chosen_on`), its rank, and the last day of articles the choice looked at |

### 3.7 `processed/forecasts.parquet` and `processed/model_metrics.json` (C → B's exporter)

One row per (story, day, horizon). The target is the story's `article_count_7d` (articles published in the 7 days ending that day) on day `day + days`, so a projection is in the same unit as the snapshot it will turn into.

| Column | Type |
|---|---|
| `story_id` | str |
| `day` | date (the as-of day the forecast was made) |
| `days` | int (the horizon, one of `config.HORIZONS`) |
| `articles_7d` | float (median projection of `article_count_7d` on `day + days`) |
| `low`, `high` | float (the range that should hold `config.FORECAST_INTERVAL` of outcomes; `low <= articles_7d <= high`) |
| `model_version` | str |
| `factors` | list<struct{name: str, contribution: float}> (top 3–5, log2 units; may be empty) |
| `actual_articles_7d` | int, null until `day + days` has happened |

The exporter groups the rows of a (story, day) into `Forecast.horizons`, and the rows with an `actual_articles_7d` into `Outcome.horizons` (`contracts.py`). `model_metrics.json` is exactly the `ModelMetrics` shape in `contracts.py`. The exporter validates it with `ModelMetrics.model_validate_json` and puts it in `meta.json`, together with `Meta.horizons`.

### 3.8 Live matching (B → D)

In `undercurrent.stories.live_match`:

```python
def nearest_story(text: str) -> tuple[str, float] | None: ...  # (story_id, similarity); None if under the Bluesky threshold
```

It loads the latest day's centroids once and reuses them, embeds the text with `vectors.embed(text, "bluesky")`, and uses `vectors.threshold("bluesky", "news")`.

### 3.9 Wikipedia text (V → B, C)

V fetches and embeds these; A's Wikipedia tables (3.2) stay the numeric pageview and edit series.

| File | Columns |
|---|---|
| `processed/wiki_events.parquet` | `id: str` (`<day>:<n>`), `day: date`, `text: str` (one bullet from the daily "Current events" page, source citations removed), `titles: list<str>` (the articles it links to), `section: str` (the page's heading, e.g. "Armed conflicts and attacks"), `topic: str` (the headings it sits under, joined with " > "; empty if none), `topic_titles: list<str>` (the articles those headings link to: the story's own article). Written by `vectors/wiki_events.py` |
| `processed/wiki_new.parquet` | `id: str` (page id), `day: date` (created), `title: str`, `text: str` (title + first sentence) |

Current-events bullets are short, curated event summaries going back years, so they embed well against headlines and work for the backfill. Their links also give B and C Wikipedia titles for free. New event articles ("2026 Kenyan election recount") are a strong "this is getting big" signal.

---

## 4. Timeline

H = hours since hacking starts. Every phase ends with a **sync**: a 5–10 minute stand-up where each human says what landed and what's blocked.

### Phase 0: before the event

| Who | Task |
|---|---|
| All | Fill in owner names in `PLAN.md`, including who takes B. Run `npm install && uv sync && npm run dev:mock`, and open the globe in a browser to check it works on your laptop. |
| All | Read your section of `docs/ARCHITECTURE.md` and all of this file. |
| All | If two agents share a laptop, set up one `git worktree` per agent. |
| All | Cloud: confirm which cloud the credits are on, request GPU quota, make one bucket and keep every machine in its region. Follow "Before you start the VPS" in `README.md`. |
| A | Apply for Reddit API access, or mark Reddit as cut in `PLAN.md`. |
| V | Download and cache the candidate embedding models, so they don't eat event wifi: `uv run python -c "import undercurrent.config; from sentence_transformers import SentenceTransformer as S; S('<model id>')"` for each candidate (see Phase 1). |
| All | Agree with the team on **what we predict**. `Humans.md` says "Wikipedia views, edits and new articles in the cluster", while `PLAN.md` and C's tasks say "the story's news volume (`article_count_7d`) over the next few days". See `PLAN.md` open questions. |
| D | Check the hackathon rules on code written beforehand. Start the Devpost note about the pre-built scaffolding. |

### Phase 1: H+0 to H+2 → M0, everyone running

| Agent | Tasks, in order | Files |
|---|---|---|
| **A** | 1. `ingest/fips.py`: FIPS 10-4 → ISO alpha-2 table (watch for UK, GM, CH, SZ, AS, RS). <br>2. `ingest/gdelt_download.py`: handles **both feeds** (read `masterfilelist.txt` and `masterfilelist-translation.txt`); `--start/--end`, `--sample-per-hour N` (fallback only), `--translation`; the default run covers both feeds, and `--translation` still selects the translated feed; skip existing zips; download to `raw/gdelt/YYYYMMDD/`. <br>3. Get **one full day of both feeds** (on Google Cloud, dry-run the BigQuery query first), measure rows, unique titles, zipped size and time, and write them in `PLAN.md` Notes. <br>4. **Start the backfill in the background right away**, on the cloud machine: the most recent 12 months at full resolution (every 15-minute file of both feeds), the newest 60 days first and then going backwards. One file per day in the bucket, skipping days that exist. `--sample-per-hour` is a fallback only, if the measurement in step 3 says the full run can't finish. See "Scaling up" in `README.md`. <br>5. `ingest/gkg_parse.py` → `articles.parquet` for that one day, with dedup **across both feeds together** (same URL; same normalised title within a day), and the `translated` and `lang` columns filled. Check whether translated-feed `<PAGE_TITLE>` is original-language or English, and write the answer in `PLAN.md` Notes. | `ingest/*.py` |
| **V** | 1. `vectors/clean.py`: one text recipe per source, versioned as `TEXT_VERSION`. <br>– **news:** HTML-unescape; strip site-name suffixes (" - BBC News", " \| Reuters"); drop boilerplate titles ("Home", "Login", "404 Not Found", "Subscribe to read", fewer than 4 words). Otherwise these junk titles form their own clusters. <br>– **bluesky:** remove URLs and @mentions, turn hashtags into words, drop posts under about 6 words. <br>2. `vectors/__init__.py` + `vectors/store.py`: `embed()`, `load()`, `threshold()` as in section 3.4. Write per source and day. Cache by sha1 of the cleaned text, so wire copies repeated across days are never embedded twice. Use `device="mps"` on Apple Silicon and batches of 256. <br>3. **Pick the model.** Benchmark 2–3 models on one GKG file's titles (if A's parser isn't ready, download one file and pull `<PAGE_TITLE>` yourself). Measure titles per second on your laptop, and quality on about 100 hand-labelled pairs (same story / related / unrelated). Candidates: the current default `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (fast, 384-d), `intfloat/multilingual-e5-small` (prefix every text with `"query: "`; its similarities bunch up around 0.7–1.0, so its thresholds differ), and one stronger model if throughput allows. Titles/s on the GPU machine (benchmark there as well as on your laptop) × the 12-month backfill (both GDELT feeds) must fit in a few hours; days are independent, so add machines if needed. Set `config.EMBEDDING_MODEL`. <br>4. `vectors/wiki_events.py`: fetch the daily "Current events" pages (`Portal:Current_events/2026_November_6`) for the backfill range → `wiki_events.parquet` (section 3.9). About one request per day (around 365 for a year). | `vectors/*.py`, `config.py` (model id only) |
| **B** | 1. Start from the working prototype in `stories/graph.py` (kNN similarity graph + Louvain; see the Stories section of `docs/ARCHITECTURE.md`). `uv run python -m undercurrent.demo` runs it end to end on hand-labelled items and scores it. Wrap it as `stories/cluster.py` for a trailing window of real days. <br>2. `stories/footprint.py`: location mentions → `FootprintPoint`s. Weight by share of mentions; `radius_km` from the GDELT location type: city (3/4) ~75, state or region (2/5) ~250, country (1) about half its width. Test on a fake articles frame. | `stories/cluster.py`, `stories/footprint.py` |
| **C** | 1. Write the target definition in `PLAN.md` Notes: `article_count_7d` on day t+h for each h in `config.HORIZONS`, modelled as `log2((future + 1) / (now + 1))` (see the Forecast section of `docs/ARCHITECTURE.md`), and which stories get a forecast (for example `vol(t-6..t) >= 20`). <br>2. `forecast/proxy.py`: proxy stories = (top GKG theme × country) daily counts from `articles.parquet`, with the same columns that `forecast/labels.py` will need later. Until A's day lands, use a generated fake frame. <br>3. **The contract is already done**, so D can build on it: `Forecast.horizons`, `Outcome.horizons`, `ModelMetrics.horizons`, `Meta.horizons` in `contracts.py`, with matching mock data. Build to it. If you need it changed, do the documented contract change in one commit and announce it. | `forecast/proxy.py` |
| **D** | 1. Zustand store: `selectedStoryId`, `currentDate`, `replayMode`, `revealOutcome`. <br>2. The globe is already built: vector countries (`globe/land.ts`), a heatmap of story footprints (`globe/heat.ts`), and flat markers coloured by forecast. **Don't re-add country polygons via `polygonsData`, or towers**; see the Web section of `docs/ARCHITECTURE.md`. Wire marker clicks (`onPointClick`) to `selectedStoryId`. <br>3. Story panel skeleton, opened by clicking a marker; fetches `/api/stories/{id}`. | `web/src/**` |

**Sync at H+2:** confirm the schemas in section 3 as written, or announce any changes. A reports the one-day GKG size for both feeds and the backfill depth chosen. V reports the chosen model, its dimension and titles per second; **the model is locked from here on**.

**Announce:** A: "`articles.parquet` for <day> is in `data/processed/`; the backfill is running." V: "`vectors.embed()`/`load()` work; model is <id>, dim <n>." C: "Contract changed: forecasts are per-horizon projections of `article_count_7d` (`Forecast.horizons`); `p_grow` is gone. Pull and rerun gen:types if needed."

### Phase 2: H+2 to H+8 → M1, real stories on the globe

**Critical path:** A's 7 days → V's vectors (day by day, as each lands) → B's clustering and tracking → B's first export → D switches from mock data.

| Agent | Tasks, in order |
|---|---|
| **A** | 1. Parse **the last 7 days in full** (every 15-minute file of both feeds, `sampled=false`). Target H+5. **Announce.** <br>2. `ingest/wiki_top.py`: daily top-1000 pageviews over the backfill range → `wiki_top.parquet`. Needs no titles. <br>3. `ingest/wiki_resolve.py`: `resolve()`/`resolve_many()` via the search API, cached, with `config.WIKIMEDIA_USER_AGENT`, max about 10 requests per second. **Announce.** <br>4. `ingest/wiki.py`: per-title pageviews and edits for a titles file. <br>5. Keep checking on the backfill and parse each day as it finishes (the parser must work one day at a time). |
| **V** | 1. Embed each of A's 7 full days as soon as it's parsed, rather than waiting for all 7. **Target H+6. Announce.** <br>2. Calibrate `threshold()` for each source pair: news–news (B uses it for tracking), bluesky–news and wiki_events–news. Label about 50 pairs spread across similarity values, and pick the cutoff that gives about 90% precision. Commit `vectors/thresholds.py`. **Target H+8. Announce.** <br>3. Embed the `wiki_events` bullets for the backfill range. |
| **B** | 1. **The tracker is built** (`stories/cluster.py`, `tracker.py`, `track.py`; see "Clustering: the story tracker" in `README.md`): run `python -m undercurrent.stories.track` over the first days, then **print 20 stories with 5 titles each and read them** before trusting any cutoff. Calibrate `vectors/thresholds.py` first (`python -m undercurrent.vectors.calibrate`), and write the parameters you end up with in Notes. <br>2. It writes `story_assignments/`, `stories_daily/` and `story_lineage/` (sections 3.5 and 3.6). New ids are `slug(label)[:40]` plus 6 hex characters. <br>3. Labels and category are done by the tracker; still to do: location (most common location, ignoring country-level entries when a city is available), entities (`resolve_many` once A ships it; until then names only), and the footprint (`stories/footprint.py`). <br>4. `stories/export.py`: build `Meta`, `Snapshot`, `StoryDetail` with the Pydantic models in `contracts.py`, write `data/export/`, run `uv run pytest`. Give every `Item` its `locations`: every place the item mentions, main place first (for a GDELT article, the `locations` of its `articles.parquet` row, most specific first; empty for most posts). The globe draws a dot at each of them, so build the story's `footprint` from the same places and the heat and the dots line up. Use `forecast=None` until `forecasts.parquet` exists. **Target H+8. Announce.** |
| **C** | 1. `forecast/labels.py` on proxy stories: for each (story, t) and horizon h, compute `article_count_7d` now and on day t+h, and the target `log2((future + 1) / (now + 1))`, with the minimum-volume filter. <br>2. `forecast/features.py`: volume (log), `growth_7d`, acceleration (growth this week minus last week), distinct domains, distinct countries, tone and Δtone. Wikipedia features are stubbed as NaN for now (HGB handles NaN). <br>3. `forecast/backtest.py`: time split (on the proxies, the last 14 days = test; on the real backfill, the fixed latest 3–6 months, with quarterly folds and a gap between train and test), baselines (persistence, and momentum = persistence scaled by `growth_7d`), and per horizon the typical miss (`mae_log2`) and the range `coverage`. Run it on the proxies end to end, so the harness is finished before real stories arrive. |
| **D** | 1. Time slider over `meta.dates`; changing it fetches `/api/snapshots/{day}`. Past `meta.latest` it continues by `meta.horizons`: a future day is the latest snapshot with each story's `article_count_7d` replaced by that horizon's `articles_7d` (a story with no forecast stays as it is), drawn by the same heat code. <br>2. Story panel: title, timeline chart (recharts: articles; posts and pageviews when present), items grouped by `kind`, entities, forecast with its factors as a signed bar list. <br>3. Model card component: per horizon, the typical miss (`mae_log2`) vs the baseline and the range `coverage` vs `Forecast.interval`, reading `meta.model.horizons`. Offer a horizon as a forecast only if the model beats the baseline there (the mock has one horizon that doesn't). Use the mock first. <br>4. When B announces the first export, run `npm run dev` against the real data and fix whatever breaks (empty fields, long titles, too many points). |

**Sync at H+8 (M1):** real stories on the globe for 7 days. If clustering still looks bad, see "Fallbacks" in section 6.

### Phase 3: H+8 to H+16 → M2, forecast backtest

| Agent | Tasks, in order |
|---|---|
| **A** | 1. Finish parsing the 12-month backfill at full resolution, one day at a time. **Announce** with the exact date range, and again when older blocks land. <br>2. Per-entity pageviews and edits for the titles B and C send (B writes `data/processed/wiki_titles.txt`). <br>3. `ingest/bluesky.py`: Jetstream consumer that writes daily parquet (filtered to English plus a cheap keyword prefilter), and the `stream_posts()` async generator. **Announce.** |
| **V** | 1. Embed the backfill day by day as A parses it, on the GPU machine (several days in parallel), and keep the vectors in the bucket. **Target H+11 for the newest 60 days**, the rest as it finishes, so B's tracking run can start. **Announce.** <br>2. Embed the Bluesky daily parquet when A ships it (H+12). Make `embed(..., "bluesky")` fast enough for live use: load the model once, and batch posts about once a second. <br>3. `vectors/wiki_new.py`: new event articles in the window → `wiki_new.parquet` → embed. The recentchanges API only keeps about 30 days; for older days, check the creation date of titles linked from `wiki_events`. <br>4. Pitch numbers: precision of cross-platform links at the chosen thresholds ("9 in 10 linked posts are about the same story"). |
| **B** | 1. Run cluster → track over **the backfill in time order, one day at a time**, so every `stories_daily` row is point-in-time. Use the scalable tracker (`stories/track.py`), not the prototype in `graph.py`. First run it over the newest 60 days (an early dataset for C), then over the whole 12 months once its vectors exist; the stored vectors make the re-run cheap. **Announce** when `stories_daily.parquet` covers each range. Only the final run feeds the backtest. <br>2. Write `wiki_titles.txt` (entities of the top stories → `resolve_many`). <br>3. `stories/attach.py`: Bluesky posts, `wiki_events` bullets and `wiki_new` articles → nearest centroid, using `vectors.threshold(<source>, "news")` (V calibrates these). Fill `post_count_7d` and `items` (each with its `locations`, as in Phase 2). <br>4. Exporter: merge `forecasts.parquet` (forecast + outcome per story-day) and `model_metrics.json` into the snapshots and `meta.json`. Add `wiki_pageviews` to the timelines. Re-export and run pytest. |
| **C** | 1. Switch from proxies to real stories: `--source stories` reads B's `story_assignments` and counts a story's future articles from them. That is valid because assignments are never rewritten once written (section 3.6). The older idea, re-matching future articles against each day's frozen centroid, is no longer possible: `stories_daily` doesn't hold centroids (section 3.5). Apply `story_lineage` where stories were merged (not done yet), and write what you decided in Notes. <br>2. Add the Wikipedia features (pageview and edit growth over the story's entities, via `wiki_resolve`). <br>3. Ridge regression (standardised features) on the log2 change, then `HistGradientBoostingRegressor` (quantile loss at 0.1, 0.5 and 0.9 for the range), one model per horizon. Pick the better one per horizon on the test window. Report honestly if neither beats persistence. <br>4. Factors: ridge coefficient × standardised value; for HGB, a per-story approximation (difference from the mean prediction with each feature set to its median), top 3–5. <br>5. Write `forecasts.parquet` (every story-day in the backfill and the live days) and `model_metrics.json`, with range coverage. **Announce.** <br>6. `forecast/examples.py`: list stories projected at t to at least double over 7 days (`articles_7d` ≥ 2× the current count) that then did. Pick 2–3 for the pitch and write the ids and dates in Notes. |
| **D** | 1. Replay mode: choose a past date → show the predictions → press a key or button to reveal the outcome (per horizon, the actual `articles_7d` next to the projection and its range, with ✓ if it fell inside). <br>2. Rings layer for stories projected to at least double over 7 days (`articles_7d` ≥ 2× `article_count_7d`): "about to break". <br>3. Offline fallback: check that `npm run dev` works with wifi off. The globe already has no CDN dependencies (the country shapes are bundled), so this should only need checking. <br>4. Start `docs/DEMO.md`: a 2-minute script, using C's pitch examples when they land. |

**Sync at H+16 (M2):** `meta.json` has real model metrics; the replay works on at least one pitch example.

### Phase 4: H+16 to H+20 → M3, replay and live

| Agent | Tasks |
|---|---|
| **A** | Keep the Jetstream consumer stable (reconnect with backoff, cursor resume). Help D wire `stream_posts()`. Then: Reddit, only if everything else is green. |
| **V** | Support B's `live_match` and D's `/api/live`: check embedding latency under live load. If everything is green: Reddit vectors. |
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
| 1 | H+1 | C → all | Contract: per-horizon forecasts (`Forecast.horizons`); already in place, C reads it and asks if it needs changing |
| 2 | H+2 | V → B, C | `embed()` / `load()` working; **model locked** |
| 3 | H+2 | A → V, B, C | `articles.parquet`, 1 day |
| 4 | H+5 | A → V, B, C | `articles.parquet`, last 7 days in full |
| 5 | H+6 | V → B, C | vectors for the last 7 days |
| 6 | H+6 | A → B, C | `resolve()` and `wiki_top.parquet` |
| 7 | H+8 | V → B, C | calibrated thresholds; `wiki_events` vectors |
| 8 | H+8 | B → D | first real `data/export/` (7 days) |
| 9 | H+10 | A → V, B, C | backfill parsed: the newest 60 days by H+10, then the rest of the 12 months as it lands |
| 10 | H+11 | V → B, C | backfill vectors (in the bucket) |
| 11 | H+12 | B → C | `stories_daily.parquet` covering the newest 60 days, then the full run |
| 12 | H+12 | A → V, B, D | Bluesky parquet and `stream_posts()` |
| 13 | H+13 | V → B | Bluesky and `wiki_new` vectors |
| 14 | H+15 | C → B | `forecasts.parquet` and `model_metrics.json` |
| 15 | H+16 | B → D | export with forecasts, outcomes and model metrics |
| 16 | H+17 | B → D | `nearest_story()` |

---

## 6. Fallbacks (decide at the sync, not alone)

| If | Then |
|---|---|
| The backfill is too slow or too big | Cut depth first (fewer months, but never below the newest 60 days), then sample 1 file per hour (on both feeds alike, `sampled=true`). Cut depth or sampling rate rather than dropping the translated feed. Keep a fixed test period of at least 14 days. |
| Clustering looks bad at H+6 | Switch to `AgglomerativeClustering(metric="cosine", linkage="average", distance_threshold≈0.35)` and export anyway, so D gets real data. Tune afterwards. |
| B's point-in-time stories aren't ready by H+12 | C trains and reports on proxy stories, and maps forecasts onto B's stories by the best overlap of articles. Say so in the Devpost. |
| The model doesn't beat persistence at a horizon | Report it honestly on the model card, and don't offer that horizon as a forecast. Show where it helps (for example on low-volume stories) if that's true. |
| Live Bluesky is flaky | Cut `/api/live`. The replay is the core of the pitch. |
| Wikipedia rate limits | Use only `wiki_top.parquet` (bulk), and restrict per-title fetches to entities of stories in the test window. |
| Embedding is too slow for the backfill | Add GPU machines first (days are independent), use the faster model (decide at H+2; never switch mid-backfill), rely on the text cache to skip repeated wire copies, or cut the backfill depth (never below the newest 60 days). |
| Posts or Wikipedia items link to the wrong stories | Raise that source's threshold (fewer, better links) and show only the top few items per story. |

---

## 7. Commit messages

Use these prefixes: `ingest:`, `vectors:`, `stories:`, `forecast:`, `api:`, `web:`, `contract:`, `docs:`. For example: `stories: track story ids across days`.
