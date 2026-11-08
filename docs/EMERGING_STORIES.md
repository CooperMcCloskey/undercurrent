# Jev emerging stories development branch

`dev/jev-emerging-stories` replaces the attention forecast view with Jev classification.
It reads the existing GCS `stories_daily`, `story_assignments`, and `articles`
tables directly. There is no popularity cap before classification, model training,
embedding, fresh GDELT ingestion, or modification of shared bucket data.

Put `JEV_API_KEY` in the repository root `.env` (gitignored). The key stays in Python;
it is never sent to the browser. The official API is
https://api.typesafe.ai/v1/systemone; request and response shapes follow
https://docs.typesafe.ai/primitives/choice.

With the standard project tools installed, run from the repository root:

```powershell
# Google Cloud CLI must be authenticated and have read access to this bucket.
# This is the default, uncapped input path.
gcloud auth login
uv run python -m undercurrent.stories.emerging --bucket undercurrent-data-cooper

# Alternatively download export-final.tgz from the Google Cloud console first:
uv run python -m undercurrent.stories.emerging --archive "C:\path\export-final.tgz"

# Or use an already unpacked export:
uv run python -m undercurrent.stories.emerging --source data/export

npm run dev
```

The CLI downloads 14 days ending on the latest tracked day and builds an **uncapped**
input from every story with at least one assigned article in the latest seven days
and a known map location. The previous seven days supply growth comparisons. It
does not apply the old junk-story or minimum-three-articles filters before Jev.
Stories without location data cannot be mapped and are counted in the export logs.
Downloads use the local Google login, GCS object generation checks, file sizes and
MD5 integrity checks; missing partitions fail instead of pretending there was no news.
Only the necessary recent files are downloaded, rather than the whole five-year bucket.

Use `--day YYYY-MM-DD` for a historical day. With an explicit `--source`, repeated
`--day` options classify multiple existing snapshots. Explicit `--archive` and
`--object path/to/archive.tgz` retain the older archive-based path, including any
limits that archive already contains.
For a small integration check, pass `--limit 5 --out data/emerging-smoke`.
This limit intentionally publishes only the selected sample; do not mistake it for
a complete day's classification.

Each story gets two Jev Choice questions: emergence and primary category. By default,
16 stories' independent questions share one HTTP request; each question contains only
that story's evidence in its structured instructions. Shared state contains no stories.
The single-story mode uses the same question format (`--batch-size 1`). Prompt version
`emerging-v2-isolated-evidence` distinguishes this format from the first archive pass;
the old results are retained separately and are not mixed into the new cache entries.
The three emergence labels are `emerging_popular`, `maybe_emerging_popular`, and
`not_emerging_popular`. Ten broad categories cover politics, conflict, economy,
society, disaster, health, technology, sport, culture, and other.

The evidence contains dated headlines, locations, recent daily counts, and weekly
growth. Future headlines, future daily counts, forecasts, outcomes, and undated
story summaries are excluded. These are qualitative assessments of attention in
the next seven days, not calibrated forecasts or verified predictions. Model
confidence describes the classification, not the chance the story will go viral.
Headline-only evidence and syndication can still bias the result.

Results retain all three labels for auditability. The globe and emerging list show
only the first two, with a checkbox to hide the tentative stories. Category legend
filters apply to both the map and list. Strong emerging stories have animated rings;
click a story for its sources and classification. Old unclassified exports show an
explicit empty state. The fallback mock has invented classifications marked as demo data.

Direct bucket outputs go to `data/emerging-bucket-export`, leaving the input unchanged.
Explicit archive/directory runs default to `data/emerging-export`. The API prefers
the direct bucket output once the selected run succeeds and `meta.json` is published.
`UC_EXPORT_DIR` can select a different export directory. An explicitly configured
directory that is not ready returns 503 instead of silently showing mock data.

Requests are cached under `data/cache/jev` by their full inputs, model alias and
prompt version; rate limits and server/transport failures have bounded retries.
Reruns reuse cached results. An interrupted classification can resume in the same
output directory when its run manifest matches. Completed exports and mismatched
runs are protected: choose a fresh `--out` directory and set `UC_EXPORT_DIR` to it.
Eight requests run concurrently by default (`--workers 1` for sequential calls).
Use a pinned model id for reproducible runs;
clear the cache or change `--model` when intentionally refreshing an alias.

The checked-in Python and frontend dependency manifests are unchanged. This machine
needed portable tools, installed in ignored `data/tools/`. For local validation here:

```powershell
$env:PYTHONPATH = "$PWD/pipeline"
$gcloud = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
data/tools/venv-x64/Scripts/python.exe -m undercurrent.stories.emerging --bucket undercurrent-data-cooper --gcloud $gcloud
$env:PATH = "$PWD/data/tools/node-v22.16.0-win-arm64;$env:PATH"
data/tools/node-v22.16.0-win-arm64/npm.cmd run dev:web
```

No commit or push is made automatically. Contract changes, generated types, and
regenerated mock snapshots belong together in a future commit.

## Initial archive pass on 8 November 2026 (superseded)

Loaded `gs://undercurrent-data-cooper/export-block1.tgz` (the final export did not
exist). Its latest snapshot is **6 November 2026**, with **300 exported stories**.
All 300 were classified with **jev-1.14.0**: 200 emerging popular, 3 maybe emerging
popular, and 97 not emerging popular. The resulting 203 eligible stories are in
`data/emerging-export` and are served automatically by the API. This processes the
exported map points, rather than all raw articles in the bucket. Earlier snapshots
remain available in the downloaded source and can be classified with `--day`.

## Direct bucket input

The 14-day tables for 24 October through 6 November contain 47,835 distinct story
IDs with assigned articles in the latest week. Of those, 43,559 have usable map
locations and reach Jev; 4,276 cannot be mapped. No top-story, minimum-three-articles,
or old quality/category filter is applied. The input and object-generation manifest
are in `data/bucket-direct/2026-11-06/`; the snapshot date remains 6 November, the
latest tracked date available in the bucket.

The map initially plots all eligible results. Category filters and the size slider
can narrow it. The list has search and pages of 100 rows; its pagination does not
cap the map. On dense views, select from the list or narrow by category for direct
marker interaction. Source text is retained in its original languages.

Completed with `jev-1.14.0` and `emerging-v2-isolated-evidence`: **5,905 emerging**,
**23,781 maybe emerging**, and **13,873 not emerging**. The API now serves all
43,559 classified records from `data/emerging-bucket-export`; the globe displays
the 29,686 eligible records. `coverage.json` records source object generations,
the date window, counts, and classification totals. The original 300-point archive
pass and the interrupted single-request pass are retained separately under `data/`.
