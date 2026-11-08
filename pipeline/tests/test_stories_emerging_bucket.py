import base64
import datetime as dt
import hashlib
from types import SimpleNamespace

import httpx
import pytest

from undercurrent.stories import emerging_bucket as b


def test_latest_day_ignores_non_daily_objects(monkeypatch):
    monkeypatch.setattr(
        b.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            stdout=(
                "gs://test/processed/stories_daily/2026-11-05.parquet\n"
                "gs://test/processed/stories_daily/2026-11-06.parquet\n"
                "gs://test/processed/stories_daily/README\n"
            )
        ),
    )
    assert b.latest_day("test", "gcloud") == dt.date(2026, 11, 6)


def test_download_verifies_integrity_and_reuses_complete_files(tmp_path):
    content = b"parquet-content"
    obj = {
        "name": "processed/articles/day=2026-11-06/part.parquet",
        "size": str(len(content)),
        "generation": "123",
        "md5Hash": base64.b64encode(hashlib.md5(content).digest()).decode(),
    }
    calls = []

    def request(req):
        calls.append(req)
        assert req.url.params["ifGenerationMatch"] == "123"
        return httpx.Response(200, content=content)

    with httpx.Client(transport=httpx.MockTransport(request)) as client:
        target = b.download_object(client, "test", obj, tmp_path)
        assert target.read_bytes() == content
        b.download_object(client, "test", obj, tmp_path)
        assert len(calls) == 1
        target.write_bytes(b"broken-content!")
        b.download_object(client, "test", obj, tmp_path)
        assert len(calls) == 2


def test_corrupt_download_is_not_published(tmp_path):
    obj = {"name": "processed/stories_daily/2026-11-06.parquet", "size": "10", "generation": "1"}
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"bad"))
    ) as client:
        with pytest.raises(ValueError, match="integrity"):
            b.download_object(client, "test", obj, tmp_path)
    assert not (tmp_path / "stories_daily" / "2026-11-06.parquet").exists()


def test_prepare_does_not_apply_popularity_or_quality_cap(tmp_path, monkeypatch):
    from undercurrent.stories import export

    received = {}
    monkeypatch.setattr(export, "run", lambda **kwargs: received.update(kwargs))
    b.prepare(tmp_path, tmp_path / "export", dt.date(2026, 11, 6))
    assert received["min_articles"] == 1
    assert received["top"] > 1_000_000
    assert received["keep_all"] is True
    assert received["classify"] is None
    assert received["split_episodes"] is False


def test_fetch_refuses_missing_previous_week(tmp_path, monkeypatch):
    factory = httpx.Client
    monkeypatch.setattr(b.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="test-token"))
    monkeypatch.setattr(
        b.httpx,
        "Client",
        lambda **kwargs: factory(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"items": []}))
        ),
    )
    with pytest.raises(ValueError, match="Missing bucket partition"):
        b.fetch_tables("test", dt.date(2026, 11, 6), tmp_path, "gcloud")
    assert not (tmp_path / "coverage.json").exists()


def test_uncapped_export_keeps_small_stories_beyond_300(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from undercurrent.contracts import Snapshot
    from undercurrent.ingest.gkg import ARTICLES_SCHEMA
    from undercurrent.stories.track import SCHEMAS

    day = dt.date(2026, 11, 6)
    articles, assignments, daily = [], [], []
    for i in range(351):
        sid, aid = f"story-{i}", f"{i:016x}"
        articles.append(
            {
                "id": aid,
                "title": f"Local development {i}",
                "url": f"https://example.org/{i}",
                "domain": "example.org",
                "published_at": dt.datetime.combine(day, dt.time(), dt.UTC),
                "locations": [
                    {"name": "Rotterdam", "country": "NL", "lat": 51.92, "lon": 4.48, "type": 4}
                ]
                if i < 350
                else [],
                "persons": [],
                "orgs": [],
                "themes": [],
            }
        )
        assignments.append({"article_id": aid, "story_id": sid, "day": day, "similarity": 0.8})
        daily.append(
            {
                "story_id": sid,
                "day": day,
                "first_seen": day,
                "n_articles_day": 1,
                "label": f"Story {i}",
                "category": "other",
            }
        )
    for relative, rows, schema in (
        (f"articles/day={day}/part.parquet", articles, ARTICLES_SCHEMA),
        (f"story_assignments/{day}.parquet", assignments, SCHEMAS["story_assignments"]),
        (f"stories_daily/{day}.parquet", daily, SCHEMAS["stories_daily"]),
    ):
        path = tmp_path / "processed" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)
    out = b.prepare(tmp_path / "processed", tmp_path / "out", day)
    snap = Snapshot.model_validate_json(
        (out / "snapshots" / f"{day}.json").read_text(encoding="utf-8")
    )
    assert len(snap.stories) == 350
    assert all(s.article_count_7d == 1 for s in snap.stories)
    assert "story-350" not in {s.id for s in snap.stories}
