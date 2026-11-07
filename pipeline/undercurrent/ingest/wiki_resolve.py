"""Entity name -> Wikipedia article, in English and in each other language, cached on disk.

Step 1, name -> English title.

GDELT names are lowercase and messy ("barack obama", "european union"). For each name:
1. try exact titles ("Barack Obama", "UN", ...) following redirects; if the match is a
   disambiguation page the name is ambiguous ("john smith") and resolves to null;
2. otherwise full-text search; the first non-disambiguation hit that plausibly matches wins.
Misses are cached as null so they aren't re-queried (use --retry-misses to try again).

Step 2, English title -> Wikidata id -> the article's title in each language (Wikidata
"sitelinks"). Batched 50 titles per request.

    from undercurrent.ingest.wiki_resolve import resolve, resolve_many, resolve_entities
    resolve("barack obama")             # -> "Barack_Obama"
    resolve_entities(["barack obama"])  # -> {"barack obama": {"qid": "Q76",
                                        #      "title": "Barack_Obama",
                                        #      "titles": {"en": "Barack_Obama", "ja": "バラク・オバマ", ...}}}

    uv run python -m undercurrent.ingest.wiki_resolve --names-file names.txt \
        --out-titles data/processed/wiki_titles.txt

Caches: data/cache/wiki_resolve.json  {"<normalised name>": "<English title>" | null}
        data/cache/wiki_links.json    {"<English title>": {"qid", "titles": {lang: title}, "checked"}}
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
from undercurrent.ingest.wiki_client import (
    ACTION,
    LANGUAGES,
    WIKIDATA,
    WikiClient,
    add_lang_arg,
    normalise_title,
)

CACHE_FILE = CACHE / "wiki_resolve.json"
LINKS_FILE = CACHE / "wiki_links.json"
BATCH = 50  # max titles / ids per Action API or Wikidata request

_lock = threading.Lock()
_cache: dict[str, str | None] | None = None
_links: dict[str, dict] | None = None


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
        _cache = json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}
    return _cache


def _save() -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        data = json.dumps(_load(), ensure_ascii=False, indent=0, sort_keys=True)
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(CACHE_FILE)


def title_variants(name: str) -> list[str]:
    """Exact-title guesses, in priority order: 'Title Case', 'As given', 'ACRONYM' (if short)."""
    variants = [" ".join(w[:1].upper() + w[1:] for w in name.split()), name[:1].upper() + name[1:]]
    if len(name) <= 5 and name.replace(".", "").isalpha():
        variants.append(name.upper())
    return list(dict.fromkeys(variants))


_AMBIGUOUS = "<ambiguous>"


def _exact_many(client: WikiClient, names: list[str]) -> dict[str, str | None]:
    """Exact-title lookup for many names in one request (all their variants <= 50 titles).

    Per name: its article title (following redirects), _AMBIGUOUS if the best variant is a
    disambiguation page, or None if no variant exists.
    """
    variants = {n: title_variants(n) for n in names}
    payload = client.get_json(
        ACTION,
        params={
            "action": "query",
            "format": "json",
            "formatversion": 2,
            "titles": "|".join(dict.fromkeys(v for vs in variants.values() for v in vs)),
            "redirects": 1,
            "prop": "pageprops",
            "ppprop": "disambiguation",
        },
    )
    query = (payload or {}).get("query") or {}
    hops = {n["from"]: n["to"] for n in query.get("normalized", [])}
    hops |= {r["from"]: r["to"] for r in query.get("redirects", [])}
    pages = {p["title"]: p for p in query.get("pages", [])}
    out: dict[str, str | None] = {}
    for name, vs in variants.items():
        out[name] = None
        for v in vs:
            title = v
            for _ in range(3):  # normalised -> redirect -> (rarely) another hop
                title = hops.get(title, title)
            page = pages.get(title)
            if page is None or page.get("missing") or page.get("invalid"):
                continue
            if "disambiguation" in (page.get("pageprops") or {}):
                out[name] = _AMBIGUOUS
            else:
                out[name] = normalise_title(page["title"])
            break
    return out


def _chunks_by_variants(names: list[str], limit: int = BATCH) -> list[list[str]]:
    """Split names so each chunk has at most `limit` title variants (one API request each)."""
    chunks: list[list[str]] = [[]]
    size = 0
    for n in names:
        k = len(title_variants(n))
        if size + k > limit and chunks[-1]:
            chunks.append([])
            size = 0
        chunks[-1].append(n)
        size += k
    return [c for c in chunks if c]


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
    names: list[str], *, retry_misses: bool = False, workers: int = 3, progress: bool = True
) -> dict[str, str | None]:
    """Resolve names (any case/spacing). Keys of the result are the names as given.

    Exact-title lookups are batched (~16 names per request); only names with no exact match
    need a search request each. Names that fail because of network errors are left out of the
    cache and map to None.
    """
    cache = _load()
    wanted = {normalise_name(n) for n in names if n and n.strip()}
    todo = sorted(k for k in wanted if k not in cache or (retry_misses and cache[k] is None))
    if todo:
        client = WikiClient()
        need_search: list[str] = []
        chunks = _chunks_by_variants(todo)
        for chunk in tqdm(chunks, desc="exact titles", disable=not progress):
            try:
                found = _exact_many(client, chunk)
            except RuntimeError as e:
                print(f"  failed exact lookup for {len(chunk)} names: {e}")
                continue
            for name, title in found.items():
                if title is None:
                    need_search.append(name)
                else:
                    cache[name] = None if title == _AMBIGUOUS else title
            _save()

        def work(key: str) -> None:
            try:
                title = _search(client, key)
            except RuntimeError as e:
                print(f"  failed: {key!r}: {e}")
                return
            with _lock:
                cache[key] = title

        with ThreadPoolExecutor(workers) as pool:
            done = pool.map(work, need_search)
            for i, _ in enumerate(
                tqdm(done, total=len(need_search), desc="search", disable=not progress), 1
            ):
                if i % 100 == 0:
                    _save()
        client.close()
        _save()
    return {n: cache.get(normalise_name(n)) for n in names}


def resolve(name: str) -> str | None:
    return resolve_many([name], progress=False)[name]


# Step 2: English title -> Wikidata id -> titles in other languages ---------------------------


def _load_links() -> dict[str, dict]:
    global _links
    if _links is None:
        _links = json.loads(LINKS_FILE.read_text(encoding="utf-8")) if LINKS_FILE.exists() else {}
    return _links


def _save_links() -> None:
    LINKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = LINKS_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(_load_links(), ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8"
    )
    tmp.replace(LINKS_FILE)


def _qids(client: WikiClient, titles: list[str]) -> dict[str, str | None]:
    """English titles (<= 50) -> Wikidata ids, following redirects. None if there's no item."""
    payload = client.get_json(
        ACTION,
        params={
            "action": "query",
            "format": "json",
            "formatversion": 2,
            "titles": "|".join(t.replace("_", " ") for t in titles),
            "redirects": 1,
            "prop": "pageprops",
            "ppprop": "wikibase_item",
        },
    )
    query = (payload or {}).get("query") or {}
    hops = {n["from"]: n["to"] for n in query.get("normalized", [])}
    hops |= {r["from"]: r["to"] for r in query.get("redirects", [])}
    pages = {p["title"]: p for p in query.get("pages", [])}
    out: dict[str, str | None] = {}
    for t in titles:
        title = t.replace("_", " ")
        for _ in range(3):
            title = hops.get(title, title)
        out[t] = ((pages.get(title) or {}).get("pageprops") or {}).get("wikibase_item")
    return out


def _sitelinks(client: WikiClient, qids: list[str], langs: list[str]) -> dict[str, dict[str, str]]:
    """Wikidata ids (<= 50) -> {lang: title} for the languages that have an article."""
    payload = client.get_json(
        WIKIDATA,
        params={
            "action": "wbgetentities",
            "format": "json",
            "formatversion": 2,
            "ids": "|".join(qids),
            "props": "sitelinks",
            "sitefilter": "|".join(f"{lang}wiki" for lang in langs),
        },
    )
    entities = (payload or {}).get("entities") or {}
    out: dict[str, dict[str, str]] = {}
    for qid in qids:
        links = (entities.get(qid) or {}).get("sitelinks") or {}
        out[qid] = {
            lang: normalise_title(links[f"{lang}wiki"]["title"])
            for lang in langs
            if f"{lang}wiki" in links
        }
    return out


def link_languages(
    en_titles: list[str], langs: list[str] | tuple[str, ...] = LANGUAGES
) -> dict[str, dict]:
    """English titles -> {"qid": "Q76" | None, "titles": {lang: title}}.

    "titles" always contains "en" (the English title itself if Wikidata has no item for it).
    Cached; a cached entry is reused only if it was looked up for all the requested languages.
    Titles that fail because of network errors are left out of the cache and of the result.
    """
    links = _load_links()
    langs = list(dict.fromkeys(["en", *langs]))
    wanted = sorted({normalise_title(t) for t in en_titles if t and t.strip()})
    todo = [t for t in wanted if t not in links or not set(langs) <= set(links[t]["checked"])]
    if todo:
        client = WikiClient()
        for i in range(0, len(todo), BATCH):
            batch = todo[i : i + BATCH]
            try:
                qids = _qids(client, batch)
                found = sorted({q for q in qids.values() if q})
                titles = _sitelinks(client, found, langs) if found else {}
            except RuntimeError as e:
                print(f"  failed linking {len(batch)} titles: {e}")
                continue
            for t in batch:
                q = qids[t]
                links[t] = {
                    "qid": q,
                    "titles": {"en": t, **(titles.get(q, {}) if q else {})},
                    "checked": langs,
                }
            _save_links()
        client.close()
    return {
        t: {
            "qid": links[t]["qid"],
            "titles": {k: v for k, v in links[t]["titles"].items() if k in langs},
        }
        for t in wanted
        if t in links
    }


def resolve_entities(
    names: list[str], langs: list[str] | tuple[str, ...] = LANGUAGES, *, progress: bool = True
) -> dict[str, dict | None]:
    """Names -> {"qid", "title" (English), "titles" {lang: title}}, or None if unresolved."""
    en = resolve_many(names, progress=progress)
    linked = link_languages([t for t in en.values() if t], langs)
    out: dict[str, dict | None] = {}
    for name, title in en.items():
        entry = linked.get(title) if title else None
        out[name] = {"title": title, **entry} if entry else None
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--names", nargs="*", default=[], help="names to resolve")
    p.add_argument("--names-file", type=Path, help="one name per line")
    p.add_argument("--retry-misses", action="store_true", help="re-query names cached as null")
    p.add_argument(
        "--out-titles", type=Path, help="write the resolved English titles here, one per line"
    )
    add_lang_arg(p)
    args = p.parse_args()

    names = list(args.names)
    if args.names_file:
        names += [
            ln.strip()
            for ln in args.names_file.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
    if not names:
        p.error("give --names or --names-file")

    resolve_many(names, retry_misses=args.retry_misses)
    result = resolve_entities(names, args.langs)
    hits = sorted({e["title"] for e in result.values() if e})
    print(f"{len(hits)} entities from {len(set(map(normalise_name, names)))} names")
    for name in names[:20]:
        e = result[name]
        if e:
            other = ", ".join(f"{k}:{v}" for k, v in e["titles"].items() if k != "en")
            print(f"  {name!r:28} -> {e['title']} ({e['qid']})  {other}")
        else:
            print(f"  {name!r:28} -> None")
    if args.out_titles:
        args.out_titles.parent.mkdir(parents=True, exist_ok=True)
        args.out_titles.write_text("".join(f"{t}\n" for t in hits), encoding="utf-8")
        print(f"wrote {args.out_titles}")


if __name__ == "__main__":
    main()
