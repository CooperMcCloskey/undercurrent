"""Build Jev inputs directly from recent bucket tables, without a top-story cap."""

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import httpx

from undercurrent.config import CACHE, DATA
from undercurrent.stories import emerging


def latest_day(bucket: str, gcloud: str) -> dt.date:
    result = subprocess.run(
        [gcloud, "storage", "ls", f"gs://{bucket}/processed/stories_daily/*.parquet"],
        check=True,
        capture_output=True,
        text=True,
    )
    dates = [
        dt.date.fromisoformat(match.group(1))
        for line in result.stdout.splitlines()
        if (match := re.search(r"/(\d{4}-\d{2}-\d{2})\.parquet$", line.strip()))
    ]
    if not dates:
        raise ValueError("No daily story tables found in the bucket")
    return max(dates)


def fetch_tables(bucket: str, day: dt.date, destination: Path, gcloud: str) -> None:
    """14 days supply this week's counts and an equally complete previous week."""
    days = [day - dt.timedelta(days=k) for k in reversed(range(14))]
    # Use the local CLI login, but stream through GCS's API: this avoids Windows
    # gcloud's parallel/sliced-download crash and preserves authenticated access.
    token = subprocess.run(
        [gcloud, "auth", "print-access-token"], check=True, capture_output=True, text=True
    ).stdout.strip()
    destination.mkdir(parents=True, exist_ok=True)
    with httpx.Client(headers={"Authorization": f"Bearer {token}"}, timeout=120) as client:
        jobs = []
        for table in ("stories_daily", "story_assignments", "articles"):
            for d in days:
                suffix = f"day={d}/" if table == "articles" else f"{d}.parquet"
                prefix = f"processed/{table}/{suffix}"
                result = client.get(
                    f"https://storage.googleapis.com/storage/v1/b/{bucket}/o",
                    params={"prefix": prefix},
                )
                result.raise_for_status()
                payload = result.json()
                if payload.get("nextPageToken"):
                    raise ValueError(f"Unexpected paginated partition: {prefix}")
                objects = [
                    item for item in payload.get("items", []) if item["name"].endswith(".parquet")
                ]
                if table != "articles":
                    objects = [item for item in objects if item["name"] == prefix]
                if not objects:
                    raise ValueError(
                        f"Missing bucket partition: {prefix}; refusing incomplete history"
                    )
                jobs.extend(objects)
        print(f"Reading {len(jobs)} bucket files: {days[0]} through {day}", flush=True)
        with ThreadPoolExecutor(max_workers=4) as pool:
            for index, _ in enumerate(
                pool.map(lambda obj: download_object(client, bucket, obj, destination), jobs), 1
            ):
                if index % 7 == 0 or index == len(jobs):
                    print(f"Bucket files ready: {index}/{len(jobs)}", flush=True)
    # Keep only this window in the export input even if the local cache spans more days.
    (destination / "coverage.json").write_text(
        json.dumps(
            {
                "bucket": bucket,
                "start": str(days[0]),
                "as_of": str(day),
                "tables": ["stories_daily", "story_assignments", "articles"],
                "top_story_limit": None,
                "min_articles_7d": 1,
                "objects": [
                    {
                        "name": obj["name"],
                        "generation": obj["generation"],
                        "size": obj["size"],
                        "md5Hash": obj.get("md5Hash"),
                    }
                    for obj in jobs
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def download_object(client: httpx.Client, bucket: str, obj: dict, destination: Path) -> Path:
    relative = obj["name"].removeprefix("processed/")
    target = (destination / relative).resolve()
    if destination.resolve() not in target.parents:
        raise ValueError("Bucket object escapes the download directory")
    expected = obj.get("md5Hash")

    def valid(path: Path) -> bool:
        if not path.exists() or path.stat().st_size != int(obj["size"]):
            return False
        if not expected:
            return True
        with path.open("rb") as stream:
            actual = base64.b64encode(hashlib.file_digest(stream, "md5").digest()).decode()
        return actual == expected

    if valid(target):
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".download")
    url = f"https://storage.googleapis.com/download/storage/v1/b/{bucket}/o/{quote(obj['name'], safe='')}"
    with client.stream(
        "GET", url, params={"alt": "media", "ifGenerationMatch": obj["generation"]}
    ) as response:
        response.raise_for_status()
        with temporary.open("wb") as stream:
            for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                stream.write(chunk)
    if not valid(temporary):
        raise ValueError(f"Downloaded object failed integrity check: {obj['name']}")
    temporary.replace(target)
    return target


def prepare(processed: Path, source: Path, day: dt.date) -> Path:
    from undercurrent.stories.export import run

    # No model is trained or loaded: Jev supplies categories in the next step.
    # Do not prefilter by popularity, the old category model, or story quality heuristics.
    return run(
        processed=processed,
        out=source,
        start=day,
        end=day,
        top=2**31 - 1,
        items=12,
        min_articles=1,
        keep_all=True,
        classify=None,
        split_episodes=False,
    )


def run_bucket(
    bucket: str,
    day: dt.date | None,
    gcloud: str,
    data: Path,
    out: Path,
    workers: int = 8,
    model: str = "jev-latest",
    cache: Path = CACHE / "jev",
    limit: int | None = None,
    batch_size: int = 16,
) -> None:
    day = day or latest_day(bucket, gcloud)
    root = data / str(day)
    fetch_tables(bucket, day, root, gcloud)
    source = root / "uncapped-export"
    coverage = (root / "coverage.json").read_text(encoding="utf-8")
    if not (
        (source / "meta.json").exists()
        and (source / "coverage.json").exists()
        and (source / "coverage.json").read_text(encoding="utf-8") == coverage
    ):
        source = prepare(root, source, day)
        (source / "coverage.json").write_text(coverage, encoding="utf-8")
    emerging.run(
        source, out, cache, [day], model, limit=limit, workers=workers, batch_size=batch_size
    )
    shutil.copyfile(root / "coverage.json", out / "coverage.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", default="undercurrent-data-cooper")
    parser.add_argument("--day", type=dt.date.fromisoformat, help="Defaults to latest tracked day")
    parser.add_argument("--gcloud", default=shutil.which("gcloud"), help="Google Cloud CLI path")
    parser.add_argument("--data", type=Path, default=DATA / "bucket-direct")
    parser.add_argument("--out", type=Path, default=DATA / "emerging-bucket-export")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--model", default=os.getenv("JEV_MODEL", "jev-latest"))
    args = parser.parse_args()
    if not args.gcloud:
        parser.error("Install Google Cloud CLI, or pass its executable path with --gcloud")
    if args.workers < 1 or args.workers > 32:
        parser.error("--workers must be between 1 and 32")
    if not 1 <= args.batch_size <= 32:
        parser.error("--batch-size must be between 1 and 32")
    run_bucket(args.bucket, args.day, args.gcloud, args.data, args.out, args.workers, args.model,
               batch_size=args.batch_size)


if __name__ == "__main__":
    main()
