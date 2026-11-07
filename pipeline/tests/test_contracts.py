"""Exported data (and the mock data) must match contracts.py, so pipeline and web app agree.

Runs against web/public/mock/ always, and against data/export/ once the pipeline has written it.
"""

import json
from pathlib import Path

import pytest

from undercurrent import contracts as c
from undercurrent.config import EXPORT, MOCK

ROOTS = [MOCK] + ([EXPORT] if (EXPORT / "meta.json").exists() else [])


def _load(path: Path) -> object:
    return json.loads(path.read_text())


@pytest.mark.parametrize("root", ROOTS, ids=lambda p: p.name)
def test_export_matches_contract(root: Path) -> None:
    meta = c.Meta.model_validate(_load(root / "meta.json"))
    assert meta.dates == sorted(meta.dates)
    assert meta.latest == meta.dates[-1]

    story_ids: set[str] = set()
    for day in meta.dates:
        snap = c.Snapshot.model_validate(_load(root / "snapshots" / f"{day.isoformat()}.json"))
        assert snap.as_of == day
        story_ids |= {s.id for s in snap.stories}

    for sid in story_ids:
        detail = c.StoryDetail.model_validate(_load(root / "stories" / f"{sid}.json"))
        assert detail.id == sid


def test_schema_exports() -> None:
    defs = c.json_schema()["$defs"]
    for model in c.TOP_LEVEL:
        assert model.__name__ in defs
