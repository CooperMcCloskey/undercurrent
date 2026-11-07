# GDELT from BigQuery

`python -m undercurrent.ingest.gdelt_bq` pulls GDELT's Global Knowledge Graph (GKG) from Google BigQuery and writes the `articles` table everyone downstream reads (`docs/WORKPLAN.md` 3.1). One query covers up to a week of days. Only the columns we need are read, and the output is one compact Parquet file per day.

| Output | What |
|---|---|
| `data/processed/articles/day=YYYY-MM-DD/part.parquet` | One row per distinct headline: `id`, `url`, `domain`, `published_at`, `title`, `locations` (ISO country codes), `persons`, `orgs`, `themes`, `tone`, `dup_count`, `sampled`, `translated`, `lang` |
| `data/processed/gdelt_totals/day=YYYY-MM-DD/part.parquet` | GKG rows per feed (English or translated) and first-location country, counted **before** any filtering, plus how many had a headline. Use it to normalise volume and spot outages |

The vector store (`vectors.store`), the story tracker (`stories.track`) and the forecast proxy (`forecast.run --source proxy`) read these files as they are. `pipeline/tests/test_ingest_gdelt.py` checks that.

## One-time setup

1. **A Google Cloud project.** Create one at console.cloud.google.com. Without billing, the BigQuery sandbox still lets you query public data, up to 1 TB a month free. Turn billing on before pulling more than that.
2. **Enable two APIs** in that project: *BigQuery API* and *BigQuery Storage API* (the second makes downloads much faster; without it the command falls back to slower paging).
3. **Log in on the machine that runs the pull:**

   ```bash
   brew install --cask google-cloud-sdk        # macOS; Google Cloud VMs already have gcloud
   gcloud auth application-default login
   ```

   On a Google Cloud VM, the VM's service account is used automatically. It needs the *BigQuery Job User* and *BigQuery Read Session User* roles.
4. **Name the project** in `.env` (gitignored): `GOOGLE_CLOUD_PROJECT=your-project-id`, or pass `--project` each time.

## First run: measure one day before anything big

```bash
uv run python -m undercurrent.ingest.gdelt_bq --schema                                  # the table's columns
uv run python -m undercurrent.ingest.gdelt_bq --start 2026-11-01 --dry-run              # bytes and cost, free
uv run python -m undercurrent.ingest.gdelt_bq --start 2026-11-01                        # pull one day
```

Then check the day and write the numbers in `PLAN.md` Notes (A):

```bash
uv run python -c "
import duckdb
a = \"read_parquet('data/processed/articles/**/*.parquet', hive_partitioning=true)\"
t = \"read_parquet('data/processed/gdelt_totals/**/*.parquet', hive_partitioning=true)\"
print(duckdb.sql(f'SELECT day, count(*) articles, sum(dup_count) copies, avg(translated::int) translated_share FROM {a} GROUP BY day'))
print(duckdb.sql(f'SELECT day, feed, sum(records) gkg_rows, sum(with_title) with_title FROM {t} GROUP BY ALL ORDER BY ALL'))
"
du -sh data/processed/articles data/processed/gdelt_totals
```

**Three things to confirm on that first day**, because the code assumes them:

- **The translated feed is in the same table**, with `-T` in `GKGRECORDID`. If `gdelt_totals` shows no `translated` rows, it isn't, and the query needs a second table.
- **Most rows have a headline** (`with_title` close to `gkg_rows`). Rows without one are dropped, because a headline is what gets embedded.
- **The partitions line up with the dates.** The table is partitioned by when GDELT loaded each file, so the query reads one partition past the last day and keeps rows by their own `DATE`. If a day looks short at its end, raise that to two partitions in `query_parameters()`.

## The 12-month pull

```bash
uv run python -m undercurrent.ingest.gdelt_bq --start 2025-11-01 --end 2026-11-06 --dry-run   # total cost first
uv run python -m undercurrent.ingest.gdelt_bq --start 2026-09-07 --end 2026-11-06             # newest 60 days first
uv run python -m undercurrent.ingest.gdelt_bq --start 2025-11-01 --end 2026-09-06             # then the rest
```

- **Resumable.** Days with an articles file are skipped, and files are written under a temporary name and then renamed, so a killed run never leaves half a day. `--force` redoes days.
- **Today is never pulled.** The command stops at yesterday (UTC), whose last files may still be loading. Re-run with `--force` for the last day or two if they look short.
- **Run it on a Google Cloud VM in the US** for the big pull: the GDELT dataset lives in the US multi-region, so downloads there are fast and cheap. Then sync `data/processed/articles/` to the bucket (`data/README.md`).

## Cost

BigQuery bills the bytes of the columns a query reads, for every partition (day) it touches, however few rows come back. The first 1 TB a month is free, then about $6.25 per TB (on-demand, US/EU).

- **Always dry-run first.** It reports exact bytes without running anything and is free.
- `--max-gb` (default 100) makes BigQuery **refuse** any single query that would bill more. Raise it deliberately if a week of data needs more.
- Each query reads one extra day of partitions (see above). Bigger `--batch-days` spreads that over more days, but downloads more per query.
- The biggest column, GCAM (emotion scores), and images, quotes and amounts are never read. Neither is `V2Locations`, which repeats `Locations` with offsets.
- Downloading results through the Storage API costs about $1.10 per TB read, so pennies for a year of compact rows.

## What the code does

| Step | Where | Detail |
|---|---|---|
| Query | `gdelt_bq.py` (`QUERY`) | `GKGRECORDID`, `DATE`, `SourceCommonName`, `DocumentIdentifier`, the `<PAGE_TITLE>` from `Extras`, `Locations`, `Persons`, `Organizations`, `Themes`, `V2Tone`, `TranslationInfo` |
| Split by day | `gkg.split_by_day` | By each row's own `DATE` (UTC), not by partition |
| Totals | `gkg.daily_totals` | Before any filtering |
| Clean | `gkg.build_articles` | Headline HTML-unescaped; rows without a headline or URL dropped |
| Dedupe | `gkg.build_articles` | The same URL keeps its first row (it can appear in both feeds). Then the same lower-cased headline on other sites folds into the earliest copy, and `dup_count` counts the URLs it stands for |
| Places | `gkg.parse_locations` | `type#name#FIPS#adm1#lat#lon#featureid`. Repeats and places without coordinates are dropped. **Countries are converted from FIPS to ISO** (`fips.py`): GDELT's `CH` is China and `GM` is Germany |
| Language | `gkg.source_language` | `srclc:fra` → `fra` (three letters, from `TranslationInfo`); null for the English feed |
| Ids | `gkg.article_id` | First 16 hex characters of sha1(url): stable across reruns |

`gkg.py` doesn't know about BigQuery. A downloader for the 15-minute zip files (for other clouds) only needs to produce the same raw columns and call it.

## Troubleshooting

- **"Could not connect to BigQuery" / credentials errors:** run `gcloud auth application-default login` on that machine.
- **"Query exceeded limit for bytes billed":** the `--max-gb` guard worked. Dry-run to see the size, then raise `--max-gb` or lower `--batch-days`.
- **"BigQuery Storage API unavailable":** enable the BigQuery Storage API, or grant *BigQuery Read Session User*. The pull still works, just slower.
- **SSL / `OSStatus -26276` errors inside a Claude Code sandbox:** set `SSL_CERT_FILE` to certifi's bundle (`uv run python -m certifi`), as in `AGENTS.md`.
