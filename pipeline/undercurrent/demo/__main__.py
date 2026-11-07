"""Demo: hand-written items -> vectors -> graph clustering -> an export the globe can show.

    uv run python -m undercurrent.demo                       # writes data/export/; npm run dev shows it
    uv run python -m undercurrent.demo --min-sim 0.45 --no-export   # try other settings

Scores the clustering against the hand labels in items.json, prints every story with its items,
then exports in the contracts.py shapes. It only writes into an export folder that is empty or
holds a previous demo run, so it never overwrites a real pipeline export.

Simplification: stories are clustered once over all days, then each day's snapshot counts the
items up to that day. The real pipeline must cluster point-in-time (see docs/WORKPLAN.md).
"""

import argparse
import datetime as dt
import hashlib
import json
import math
import re
import shutil
from collections import defaultdict
from pathlib import Path

import numpy as np
from pydantic import BaseModel
from sklearn.metrics import adjusted_rand_score

from undercurrent import contracts as c
from undercurrent.config import EXPORT
from undercurrent.stories.graph import centroid, cluster
from undercurrent.vectors import categorize, embed
from undercurrent.vectors.categories import prototypes

ITEMS = Path(__file__).with_name("items.json")
MARKER = ".demo-export"
RADIUS_KM = {"city": 75, "region": 250, "country": 500}
SOURCE = {"news": "news", "bluesky": "bluesky", "wikipedia": "wiki_events", "reddit": "reddit"}
HOST = {"news": "example.com", "bluesky": "example.bsky.social", "wikipedia": "en.wikipedia.org"}


def day_of(item: dict) -> dt.date:
    return dt.date.fromisoformat(item["day"])


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40].rstrip("-")


def embed_items(items: list[dict]) -> np.ndarray:
    """Embed each source with its own cleaning, keeping the original order."""
    by_source: dict[str, list[int]] = defaultdict(list)
    for i, item in enumerate(items):
        by_source[SOURCE[item["kind"]]].append(i)
    rows: dict[int, np.ndarray] = {}
    for source, idx in by_source.items():
        for i, v in zip(idx, embed([items[i]["text"] for i in idx], source), strict=True):
            rows[i] = v
    return np.stack([rows[i] for i in range(len(items))])


def item_places(items: list[dict]) -> list[list[tuple[float, float, str | None]]]:
    """(lat, lon, country) of every place each item mentions; the clustering uses these."""
    return [[(loc["lat"], loc["lon"], loc["country"]) for loc in it["locations"]] for it in items]


def item_locations(item: dict) -> list[c.Location]:
    """Every place an item mentions (the same places its story's footprint is built from), cities first."""
    places = sorted(item["locations"], key=lambda p: p["type"] != "city")  # stable
    return [
        c.Location(name=p["name"], country=p["country"], lat=p["lat"], lon=p["lon"]) for p in places
    ]


def build_stories(items: list[dict], vectors: np.ndarray, labels: np.ndarray) -> list[dict]:
    stories = []
    for label in sorted(set(labels.tolist()) - {-1}):
        idx = np.flatnonzero(labels == label)
        center = centroid(vectors[idx])
        sims = vectors[idx] @ center
        news = [k for k, i in enumerate(idx) if items[i]["kind"] == "news"]
        best = max(news or range(len(idx)), key=lambda k: sims[k])  # most central headline
        title = items[idx[best]]["text"]
        members = sorted(items[i]["id"] for i in idx)
        digest = hashlib.sha1(" ".join(members).encode()).hexdigest()[:4]
        stories.append(
            {
                "id": f"{slug(title)}-{digest}",
                "title": title,
                "label": int(label),
                "centroid": center,
                "members": [(items[i], float(s)) for i, s in zip(idx, sims, strict=True)],
            }
        )
    if stories:  # what each story is about: nearest category prototype to its centroid
        categories, scores = categorize(np.stack([s["centroid"] for s in stories]))
        for s, category, row in zip(stories, categories, scores, strict=True):
            s["category"], s["category_scores"] = category, row
    return stories


def footprint(
    members: list[tuple[dict, float]],
) -> tuple[list[c.FootprintPoint], c.Location | None]:
    """Each item's attention is split over its locations; places are weighted by their share."""
    weight: dict[str, float] = defaultdict(float)
    place: dict[str, dict] = {}
    for item, _ in members:
        for loc in item["locations"]:
            weight[loc["name"]] += 1 / len(item["locations"])
            place[loc["name"]] = loc
    if not weight:
        return [], None
    top = sorted(weight.items(), key=lambda kv: -kv[1])[:10]
    total = sum(w for _, w in top)
    points = [
        c.FootprintPoint(
            lat=place[name]["lat"],
            lon=place[name]["lon"],
            weight=w / total,
            radius_km=place[name].get("radius_km", RADIUS_KM[place[name]["type"]]),
        )
        for name, w in top
    ]
    # Marker: the most-mentioned place; if that's a region or country, its top city instead.
    main = place[top[0][0]]
    if main["type"] != "city":
        cities = [
            n
            for n, _ in top
            if place[n]["type"] == "city" and place[n]["country"] == main["country"]
        ]
        main = place[cities[0]] if cities else main
    location = c.Location(
        name=main["name"], country=main["country"], lat=main["lat"], lon=main["lon"]
    )
    return points, location


def report(
    items: list[dict],
    vectors: np.ndarray,
    labels: np.ndarray,
    stories: list[dict],
    hand_categories: dict[str, str],
) -> None:
    # Noise (hand label null, or predicted -1) counts as its own singleton in the score.
    truth = [it["story"] or f"noise:{it['id']}" for it in items]
    pred = [f"s{label}" if label >= 0 else f"noise:{i}" for i, label in enumerate(labels)]
    print(
        f"\nAdjusted Rand index vs hand labels: {adjusted_rand_score(truth, pred):.3f} (1 = perfect)"
    )
    names = prototypes()[0]
    right = sum(s["category"] == hand_categories.get(majority(s)) for s in stories)
    print(f"Categories matching hand labels: {right}/{len(stories)}")

    for s in stories:
        top = np.argsort(-s["category_scores"])[:2]
        scores = ", ".join(f"{names[k]} {s['category_scores'][k]:.2f}" for k in top)
        hand = hand_categories.get(majority(s))
        flag = "" if s["category"] == hand else f"   ← hand label: {hand}"
        print(f"\n■ story {s['label']}: {s['title']}  [{s['id']}]")
        print(f"   category: {s['category']} ({scores}){flag}")
        for item, sim in sorted(s["members"], key=lambda m: -m[1]):
            flag = "" if item["story"] == majority(s) else f"   ← hand label: {item['story']}"
            print(f"   {sim:.2f}  {item['kind']:<9} {item['text'][:80]}{flag}")
    unclustered = [items[i] for i in np.flatnonzero(labels == -1)]
    print("\n□ unclustered:")
    for item in unclustered:
        flag = "" if item["story"] is None else f"   ← hand label: {item['story']}"
        print(f"         {item['kind']:<9} {item['text'][:80]}{flag}")

    # Where would a threshold sit? Similarities within hand-labelled stories vs across them.
    sims = vectors @ vectors.T
    same, diff = [], []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if items[i]["story"] and items[i]["story"] == items[j]["story"]:
                same.append(sims[i, j])
            elif items[i]["story"] and items[j]["story"]:
                diff.append(sims[i, j])
    q = lambda xs, p: float(np.percentile(xs, p))  # noqa: E731
    print(
        f"\nCosine within a story: median {q(same, 50):.2f}, 10th pct {q(same, 10):.2f} | "
        f"across stories: median {q(diff, 50):.2f}, 99th pct {q(diff, 99):.2f}"
    )


def count_kind(
    members: list[tuple[dict, float]], kind: str, as_of: dt.date, back_from: int, back_to: int
) -> int:
    """Items of `kind` published between back_to and back_from days before as_of (inclusive)."""
    lo, hi = as_of - dt.timedelta(back_to), as_of - dt.timedelta(back_from)
    return sum(it["kind"] == kind and lo <= day_of(it) <= hi for it, _ in members)


def majority(story: dict) -> str | None:
    labels = [item["story"] for item, _ in story["members"]]
    return max(set(labels), key=labels.count)


def export(items: list[dict], stories: list[dict], out: Path, force: bool) -> None:
    if out.exists() and any(out.iterdir()) and not (out / MARKER).exists() and not force:
        raise SystemExit(f"{out} holds a real export (no {MARKER} marker). Use --out or --force.")
    shutil.rmtree(out, ignore_errors=True)
    days = sorted({day_of(it) for it in items})

    def write(rel: str, model: BaseModel) -> None:
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(model.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"
        )

    exported = []
    for s in stories:
        points, location = footprint(s["members"])
        if location is None:
            print(f"skipped (no locations): {s['title']}")
            continue
        exported.append(s)
        members = s["members"]
        write(
            f"stories/{s['id']}.json",
            c.StoryDetail(
                id=s["id"],
                title=s["title"],
                summary="DEMO DATA: hand-written example items, clustered by undercurrent.stories.graph.",
                first_seen=min(day_of(it) for it, _ in members),
                locations=[location],
                entities=[],
                timeline=[
                    c.TimelinePoint(
                        day=d,
                        articles=sum(day_of(it) == d and it["kind"] == "news" for it, _ in members),
                        posts=sum(day_of(it) == d and it["kind"] == "bluesky" for it, _ in members),
                    )
                    for d in days
                ],
                items=[
                    c.Item(
                        id=it["id"],
                        kind=it["kind"],
                        title=it["text"],
                        url=f"https://{HOST[it['kind']]}/demo/{it['id']}",
                        published_at=dt.datetime.combine(day_of(it), dt.time(12), tzinfo=dt.UTC),
                        source=HOST[it["kind"]],
                        language=it["lang"],
                        similarity=round(min(1.0, sim), 3),
                        locations=item_locations(it),
                    )
                    for it, sim in sorted(members, key=lambda m: -m[1])
                ],
            ),
        )

    for as_of in days:
        summaries = []
        for s in exported:
            seen = [(it, sim) for it, sim in s["members"] if day_of(it) <= as_of]
            points, location = footprint(seen)
            if location is None:
                continue

            last7 = count_kind(seen, "news", as_of, 0, 6)
            prev7 = count_kind(seen, "news", as_of, 7, 13)
            summaries.append(
                c.StorySummary(
                    id=s["id"],
                    title=s["title"],
                    location=location,
                    category=s["category"],
                    footprint=points,
                    article_count_7d=last7,
                    post_count_7d=count_kind(seen, "bluesky", as_of, 0, 6),
                    growth_7d=round(math.log2((last7 + 1) / (prev7 + 1)), 3),
                    first_seen=min(day_of(it) for it, _ in seen),
                )
            )
        write(f"snapshots/{as_of.isoformat()}.json", c.Snapshot(as_of=as_of, stories=summaries))

    write("meta.json", c.Meta(dates=days, latest=days[-1]))
    (out / MARKER).write_text("written by: uv run python -m undercurrent.demo\n")
    print(f"\nExported {len(exported)} stories x {len(days)} days to {out}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--min-sim", type=float, default=0.42, help="cosine cutoff for a link")
    parser.add_argument("--k", type=int, default=10, help="links per item (nearest neighbours)")
    parser.add_argument("--resolution", type=float, default=3.5, help=">1 = smaller stories")
    parser.add_argument("--min-size", type=int, default=3, help="smaller groups are noise")
    parser.add_argument(
        "--geo-km",
        type=float,
        default=1000,
        help="items further apart, sharing no country, never link",
    )
    parser.add_argument("--out", type=Path, default=EXPORT)
    parser.add_argument("--no-export", action="store_true")
    parser.add_argument("--force", action="store_true", help="overwrite a non-demo export")
    args = parser.parse_args()

    data = json.loads(ITEMS.read_text())
    items = data["items"]
    vectors = embed_items(items)
    labels = cluster(
        vectors,
        places=item_places(items),
        k=args.k,
        min_sim=args.min_sim,
        resolution=args.resolution,
        min_size=args.min_size,
        geo_km=args.geo_km,
    )
    stories = build_stories(items, vectors, labels)
    print(f"{len(items)} items, {vectors.shape[1]}-d vectors -> {len(stories)} stories")
    report(items, vectors, labels, stories, data["categories"])
    if not args.no_export:
        export(items, stories, args.out, args.force)


if __name__ == "__main__":
    main()
