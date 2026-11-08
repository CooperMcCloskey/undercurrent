# Architecture

## Data flow

```
 GDELT GKG (15-min files) ─┐
 Bluesky Jetstream (live) ─┤  A: ingest/                    V: vectors/       B: stories/              C: forecast/
 Wikipedia pageviews/edits ┼─▶ data/raw ─▶ data/processed ─▶ vectors ─────▶ clusters ─▶ stories ─▶ features ─▶ model
 Reddit (stretch) ─────────┘                    ▲                                   │                      │
 Wikipedia event text ── (fetched by V) ────────┘                                   └─ export (contracts) ◀┘
                                                                         │
                                                                   data/export/*.json
                                                                         │
                                                      D: api/ (FastAPI, :8000) ── /api/live (websocket)
                                                                         │
                                                      D: web/ (Vite + React + react-globe.gl, :5173)
```

The JSON files in `data/export/` are the interface between the pipeline and the app:

| File | Shape (in `contracts.py`) | API route |
|---|---|---|
| `meta.json` | `Meta`: snapshot dates, latest date, model metrics | `GET /api/meta` |
| `snapshots/<YYYY-MM-DD>.json` | `Snapshot`: every story as of that day, with forecast (and outcome, for past days) | `GET /api/snapshots/{day}` |
| `stories/<id>.json` | `StoryDetail`: timeline, items from every source (each with the places it mentions), entities, locations | `GET /api/stories/{id}` |
| (websocket) | `LivePost` | `WS /api/live` (to do) |

Until `data/export/meta.json` exists, the API serves `web/public/mock/` instead. `npm run dev:mock` skips the API entirely.

## Data sources and pitfalls

### GDELT GKG 2.1 (Ingest)

How we pull it (from BigQuery, or from the 15-minute zip files) and what is kept: `pipeline/undercurrent/ingest/GDELT.md`.

**Files**

- A new file is published every 15 minutes at `http://data.gdeltproject.org/gdeltv2/YYYYMMDDHHMMSS.gkg.csv.zip`.
- The full index is `http://data.gdeltproject.org/gdeltv2/masterfilelist.txt`, and the newest file is listed in `lastupdate.txt`.
- The main feed covers English-language sources. A separate machine-translated feed (`*.translation.gkg.csv.zip`, indexed in `masterfilelist-translation.txt` and `lastupdate-translation.txt`) covers other languages. **We ingest both feeds from the start**, because English-only data would bias the stories towards Anglophone sources.

**Format**

- Tab-separated, **no header**, 27 columns.
- Some rows contain invalid UTF-8, so read with `encoding_errors="replace"`.
- The columns you'll use:
  - `DATE`
  - `SourceCommonName` (the domain)
  - `DocumentIdentifier` (the URL)
  - `V1Themes`
  - `V2EnhancedLocations` / `V1Locations`
  - `V1Persons`, `V1Organizations`
  - `V1.5Tone` (the first comma-separated value is the average tone)
  - `V2.1TranslationInfo` (translated feed only; gives the source language)
  - `V2ExtrasXML` (contains `<PAGE_TITLE>`)

**Pitfalls**

- **No article text.** The headline is in `<PAGE_TITLE>` inside the Extras field. Don't scrape articles.
- **Location format.** `V1Locations` entries look like `type#name#countrycode#adm1#lat#lon#featureid`, separated by `;`.
- **Country codes are FIPS 10-4, not ISO.** Several codes clash: `UK`→`GB`, `GM`→`DE`, `CH`→China (ISO `CH` is Switzerland), `SZ`→Switzerland, `AS`→Australia, `RS`→Russia. Use a mapping table and convert everything to ISO alpha-2 at ingest time.
- **Size.** GKG is large. Download one day and measure it before planning a backfill. The plan is full resolution (every file), extracted on the cloud machine: see "Scaling up" in `README.md`. Taking one 15-minute file per hour cuts the download by 4×, and growth ratios still hold on a consistent sample, but it makes small-story counts noisy, so it is a fallback only.
- Syndicated stories appear many times (the same wire copy on many domains). Deduplicate before counting volume, or count unique domains instead.
- **Translated feed.**
  - It doubles the file count. Measure **both** feeds' file sizes for one day before planning a backfill.
  - Use the same sampling rate for both feeds on every day, so growth ratios stay comparable.
  - Deduplicate across both feeds together, not per feed.
  - Its `TranslationInfo` field gives the source language (for example `srclc:fra;eng:...`). It's empty in the English feed.
  - `<PAGE_TITLE>` in the translated feed is in the **original language**, not English (checked on 2026-11-05/06). About 70% of articles come from this feed.

### Wikipedia (Ingest; used as features by Forecast)

How these numbers reach the forecast, and the rules that keep them point-in-time: "Wikipedia features, end to end" below.

| What | Endpoint |
|---|---|
| Daily pageviews per article | `https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/<Title>/daily/<YYYYMMDD>/<YYYYMMDD>` |
| Top 1000 articles per day (cheap bulk signal) | `https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/all-access/<YYYY>/<MM>/<DD>` |
| Edit history | `https://en.wikipedia.org/w/api.php?action=query&prop=revisions&rvprop=timestamp&rvlimit=max&titles=<Title>` (paginate) |
| Live edits | `https://stream.wikimedia.org/v2/stream/recentchange` (server-sent events) |

- Send `config.WIKIMEDIA_USER_AGENT` as the User-Agent header. Wikimedia blocks generic ones.
- Cache everything to disk and keep request rates polite.
- GDELT person names are lowercase and messy (`barack obama`). Resolve them to Wikipedia titles with the search API (`action=query&list=search`) and cache the results.

### Wikipedia text (Vectors)

| What | Where |
|---|---|
| Daily "Current events" summaries (curated, years of history) | `action=parse&page=Portal:Current_events/<YYYY>_<Month>_<D>` (e.g. `2026_November_6`); one bullet per event, with links to the articles involved |
| New articles (event pages get created when a story gets big) | `action=query&list=recentchanges&rctype=new&rcnamespace=0` (only keeps about 30 days); for older days, the first revision's timestamp of a title |

Embed the bullet text, or the title plus first sentence of a new article. A Wikipedia article about a *place* or *person* doesn't embed close to a news event about it, which is why entity pages are linked by title (above), not by vector.

### Bluesky (Ingest)

- **Jetstream:** `wss://jetstream2.us-east.bsky.network/subscribe?wantedCollections=app.bsky.feed.post`. No auth, JSON messages.
- Each post is in `commit.record.text`, with `commit.record.langs` and `commit.record.createdAt`.
- Post URL: `https://bsky.app/profile/<did>/post/<rkey>`.
- The firehose is high-volume. Filter by language, and cheaply by keyword or entity, before embedding.
- Historical search exists (`app.bsky.feed.searchPosts`) but may need authentication. Treat Bluesky as live-only; that's why it isn't a model feature.

### Reddit (stretch goal)

You need an approved OAuth "script" app and you're rate limited. Pushshift is gone. Only do this if access was approved before the event.

## Vectors (Vectors workstream)

The interface is in `docs/WORKPLAN.md` section 3.4: `embed()`, `load()` and `threshold()` in `undercurrent.vectors`.

- **One space for every source.** Headlines, posts and Wikipedia text are embedded by the same model (`config.EMBEDDING_MODEL`), L2-normalised, so cosine similarity is a dot product. Store them per source and day as float16, in cloud storage, not git or laptops (see "Vectors" in `README.md` for why we keep them).
- **Clean before embedding; this matters more than the choice of model.** GDELT titles carry site-name suffixes (" - BBC News") and boilerplate ("Home", "Subscribe to read") that would otherwise form their own clusters. Posts carry URLs, @mentions and hashtags.
- **Cache by text hash.** Wire stories repeat the same headline on many sites and days, so identical text is embedded once.
- **Thresholds are per source pair and per model.** A short post and a headline about the same event score lower than two headlines do, and models differ (e5 models put almost everything between 0.7 and 1.0). Calibrate each cutoff on about 50 labelled pairs. Everyone else calls `threshold()`.
- **Point-in-time:** a vector depends only on its own text, so embeddings never leak the future. Anything *fitted* on the corpus (PCA, UMAP, topic models) must be fitted on training-period data only.
- **Throughput:** measure titles per second on the GPU machine as well as the laptop (`device="mps"` on Apple Silicon). Backfill size × that rate must fit in a few hours (days are independent, so add machines), or pick a faster model.

## Stories (Stories workstream)

1. **Vectors** come from V: `vectors.load("news", days)`. Never embed inside `stories/`, so every workstream uses the same space.
   - Titles may be non-English, because both GDELT feeds are ingested. The multilingual model handles this, but tune similarity thresholds with non-English titles in the sample (the demo items include French, Spanish and Dutch).
2. **Cluster** the items that matched no known story (`stories/cluster.py`, which scales to a real day; `stories/graph.py` is the small prototype it grew from, described here):
   - Link each item to its `k` most similar items above `min_sim` (cosine).
   - Run Louvain community detection; each community is a story, and groups smaller than `min_size` are noise.
   - Louvain maximises modularity, so it doesn't *chain* the way connected components do: two dense groups joined by one weak link stay separate.
   - On the demo items (`uv run python -m undercurrent.demo`), it recovers all 9 hand-labelled stories exactly: two different Rotterdam stories stay apart, French/Spanish/Dutch headlines join their English stories, and noise stays out. That holds for `min_sim` 0.40–0.45, `k` 8–10 and resolution 1.0–2.0, with MiniLM.
   - `min_sim` is model-specific, so recalibrate it on real data. The prototype's similarity matrix and Python Louvain don't scale; `cluster.py` finds neighbours with FAISS, takes connected components, and runs Louvain inside each component (keeping a split only if it makes the groups clearly tighter). Measured numbers are in "Clustering: the story tracker" in `README.md`.
3. **Track stories** day to day (`stories/tracker.py`, run by `stories/track.py`). Each day: match items to the existing story with the most similar centroid above `assign_sim` (so the id stays the same, unless the place rule objects), start new stories from the leftovers with step 2, merge stories whose centroids have become the same (written to a lineage table), retire the idle. Assignments are never rewritten, so every row is point-in-time. Ids are slugs plus 6 hex characters, matching `contracts.STORY_ID_PATTERN`.
4. **Attach other sources** (posts, Wikipedia event text) to the nearest story centroid, above `vectors.threshold(<source>, "news")`.
5. **Location** of a story: the GDELT location mentioned most often in its articles. This anchors the story's marker.
6. **Footprint** (`StorySummary.footprint`): where the story is happening, drawn as the hovered story's outline (it was a heatmap of every story until 2026-11-08).
   - Group the story's GDELT location mentions by place. Weight each place by its share of mentions (weights sum to 1); keep the top ~10.
   - Set `radius_km` from GDELT's location type: city (types 3/4) ~75, state or region (2/5) ~250, whole country (1) about half the country's width (300–1000).
7. **Category** (`StorySummary.category`): `vectors.categorize(centroid)` (`pipeline/undercurrent/vectors/categories.py`).
   - Each category (politics, conflict, economy, society, disaster, health, technology) is described by ~8 short headline-style examples. Their vectors are averaged into a prototype, and the story takes the nearest one ("other" below 0.2 similarity). No extra model or training data, and it works across sources and languages.
   - On the demo it gets all 9 stories right, but some calls are close: Bolivia's election blockades score politics 0.38 vs society 0.38; the Rotterdam flood scores disaster 0.25 vs health 0.24. To firm it up, add examples to the weak categories. With real GDELT data, add a second vote from the articles' theme tags (`ELECTION`, `PROTEST`, `NATURAL_DISASTER`, `ECON_*`…).
   - **Update 2026-11-08: the exporter re-decides the category** (`stories/export.py`, `recategorize`) from each story's own English headlines, classified by logistic regression on 246 hand-labelled headlines (`vectors/category_labels.json`, `classify_titles`): 75% on an unseen headline, 80% when its confidence is 0.35 or more. The tracker's centroid-against-prototypes category (65% on the same headlines) stays as the fallback for stories with no English headline. Improve it by adding labelled headlines and running `uv run python -m undercurrent.vectors.categories --evaluate`.

## Wikipedia features, end to end (A → B → C)

**Why.** When something is about to become big news, people start reading the Wikipedia articles about the people and organisations involved, and editors start updating them. That often happens before the news peaks. The forecast uses it as an early signal, alongside the story's own article counts.

**The flow.** Each step reads the previous one's files; `scripts/merge.sh` runs B's and C's steps before training.

```
GDELT articles (persons, orgs)
  │ A  ingest/wiki_names.py    each ISO week's 50 most-mentioned people and 50 orgs (top 15 of each = tier 1)
  ▼
  │ A  ingest/wiki_bulk.py     name → English Wikipedia title → Wikidata ID (qid); daily numbers for 2021-10-10 → 2026-11-06:
  │                            tier 1 = views in 8 languages + English edits, tier 2 = English views
  ▼
wiki_selection.parquet       name → qid, first_week, tier1_week        } in gs://undercurrent-data-cooper/wiki/
wiki_entity_daily.parquet    qid, day, views, views_en, edits, ...    }
  │ B  stories/wiki_entities.py   each story's top 5 entities, from its first days
  ▼
story_wiki_entities.parquet  story_id, qid, full_from (+ title_en, name, articles, rank, chosen_on, for people)
  │ C  forecast/panel.py (wiki_sql)   the story's entities summed per day, only around the story's own days
  ▼
wiki panel                   story_id, day, wiki_views, wiki_edits, wiki_views_en
  │ C  forecast/features.py
  ▼
5 features: wiki_log_views7, wiki_growth_7d, wiki_edit_growth_7d, wiki_non_en_share, wiki_views_per_article
```

**How a story gets its entities** (B). Take the people and organisations GDELT names in the story's articles, normalised the way `wiki_names` counts them (lower case, single spaces), and match them to `wiki_selection.name`. Names that point to the same entity (`joe biden`, `jo biden`) count once per article. Keep the 5 entities named in the most articles, each in at least 2. Leave out names that aren't the story's subject:

- countries, regions and capitals, and dateline cities (`Los_Angeles`)
- seats of government (`White_House`)
- news agencies, media and platforms: they're named because they're the source (`Reuters`, `Instagram`)
- generic or wrong matches from the name resolver (`Supreme_court`, `Court`, `Secondary_school`)

The list is `excluded()` in `stories/wiki_entities.py`: a hand-picked set plus a rule for generic titles.

**What keeps it honest.** Every feature at day t may only use what was known on day t. Five things could leak the future, and each has a rule:

| What could leak | Rule | Where |
|---|---|---|
| Which entities a story has. Choosing them from later articles would see how the story turned out | They're chosen from the story's articles up to `chosen_on`: the earlier of its 3rd day and the first day its articles so far reach 20 (`MIN_ARTICLES_7D`). A story only gets a forecast on days with ≥ 20 articles in the last 7, and that can't happen before `chosen_on`, so the choice is always in the past. Checked on real data with C's own code | B |
| Whether an entity has Wikipedia data at all. A only fetched names that made a weekly top 50 at some point, maybe later | A name counts for a story only if its `first_week` had ended by `chosen_on` | B |
| 8 languages and edits. A fetched these for the whole history of any entity that was ever a weekly top 15, maybe later | `tier1_week` (A) → `full_from` = the end of that week (B). Before it, the panel uses English views and treats edits as unknown; from it, all 8 languages and edits (C) | A, B, C |
| Pageviews for day t are published around day t + 1 | Features use views up to t − 1 and edits up to t (`forecast/settings.py`) | C |
| Missing data read as "no interest" | A story with no entities, or a day with no numbers, is NaN, not 0. The gradient-boosted trees handle NaN | C |

**Size.** The panel joins each story's entities only from 43 days before its first article to 7 days after its last (`WIKI_DAYS_BEFORE/AFTER` in `forecast/panel.py`); no feature looks further back than 28 days. B also writes entities only for stories that ever get a forecast row. On 12 real days this made the Wikipedia panel 34 times smaller, with every feature identical. Without it, the full merge would have built about 300 million rows.

**Coverage and gaps** (2026-11-08):

- About 43% of the stories that get forecasts have Wikipedia entities, measured on 12 real days. The rest name nobody who made a weekly top 50, and their Wikipedia features are NaN.
- Names are only picked from 2022-10-10 onwards; sections 9 and 10 are out of scope for now. Pageviews go back to 2021-10-10.
- Places (GDELT locations) aren't fetched.
- The exporter doesn't show Wikipedia numbers on the globe yet (`Entity.wikipedia_title`, `pageviews_7d`, `TimelinePoint.wiki_pageviews`).

**Running it by hand:**

```bash
gcloud storage cp gs://undercurrent-data-cooper/wiki/wiki_selection.parquet \
    gs://undercurrent-data-cooper/wiki/wiki_entity_daily.parquet data/merged/
uv run python -m undercurrent.stories.wiki_entities --processed data/merged   # -> story_wiki_entities.parquet
uv run python -m undercurrent.forecast.run --source stories --processed data/merged --out data/merged
```

`scripts/merge.sh` does exactly this. If the Wikipedia step fails, the forecast still runs, without the Wikipedia features. `WIKI=0` skips the step. Where the files come from and how A keeps them up to date: `docs/DATA_COVERAGE.md`, "Wikipedia from Lucy's laptop". A's own guide: `pipeline/undercurrent/ingest/WIKIPEDIA.md`.

## Forecast (Forecast workstream)

- **Target:** for each (story, day t) and each horizon h in `config.HORIZONS`, the story's `article_count_7d` on day t+h: the articles published in the 7 days ending that day. This is the number the globe draws a story's heat from, so a projection for day t+h is drawn by the same code as the real snapshot of day t+h, and the model is graded on the number that snapshot will contain. The contract shape is `Forecast.horizons` (`HorizonForecast` in `contracts.py`).
  - **Model the change, not the count.** Fit `y = log2((articles_7d(t+h) + 1) / (articles_7d(t) + 1))` and convert back to articles. Counts span orders of magnitude and zeros are common, so the +1 and the log keep the error comparable between a 5-article story and a 500-article one.
  - **Output per horizon:** the median `articles_7d`, plus a `low`–`high` range that should hold `config.FORECAST_INTERVAL` (80%) of outcomes. One way: `HistGradientBoostingRegressor` with quantile loss at 0.1, 0.5 and 0.9. Cheaper with little data: one point model, plus residual quantiles from a validation block.
  - **The future globe** is the snapshot of the latest day with every story's `article_count_7d` swapped for the chosen horizon's `articles_7d`, drawn by the usual heat code (the `low`/`high` give the uncertainty). Scrubbing back to day t and stepping forward shows the same projections next to the `outcome`. A story with no forecast is drawn unchanged.
  - **What it can't show:** stories that don't exist yet. The future view is today's stories continuing or fading in their current footprint.
  - **Horizons:** 1, 3, 7 and 14 days to start. Single stories a month out mostly just end, so add 30 only with months of history, or forecast an aggregate (category × region) instead. This is why we use 12+ months of history: a 60-day backfill would give only about 40 usable label days at 7 days and about 33 at 14 (the features need 13 days of history, and the target needs h days of future), and a 30-day horizon would leave about 17.
- **No lookahead.** Features at day t use only data from day t or earlier. Story membership must be point-in-time too. If you cluster all of history at once and then backtest, the future leaks into which story an article belongs to.
  - Clean method: freeze each story's centroid at day t, then count future articles as the ones whose nearest centroid is that story.
- **Baseline:** persistence (`articles_7d(t+h) = articles_7d(t)`), and also momentum (persistence scaled by `growth_7d`). News attention fades fast, so persistence is harder to beat than it looks. If the model doesn't beat the baseline at a horizon, report that honestly and don't present that horizon as a forecast; HRT judges will respect that more than an unexplained score.
- **Split by time:** train on the earlier weeks and test on the last 2 weeks. Never use a random split.
- **Report:** per horizon, in `ModelMetrics.horizons`: the typical miss (`mae_log2`, as a factor of 2^x) for the model and the baseline, and `coverage` of the range (near 80% means the ranges are honest). Give each story the top `factors` behind its projection: ridge coefficients × feature values, or permutation importance.

## Web (Web workstream)

- **Colours are categories** (`web/src/globe/categories.ts`): one fixed colour per `StorySummary.category`, used by the heat, the markers, the tooltip and the legend (which groups stories by category). It's typed as `Record<Category, …>`, so a new category in `contracts.py` without a colour is a type error. Stories sharing an anchor are fanned out in a small ring.
- **Heatmap** (`web/src/globe/heat.ts`): each story's `article_count_7d` is spread over its footprint as Gaussians, in its category's colour. Where stories overlap, colours blend by heat.
  - **Intensity is shown by brightness within the colour:** dark at the edges, full colour in the body, near white at the core. Each hue is rescaled to the same luminance first, so yellow doesn't look hotter than indigo at the same heat.
  - **Sizes are absolute, in km on the globe.** Every place gets a fixed minimum spread (`HEAT_SPREAD_KM` = 200 in `App.tsx`), so a story's places merge into one field. Zooming only magnifies the layer; it never recomputes or reshapes it.
  - Density is computed into a 0.5° grid only when the data changes (35–65 ms for 5,000 points). A shader colours it every frame on a single transparent sphere, using a half-float texture so the faint edges don't band.
  - Don't use react-globe.gl's `heatmapsData`. It rebuilds a ~130k-vertex sphere on every update, and needs WebGPU or takes seconds on the CPU.
  - Heat is normalised to the day's biggest story and shown on a √ scale, so small stories stay visible.
- **Story names from Wikipedia** (`pipeline/undercurrent/stories/wiki_topics.py`, run by the export command when `wiki_events.parquet` is there): a story's headlines are matched to Wikipedia's current-events bullets (cosine at least 0.62: about 85% right by eye, perfect from 0.65). A story that matches a topic few others do is named after it (91 of the 6 Nov export's matched stories); one under an umbrella ("Middle Eastern crisis", "2026 European unrest", over 2 stories) keeps its headline and gets "Part of: <topic>" as its summary. Names apply from the day the topic first appears on Wikipedia. The replaced headlines are kept in `.wiki_topics_original.json`, so it can be run again.
- **The story chart** (`web/src/story/ForecastChart.tsx`) covers the story's whole life, from its first day with articles (up to 400 days back) to the selected day, which is marked with its date, plus the forecast horizons. "Per day" (the default) shows articles each day, so bursts and gaps are visible, with the forecast drawn as the daily average of its week; "7-day total" shows the number the globe sizes a story by and the forecast predicts. The time bar shows the exact selected date in a label on its handle (amber, with "forecast", past the latest day).
- **No placeholder history.** The time bar runs from the first day in `meta.dates` to the latest, plus the forecast horizons; the illustrative archive (`timeline/history.ts`, real events with made-up numbers) was deleted on 2026-11-08. A day with no snapshot, such as a gap between sections, shows the last snapshot before it, with a note in the header.
- **Episodes** (`stories/export.py`, `episode_starts`): the tracker never closes a story, so a recurring topic (North Korea: 18 bursts over 7 months, one story with 7,700 articles) becomes one long spiky story. The exporter now starts a new story, `<id>-e2`, `-e3`..., when a story that has been quiet for 7 days (fewer than 15 articles a day, or under 5% of its biggest day so far) starts up again. The first episode keeps the tracker's id; stories under 100 articles are never split; only past days are used, so snapshots stay point-in-time; a forecast made for the tracker's story attaches to the episode alive that day. On the 183-day export it would split 3,000+ stories, mostly small ones that never reach a snapshot (North Korea into 9 episodes, the Kenya cult story into 2). `--no-episodes` turns it off. It needs the pipeline's tables, so it only shows after a re-export on the VM.
- **The Emerging card** (`web/src/story/Emerging.tsx`, always on screen at the left, collapsible): near the present it is a **Prediction**, and further back it shows **what really jumped**. Near means within the model's reach of the latest day: 7 days, or up to 14 if the model card says it beats "stays the size it is" that far (`lookaheadDays`). Prediction: stories whose forecast range is above today's size, ranked by projected growth, with the range, main reason and a sketch; the globe's rings mark the same stories. Earlier days: stories that at least doubled by the snapshot 7 to 14 days later, read from the later snapshot (`jumpedStories`). With no forecasts exported at all, a near day falls back to the fastest-growing stories, labelled as not a prediction.
- **"New" instead of a huge multiple.** `growth_7d` compares this week with the week before. A story with fewer than 10 articles the week before (worked back from `growth_7d`, `previousWeek`) shows "New" in the story panel and the Emerging card instead of "88×"; on the 2026-11-06 export that is 171 of 300 stories.
- **The Earth** (`web/src/globe/ocean.ts`, `land.ts`): the sea is a shader, not a flat colour: ocean blue, brighter in the tropics and navy toward the poles, with slow ripples (noise drifting through the sphere's own coordinates, so no texture and no seam), a few glints, and a pale blue rim; it is updated each frame from the same hook as the stars. The land has vertex colours: natural greens by latitude (rainforest, savanna, temperate, northern forest, tundra), ice at the poles, and the big deserts (Sahara, Arabia, Gobi, the outback, ...) in tan, broken up with noise. Dark enough that the story markers stay the brightest things. (The first version, with a teal sea and grey-olive land, looked alien and dusty.)
- **Stars** (`web/src/globe/stars.ts`): 2,400 points on a sphere of radius 4,000 around the globe (added to the scene in `App.tsx`, which also raises the camera's far plane to reach them), fixed in space, seeded so the sky is always the same, a few bright and tinted, each twinkling slowly. Depth-tested, so the globe hides the ones behind it.
- **The time bar** (`web/src/timeline/TimeBar.tsx`, marks in `ticks.ts`) labels years under the line, and months, then days, as you zoom. Scroll over it to zoom around the pointer, scroll sideways to move along it; `+`/`-` and `0` do the same from the keyboard. Playing crosses whatever is in view in about 130 s (`SWEEP_SECONDS`), so the whole 5 years play at 14 days a second and a month at the floor of one day per 2 s; the speed is shown beside the bar, next to three speed buttons (0.5×, 1×, 3×; keys `[` and `]`; remembered in the browser) that multiply it.
- Story markers are flat `pointsData` dots (for hover and click), in the story's colour, sized by article count on a log scale, from the smallest story drawn (radius 0.3°) to the biggest (1.4°), against 0.25° for an article dot; the legend carries a size key (three sample markers with the smallest, middle and biggest counts); the forecast is in the tooltip. **How many stories are drawn is a slider** (header, logarithmic, 10 up to however many the day has; 60 to start), the biggest first, because hundreds of overlapping markers read as noise. **Crowded areas come last** (`globe/select.ts`): stories are ordered by how many bigger ones sit within 2.5° (fewer than 3 first, then fewer than 6, and so on), biggest first within each group, so a crowded capital keeps its biggest few and the rest of the world gets the slots first. Every story is in the order, so the slider's far end shows all of them (on the 2026-11-06 snapshot the first 181 of 300 are the uncrowded ones). Stories projected to grow and the selected one are always drawn. Stories at the same place (within half a degree) keep the biggest at the place and put the rest on rings 2.2° apart, so they don't touch. `ringsData` pulses mark the stories projected to grow (`emergingStories`). There is no heatmap any more: it blended every story's footprint into a pastel haze, and as separate small lights it read as noise. Each marker has a **soft glow** behind it (`web/src/globe/glow.ts`, one additive `Points` object, 2.2° to 6° across by story size), so a story is one lit dot and a busy region is where glows add up. A story's spread over places is shown only for the hovered story: its outline (`globe/heat.ts`, style `outline`, `OUTLINE_SPREAD_KM`) and its article dots.
- **Article dots** (`web/src/globe/constellation.ts`, layout in `web/src/story/articles.ts`): hovering or selecting a story fetches its `StoryDetail` (react-query, cached) and draws one dot per article per place it mentions (`Item.locations`; the first 4 places of each article, at most 80 dots, most important articles first).
  - **Placement and size are in degrees on the globe**, like the heat and the markers, so the dots turn with the globe and scale with zoom. Every dot is the same small size (radius 0.25°, against 0.6° for a story marker and 1.1° when it's hovered), so the story is the only big thing on the globe. Articles, Wikipedia pages included, are circles; social posts are diamonds (`KIND_STYLE`). Lines from the marker to each dot follow the surface. Don't lay the dots out in screen pixels around the marker: we tried, and zoomed out they spread across continents. Don't use the html layer for the dots either.
  - **Heat and dots come from the same places**, so every patch of heat has a dot in it. Dots whose places coincide (three articles about the same city) are nudged apart just enough not to overlap and to clear the marker (`layoutDots`). An item with no place (most posts) gets one dot in a spiral beside the marker, and its tooltip says so.
  - **Importance has no field in the contract**, so it's derived: similarity² to the story centroid, times a weight for the kind (news and Wikipedia 1, posts 0.6), relative to the story's top item. It only orders things (the panel's "most important" sort, and which articles make the 80-dot cut); it does not size the dots, because a size that varies made the dots compete with the story markers. Replace it when the export carries a real signal (`dup_count` of syndicated copies, engagement, pageviews).
  - **Hover and click** work through the custom layer: each dot is its own datum, so `onCustomLayerHover/Click` receive its article. The land and heat layers have raycasting off, so only dots are hit. The dots are tiny, so each carries an invisible, larger hit disc; it is switched off while the dot bursts out of the marker, or the stack of dots would cover the marker and take its hover. Leaving a marker ends its hover after 200 ms, so the pointer can cross the gap to one of its dots. Hovering any dot of an article lights all of that article's dots and its row in the panel.
  - **Place labels** ("Ankara · 1 mention") use the `html` layer: text in screen pixels pinned to the place, so it stays readable at any zoom. Set with `textContent`, never `innerHTML`, since place names come from scraped data.
  - **The story panel** (`web/src/story/StoryPanel.tsx`) lists every article with its places and a link. Links are only rendered for http(s) URLs (`safeHref`), because URLs come from scraped data.
- **Don't use `polygonsData` for the countries.** The land is one merged mesh plus one set of border lines, built in `web/src/globe/land.ts` and added as a custom layer: 2 draw calls and about 60 ms to build. Through `polygonsData`, the 1,616 polygons at 1:50m cost about 4,800 draw calls per frame and 3.7 s of blocking work at load. `polygonsData` is fine for a handful of highlighted countries.
- **Keep `<Globe>` props stable.** Accessor functions, materials and config go at module scope, and data arrays are memoised. react-globe.gl re-processes a whole layer whenever a prop changes identity, and inline arrow functions change on every render.
- **Always pass `customThreeObjectUpdate`**, even as a no-op. Without it, the custom layer empties its scene on every data change and only re-adds *new* items. Existing items (the land, the heatmap) silently disappear.
- With 1,000+ stories, set `pointsMerge` (one draw call, but no per-point hover) or switch to `hexBinPointsData`.
- All server data goes through `web/src/api/client.ts` (react-query). UI state (selected story, current date, replay mode) lives in a zustand store.
- The production bundle is about 2 MB because of three.js. That's fine for the demo, so don't optimise it.
