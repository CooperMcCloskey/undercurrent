# Architecture

## Data flow

```
 GDELT GKG (15-min files) ─┐
 Bluesky Jetstream (live) ─┤  A: ingest/         B: stories/                C: forecast/
 Wikipedia pageviews/edits ┼─▶ data/raw ─▶ data/processed ─▶ embeddings ─▶ clusters ─▶ stories ─▶ features ─▶ model
 Reddit (stretch) ─────────┘                       │                               │                    │
                                                   └────────── export (contracts.py shapes) ◀───────────┘
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
- The main feed covers English-language sources. A separate machine-translated feed (`*.translation.gkg.csv.zip`) covers other languages. Start with English and add the translated feed later if there's time.

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
  - `V2ExtrasXML` (contains `<PAGE_TITLE>`)

**Pitfalls**

- **No article text.** The headline is in `<PAGE_TITLE>` inside the Extras field. Don't scrape articles.
- **Location format.** `V1Locations` entries look like `type#name#countrycode#adm1#lat#lon#featureid`, separated by `;`.
- **Country codes are FIPS 10-4, not ISO.** Several codes clash: `UK`→`GB`, `GM`→`DE`, `CH`→China (ISO `CH` is Switzerland), `SZ`→Switzerland, `AS`→Australia, `RS`→Russia. Use a mapping table and convert everything to ISO alpha-2 at ingest time.
- **Size.** GKG is large. Download one day and measure it before planning a backfill. For history, take one 15-minute file per hour. Growth ratios still hold on a consistent sample, which cuts the download by 4×. Use every file for the last few days, which are shown live.
- Syndicated stories appear many times (the same wire copy on many domains). Deduplicate before counting volume, or count unique domains instead.

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

### Bluesky (Ingest)

- **Jetstream:** `wss://jetstream2.us-east.bsky.network/subscribe?wantedCollections=app.bsky.feed.post`. No auth, JSON messages.
- Each post is in `commit.record.text`, with `commit.record.langs` and `commit.record.createdAt`.
- Post URL: `https://bsky.app/profile/<did>/post/<rkey>`.
- The firehose is high-volume. Filter by language, and cheaply by keyword or entity, before embedding.
- Historical search exists (`app.bsky.feed.searchPosts`) but may need authentication. Treat Bluesky as live-only; that's why it isn't a model feature.

### Reddit (stretch goal)

You need an approved OAuth "script" app and you're rate limited. Pushshift is gone. Only do this if access was approved before the event.

## Stories (Stories workstream)

1. **Embed** titles (and post text) with `config.EMBEDDING_MODEL`, a multilingual model with 384-dimension vectors. L2-normalise the vectors, so cosine similarity becomes a dot product.
2. **Cluster news** over a trailing window, using HDBSCAN (in scikit-learn) on the normalised vectors, or agglomerative clustering with a cosine distance threshold. Use FAISS for nearest-neighbour queries once the data is too big for brute force.
   - Don't link items by pairwise threshold and take connected components. That *chains* unrelated stories together.
3. **Track stories** day to day. Match each new cluster to the existing story with the most similar centroid, above a threshold, so the id stays the same. Otherwise create a new story. Ids are slugs, matching `contracts.STORY_ID_PATTERN`.
4. **Attach other sources** (posts, Wikipedia) to the nearest story centroid. Each source needs its own threshold, because short posts and headlines sit in different parts of embedding space.
5. **Location** of a story: the GDELT location mentioned most often in its articles.

## Forecast (Forecast workstream)

- **Target:** for each (story, day t), `grew = volume(t+1..t+7) >= GROWTH_RATIO × volume(t-6..t)`. Only include stories above a minimum volume, so noise isn't counted as growth.
- **No lookahead.** Features at day t use only data from day t or earlier. Story membership must be point-in-time too. If you cluster all of history at once and then backtest, the future leaks into which story an article belongs to.
  - Clean method: freeze each story's centroid at day t, then count future articles as the ones whose nearest centroid is that story.
- **Baseline:** momentum (`growth_7d`) alone. If the model doesn't beat it, report that honestly; HRT judges will respect that more than an unexplained score.
- **Split by time:** train on the earlier weeks and test on the last 2 weeks. Never use a random split.
- **Report:** AUC, Brier score and a calibration curve (`sklearn.calibration.calibration_curve`) in `ModelMetrics`. Give each story the top `factors` behind its forecast: logistic regression coefficients × feature values, or permutation importance.

## Web (Web workstream)

- `react-globe.gl` layers: `pointsData` or `hexBinPointsData` for stories, `ringsData` for "growing" pulses, and `labelsData` for the top stories.
- **Don't use `polygonsData` for the countries.** The land is one merged mesh plus one set of border lines, built in `web/src/globe/land.ts` and added as a custom layer: 2 draw calls and about 60 ms to build. Through `polygonsData`, the 1,616 polygons at 1:50m cost about 4,800 draw calls per frame and 3.7 s of blocking work at load. `polygonsData` is fine for a handful of highlighted countries.
- **Keep `<Globe>` props stable.** Accessor functions, materials and config go at module scope, and data arrays are memoised. react-globe.gl re-processes a whole layer whenever a prop changes identity, and inline arrow functions change on every render.
- With 1,000+ stories, set `pointsMerge` (one draw call, but no per-point hover) or switch to `hexBinPointsData`.
- All server data goes through `web/src/api/client.ts` (react-query). UI state (selected story, current date, replay mode) lives in a zustand store.
- The production bundle is about 2 MB because of three.js. That's fine for the demo, so don't optimise it.
