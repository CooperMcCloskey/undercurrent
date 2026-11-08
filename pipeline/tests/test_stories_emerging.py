import datetime as dt
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from undercurrent import contracts as c
from undercurrent.api import app as api
from undercurrent.stories import emerging as e

DAY = dt.date(2026, 11, 6)


def story():
    return c.StorySummary(
        id="test-story",
        title="Port strike expands",
        category="society",
        location=c.Location(name="Rotterdam", lat=51.92, lon=4.48),
        first_seen=DAY,
        article_count_7d=20,
        growth_7d=1,
    )


def response(label="emerging_popular"):
    return {
        "model": "jev-test",
        "answers": {
            "emergence": {
                "type": "choice",
                "choice": label,
                "confidence": 0.8,
                "probabilities": {k: 0.8 if k == label else 0.1 for k in e.LABELS},
            },
            "category": {"type": "choice", "choice": "conflict", "confidence": 0.9},
        },
    }


def test_input_excludes_future_and_forecasts():
    detail = c.StoryDetail(
        id="test-story",
        title="Future title",
        summary="Future outcome",
        first_seen=DAY,
        locations=[],
        entities=[],
        timeline=[
            c.TimelinePoint(day=DAY, articles=20),
            c.TimelinePoint(day=DAY + dt.timedelta(days=1), articles=999),
        ],
        items=[
            c.Item(
                id=str(i),
                kind="news",
                title=title,
                url="https://example.org",
                published_at=dt.datetime(2026, 11, day, tzinfo=dt.UTC),
                similarity=1,
            )
            for i, (day, title) in enumerate([(6, "Known then"), (7, "Future event")])
        ],
    )
    state = e.state_for(story(), detail, DAY)
    assert state["title"] == "Known then"
    assert len(state["headlines"]) == len(state["daily_activity"]) == 1
    assert "Future" not in json.dumps(state)
    assert "forecast" not in state and "outcome" not in state


def test_invalid_jev_answers_are_rejected():
    payload = response()
    payload["answers"]["emergence"]["probabilities"]["not_emerging_popular"] = 0.8
    with pytest.raises(ValueError):
        e.parse_result(payload, DAY)
    payload = response()
    payload["answers"]["category"]["choice"] = "unknown"
    with pytest.raises(ValueError):
        e.parse_result(payload, DAY)


def test_retry_cache_and_prompt_invalidation(tmp_path, monkeypatch):
    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        payload = response()
        payload["answers"] = {
            name: payload["answers"][name.rsplit("_", 1)[0]] for name in calls[-1]["questions"]
        }
        return httpx.Response(429 if len(calls) == 1 else 200, json=payload)

    monkeypatch.setattr(e.time, "sleep", lambda _: None)
    state = e.state_for(story(), None, DAY)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        e.classify(client, state, "jev-test", tmp_path)
        e.classify(client, state, "jev-test", tmp_path)
        assert len(calls) == 2
        monkeypatch.setattr(e, "PROMPT_VERSION", "v2")
        e.classify(client, state, "jev-test", tmp_path)
        assert len(calls) == 3


@pytest.mark.parametrize("workers", [1, 4])
def test_export_keeps_all_labels_and_removes_forecasts(tmp_path, monkeypatch, workers):
    source, out = tmp_path / "source", tmp_path / "out"
    (source / "snapshots").mkdir(parents=True)
    (source / "stories").mkdir()
    items = [
        story().model_copy(update={"id": f"test-{i}", "article_count_7d": 20 + i}) for i in range(3)
    ]
    snapshot = c.Snapshot(as_of=DAY, stories=items)
    (source / "snapshots" / f"{DAY}.json").write_text(snapshot.model_dump_json())
    (source / "meta.json").write_text(c.Meta(dates=[DAY], latest=DAY).model_dump_json())
    for item in items:
        detail = c.StoryDetail(
            id=item.id,
            title=item.title,
            first_seen=DAY,
            locations=[],
            entities=[],
            timeline=[],
            items=[],
        )
        (source / "stories" / f"{item.id}.json").write_text(detail.model_dump_json())
    monkeypatch.setattr(
        e,
        "classify",
        lambda client, state, *args: response(list(e.LABELS)[state["article_count_7d"] - 20]),
    )
    monkeypatch.setenv("JEV_API_KEY", "test-secret")
    e.run(source, out, tmp_path / "cache", [DAY], "jev-test", workers=workers)
    result = c.Snapshot.model_validate_json((out / "snapshots" / f"{DAY}.json").read_text())
    assert {s.emergence.label for s in result.stories} == set(e.LABELS)
    assert all(s.forecast is None and s.outcome is None for s in result.stories)
    assert all(s.category == "conflict" for s in result.stories)
    assert c.Meta.model_validate_json((out / "meta.json").read_text()).horizons == []
    unchanged = c.Snapshot.model_validate_json((source / "snapshots" / f"{DAY}.json").read_text())
    assert all(s.emergence is None and s.category == "society" for s in unchanged.stories)
    monkeypatch.setenv("UC_EXPORT_DIR", str(out))
    client = TestClient(api.app)
    assert (
        client.get(f"/api/snapshots/{DAY}").json()["stories"][2]["emergence"]["label"]
        == "not_emerging_popular"
    )
    assert client.get("/api/stories/test-0").status_code == 200
    assert "test-secret" not in (out / "snapshots" / f"{DAY}.json").read_text()


def test_failed_run_never_publishes_meta(tmp_path, monkeypatch):
    source = tmp_path / "source"
    (source / "snapshots").mkdir(parents=True)
    (source / "stories").mkdir()
    (source / "stories" / "test-story.json").write_text(
        c.StoryDetail(
            id="test-story",
            title="Port strike",
            first_seen=DAY,
            locations=[],
            entities=[],
            timeline=[],
            items=[],
        ).model_dump_json()
    )
    (source / "meta.json").write_text(c.Meta(dates=[DAY], latest=DAY).model_dump_json())
    (source / "snapshots" / f"{DAY}.json").write_text(
        c.Snapshot(as_of=DAY, stories=[story()]).model_dump_json()
    )
    monkeypatch.setenv("JEV_API_KEY", "test")

    def fail(*args):
        raise ValueError("Invalid upstream response")

    monkeypatch.setattr(e, "classify", fail)
    with pytest.raises(ValueError):
        e.run(source, tmp_path / "out", tmp_path / "cache", [DAY], "jev-test")
    assert not (tmp_path / "out" / "meta.json").exists()
    monkeypatch.setattr(e, "classify", lambda *args: response())
    e.run(source, tmp_path / "out", tmp_path / "cache", [DAY], "jev-test")
    assert (tmp_path / "out" / "meta.json").exists()


def test_explicit_unready_export_does_not_fall_back(tmp_path, monkeypatch):
    monkeypatch.setenv("UC_EXPORT_DIR", str(tmp_path))
    assert TestClient(api.app).get("/api/meta").status_code == 503


def test_bucket_uses_existing_block_export_when_final_is_missing(tmp_path, monkeypatch):
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout="gs://test/export-partial.tgz\ngs://test/export-block1.tgz\n")

    monkeypatch.setattr(e.shutil, "which", lambda _: "gcloud")
    monkeypatch.setattr(e.subprocess, "run", command)
    monkeypatch.setattr(e, "unpack", lambda archive, destination: destination)
    e.download_bucket("test", tmp_path)
    assert calls[1][3] == "gs://test/export-block1.tgz"


def test_batch_keeps_each_story_evidence_in_its_own_question(tmp_path):
    states = [
        {"as_of": str(DAY), "title": "First story"},
        {"as_of": str(DAY), "title": "Second story"},
    ]
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        assert "First story" not in body["state"] and "Second story" not in body["state"]
        answers = {}
        for name, question in body["questions"].items():
            kind, index = name.rsplit("_", 1)
            assert question["instructions"]["evidence"] == states[int(index)]
            label = "emerging_popular" if index == "0" else "not_emerging_popular"
            answers[name] = response(label)["answers"][kind]
        return httpx.Response(200, json={"model": "jev-test", "answers": answers})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        results = e.classify_batch(client, states, "jev-test", tmp_path)
        assert [r["answers"]["emergence"]["choice"] for r in results] == [
            "emerging_popular",
            "not_emerging_popular",
        ]
        e.classify_batch(client, states, "jev-test", tmp_path)
        assert len(calls) == 1
