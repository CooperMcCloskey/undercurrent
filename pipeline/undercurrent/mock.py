"""Deterministic mock data in the exact export/API shape, written to web/public/mock/.

Lets the web app (and the API's fallback) work before the pipeline exports real data.
Everything here is invented. Regenerate after changing contracts.py:  npm run gen:mock
"""

import datetime as dt
import json
import math
import random
import shutil
from pathlib import Path

from pydantic import BaseModel

from undercurrent import contracts as c
from undercurrent.config import MOCK

SNAPSHOT_DATES = [dt.date(2026, 10, 30), dt.date(2026, 11, 6)]
HISTORY_START = dt.date(2026, 10, 10)

# id, title, place, ISO2, lat, lon, base daily articles, weekly growth multiplier
STORIES = [
    ("mock-port-strike", "Port workers' strike spreads to a second terminal", "Rotterdam", "NL", 51.92, 4.48, 18, 1.3),
    ("mock-grain-curbs", "Drought prompts grain export curbs", "Odesa", "UA", 46.48, 30.72, 45, 0.9),
    ("mock-vote-recount", "Disputed vote count triggers a recount", "Nairobi", "KE", -1.29, 36.82, 12, 1.7),
    ("mock-chip-tariffs", "New tariffs announced on chip imports", "Taipei", "TW", 25.03, 121.57, 60, 1.05),
    ("mock-glacier-flood", "Glacial lake flood warning issued", "Kathmandu", "NP", 27.72, 85.32, 6, 2.3),
    ("mock-rate-hold", "Central bank holds rates despite inflation", "Buenos Aires", "AR", -34.60, -58.38, 22, 0.8),
]  # fmt: skip

# Where each story is happening: (lat, lon, weight, radius_km). Weights sum to 1.
# Small radius = a city; large radius = a whole region or country.
FOOTPRINTS = {
    "mock-port-strike": [(51.92, 4.48, 0.55, 60), (51.22, 4.40, 0.25, 60), (53.55, 9.99, 0.20, 80)],
    "mock-grain-curbs": [(46.48, 30.72, 0.40, 80), (46.97, 31.99, 0.20, 80), (49.0, 31.4, 0.25, 450), (30.04, 31.24, 0.15, 120)],
    "mock-vote-recount": [(-1.29, 36.82, 0.60, 80), (0.0, 37.9, 0.30, 350), (-0.09, 34.77, 0.10, 60)],
    "mock-chip-tariffs": [(25.03, 121.57, 0.35, 80), (24.80, 120.97, 0.25, 50), (38.90, -77.04, 0.25, 100), (37.57, 126.98, 0.15, 100)],
    "mock-glacier-flood": [(27.72, 85.32, 0.40, 60), (28.2, 84.5, 0.40, 300), (27.33, 88.61, 0.20, 80)],
    "mock-rate-hold": [(-34.60, -58.38, 0.80, 80), (-38.4, -63.6, 0.20, 700)],
}  # fmt: skip

CATEGORIES: dict[str, c.Category] = {
    "mock-port-strike": "society",
    "mock-grain-curbs": "economy",
    "mock-vote-recount": "politics",
    "mock-chip-tariffs": "economy",
    "mock-glacier-flood": "disaster",
    "mock-rate-hold": "economy",
}


def _days(start: dt.date, end: dt.date) -> list[dt.date]:
    return [start + dt.timedelta(d) for d in range((end - start).days + 1)]


def _write(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model.model_dump(mode="json"), indent=2) + "\n")


def build(out: Path = MOCK) -> None:
    rng = random.Random(42)
    latest = SNAPSHOT_DATES[-1]
    days = _days(HISTORY_START, latest)
    shutil.rmtree(out, ignore_errors=True)

    volumes: dict[str, dict[dt.date, int]] = {}
    for sid, title, place, iso2, lat, lon, base, growth in STORIES:
        vol = {
            d: max(0, round(base * growth ** (i / 7) * rng.uniform(0.7, 1.3)))
            for i, d in enumerate(days)
        }
        volumes[sid] = vol
        loc = c.Location(name=place, country=iso2, lat=lat, lon=lon)
        items = [
            c.Item(
                id=f"{sid}-news-{n}",
                kind="news",
                title=f"{title} ({place})",
                url=f"https://example.com/{sid}/{n}",
                published_at=dt.datetime.combine(latest - dt.timedelta(n), dt.time(9 + n)),
                source="example.com",
                language="en",
                similarity=round(0.9 - 0.05 * n, 2),
                locations=[loc],
            )
            for n in range(3)
        ] + [
            c.Item(
                id=f"{sid}-bsky-0",
                kind="bluesky",
                title=f"Watching the {place} situation closely. This is getting bigger.",
                url="https://bsky.app/profile/example.bsky.social/post/mock",
                published_at=dt.datetime.combine(latest, dt.time(12)),
                source="example.bsky.social",
                language="en",
                similarity=0.71,
            )
        ]
        _write(
            out / "stories" / f"{sid}.json",
            c.StoryDetail(
                id=sid,
                title=title,
                summary="MOCK DATA: invented for UI development.",
                first_seen=HISTORY_START,
                locations=[loc],
                entities=[
                    c.Entity(
                        name=place,
                        kind="location",
                        wikipedia_title=place.replace(" ", "_"),
                        pageviews_7d=sum(vol[d] for d in days[-7:]) * 40,
                        edits_7d=rng.randint(0, 30),
                    )
                ],
                timeline=[
                    c.TimelinePoint(
                        day=d, articles=vol[d], posts=vol[d] * 3, wiki_pageviews=vol[d] * 40
                    )
                    for d in days
                ],
                items=items,
            ),
        )

    for as_of in SNAPSHOT_DATES:
        stories = []
        for sid, title, place, iso2, lat, lon, _, _ in STORIES:
            vol = volumes[sid]
            last7 = sum(vol.get(as_of - dt.timedelta(k), 0) for k in range(7))
            prev7 = sum(vol.get(as_of - dt.timedelta(k), 0) for k in range(7, 14))
            next7 = [vol.get(as_of + dt.timedelta(k), None) for k in range(1, 8)]
            growth = math.log2(max(last7, 1) / max(prev7, 1))
            p = 1 / (1 + math.exp(-(2.5 * growth - 1 + rng.uniform(-0.5, 0.5))))
            outcome = None
            if all(v is not None for v in next7):
                ratio = sum(next7) / max(last7, 1)
                outcome = c.Outcome(growth_ratio=round(ratio, 3), grew=ratio >= 2)
            stories.append(
                c.StorySummary(
                    id=sid,
                    title=title,
                    location=c.Location(name=place, country=iso2, lat=lat, lon=lon),
                    category=CATEGORIES[sid],
                    footprint=[
                        c.FootprintPoint(lat=fy, lon=fx, weight=w, radius_km=km)
                        for fy, fx, w, km in FOOTPRINTS[sid]
                    ],
                    article_count_7d=last7,
                    post_count_7d=last7 * 3,
                    growth_7d=round(growth, 3),
                    tone=round(rng.uniform(-6, 1), 2),
                    first_seen=HISTORY_START,
                    forecast=c.Forecast(
                        p_grow=round(p, 3),
                        model_version="mock",
                        factors=[
                            c.Factor(name="growth_7d", contribution=round(growth, 2)),
                            c.Factor(
                                name="wiki_pageview_growth",
                                contribution=round(rng.uniform(-0.5, 1), 2),
                            ),
                        ],
                    ),
                    outcome=outcome,
                )
            )
        _write(
            out / "snapshots" / f"{as_of.isoformat()}.json",
            c.Snapshot(as_of=as_of, stories=stories),
        )

    _write(
        out / "meta.json",
        c.Meta(
            dates=SNAPSHOT_DATES,
            latest=latest,
            model=c.ModelMetrics(
                model_version="mock",
                test_start=dt.date(2026, 10, 24),
                test_end=dt.date(2026, 10, 30),
                n_test=600,
                auc=0.74,
                brier=0.17,
                baseline_auc=0.66,
                baseline_brier=0.20,
            ),
        ),
    )
    print(f"wrote mock data to {out}")


if __name__ == "__main__":
    build()
