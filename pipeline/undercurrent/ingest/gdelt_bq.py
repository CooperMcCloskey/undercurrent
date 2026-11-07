"""Pull GDELT GKG articles from Google BigQuery into the `articles` table, one file per day.

    uv run python -m undercurrent.ingest.gdelt_bq --schema                      # check the table
    uv run python -m undercurrent.ingest.gdelt_bq --start D --end D --dry-run   # bytes + cost, free
    uv run python -m undercurrent.ingest.gdelt_bq --start D --end D             # pull

Writes, for every UTC day in the range:
    data/processed/articles/day=YYYY-MM-DD/part.parquet       docs/WORKPLAN.md 3.1 (gkg.py)
    data/processed/gdelt_totals/day=YYYY-MM-DD/part.parquet   counts before filtering (gkg.py)

Days that already have an articles file are skipped, so a killed run resumes where it stopped
(--force redoes them). Consecutive missing days are fetched in one query of up to --batch-days.

Setup and costs: pipeline/undercurrent/ingest/GDELT.md. In short, BigQuery bills by the bytes of
the columns it reads, for every partition (day) it touches, however few rows come back. The
first 1 TB a month is free, then about $6.25 per TB. --max-gb makes BigQuery refuse any single
query that would bill more, and --dry-run reports the exact bytes without running anything.
"""

import argparse
import datetime as dt
import logging
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from undercurrent.config import PROCESSED
from undercurrent.ingest import gkg

log = logging.getLogger("gdelt_bq")

TABLE = "gdelt-bq.gdeltv2.gkg_partitioned"
USD_PER_TB = 6.25  # on-demand price in the US/EU multi-regions; the first 1 TB a month is free

# Only these columns are read (and billed). GCAM, images, quotes, amounts etc. are left out.
QUERY = f"""
SELECT
  GKGRECORDID AS record_id,
  DATE AS date_int,
  SourceCommonName AS domain,
  DocumentIdentifier AS url,
  REGEXP_EXTRACT(Extras, r'<PAGE_TITLE>(.*?)</PAGE_TITLE>') AS title,
  Locations AS locations,
  Persons AS persons,
  Organizations AS orgs,
  Themes AS themes,
  V2Tone AS tone,
  TranslationInfo AS translation_info
FROM `{TABLE}`
WHERE _PARTITIONTIME BETWEEN @first_partition AND @last_partition
  AND DATE BETWEEN @date_from AND @date_to
"""


def articles_path(processed: Path, day: dt.date) -> Path:
    return processed / "articles" / f"day={day:%Y-%m-%d}" / "part.parquet"


def totals_path(processed: Path, day: dt.date) -> Path:
    return processed / "gdelt_totals" / f"day={day:%Y-%m-%d}" / "part.parquet"


def days_between(start: dt.date, end: dt.date) -> list[dt.date]:
    return [start + dt.timedelta(d) for d in range((end - start).days + 1)]


def batches(days: list[dt.date], max_days: int) -> Iterator[list[dt.date]]:
    """Runs of consecutive days, each at most max_days long."""
    run: list[dt.date] = []
    for day in sorted(days):
        if run and (day - run[-1] != dt.timedelta(1) or len(run) == max_days):
            yield run
            run = []
        run.append(day)
    if run:
        yield run


def query_parameters(first: dt.date, last: dt.date) -> dict:
    """Values for QUERY. The table is partitioned by when GDELT loaded each file, and the files
    stamped late in a day can land in the next day's partition, so one extra partition is read
    after the last day. Rows are then kept by their own DATE, so every day is complete."""
    return {
        "first_partition": dt.datetime.combine(first, dt.time(), dt.UTC),
        "last_partition": dt.datetime.combine(last + dt.timedelta(1), dt.time(), dt.UTC),
        "date_from": int(f"{first:%Y%m%d}000000"),
        "date_to": int(f"{last:%Y%m%d}235959"),
    }


def _client(project: str | None):
    try:
        from google.cloud import bigquery
    except ImportError as e:  # pragma: no cover
        raise SystemExit("google-cloud-bigquery is not installed: run `uv sync`.") from e
    project = project or os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise SystemExit(
            "No Google Cloud project: pass --project or set GOOGLE_CLOUD_PROJECT (e.g. in .env). "
            "BigQuery bills the queries to it. See ingest/GDELT.md."
        )
    try:
        return bigquery.Client(project=project)
    except Exception as e:  # google.auth.exceptions.DefaultCredentialsError and friends
        raise SystemExit(
            f"Could not connect to BigQuery ({e.__class__.__name__}: {e}). Log in with "
            "`gcloud auth application-default login` first; see ingest/GDELT.md."
        ) from e


def _job_config(params: dict, *, dry_run: bool, max_gb: float):
    from google.cloud import bigquery

    types = {
        "first_partition": "TIMESTAMP",
        "last_partition": "TIMESTAMP",
        "date_from": "INT64",
        "date_to": "INT64",
    }
    return bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter(k, types[k], v) for k, v in params.items()],
        dry_run=dry_run,
        use_query_cache=not dry_run,
        maximum_bytes_billed=int(max_gb * 1e9),
    )


def estimate(client, days: list[dt.date], *, max_gb: float) -> int:
    """Bytes the query for these days would bill (a free dry run)."""
    job = client.query(
        QUERY,
        job_config=_job_config(query_parameters(days[0], days[-1]), dry_run=True, max_gb=max_gb),
    )
    return int(job.total_bytes_processed or 0)


def fetch(client, days: list[dt.date], *, max_gb: float) -> pd.DataFrame:
    """Raw GKG rows for these consecutive days (gkg.RAW_COLUMNS)."""
    job = client.query(
        QUERY,
        job_config=_job_config(query_parameters(days[0], days[-1]), dry_run=False, max_gb=max_gb),
    )
    rows = job.result()
    try:  # the Storage Read API is much faster for big results; fall back to plain paging
        table = rows.to_arrow(create_bqstorage_client=True)
    except Exception as e:  # pragma: no cover - depends on the account's permissions
        log.warning("BigQuery Storage API unavailable (%s); downloading more slowly.", e)
        table = job.result().to_arrow(create_bqstorage_client=False)
    log.info(
        "fetched %d rows for %s..%s, billed %.2f GB",
        table.num_rows,
        days[0],
        days[-1],
        (job.total_bytes_billed or 0) / 1e9,
    )
    return table.to_pandas()


def _write(path: Path, table: pa.Table) -> None:
    """Write via a temporary name and rename, so a killed run never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    try:
        pq.write_table(table, tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_days(
    processed: Path, days: list[dt.date], raw: pd.DataFrame
) -> list[tuple[dt.date, int, int]]:
    """Split raw rows by day and write each day's articles and totals. Returns (day, raw rows,
    articles kept). Days with no rows still get (empty) files, so they count as done."""
    by_day = gkg.split_by_day(raw)
    report = []
    for day in days:
        part = by_day.get(day, raw.iloc[0:0])
        articles = gkg.build_articles(part)
        _write(totals_path(processed, day), gkg.daily_totals(part))
        _write(articles_path(processed, day), articles)  # last: its presence marks the day done
        if part.empty:
            log.warning("%s: no rows at all. A GDELT outage, or not loaded yet?", day)
        report.append((day, len(part), articles.num_rows))
    return report


def run(
    client,
    start: dt.date,
    end: dt.date,
    *,
    processed: Path = PROCESSED,
    batch_days: int = 7,
    max_gb: float = 100.0,
    dry_run: bool = False,
    force: bool = False,
) -> None:
    todo = [
        d for d in days_between(start, end) if force or not articles_path(processed, d).exists()
    ]
    skipped = len(days_between(start, end)) - len(todo)
    if skipped:
        log.info("%d day(s) already done, skipping (use --force to redo)", skipped)
    if not todo:
        return
    total_bytes = 0
    for days in batches(todo, batch_days):
        if dry_run:
            b = estimate(client, days, max_gb=max_gb)
            total_bytes += b
            print(f"{days[0]}..{days[-1]} ({len(days)} days): {b / 1e9:,.1f} GB")
            continue
        for day, n_raw, n_kept in write_days(processed, days, fetch(client, days, max_gb=max_gb)):
            print(f"{day}: {n_raw:,} GKG rows -> {n_kept:,} articles")
    if dry_run:
        tb = total_bytes / 1e12
        print(
            f"Total {total_bytes / 1e9:,.1f} GB for {len(todo)} days "
            f"(~${tb * USD_PER_TB:,.2f} at ${USD_PER_TB}/TB; the first 1 TB a month is free)."
        )


def print_schema(client) -> None:
    table = client.get_table(TABLE)
    print(
        f"{TABLE}: {table.num_rows:,} rows, {table.num_bytes / 1e12:,.1f} TB, partitioned by "
        f"{table.time_partitioning.type_ if table.time_partitioning else 'nothing'}"
    )
    for field in table.schema:
        print(f"  {field.name:<28} {field.field_type}")


def _date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--start", type=_date, help="first UTC day, YYYY-MM-DD")
    parser.add_argument("--end", type=_date, help="last UTC day (inclusive); default --start")
    parser.add_argument(
        "--project", help="Google Cloud project to bill (default $GOOGLE_CLOUD_PROJECT)"
    )
    parser.add_argument("--dry-run", action="store_true", help="report bytes and cost only (free)")
    parser.add_argument(
        "--schema", action="store_true", help="print the GKG table's columns and exit"
    )
    parser.add_argument("--batch-days", type=int, default=7, help="days per query (default 7)")
    parser.add_argument("--max-gb", type=float, default=100.0, help="refuse any query billing more")
    parser.add_argument("--force", action="store_true", help="redo days that already have files")
    parser.add_argument("--out", type=Path, default=PROCESSED, help="processed data folder")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    client = _client(args.project)
    if args.schema:
        print_schema(client)
        return
    if not args.start:
        parser.error("--start is required (or use --schema)")
    end = args.end or args.start
    yesterday = dt.datetime.now(dt.UTC).date() - dt.timedelta(1)
    if end > yesterday:
        print(f"{end} isn't complete yet; stopping at {yesterday}.", file=sys.stderr)
        end = yesterday
    if end < args.start:
        parser.error("--end is before --start")
    run(
        client,
        args.start,
        end,
        processed=args.out,
        batch_days=args.batch_days,
        max_gb=args.max_gb,
        dry_run=args.dry_run,
        force=args.force,
    )


if __name__ == "__main__":
    main()
