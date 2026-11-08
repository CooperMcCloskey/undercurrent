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
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.json_schema import models_json_schema

SourceKind = Literal["news", "bluesky", "reddit", "wikipedia"]
EntityKind = Literal["person", "organization", "location", "other"]
# What a story is about; the globe colours stories by this. Assigned by
# undercurrent.vectors.categories (embedding similarity to category descriptions).
Category = Literal[
    "politics",
    "conflict",
    "economy",
    "society",
    "disaster",
    "health",
    "technology",
    "sport",
    "culture",
    "other",
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
    contribution: float = Field(
        description="Signed, in log2 units of the projected change in volume; positive pushes "
        "the projection up"
    )


def _ascending(days: list[int]) -> list[int]:
    if any(a >= b for a, b in zip(days, days[1:], strict=False)):
        raise ValueError(f"horizons must be strictly ascending by days, got {days}")
    return days


class HorizonForecast(_Model):
    """A story's projected size `days` days after the snapshot's `as_of`.

    The unit is `StorySummary.article_count_7d`: the number the globe draws a story's heat from.
    So the projection for day t+h is drawn by the same code as the real snapshot of day t+h. To
    look ahead, take the story from the snapshot of day t and swap `article_count_7d` for
    `articles_7d`. The model is judged on the same number (see `Outcome`).
    """

    days: int = Field(ge=1, description="How many days after the snapshot's as_of")
    articles_7d: float = Field(
        ge=0,
        description="Median projection of article_count_7d on day as_of + days: articles "
        "published in the 7 days ending that day",
    )
    low: float = Field(ge=0, description="Lower end of the central `Forecast.interval` range")
    high: float = Field(ge=0, description="Upper end of the central `Forecast.interval` range")
    factors: list[Factor] = Field(
        default_factory=list, description="Top drivers of this projection; may be empty"
    )

    @model_validator(mode="after")
    def _low_median_high(self) -> Self:
        if not self.low <= self.articles_7d <= self.high:
            raise ValueError(
                f"need low <= articles_7d <= high, got {self.low}, {self.articles_7d}, {self.high}"
            )
        return self


class Forecast(_Model):
    """Where a story is expected to be over the next few days: one projection per horizon."""

    model_version: str
    interval: float = Field(
        0.8,
        gt=0,
        lt=1,
        description="Share of outcomes that should fall between each horizon's low and high "
        "(0.8 = the 10th to 90th percentile)",
    )
    horizons: list[HorizonForecast] = Field(
        min_length=1,
        description="Strictly ascending by days; every story in a snapshot uses a subset of "
        "Meta.horizons",
    )

    @field_validator("horizons")
    @classmethod
    def _ascending_days(cls, horizons: list[HorizonForecast]) -> list[HorizonForecast]:
        _ascending([h.days for h in horizons])
        return horizons


class HorizonOutcome(_Model):
    days: int = Field(ge=1)
    articles_7d: int = Field(
        ge=0,
        description="What article_count_7d actually read on as_of + days (0 if the story had "
        "gone quiet). Equals that day's snapshot whenever the story is in it.",
    )


class Outcome(_Model):
    """What actually happened. Only present on past snapshots (backtest / replay), and only for
    the horizons whose day is on or before Meta.latest."""

    horizons: list[HorizonOutcome] = Field(
        min_length=1, description="Strictly ascending by days; a subset of the forecast's horizons"
    )

    @field_validator("horizons")
    @classmethod
    def _ascending_days(cls, horizons: list[HorizonOutcome]) -> list[HorizonOutcome]:
        _ascending([h.days for h in horizons])
        return horizons


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
    forecast: Forecast | None = Field(
        None,
        description="Where the story is expected to be in the coming days. None = no projection "
        "(too little history): the future globe then draws it unchanged.",
    )
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


class HorizonMetrics(_Model):
    """How well the projections `days` ahead did on the held-out test days."""

    days: int = Field(ge=1)
    n_test: int = Field(description="(story, day) pairs scored")
    mae_log2: float = Field(
        ge=0,
        description="Mean |log2((projected + 1) / (actual + 1))|: the typical miss as a factor "
        "of 2^x (0 = perfect, 1 = off by 2x)",
    )
    baseline_mae_log2: float = Field(
        ge=0, description="The same error for the baseline; the model earns its place by beating it"
    )
    coverage: float = Field(
        ge=0,
        le=1,
        description="Share of actuals inside [low, high]. Should be close to Forecast.interval; "
        "much lower means the ranges are overconfident",
    )


class ModelMetrics(_Model):
    model_version: str
    test_start: dt.date
    test_end: dt.date
    baseline_name: str = Field(
        "persistence", description="'persistence' = the story stays as big as it is now"
    )
    horizons: list[HorizonMetrics] = Field(
        description="One per horizon, ascending by days. A horizon is only worth showing as a "
        "forecast if mae_log2 < baseline_mae_log2."
    )

    @field_validator("horizons")
    @classmethod
    def _ascending_days(cls, horizons: list[HorizonMetrics]) -> list[HorizonMetrics]:
        _ascending([h.days for h in horizons])
        return horizons


class Meta(_Model):
    dates: list[dt.date] = Field(description="Days that have a snapshot, ascending")
    latest: dt.date
    horizons: list[int] = Field(
        default_factory=list,
        description="Forecast horizons in days, ascending: how far past `latest` the globe can "
        "look. Empty if there are no forecasts.",
    )
    model: ModelMetrics | None = None

    @field_validator("horizons")
    @classmethod
    def _ascending_days(cls, horizons: list[int]) -> list[int]:
        return _ascending(horizons)


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
