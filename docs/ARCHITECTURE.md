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
7. **Category** (`StorySummary.category`): `vectors.categorize(centroid)` (`pipeline/undercurrent/vectors/categories.py`).
   - Each category (politics, conflict, economy, society, disaster, health, technology) is described by ~8 short headline-style examples. Their vectors are averaged into a prototype, and the story takes the nearest one ("other" below 0.2 similarity). No extra model or training data, and it works across sources and languages.
   - On the demo it gets all 9 stories right, but some calls are close: Bolivia's election blockades score politics 0.38 vs society 0.38; the Rotterdam flood scores disaster 0.25 vs health 0.24. To firm it up, add examples to the weak categories. With real GDELT data, add a second vote from the articles' theme tags (`ELECTION`, `PROTEST`, `NATURAL_DISASTER`, `ECON_*`…).

## Forecast (Forecast workstream)

- **Target:** for each (story, day t) and each horizon h in `config.HORIZONS`, the story's `article_count_7d` on day t+h: the articles published in the 7 days ending that day. This is the number the globe draws a story's heat from, so a projection for day t+h is drawn by the same code as the real snapshot of day t+h, and the model is graded on the number that snapshot will contain. The contract shape is `Forecast.horizons` (`HorizonForecast` in `contracts.py`).
  - **Model the change, not the count.** Fit `y = log2((articles_7d(t+h) + 1) / (articles_7d(t) + 1))` and convert back to articles. Counts span orders of magnitude and zeros are common, so the +1 and the log keep the error comparable between a 5-article story and a 500-article one.
  - **Output per horizon:** the median `articles_7d`, plus a `low`–`high` range that should hold `config.FORECAST_INTERVAL` (80%) of outcomes. One way: `HistGradientBoostingRegressor` with quantile loss at 0.1, 0.5 and 0.9. Cheaper with little data: one point model, plus residual quantiles from a validation block.
  - **The future globe** is the snapshot of the latest day with every story's `article_count_7d` swapped for the chosen horizon's `articles_7d`, drawn by the usual heat code (the `low`/`high` give the uncertainty). Scrubbing back to day t and stepping forward shows the same projections next to the `outcome`. A story with no forecast is drawn unchanged.
  - **What it can't show:** stories that don't exist yet. The future view is today's stories continuing or fading in their current footprint.
  - **Horizons:** 1, 3, 7 and 14 days to start. Single stories a month out mostly just end, so add 30 only with months of history, or forecast an aggregate (category × region) instead. A 60-day backfill gives about 40 usable label days at 7 days and about 33 at 14 (the features need 13 days of history, and the target needs h days of future), and a 30-day horizon leaves about 17.
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
- Story markers are small flat `pointsData` dots (for hover and click), in the story's colour; the forecast is in the tooltip. Possible additions: `ringsData` pulses for growing stories, and `labelsData` for the top few.
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
