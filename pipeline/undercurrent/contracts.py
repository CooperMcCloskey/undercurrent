"""The shared data contract: the source of truth for every JSON shape that crosses a workstream.

The pipeline writes these shapes to data/export/, the API serves them unchanged, and the web app
reads them through types generated from this file.

Changing it (do all of this in ONE commit, then tell the team):
  1. edit this file (prefer adding optional fields over renaming or removing)
  2. npm run gen:types      -> web/src/api/types.gen.ts
  3. update mock.py, then npm run gen:mock   -> web/public/mock/
  4. uv run pytest

Files on disk (data/export/ and web/public/mock/ use the same layout):
  meta.json                 Meta
  snapshots/<YYYY-MM-DD>.json   Snapshot
  stories/<story_id>.json   StoryDetail
"""

import datetime as dt
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.json_schema import models_json_schema

SourceKind = Literal["news", "bluesky", "reddit", "wikipedia"]
EntityKind = Literal["person", "organization", "location", "other"]
# What a story is about; the globe colours stories by this. Assigned by
# undercurrent.vectors.categories (embedding similarity to category descriptions).
Category = Literal[
    "politics", "conflict", "economy", "society", "disaster", "health", "technology", "other"
]

STORY_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,80}$"


class _Model(BaseModel):
    # extra="forbid": typos in field names fail validation instead of silently passing.
    # serialization_defaults_required: fields with defaults are always present in exported JSON,
    # so the generated TypeScript marks them as required.
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


class Location(_Model):
    name: str
    country: str | None = Field(
        None,
        description="ISO 3166-1 alpha-2. GDELT uses FIPS 10-4 codes (UK, GM, CH...): convert in ingest.",
    )
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class Entity(_Model):
    name: str
    kind: EntityKind
    wikipedia_title: str | None = None
    pageviews_7d: int | None = None
    edits_7d: int | None = None


class Item(_Model):
    """One piece of media attached to a story."""

    id: str
    kind: SourceKind
    title: str = Field(description="Headline, or post text truncated to ~280 chars")
    url: str
    published_at: dt.datetime
    source: str | None = Field(None, description="Domain, Bluesky handle or subreddit")
    language: str | None = None
    similarity: float = Field(ge=-1, le=1, description="Cosine similarity to the story centroid")
    locations: list[Location] = Field(
        default_factory=list,
        description="Every place this item mentions, the main one first (for a GDELT article: "
        "its most specific location first). The globe puts a dot at each of them, which is also "
        "why the story's heat sits where it does. Empty if unknown, e.g. most social posts.",
    )


class Factor(_Model):
    name: str
    contribution: float = Field(description="Signed; positive pushes toward growth")


class Forecast(_Model):
    p_grow: float = Field(
        ge=0,
        le=1,
        description="P(volume over next horizon_days >= GROWTH_RATIO x previous horizon_days)",
    )
    horizon_days: int = 7
    model_version: str
    factors: list[Factor] = Field(default_factory=list)


class Outcome(_Model):
    """What actually happened. Only present on past snapshots (backtest / replay)."""

    growth_ratio: float = Field(description="Volume next horizon_days / previous horizon_days")
    grew: bool


class FootprintPoint(_Model):
    """One place a story is happening. A story's footprint (several of these) is how far it
    spreads on the heatmap. Build it from the story's GDELT location mentions."""

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    weight: float = Field(gt=0, description="Share of the story's attention here; sums to 1")
    radius_km: float = Field(
        150,
        gt=0,
        description="How far this mention spreads. Use GDELT's location type: city ~75, "
        "state/region ~250, whole country ~half its width (300-1000)",
    )


class StorySummary(_Model):
    id: str = Field(pattern=STORY_ID_PATTERN, description="Stable across days")
    title: str
    location: Location = Field(description="Main location: anchor for the marker and label")
    category: Category = Field("other", description="What the story is about; sets its colour")
    footprint: list[FootprintPoint] = Field(
        default_factory=list,
        description="Where the story is happening, for the heatmap. Empty means just `location`.",
    )
    article_count_7d: int
    post_count_7d: int = 0
    growth_7d: float = Field(description="log2(volume last 7 days / volume previous 7 days)")
    tone: float | None = Field(None, description="Mean GDELT tone of the story's articles")
    first_seen: dt.date
    forecast: Forecast | None = None
    outcome: Outcome | None = None

    @field_validator("footprint")
    @classmethod
    def _weights_sum_to_one(cls, footprint: list[FootprintPoint]) -> list[FootprintPoint]:
        total = sum(p.weight for p in footprint)
        if footprint and abs(total - 1) > 0.01:
            raise ValueError(f"footprint weights must sum to 1, got {total:.3f}")
        return footprint


class Snapshot(_Model):
    """Every story as of one day. Computed using only data up to and including as_of."""

    as_of: dt.date
    stories: list[StorySummary]


class TimelinePoint(_Model):
    day: dt.date
    articles: int
    posts: int = 0
    wiki_pageviews: int | None = None


class StoryDetail(_Model):
    id: str = Field(pattern=STORY_ID_PATTERN)
    title: str
    summary: str | None = None
    first_seen: dt.date
    locations: list[Location]
    entities: list[Entity]
    timeline: list[TimelinePoint]
    items: list[Item]


class ModelMetrics(_Model):
    model_version: str
    test_start: dt.date
    test_end: dt.date
    n_test: int
    auc: float
    brier: float
    baseline_name: str = "momentum"
    baseline_auc: float
    baseline_brier: float


class Meta(_Model):
    dates: list[dt.date] = Field(description="Days that have a snapshot, ascending")
    latest: dt.date
    model: ModelMetrics | None = None


class LivePost(_Model):
    """Message on the /api/live websocket."""

    item: Item
    story_id: str | None = None


TOP_LEVEL: list[type[BaseModel]] = [Meta, Snapshot, StoryDetail, LivePost]


def _drop_field_titles(node: object) -> None:
    # Pydantic titles every field; json2ts turns each title into an alias type (Name1, Kind1...).
    if isinstance(node, dict):
        for prop in node.get("properties", {}).values():
            prop.pop("title", None)
        for value in node.values():
            _drop_field_titles(value)
    elif isinstance(node, list):
        for value in node:
            _drop_field_titles(value)


def json_schema() -> dict:
    _, schema = models_json_schema(
        [(m, "serialization") for m in TOP_LEVEL], title="UndercurrentContract"
    )
    _drop_field_titles(schema)
    return schema


if __name__ == "__main__":
    print(json.dumps(json_schema(), indent=2))
