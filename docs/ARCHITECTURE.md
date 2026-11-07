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
| `stories/<id>.json` | `StoryDetail`: timeline, items from every source, entities, locations | `GET /api/stories/{id}` |
| (websocket) | `LivePost` | `WS /api/live` (to do) |

Until `data/export/meta.json` exists, the API serves `web/public/mock/` instead. `npm run dev:mock` skips the API entirely.

## Data sources and pitfalls

### GDELT GKG 2.1 (Ingest)

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
- **Size.** GKG is large. Download one day and measure it before planning a backfill. For history, take one 15-minute file per hour. Growth ratios still hold on a consistent sample, which cuts the download by 4×. Use every file for the last few days, which are shown live.
- Syndicated stories appear many times (the same wire copy on many domains). Deduplicate before counting volume, or count unique domains instead.
- **Translated feed.**
  - It doubles the file count. Measure **both** feeds' file sizes for one day before planning a backfill.
  - Use the same sampling rate for both feeds on every day, so growth ratios stay comparable.
  - Deduplicate across both feeds together, not per feed.
  - Its `TranslationInfo` field gives the source language (for example `srclc:fra;eng:...`). It's empty in the English feed.
  - It's unverified whether `<PAGE_TITLE>` in the translated feed is in the original language or in English. The ingest agent must check and record the answer in `PLAN.md` Notes.

### Wikipedia (Ingest; used as features by Forecast)

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

- **One space for every source.** Headlines, posts and Wikipedia text are embedded by the same model (`config.EMBEDDING_MODEL`), L2-normalised, so cosine similarity is a dot product. Store them per source and day as float16.
- **Clean before embedding; this matters more than the choice of model.** GDELT titles carry site-name suffixes (" - BBC News") and boilerplate ("Home", "Subscribe to read") that would otherwise form their own clusters. Posts carry URLs, @mentions and hashtags.
- **Cache by text hash.** Wire stories repeat the same headline on many sites and days, so identical text is embedded once.
- **Thresholds are per source pair and per model.** A short post and a headline about the same event score lower than two headlines do, and models differ (e5 models put almost everything between 0.7 and 1.0). Calibrate each cutoff on about 50 labelled pairs. Everyone else calls `threshold()`.
- **Point-in-time:** a vector depends only on its own text, so embeddings never leak the future. Anything *fitted* on the corpus (PCA, UMAP, topic models) must be fitted on training-period data only.
- **Throughput:** measure titles per second on the laptop at H+0 to H+2 (`device="mps"` on Apple Silicon). Backfill size × that rate must fit in about an hour, or pick a faster model.

## Stories (Stories workstream)

1. **Vectors** come from V: `vectors.load("news", days)`. Never embed inside `stories/`, so every workstream uses the same space.
   - Titles may be non-English, because both GDELT feeds are ingested. The multilingual model handles this, but tune similarity thresholds with non-English titles in the sample (the demo items include French, Spanish and Dutch).
2. **Cluster** over a trailing window with a similarity graph (`stories/graph.py`, a working prototype):
   - Link each item to its `k` most similar items above `min_sim` (cosine).
   - Run Louvain community detection; each community is a story, and groups smaller than `min_size` are noise.
   - Louvain maximises modularity, so it doesn't *chain* the way connected components do: two dense groups joined by one weak link stay separate.
   - On the demo items (`uv run python -m undercurrent.demo`), it recovers all 9 hand-labelled stories exactly: two different Rotterdam stories stay apart, French/Spanish/Dutch headlines join their English stories, and noise stays out. That holds for `min_sim` 0.40–0.45, `k` 8–10 and resolution 1.0–2.0, with MiniLM.
   - `min_sim` is model-specific, so recalibrate it on real data. For very large days, swap the block matmul in `knn_edges` for a FAISS index; networkx Louvain handles a few hundred thousand edges.
3. **Track stories** day to day. Match each new cluster to the existing story with the most similar centroid, above a threshold, so the id stays the same. Otherwise create a new story. Ids are slugs, matching `contracts.STORY_ID_PATTERN`.
4. **Attach other sources** (posts, Wikipedia event text) to the nearest story centroid, above `vectors.threshold(<source>, "news")`.
5. **Location** of a story: the GDELT location mentioned most often in its articles. This anchors the story's marker.
6. **Footprint** (`StorySummary.footprint`): where the story is happening, drawn as the heatmap.
   - Group the story's GDELT location mentions by place. Weight each place by its share of mentions (weights sum to 1); keep the top ~10.
   - Set `radius_km` from GDELT's location type: city (types 3/4) ~75, state or region (2/5) ~250, whole country (1) about half the country's width (300–1000).

## Forecast (Forecast workstream)

- **Target:** for each (story, day t), `grew = volume(t+1..t+7) >= GROWTH_RATIO × volume(t-6..t)`. Only include stories above a minimum volume, so noise isn't counted as growth.
- **No lookahead.** Features at day t use only data from day t or earlier. Story membership must be point-in-time too. If you cluster all of history at once and then backtest, the future leaks into which story an article belongs to.
  - Clean method: freeze each story's centroid at day t, then count future articles as the ones whose nearest centroid is that story.
- **Baseline:** momentum (`growth_7d`) alone. If the model doesn't beat it, report that honestly; HRT judges will respect that more than an unexplained score.
- **Split by time:** train on the earlier weeks and test on the last 2 weeks. Never use a random split.
- **Report:** AUC, Brier score and a calibration curve (`sklearn.calibration.calibration_curve`) in `ModelMetrics`. Give each story the top `factors` behind its forecast: logistic regression coefficients × feature values, or permutation importance.

## Web (Web workstream)

- **Story colours** (`web/src/globe/storyColors.ts`): nine well-separated hues, picked by a hash of the story id, so a story keeps its colour as the time slider moves. Colours repeat beyond nine stories on screen. Markers, heat and the legend all use them; stories that share an anchor are fanned out in a small ring.
- **Heatmap** (`web/src/globe/heat.ts`): each story's `article_count_7d` is spread over its footprint as Gaussians, in the story's colour. Where stories overlap, colours blend by heat. Density is computed into a 0.5° grid only when the data changes (~35 ms for 5,000 points). A shader then colours it every frame on a single transparent sphere.
  - Don't use react-globe.gl's `heatmapsData`. It rebuilds a ~130k-vertex sphere on every update, and needs WebGPU or takes seconds on the CPU.
  - Heat is normalised to the day's biggest story and shown on a √ scale, so small stories stay visible.
- Story markers are small flat `pointsData` dots (for hover and click), coloured by forecast. Possible additions: `ringsData` pulses for growing stories, and `labelsData` for the top few.
- **Don't use `polygonsData` for the countries.** The land is one merged mesh plus one set of border lines, built in `web/src/globe/land.ts` and added as a custom layer: 2 draw calls and about 60 ms to build. Through `polygonsData`, the 1,616 polygons at 1:50m cost about 4,800 draw calls per frame and 3.7 s of blocking work at load. `polygonsData` is fine for a handful of highlighted countries.
- **Keep `<Globe>` props stable.** Accessor functions, materials and config go at module scope, and data arrays are memoised. react-globe.gl re-processes a whole layer whenever a prop changes identity, and inline arrow functions change on every render.
- With 1,000+ stories, set `pointsMerge` (one draw call, but no per-point hover) or switch to `hexBinPointsData`.
- All server data goes through `web/src/api/client.ts` (react-query). UI state (selected story, current date, replay mode) lives in a zustand store.
- The production bundle is about 2 MB because of three.js. That's fine for the demo, so don't optimise it.
