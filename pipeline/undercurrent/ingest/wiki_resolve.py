"""Entity name -> English Wikipedia title, via the search API, cached on disk.

GDELT names are lowercase and messy ("barack obama", "european union"). For each name:
1. try exact titles ("Barack Obama", "UN", ...) following redirects; if the match is a
   disambiguation page the name is ambiguous ("john smith") and resolves to null;
2. otherwise full-text search; the first non-disambiguation hit that plausibly matches wins.
Misses are cached as null so they aren't re-queried (use --retry-misses to try again).

    from undercurrent.ingest.wiki_resolve import resolve, resolve_many
    resolve("barack obama")  # -> "Barack_Obama"

    uv run python -m undercurrent.ingest.wiki_resolve --names-file names.txt \
        --out-titles data/processed/wiki_titles.txt

Cache: data/cache/wiki_resolve.json  {"<normalised name>": "<Title>" | null}
"""

import argparse
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path

from tqdm import tqdm

from undercurrent.config import CACHE
from undercurrent.ingest.wiki_client import ACTION, WikiClient, normalise_title

CACHE_FILE = CACHE / "wiki_resolve.json"

_lock = threading.Lock()
_cache: dict[str, str | None] | None = None


def normalise_name(name: str) -> str:
    return " ".join(name.lower().split())


def _tokens(s: str) -> list[str]:
    s = re.sub(r"\([^)]*\)", " ", s.lower().replace("_", " "))  # drop "(politician)" etc.
    return re.findall(r"\w+", s)


def plausible(name: str, title: str) -> bool:
    """Does the search hit actually look like the name? Guards against search noise."""
    n, t = _tokens(name), _tokens(title)
    if not n or not t:
        return False
    # every name token appears (allowing small spelling differences) in the title
    if all(any(SequenceMatcher(None, a, b).ratio() >= 0.8 for b in t) for a in n):
        return True
    return SequenceMatcher(None, " ".join(n), " ".join(t)).ratio() >= 0.75


def _load() -> dict[str, str | None]:
    global _cache
    if _cache is None:
        _cache = json.loads(CACHE_FILE.read_text()) if CACHE_FILE.exists() else {}
    return _cache


def _save() -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        data = json.dumps(_load(), ensure_ascii=False, indent=0, sort_keys=True)
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(data)
    tmp.replace(CACHE_FILE)


def title_variants(name: str) -> list[str]:
    """Exact-title guesses, in priority order: 'Title Case', 'As given', 'ACRONYM' (if short)."""
    variants = [" ".join(w[:1].upper() + w[1:] for w in name.split()), name[:1].upper() + name[1:]]
    if len(name) <= 5 and name.replace(".", "").isalpha():
        variants.append(name.upper())
    return list(dict.fromkeys(variants))


_AMBIGUOUS = "<ambiguous>"


def _exact(client: WikiClient, name: str) -> str | None:
    """Existing non-disambiguation article for one of the title variants (following redirects).

    Returns _AMBIGUOUS if the best variant is a disambiguation page, None if nothing exists.
    """
    variants = title_variants(name)
    payload = client.get_json(
        ACTION,
        params={
            "action": "query",
            "format": "json",
            "formatversion": 2,
            "titles": "|".join(variants),
            "redirects": 1,
            "prop": "pageprops",
            "ppprop": "disambiguation",
        },
    )
    query = (payload or {}).get("query") or {}
    hops = {n["from"]: n["to"] for n in query.get("normalized", [])}
    hops |= {r["from"]: r["to"] for r in query.get("redirects", [])}
    pages = {p["title"]: p for p in query.get("pages", [])}
    for v in variants:
        title = v
        for _ in range(3):  # normalised -> redirect -> (rarely) another hop
            title = hops.get(title, title)
        page = pages.get(title)
        if page is None or page.get("missing") or page.get("invalid"):
            continue
        if "disambiguation" in (page.get("pageprops") or {}):
            return _AMBIGUOUS
        return normalise_title(page["title"])
    return None


def _lookup(client: WikiClient, name: str) -> str | None:
    exact = _exact(client, name)
    if exact == _AMBIGUOUS:
        return None
    return exact or _search(client, name)


def _search(client: WikiClient, name: str) -> str | None:
    payload = client.get_json(
        ACTION,
        params={
            "action": "query",
            "format": "json",
            "formatversion": 2,
            "generator": "search",
            "gsrsearch": name,
            "gsrnamespace": 0,
            "gsrlimit": 5,
            "prop": "pageprops",
            "ppprop": "disambiguation",
            "redirects": 1,
        },
    )
    pages = ((payload or {}).get("query") or {}).get("pages", [])
    for page in sorted(pages, key=lambda p: p.get("index", 99)):
        if "disambiguation" in (page.get("pageprops") or {}):
            continue
        if plausible(name, page["title"]):
            return normalise_title(page["title"])
    return None


def resolve_many(
    names: list[str], *, retry_misses: bool = False, workers: int = 4, progress: bool = True
) -> dict[str, str | None]:
    """Resolve names (any case/spacing). Keys of the result are the names as given.

    Names that fail because of network errors are left out of the cache and map to None.
    """
    cache = _load()
    wanted = {normalise_name(n) for n in names if n and n.strip()}
    todo = sorted(k for k in wanted if k not in cache or (retry_misses and cache[k] is None))
    if todo:
        client = WikiClient(max_rps=8)

        def work(key: str) -> None:
            try:
                title = _lookup(client, key)
            except RuntimeError as e:
                print(f"  failed: {key!r}: {e}")
                return
            with _lock:
                cache[key] = title

        with ThreadPoolExecutor(workers) as pool:
            done = pool.map(work, todo)
            for i, _ in enumerate(tqdm(done, total=len(todo), disable=not progress), 1):
                if i % 200 == 0:
                    _save()
        client.close()
        _save()
    return {n: cache.get(normalise_name(n)) for n in names}


def resolve(name: str) -> str | None:
    return resolve_many([name], progress=False)[name]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--names", nargs="*", default=[], help="names to resolve")
    p.add_argument("--names-file", type=Path, help="one name per line")
    p.add_argument("--retry-misses", action="store_true", help="re-query names cached as null")
    p.add_argument("--out-titles", type=Path, help="write the resolved titles here, one per line")
    args = p.parse_args()

    names = list(args.names)
    if args.names_file:
        names += [ln.strip() for ln in args.names_file.read_text().splitlines() if ln.strip()]
    if not names:
        p.error("give --names or --names-file")

    result = resolve_many(names, retry_misses=args.retry_misses)
    hits = sorted({t for t in result.values() if t})
    print(f"{len(hits)} titles from {len(set(map(normalise_name, names)))} names")
    for name in names[:20]:
        print(f"  {name!r:40} -> {result[name]}")
    if args.out_titles:
        args.out_titles.parent.mkdir(parents=True, exist_ok=True)
        args.out_titles.write_text("".join(f"{t}\n" for t in hits))
        print(f"wrote {args.out_titles}")


if __name__ == "__main__":
    main()
