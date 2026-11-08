"""Wikipedia data for every name in wiki_names.parquet, split across several machines.

Wikimedia allows about 200 requests a minute per client, and anonymous clients are told apart
by IP address. So n machines on n different networks (a home connection, a phone hotspot, a
cloud VM) fetch about n times as fast. n machines behind the SAME router share one budget and
gain nothing: use one machine there, or set UC_WIKI_REQUESTS_PER_MINUTE=190/n on each.

    # once, on one machine (see wiki_names.py), then copy data/processed/wiki_names.parquet
    # to the same path on every machine
    uv run python -m undercurrent.ingest.wiki_names --bucket gs://undercurrent-data-cooper

    # on machine i of n (i = 0..n-1); resumable, so just run it again if it stops
    uv run python -m undercurrent.ingest.wiki_bulk fetch --shard 0/4
    #   -> data/wiki_shard-0-of-4.tar.gz   send it to whoever merges

    # on the merging machine, with every machine's tarball
    uv run python -m undercurrent.ingest.wiki_bulk merge wiki_shard-*-of-4.tar.gz
    #   -> data/processed/wiki_{pageviews,edits,entity_daily,top,selection}.parquet

What `fetch --shard i/n` does, all through the cached, rate-limited code in wiki*.py:
1. Top-1000 lists, 8 languages, for the days where day.toordinal() % n == i (the same rule as
   vectors.store --shard). Needs no names. Skip with --no-top.
2. Its share of the names (sha1(name) % n == i, so every machine agrees without talking):
   resolve them to English titles, and link every title to all 8 languages through Wikidata.
3. Tier 1: daily pageviews in all 8 languages, then English edits (--no-edits skips them).
4. Tier 2: daily English pageviews only.
5. Rebuild this machine's tables and pack the caches into data/wiki_shard-i-of-n.tar.gz.
One pageviews request covers the whole date range, so most of the time goes on names, not days.
The default range is fixed, 2021-10-10 (section 10's warm-up, docs/DATA_COVERAGE.md) to
2026-11-06 (the last GDELT day), not "to yesterday". An article's cache is reused only if it
covers the whole requested range up to a day that's already complete, so a moving end date would
refetch every cached article on every run, and so would moving the start earlier. Keep the same
--start/--end for the whole run so each re-run fetches only the new names.

`merge` unpacks each tarball, adds its articles and caches to this machine's data/, then runs
`build`. `build` (also its own command) rebuilds every table from the caches with no network,
and writes wiki_selection.parquet: name, kind, tier, first_week, mentions, title_en, qid.
"""

import argparse
import datetime as dt
import hashlib
import json
import shutil
import tarfile
import tempfile
from collections.abc import Iterable
from pathlib import Path

import pandas as pd
from tqdm import tqdm

from undercurrent.config import DATA
from undercurrent.ingest import wiki, wiki_resolve, wiki_top
from undercurrent.ingest.wiki_client import (
    LANGUAGES,
    WikiClient,
    add_lang_arg,
    days_between,
)
from undercurrent.ingest.wiki_names import OUT as NAMES
from undercurrent.ingest.wiki_names import entities

PLAN_START = dt.date(2021, 10, 10)  # docs/DATA_COVERAGE.md: section 10's warm-up (five years)
PLAN_END = dt.date(2026, 11, 6)  # the last GDELT day; fixed so re-runs reuse the cache

# What a shard ships, relative to data/: per-article caches, top-1000 lists and the lookups.
PACKED = (
    "raw/wiki/pageviews",
    "raw/wiki/edits",
    "raw/wiki/top",
    "cache/wiki_siteinfo",
    "cache/wiki_resolve.json",
    "cache/wiki_links.json",
)


def parse_shard(s: str) -> tuple[int, int]:
    try:
        i, n = (int(x) for x in s.split("/"))
    except ValueError:
        raise SystemExit("--shard must look like i/n, e.g. 0/4") from None
    if not 0 <= i < n:
        raise SystemExit("--shard must look like i/n with 0 <= i < n")
    return i, n


def in_shard(name: str, i: int, n: int) -> bool:
    """Stable across machines and Python runs (unlike hash())."""
    return int(hashlib.sha1(name.encode()).hexdigest()[:8], 16) % n == i


def shard_entities(names: pd.DataFrame, i: int, n: int) -> pd.DataFrame:
    ents = entities(names)
    return ents[[in_shard(x, i, n) for x in ents["name"]]].reset_index(drop=True)


def resolved_titles(ents: pd.DataFrame) -> tuple[list[str], list[str]]:
    """Resolve names -> (tier-1 English titles, tier-2 titles not already in tier 1)."""
    titles = wiki_resolve.resolve_many(list(ents["name"]))
    t1: dict[str, None] = {}
    t2: dict[str, None] = {}
    for name, tier in zip(ents["name"], ents["tier"], strict=True):
        title = titles.get(name)
        if title:
            (t1 if tier == 1 else t2)[title] = None
    return list(t1), [t for t in t2 if t not in t1]


def fetch_top(i: int, n: int, start: dt.date, end: dt.date, langs: Iterable[str]) -> None:
    days = [d for d in days_between(start, end) if d.toordinal() % n == i][::-1]  # newest first
    jobs = [(lang, access, d) for d in days for lang in langs for access in wiki_top.ACCESS]
    client = WikiClient()
    statuses: dict[str, int] = {}
    for lang, access, d in tqdm(jobs, desc="top-1000"):
        try:
            s = wiki_top.download(client, lang, access, d)
        except RuntimeError as e:
            print(f"  failed top {lang} {access} {d}: {e}")
            s = "failed"
        statuses[s] = statuses.get(s, 0) + 1
    client.close()
    print(f"top-1000, {len(days)} days: {statuses}")


def pack(i: int, n: int, data: Path = DATA) -> Path:
    out = data / f"wiki_shard-{i}-of-{n}.tar.gz"
    tmp = out.with_suffix(".tmp")
    with tarfile.open(tmp, "w:gz") as tar:
        for rel in PACKED:
            if (data / rel).exists():
                tar.add(data / rel, arcname=rel)
    tmp.replace(out)
    print(f"packed {out} ({out.stat().st_size / 1e6:.1f} MB)")
    return out


# merge -----------------------------------------------------------------------------------------


def _better_record(a: dict, b: dict) -> bool:
    """Is cached article b better than a? More complete days, then a wider range."""
    return (b["complete_to"], b["end"], _neg(b["start"])) > (
        a["complete_to"],
        a["end"],
        _neg(a["start"]),
    )


def _neg(day: str) -> int:
    return -dt.date.fromisoformat(day).toordinal()


def merge_resolve(dst: dict, src: dict) -> int:
    """name -> title. A real title beats null (a miss on one machine, a hit on another)."""
    added = 0
    for k, v in src.items():
        if k not in dst or (dst[k] is None and v is not None):
            dst[k] = v
            added += 1
    return added


def merge_links(dst: dict, src: dict) -> int:
    """English title -> qid and titles. Keep the entry checked in more languages."""
    added = 0
    for k, v in src.items():
        if k not in dst or len(v.get("checked", [])) > len(dst[k].get("checked", [])):
            dst[k] = v
            added += 1
    return added


def _merge_json(dst: Path, src: Path, how) -> int:
    if not src.exists():
        return 0
    have = json.loads(dst.read_text(encoding="utf-8")) if dst.exists() else {}
    added = how(have, json.loads(src.read_text(encoding="utf-8")))
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".tmp")
    tmp.write_text(json.dumps(have, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
    tmp.replace(dst)
    return added


def merge_tree(src: Path, data: Path = DATA) -> dict[str, int]:
    """Add one unpacked shard (a folder laid out like data/) into data/."""
    stats = {"articles": 0, "top": 0}
    for kind in wiki.KINDS:
        for f in (src / "raw/wiki" / kind).glob("*.json"):
            dst = data / "raw/wiki" / kind / f.name
            new = json.loads(f.read_text(encoding="utf-8"))
            if dst.exists() and not _better_record(
                json.loads(dst.read_text(encoding="utf-8")), new
            ):
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, dst)
            stats["articles"] += 1
    for sub, key in (("raw/wiki/top", "top"), ("cache/wiki_siteinfo", "")):
        for f in (src / sub).rglob("*.json"):
            dst = data / sub / f.relative_to(src / sub)
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(f, dst)
                if key:
                    stats[key] += 1
    stats["names"] = _merge_json(
        data / "cache/wiki_resolve.json", src / "cache/wiki_resolve.json", merge_resolve
    )
    stats["links"] = _merge_json(
        data / "cache/wiki_links.json", src / "cache/wiki_links.json", merge_links
    )
    return stats


def merge(tarballs: list[Path], data: Path = DATA) -> None:
    for tb in tarballs:
        with tempfile.TemporaryDirectory() as tmp, tarfile.open(tb) as tar:
            tar.extractall(tmp, filter="data")
            print(f"{tb.name}: added {merge_tree(Path(tmp), data)}")
    # the resolver keeps its caches in memory: make it reread the merged files
    wiki_resolve._cache = None
    wiki_resolve._links = None


# build -----------------------------------------------------------------------------------------


def selection_table(names: pd.DataFrame, resolve: dict, links: dict) -> pd.DataFrame:
    ents = entities(names)
    title = ents["name"].map(lambda x: resolve.get(wiki_resolve.normalise_name(x)))
    qid = title.map(lambda t: (links.get(t) or {}).get("qid") if t else None)
    return ents.assign(title_en=title, qid=qid)


def build(langs: list[str] | tuple[str, ...] = LANGUAGES, names_path: Path = NAMES) -> None:
    """Every table from the caches. Needs no network once the caches are merged."""
    wiki.build(langs)
    client = WikiClient()
    top = wiki_top.build_table(client)
    client.close()
    print(f"wiki_top: {len(top):,} rows")
    if names_path.exists():
        resolve = wiki_resolve._load()
        links = wiki_resolve._load_links()
        sel = selection_table(pd.read_parquet(names_path), resolve, links)
        out = wiki.PROCESSED / "wiki_selection.parquet"
        sel.to_parquet(out, index=False)
        print(f"{out.name}: {len(sel):,} names, {sel['title_en'].notna().sum():,} with an article")
    else:
        print(f"no {names_path}: wiki_selection.parquet not written")


def fetch_shard(
    names: pd.DataFrame | None,
    i: int,
    n: int,
    start: dt.date,
    end: dt.date,
    langs: list[str] | tuple[str, ...] = LANGUAGES,
    *,
    top: bool = True,
    edits: bool = True,
) -> None:
    """Machine i of n: top-1000 lists for its days, then its names' pageviews (and edits)."""
    if top:
        fetch_top(i, n, start, end, langs)
    if names is None:
        return
    ents = shard_entities(names, i, n)
    print(
        f"shard {i}/{n}: {len(ents):,} names ({(ents['tier'] == 1).sum():,} tier 1), {start}..{end}"
    )
    t1, t2 = resolved_titles(ents)
    print(f"resolved: {len(t1):,} tier-1 and {len(t2):,} tier-2 articles")
    wiki.download(t1, start, end, ["pageviews"], langs)
    if edits:
        wiki.download(t1, start, end, ["edits"], ["en"])
    wiki.download(t2, start, end, ["pageviews"], ["en"], link_langs=langs)
    wiki.build(langs)


# CLI -------------------------------------------------------------------------------------------


def cmd_fetch(args: argparse.Namespace) -> None:
    i, n = parse_shard(args.shard)
    if not NAMES.exists() and not args.top_only:
        raise SystemExit(f"{NAMES} is missing: run wiki_names first, or copy its output here")
    names = None if args.top_only else pd.read_parquet(NAMES)
    fetch_shard(
        names, i, n, args.start, args.end, args.langs, top=not args.no_top, edits=not args.no_edits
    )
    pack(i, n)
    print("Send that file to whoever runs `wiki_bulk merge`.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="fetch this machine's share, then pack it")
    f.add_argument("--shard", default="0/1", help="i/n: this is machine i of n (default 0/1)")
    f.add_argument(
        "--start", type=dt.date.fromisoformat, default=PLAN_START, help=f"default {PLAN_START}"
    )
    f.add_argument(
        "--end",
        type=dt.date.fromisoformat,
        default=PLAN_END,
        help=f"default {PLAN_END}; keep it fixed for the whole run, or every re-run refetches",
    )
    f.add_argument("--no-top", action="store_true", help="skip the top-1000 lists")
    f.add_argument("--top-only", action="store_true", help="only the top-1000 lists")
    f.add_argument("--no-edits", action="store_true", help="skip tier-1 English edits")
    add_lang_arg(f)

    m = sub.add_parser("merge", help="add shard tarballs to this machine's data/, then build")
    m.add_argument("tarballs", nargs="+", type=Path)
    add_lang_arg(m)

    b = sub.add_parser("build", help="rebuild every Wikipedia table from the caches (no network)")
    add_lang_arg(b)
    pk = sub.add_parser("pack", help="pack this machine's caches without fetching")
    pk.add_argument("--shard", default="0/1")

    args = p.parse_args()
    if args.cmd == "fetch":
        cmd_fetch(args)
    elif args.cmd in ("merge", "build"):
        if args.cmd == "merge":
            merge(args.tarballs)
        build(args.langs)
    elif args.cmd == "pack":
        pack(*parse_shard(args.shard))


if __name__ == "__main__":
    main()
