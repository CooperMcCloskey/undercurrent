"""Exported data (and the mock data) must match contracts.py, so pipeline and web app agree.

Runs against web/public/mock/ always, and against data/export/ once the pipeline has written it.
"""

import datetime as dt
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


@pytest.mark.parametrize("root", ROOTS, ids=lambda p: p.name)
def test_forecasts_are_consistent(root: Path) -> None:
    """Forecasts are in the unit the globe draws, and outcomes agree with the later snapshots."""
    meta = c.Meta.model_validate(_load(root / "meta.json"))
    snaps = {
        day: c.Snapshot.model_validate(_load(root / "snapshots" / f"{day.isoformat()}.json"))
        for day in meta.dates
    }
    if meta.model:
        assert [h.days for h in meta.model.horizons] == meta.horizons

    for as_of, snap in snaps.items():
        for story in snap.stories:
            if story.forecast:
                assert {h.days for h in story.forecast.horizons} <= set(meta.horizons)
            if story.outcome:
                assert story.forecast, "an outcome without the forecast it grades"
                forecast_days = {h.days for h in story.forecast.horizons}
                for h in story.outcome.horizons:
                    day = as_of + dt.timedelta(h.days)
                    assert h.days in forecast_days
                    assert day <= meta.latest, "outcome for a day that hasn't happened yet"
                    # The target is the later snapshot's own number, whenever the story is in it.
                    later = snaps.get(day)
                    match = [s for s in later.stories if s.id == story.id] if later else []
                    if match:
                        assert h.articles_7d == match[0].article_count_7d


def test_forecast_rejects_bad_ranges() -> None:
    ok = {"days": 7, "articles_7d": 10.0, "low": 6.0, "high": 15.0}
    c.HorizonForecast.model_validate(ok)
    with pytest.raises(ValueError):
        c.HorizonForecast.model_validate({**ok, "low": 11.0})  # median below the low end
    with pytest.raises(ValueError):
        c.Forecast.model_validate(
            {"model_version": "t", "horizons": [ok, {**ok, "days": 3}]}  # not ascending
        )


def test_schema_exports() -> None:
    defs = c.json_schema()["$defs"]
    for model in c.TOP_LEVEL:
        assert model.__name__ in defs
