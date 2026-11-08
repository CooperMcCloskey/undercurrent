"""Check local tracking outputs without loading the tracker or GPU libraries.

    uv run python -m undercurrent.stories.progress --start 2024-01-10 --end 2024-05-07
    uv run python -m undercurrent.stories.progress --watch 10

Dates default to PULL_START and OWN_END from a sourced block.env. Completion means
both required Parquet outputs are readable for every day, including warm-up days.
It does not verify tracker checkpoint state, data quality, or bucket uploads.
Lineage is optional: the tracker omits it on days without merges.
"""

import argparse
import datetime as dt
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from undercurrent import config


@dataclass(frozen=True)
class TrackingProgress:
    start: dt.date
    end: dt.date
    total_days: int
    completed_days: int
    missing_days: tuple[dt.date, ...]
    invalid_files: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return self.completed_days == self.total_days

    @property
    def percent(self) -> float:
        return 100 * self.completed_days / self.total_days


def tracking_progress(processed: Path, start: dt.date, end: dt.date) -> TrackingProgress:
    """Count days with readable assignment and summary outputs in an inclusive range.

    Read only Parquet footers, not article rows. Empty tables are valid; temporary
    files, outputs outside the range, and old TRACK_DONE flags do not count.
    """
    if end < start:
        raise ValueError("end must be on or after start")
    total = (end - start).days + 1
    missing = []
    invalid = []
    for offset in range(total):
        day = start + dt.timedelta(days=offset)
        ready = True
        for folder in ("story_assignments", "stories_daily"):
            path = processed / folder / f"{day}.parquet"
            try:
                pq.read_metadata(path)
            except FileNotFoundError:
                ready = False
            except (OSError, pa.ArrowException) as exc:
                ready = False
                invalid.append(f"{path}: {exc}")
        if not ready:
            missing.append(day)
    return TrackingProgress(start, end, total, total - len(missing), tuple(missing), tuple(invalid))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=dt.date.fromisoformat, default=os.getenv("PULL_START"))
    parser.add_argument("--end", type=dt.date.fromisoformat, default=os.getenv("OWN_END"))
    parser.add_argument("--processed", type=Path, default=config.PROCESSED)
    parser.add_argument("--watch", type=float, metavar="SECONDS", help="refresh until complete")
    parser.add_argument("--json", action="store_true", help="emit one JSON object per check")
    args = parser.parse_args(argv)
    if args.start is None or args.end is None:
        parser.error("provide --start and --end, or source block.env (PULL_START and OWN_END)")
    if args.end < args.start:
        parser.error("end must be on or after start")
    if args.watch is not None and (not 0 < args.watch < float("inf")):
        parser.error("--watch must be a positive finite number of seconds")
    try:
        while True:
            progress = tracking_progress(args.processed, args.start, args.end)
            if args.json:
                print(
                    json.dumps(
                        {
                            **asdict(progress),
                            "complete": progress.complete,
                            "percent": progress.percent,
                        },
                        default=str,
                    ),
                    flush=True,
                )
            else:
                state = "COMPLETE" if progress.complete else "INCOMPLETE"
                print(
                    f"{dt.datetime.now().astimezone():%H:%M:%S %Z} Tracking outputs {state}: "
                    f"{progress.completed_days}/{progress.total_days} days "
                    f"({progress.percent:.1f}%), {len(progress.missing_days)} remaining "
                    f"[{progress.start}..{progress.end}]",
                    flush=True,
                )
                if progress.missing_days:
                    print(f"First missing/incomplete day: {progress.missing_days[0]}", flush=True)
                for error in progress.invalid_files:
                    print(f"Unreadable output: {error}", flush=True)
            if args.watch is None or progress.complete:
                break
            time.sleep(args.watch)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
