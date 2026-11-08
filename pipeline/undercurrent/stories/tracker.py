"""Track stories day by day: match each day's items to the stories we already know, start new
ones from what is left, merge duplicates, retire the idle.

This replaces clustering all of history at once (graph.py), which can't scale and isn't
point-in-time. The state after day t depends only on days <= t, and a decision about an article is
never changed afterwards: stories can later be merged (recorded in a lineage table), but the rows
already written keep the id they had.

One day, in order (Tracker.step):
 1. Every story's centroid fades (half-life), so it follows how the story is worded now.
 2. Each item goes to the most similar story centroid above assign_sim, unless it is clearly
    about somewhere else (the place rule from graph.py).
 3. Items that matched nothing are clustered among themselves (cluster.py); each group of at
    least min_size becomes a new story.
 4. Stories whose centroids are now nearly the same are merged; the oldest id survives.
 5. Stories with no new item for idle_days are retired.

Items that belong to no story (single articles nobody else wrote about) get no row: they are the
noise. Items must be fed in day order; a day can't be processed twice.
"""

import datetime as dt
import hashlib
import os
import pickle
import re
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

from undercurrent.stories.cluster import cluster_items
from undercurrent.stories.graph import Place, far_apart
from undercurrent.stories.search import top_k
from undercurrent.vectors.thresholds import threshold

STATE_VERSION = 1
PlaceKey = tuple[float, float, str | None]
Categorizer = Callable[[np.ndarray], tuple[list[str], np.ndarray]]


@dataclass(frozen=True)
class TrackerConfig:
    """The knobs. The two cutoffs default from vectors.threshold("news", "news"), which is still a
    placeholder: calibrate it, then read 20 real stories before trusting any of these."""

    link_sim: float | None = None  # item <-> item, when starting a story from leftovers
    assign_sim: float | None = (
        None  # item -> an existing story's centroid (default: link_sim + 0.05)
    )
    candidates: int = 5  # centroids tried per item (the place rule may reject the best)
    knn: int = 10  # neighbours per item when starting stories
    min_size: int = 3  # items needed to start a story
    max_component: int = 300
    geo_km: float = 1000.0
    resolution: float = 1.0
    min_gain: float = 0.05  # see cluster._tighter
    half_life_days: float = 7.0  # how fast a story's centroid forgets its old wording
    idle_days: int = 14  # a story with no new item for this long is retired
    merge_sim: float | None = 0.85  # centroids this alike are one story (None = never merge)
    max_places: int = 8  # places remembered per story, for the place rule
    seed: int = 0

    def resolved(self) -> "TrackerConfig":
        link = self.link_sim if self.link_sim is not None else threshold("news", "news")
        assign = self.assign_sim if self.assign_sim is not None else link + 0.05
        return replace(self, link_sim=link, assign_sim=assign)


@dataclass
class DayResult:
    day: dt.date
    assignments: pd.DataFrame  # article_id, story_id, day, similarity
    stories: pd.DataFrame  # story_id, day, n_articles_day, first_seen, label, category
    lineage: pd.DataFrame  # old_story_id, new_story_id, day (stories merged today)
    stats: dict[str, int]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40].rstrip("-") or "story"


class Tracker:
    def __init__(
        self,
        dim: int,
        config: TrackerConfig | None = None,
        *,
        categorize: Categorizer | None = None,
    ) -> None:
        self.cfg = (config or TrackerConfig()).resolved()
        self.dim = dim
        self.categorize = categorize
        self.day: dt.date | None = None  # the last day processed
        self.ids: list[str] = []
        self.sums = np.zeros((0, dim), dtype=np.float32)  # faded sum of member vectors
        self.counts = np.zeros(0, dtype=np.int64)  # members ever
        self.first_day = np.zeros(0, dtype=np.int64)  # date ordinals
        self.last_day = np.zeros(0, dtype=np.int64)
        self.labels: list[str] = []
        self.places: list[dict[PlaceKey, int]] = []
        self._top_cache: list[list[Place] | None] = []
        self.used_ids: set[str] = set()
        self._codes: dict[str, int] = {}

    # ---- small helpers ---------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.ids)

    def _unit(self) -> np.ndarray:
        norms = np.maximum(np.linalg.norm(self.sums, axis=1, keepdims=True), 1e-12)
        return np.ascontiguousarray(self.sums / norms, dtype=np.float32)

    def _code(self, country: str | None) -> int:
        return -1 if not country else self._codes.setdefault(country, len(self._codes))

    def _top(self, i: int) -> list[Place]:
        cached = self._top_cache[i]
        if cached is None:
            ranked = sorted(self.places[i].items(), key=lambda kv: -kv[1])[: self.cfg.max_places]
            cached = [(lat, lon, cc) for (lat, lon, cc), _ in ranked]
            self._top_cache[i] = cached
        return cached

    def _add_places(self, i: int, item_places: Sequence[Place]) -> None:
        book = self.places[i]
        for lat, lon, cc in item_places[: self.cfg.max_places]:
            key = (round(lat, 2), round(lon, 2), cc)
            book[key] = book.get(key, 0) + 1
        if len(book) > 4 * self.cfg.max_places:
            keep = sorted(book.items(), key=lambda kv: -kv[1])[: 2 * self.cfg.max_places]
            self.places[i] = dict(keep)
        self._top_cache[i] = None

    def _item_geo(
        self, places: Sequence[Sequence[Place]] | None, n: int
    ) -> tuple[np.ndarray, np.ndarray]:
        if places is None:
            return np.zeros(n, dtype=bool), np.full(n, -1, dtype=np.int64)
        has = np.fromiter((len(p) > 0 for p in places), dtype=bool, count=n)
        cc = np.fromiter(
            (self._code(p[0][2]) if p else -1 for p in places), dtype=np.int64, count=n
        )
        return has, cc

    def _story_geo(self) -> tuple[np.ndarray, np.ndarray]:
        n = len(self.ids)
        has = np.fromiter((len(p) > 0 for p in self.places), dtype=bool, count=n)
        cc = np.fromiter(
            (self._code(self._top(i)[0][2]) if self.places[i] else -1 for i in range(n)),
            dtype=np.int64,
            count=n,
        )
        return has, cc

    def _new_id(self, label: str, first_article: str) -> str:
        salt = 0
        while True:
            digest = hashlib.sha1(f"{first_article}|{salt}".encode()).hexdigest()[:6]
            story_id = f"{slug(label)}-{digest}"
            if story_id not in self.used_ids:
                self.used_ids.add(story_id)
                return story_id
            salt += 1

    # ---- one day ---------------------------------------------------------------------------

    def step(
        self,
        day: dt.date,
        ids: Sequence[str],
        vectors: np.ndarray,
        titles: Sequence[str] | None = None,
        places: Sequence[Sequence[Place]] | None = None,
    ) -> DayResult:
        """Process one day's items (unit-length `vectors`, one row per id). `places`: per item,
        (lat, lon, country) of each place it mentions; `titles` name new stories."""
        if self.day is not None and day <= self.day:
            raise ValueError(
                f"{day} is not after the last processed day {self.day}: days go in order, once"
            )
        n = len(ids)
        if (
            len(vectors) != n
            or (titles is not None and len(titles) != n)
            or (places is not None and len(places) != n)
        ):
            raise ValueError("ids, vectors, titles and places must have one entry per item")
        cfg = self.cfg
        t_start = time.perf_counter()

        # Sort by id, so the result doesn't depend on the order the items arrived in.
        order = np.argsort(np.asarray(ids, dtype=str), kind="stable")
        ids = [ids[i] for i in order]
        vectors = np.ascontiguousarray(vectors[order], dtype=np.float32)
        titles = [titles[i] for i in order] if titles is not None else None
        places = [places[i] for i in order] if places is not None else None

        # 1. fade
        elapsed = 1 if self.day is None else (day - self.day).days
        n_old = len(self.ids)
        if n_old:
            self.sums *= 0.5 ** (elapsed / cfg.half_life_days)

        # 2. match items to known stories
        assigned = np.full(n, -1, dtype=np.int64)
        similarity = np.zeros(n, dtype=np.float32)
        if n_old and n:
            sims, nearest = top_k(vectors, self._unit(), cfg.candidates)
            item_has, item_cc = self._item_geo(places, n)
            story_has, story_cc = self._story_geo()
            for j in range(sims.shape[1]):
                pending = np.flatnonzero((assigned < 0) & (sims[:, j] >= cfg.assign_sim))
                if len(pending) == 0:
                    break  # each row is sorted best first: nothing later can pass either
                candidate = nearest[pending, j]
                ok = np.ones(len(pending), dtype=bool)
                if places is not None:
                    needs_check = (
                        item_has[pending]
                        & story_has[candidate]
                        & ((item_cc[pending] != story_cc[candidate]) | (item_cc[pending] < 0))
                    )
                    for t in np.flatnonzero(needs_check):
                        ok[t] = not far_apart(
                            places[pending[t]], self._top(candidate[t]), cfg.geo_km
                        )
                taken = pending[ok]
                assigned[taken] = nearest[taken, j]
                similarity[taken] = sims[taken, j]

        t_matched = time.perf_counter()

        # 3. start new stories from the leftovers
        left = np.flatnonzero(assigned < 0)
        n_new = 0
        if len(left) >= cfg.min_size:
            labels = cluster_items(
                vectors[left],
                [places[i] for i in left] if places is not None else None,
                k=cfg.knn,
                min_sim=cfg.link_sim,
                min_size=cfg.min_size,
                max_component=cfg.max_component,
                geo_km=cfg.geo_km,
                resolution=cfg.resolution,
                min_gain=cfg.min_gain,
                seed=cfg.seed,
            )
            in_story = np.flatnonzero(labels >= 0)
            if len(in_story):
                by_label = in_story[np.argsort(labels[in_story], kind="stable")]
                sizes = np.bincount(labels[in_story])
                starts = np.r_[0, np.cumsum(sizes[sizes > 0])]
                members_all = left[by_label]
                sums = np.add.reduceat(vectors[members_all], starts[:-1], axis=0)
                n_new = len(starts) - 1
                new_sums, new_ids, new_labels, new_places = [], [], [], []
                for g in range(n_new):
                    members = members_all[starts[g] : starts[g + 1]]
                    unit = sums[g] / max(np.linalg.norm(sums[g]), 1e-12)
                    member_sims = vectors[members] @ unit
                    central = members[int(np.argmax(member_sims))]
                    label = titles[central] if titles is not None else ids[central]
                    new_ids.append(self._new_id(label, ids[members[0]]))
                    new_labels.append(label)
                    new_sums.append(sums[g])
                    book: dict[PlaceKey, int] = {}
                    new_places.append(book)
                    assigned[members] = n_old + g
                    similarity[members] = member_sims
                self.ids.extend(new_ids)
                self.labels.extend(new_labels)
                self.sums = np.vstack([self.sums, np.asarray(new_sums, dtype=np.float32)])
                member_counts = np.diff(starts)
                self.counts = np.r_[self.counts, member_counts]
                self.first_day = np.r_[self.first_day, np.full(n_new, day.toordinal())]
                self.last_day = np.r_[self.last_day, np.full(n_new, day.toordinal())]
                self.places.extend(new_places)
                self._top_cache.extend([None] * n_new)

        t_clustered = time.perf_counter()

        # 4. fold today's items into the stories they joined
        matched = np.flatnonzero(assigned >= 0)
        total = len(self.ids)
        if len(matched):
            story_of = assigned[matched]
            order = np.argsort(story_of, kind="stable")
            story_sorted = story_of[order]
            touched, starts = np.unique(story_sorted, return_index=True)
            added = np.add.reduceat(vectors[matched][order], starts, axis=0)
            is_old = touched < n_old  # new stories already hold their members' sum
            self.sums[touched[is_old]] += added[is_old]
            self.counts[touched[is_old]] += np.diff(np.r_[starts, len(story_sorted)])[is_old]
            self.last_day[touched] = day.toordinal()
            if places is not None:
                for item in matched:
                    if places[item]:
                        self._add_places(int(assigned[item]), places[item])
        today = np.bincount(assigned[matched], minlength=total)

        # 5. merge stories that have become the same
        merged_into: dict[int, int] = {}
        lineage: list[tuple[str, str, dt.date]] = []
        dead = np.zeros(total, dtype=bool)
        if cfg.merge_sim is not None and total > 1 and len(matched):
            self._merge(day, today, dead, merged_into, lineage)

        t_merged = time.perf_counter()

        # 6. retire the idle
        idle = (day.toordinal() - self.last_day) > cfg.idle_days
        retired = int((idle & ~dead).sum())
        dead |= idle

        # 7. what to write
        remap = np.arange(total)
        for old, keep in merged_into.items():
            remap[old] = keep
        story_ids = np.asarray(self.ids, dtype=object)
        assignments = pd.DataFrame(
            {
                "article_id": [ids[i] for i in matched],
                "story_id": story_ids[remap[assigned[matched]]],
                "day": day,
                "similarity": np.clip(similarity[matched], -1.0, 1.0),
            }
        )
        active_today = np.flatnonzero((today > 0) & ~dead)
        categories: list[str] = ["other"] * len(active_today)
        if self.categorize is not None and len(active_today):
            categories = list(self.categorize(self._unit()[active_today])[0])
        stories = pd.DataFrame(
            {
                "story_id": story_ids[active_today],
                "day": day,
                "n_articles_day": today[active_today],
                "first_seen": [dt.date.fromordinal(int(d)) for d in self.first_day[active_today]],
                "label": [self.labels[i] for i in active_today],
                "category": categories,
            }
        )
        lineage_df = pd.DataFrame(lineage, columns=["old_story_id", "new_story_id", "day"])
        stats = {
            "items": n,
            "joined_existing": int(((assigned >= 0) & (assigned < n_old)).sum()),
            "in_new_stories": int((assigned >= n_old).sum()),
            "no_story": int((assigned < 0).sum()),
            "new_stories": n_new,
            "merged": len(lineage),
            "retired": retired,
            # where the time went, in seconds (not saved anywhere): matching to known stories,
            # clustering the leftovers, folding in and merging
            "t_match": round(t_matched - t_start, 1),
            "t_cluster": round(t_clustered - t_matched, 1),
            "t_merge": round(t_merged - t_clustered, 1),
        }

        # 8. forget the dead
        keep = np.flatnonzero(~dead)
        self.ids = [self.ids[i] for i in keep]
        self.labels = [self.labels[i] for i in keep]
        self.places = [self.places[i] for i in keep]
        self._top_cache = [self._top_cache[i] for i in keep]
        self.sums, self.counts = self.sums[keep], self.counts[keep]
        self.first_day, self.last_day = self.first_day[keep], self.last_day[keep]
        self.day = day
        stats["active"] = len(self.ids)
        return DayResult(day, assignments, stories, lineage_df, stats)

    def _merge(
        self,
        day: dt.date,
        today: np.ndarray,
        dead: np.ndarray,
        merged_into: dict[int, int],
        lineage: list[tuple[str, str, dt.date]],
    ) -> None:
        """Merge stories whose centroids have become nearly the same. Only stories that changed
        today can have crossed the line, so only they are looked up."""
        cfg = self.cfg
        total = len(self.ids)
        centroids = self._unit()
        queries = np.flatnonzero(today > 0)
        sims, nearest = top_k(centroids[queries], centroids, 5)
        parent = list(range(total))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for row, a in enumerate(queries):
            for sim, b in zip(sims[row], nearest[row], strict=True):
                if b == a or sim < cfg.merge_sim:
                    continue
                if (
                    self.places[a]
                    and self.places[b]
                    and far_apart(self._top(a), self._top(int(b)), cfg.geo_km)
                ):
                    continue
                parent[find(int(b))] = find(int(a))
        groups: dict[int, list[int]] = defaultdict(list)
        for i in range(total):
            groups[find(i)].append(i)
        for members in groups.values():
            if len(members) < 2:
                continue
            keep = min(members, key=lambda i: (self.first_day[i], -self.counts[i], self.ids[i]))
            for other in members:
                if other == keep:
                    continue
                self.sums[keep] += self.sums[other]
                self.counts[keep] += self.counts[other]
                self.first_day[keep] = min(self.first_day[keep], self.first_day[other])
                self.last_day[keep] = max(self.last_day[keep], self.last_day[other])
                for key, count in self.places[other].items():
                    self.places[keep][key] = self.places[keep].get(key, 0) + count
                self._top_cache[keep] = None
                today[keep] += today[other]
                today[other] = 0
                dead[other] = True
                merged_into[other] = keep
                lineage.append((self.ids[other], self.ids[keep], day))

    # ---- for the live matcher ---------------------------------------------------------------

    def nearest(
        self, vectors: np.ndarray, min_sim: float | None = None
    ) -> list[tuple[str, float] | None]:
        """The closest active story for each vector, or None if none is above min_sim (default:
        assign_sim). For matching a live post to a story."""
        if not len(self.ids):
            return [None] * len(vectors)
        sims, nearest = top_k(vectors, self._unit(), 1)
        floor = self.cfg.assign_sim if min_sim is None else min_sim
        return [
            (self.ids[int(i)], float(s)) if s >= floor else None
            for s, i in zip(sims[:, 0], nearest[:, 0], strict=True)
        ]

    # ---- saving and resuming ----------------------------------------------------------------

    def save(self, path: Path) -> None:
        state = {
            "version": STATE_VERSION,
            "cfg": self.cfg,
            "dim": self.dim,
            "day": self.day,
            "ids": self.ids,
            "sums": self.sums,
            "counts": self.counts,
            "first_day": self.first_day,
            "last_day": self.last_day,
            "labels": self.labels,
            "places": self.places,
            "used_ids": self.used_ids,
            "codes": self._codes,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
        try:
            with open(tmp, "wb") as f:
                pickle.dump(state, f, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)

    @classmethod
    def load(cls, path: Path, *, categorize: Categorizer | None = None) -> "Tracker":
        """Resume from a saved state. Only load files this program wrote: it is a pickle."""
        with open(path, "rb") as f:
            state = pickle.load(f)  # noqa: S301
        if state.get("version") != STATE_VERSION:
            raise ValueError(
                f"{path} was saved by another version of the tracker; start a fresh run"
            )
        tracker = cls(state["dim"], state["cfg"], categorize=categorize)
        tracker.day = state["day"]
        tracker.ids, tracker.sums, tracker.counts = state["ids"], state["sums"], state["counts"]
        tracker.first_day, tracker.last_day = state["first_day"], state["last_day"]
        tracker.labels, tracker.places = state["labels"], state["places"]
        tracker._top_cache = [None] * len(tracker.ids)
        tracker.used_ids, tracker._codes = state["used_ids"], state["codes"]
        return tracker
