import datetime as dt

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from undercurrent.stories.progress import main, tracking_progress

DAY = dt.date(2024, 1, 10)


def write_day(root, day):
    for folder in ("story_assignments", "stories_daily"):
        path = root / folder / f"{day}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.table({"day": pa.array([], type=pa.date32())}), path)


def test_empty_tables_complete_without_lineage(tmp_path):
    write_day(tmp_path, DAY)
    result = tracking_progress(tmp_path, DAY, DAY)
    assert result.complete
    assert result.percent == 100


def test_holes_partial_days_and_out_of_range_do_not_count(tmp_path):
    write_day(tmp_path, DAY)
    write_day(tmp_path, DAY + dt.timedelta(days=2))
    write_day(tmp_path, DAY + dt.timedelta(days=3))
    (tmp_path / "story_assignments" / f"{DAY}.parquet").unlink()
    (tmp_path / "TRACK_DONE").write_text("old run")
    result = tracking_progress(tmp_path, DAY, DAY + dt.timedelta(days=2))
    assert result.completed_days == 1
    assert result.missing_days == (DAY, DAY + dt.timedelta(days=1))
    assert not result.complete


def test_corrupt_output_is_reported(tmp_path):
    write_day(tmp_path, DAY)
    (tmp_path / "stories_daily" / f"{DAY}.parquet").write_bytes(b"broken")
    result = tracking_progress(tmp_path, DAY, DAY)
    assert not result.complete
    assert len(result.invalid_files) == 1


def test_reversed_range(tmp_path):
    with pytest.raises(ValueError):
        tracking_progress(tmp_path, DAY, DAY - dt.timedelta(days=1))


def test_cli_environment_and_watch_stops_when_complete(tmp_path, monkeypatch, capsys):
    write_day(tmp_path, DAY)
    monkeypatch.setenv("PULL_START", str(DAY))
    monkeypatch.setenv("OWN_END", str(DAY))
    main(["--processed", str(tmp_path), "--watch", "10", "--json"])
    assert '"complete": true' in capsys.readouterr().out
