"""Where a story is happening: its footprint (the weighted places the globe draws heat from) and
its main location (where the marker and label go). Pure functions; `stories.export` feeds them.

Every article spreads one unit of attention evenly over the places it counts towards
(`article_places`); a story's footprint is the sum over its articles. The globe draws a dot at
each place in `Item.locations`, built by the same function, so the dots and the heat line up.
`stories/export.py` does the same sum in SQL for speed (`PLACE_WEIGHTS_SQL` there); the tests
check the two agree.
"""

from dataclasses import dataclass

from undercurrent import contracts as c

# GDELT location types: 1 country, 2 US state, 3 US city, 4 world city, 5 world state/region.
KIND = {1: "country", 2: "region", 3: "city", 4: "city", 5: "region"}
RADIUS_KM = {"city": 75, "region": 250, "country": 500}
SPECIFIC_FIRST = {"city": 0, "region": 1, "country": 2}
COUNTRY = 1


def kind(gdelt_type: int | None) -> str:
    return KIND.get(gdelt_type or 0, "region")


@dataclass(frozen=True)
class Place:
    name: str
    type: int
    country: str | None
    lat: float
    lon: float

    @property
    def kind(self) -> str:
        return kind(self.type)

    def location(self) -> c.Location:
        return c.Location(
            name=self.name, country=self.country, lat=round(self.lat, 4), lon=round(self.lon, 4)
        )


def place(loc: dict) -> Place:
    return Place(loc["name"], int(loc.get("type") or 0), loc.get("country"), loc["lat"], loc["lon"])


def article_places(locations: list[dict] | None) -> list[Place]:
    """The places an article counts towards, most specific first: everything it mentions, minus a
    country that one of its cities or regions already stands in ("Rotterdam" and "Netherlands"
    count once, in Rotterdam), so countries only get heat from articles that name nothing finer."""
    places = [place(loc) for loc in locations or []]
    covered = {p.country for p in places if p.type != COUNTRY and p.country}
    kept = [p for p in places if not (p.type == COUNTRY and p.country in covered)]
    return sorted(kept, key=lambda p: SPECIFIC_FIRST[p.kind])  # stable: GDELT's order otherwise


def add_article(weights: dict[Place, float], locations: list[dict] | None) -> None:
    places = article_places(locations)
    for p in places:
        weights[p] = weights.get(p, 0.0) + 1 / len(places)


def main_place(weights: dict[Place, float]) -> Place:
    """The most-mentioned place; if that's a country or region, its most-mentioned city (or, for a
    country, region) instead, so the marker sits on something specific."""
    ranked = sorted(weights, key=lambda p: (-weights[p], p.name))
    top = ranked[0]
    finer = [k for k, rank in SPECIFIC_FIRST.items() if rank < SPECIFIC_FIRST[top.kind]]
    for want in finer:  # city first
        inside = [p for p in ranked if p.kind == want and p.country and p.country == top.country]
        if inside:
            return inside[0]
    return top


def footprint(
    weights: dict[Place, float], limit: int = 10
) -> tuple[list[c.FootprintPoint], c.Location] | None:
    """The `limit` heaviest places as footprint points (weights summing to 1) and the main
    location. None if the story mentions no places."""
    weights = {p: w for p, w in weights.items() if w > 0}
    if not weights:
        return None
    top = sorted(weights, key=lambda p: (-weights[p], p.name))[:limit]
    total = sum(weights[p] for p in top)
    points = [
        c.FootprintPoint(
            lat=round(p.lat, 4),
            lon=round(p.lon, 4),
            weight=max(round(weights[p] / total, 4), 1e-4),
            radius_km=RADIUS_KM[p.kind],
        )
        for p in top
    ]
    return points, main_place(weights).location()


def top_locations(weights: dict[Place, float], limit: int = 5) -> list[c.Location]:
    """The main location first, then the next heaviest places."""
    if not weights:
        return []
    main = main_place(weights)
    rest = [p for p in sorted(weights, key=lambda p: (-weights[p], p.name)) if p != main]
    return [p.location() for p in [main, *rest][:limit]]
