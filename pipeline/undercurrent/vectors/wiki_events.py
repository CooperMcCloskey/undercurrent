"""Wikipedia's daily "Current events" bullets: short, curated summaries of what happened each day.

    uv run python -m undercurrent.vectors.wiki_events --days 30
    uv run python -m undercurrent.vectors.wiki_events --start 2024-11-07 --end 2026-11-06

One request per day (the page `Portal:Current_events/<year>_<Month>_<day>`), so two years is about
730 requests, a few minutes. It goes through ingest/wiki_client.WikiClient, which shares
Wikimedia's rate limit (200 requests a minute for everyone running our code): don't run it at
the same moment as another Wikipedia download.

Raw   -> data/raw/wiki/current_events/YYYY-MM-DD.json   (the page's HTML and when it was fetched)
Table -> data/processed/wiki_events.parquet             (docs/WORKPLAN.md 3.9)
         id         "<day>:<n>", n counts the day's events from 0, in page order
         day        the day the event happened
         text       one bullet, with the source citations "(AP)", "(Reuters)" taken off
         titles     the Wikipedia articles the bullet links to, as "Title_with_underscores"
         section    the page's heading: "Armed conflicts and attacks", "Business and economy"...
         topic      the headings the bullet sits under: "Kivu conflict > Allied Democratic Forces
                    insurgency" (empty if it sits directly under the section)
         topic_titles  the articles those headings link to: the story's own Wikipedia article,
                    which B and C can use as an entity for free
The table is rebuilt from every raw file on disk, so runs accumulate, and a rerun downloads
only the days it doesn't have.

Then embed them (the vector store already knows this table), but only days old enough to have
settled, or a later-added bullet would never get a vector:
    uv run python -m undercurrent.vectors.store --source wiki_events --start ... --end <today - 4 days>

Point in time: bullets can be added to a day's page for a few days after that day (SETTLE_DAYS),
so a page fetched on day t+1 may be missing bullets that someone adds on t+2. Anything that must
be strictly "as of day t" should only use pages fetched at least SETTLE_DAYS after the day.

How a bullet is recognised: the page nests lists three deep (topic > sub-topic > event). A list
item is an event if its own text (not its sub-lists') carries a source link such as "(AP)", or
if it has no sub-list and is a full sentence (8+ words). A topic heading has sub-lists and no
source, even when it is long ("COVID-19 pandemic in Ukraine, COVID-19 vaccination in Ukraine"),
so it is not emitted: it becomes the `topic` of the events under it.
"""

import argparse
import datetime as dt
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

import pyarrow as pa
import pyarrow.parquet as pq

from undercurrent.config import PROCESSED, RAW
from undercurrent.ingest.wiki_client import (
    ACTION,
    WikiClient,
    add_date_args,
    date_range,
    days_between,
)

RAW_DIR = RAW / "wiki" / "current_events"
OUT = PROCESSED / "wiki_events.parquet"

# A day's page keeps gaining bullets for a few days. A copy fetched before then is fetched again
# on the next run, until it was fetched at least this long after the day.
SETTLE_DAYS = 3
# An item with no source link and no sub-list counts as an event if its text is at least this long.
MIN_EVENT_WORDS = 8

MONTHS = (
    "January February March April May June July August September October November December"
).split()
# Namespaces whose links are not articles (the bullets link to files, categories, the portal...).
NON_ARTICLE_NAMESPACES = {
    n + suffix
    for n in (
        "File Image Media Category Help Wikipedia WP Project Portal Special Template Talk User "
        "Draft Module MediaWiki Book TimedText"
    ).split()
    for suffix in ("", "_talk")
}

SCHEMA = pa.schema(
    [
        ("id", pa.string()),
        ("day", pa.date32()),
        ("text", pa.string()),
        ("titles", pa.list_(pa.string())),
        ("section", pa.string()),
        ("topic", pa.string()),
        ("topic_titles", pa.list_(pa.string())),
    ]
)


def _now() -> dt.datetime:  # a function so tests can fix the clock
    return dt.datetime.now(dt.UTC)


def page_title(day: dt.date) -> str:
    """'Portal:Current_events/2026_November_6' (no zero padding on the day)."""
    return f"Portal:Current_events/{day.year}_{MONTHS[day.month - 1]}_{day.day}"


# Parsing --------------------------------------------------------------------------------------------
#
# The standard library's HTMLParser is enough: the page is well-formed MediaWiki output. It is
# turned into a small tree first, because which sub-lists belong to which item matters.

_VOID = {"br", "hr", "img", "meta", "link", "input", "wbr"}
_SKIPPED = {"style", "script", "sup"}  # never part of a bullet's text


class Node:
    def __init__(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tag = tag
        self.attrs = {k: v or "" for k, v in attrs}
        self.children: list[Node | str] = []

    def classes(self) -> list[str]:
        return self.attrs.get("class", "").split()

    def elements(self, tag: str) -> list["Node"]:
        return [c for c in self.children if isinstance(c, Node) and c.tag == tag]


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("root", [])
        self._open = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, attrs)
        self._open[-1].children.append(node)
        if tag not in _VOID:
            self._open.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._open[-1].children.append(Node(tag, attrs))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self._open) - 1, 0, -1):  # close the nearest open one, and what's in it
            if self._open[i].tag == tag:
                del self._open[i:]
                return

    def handle_data(self, data: str) -> None:
        self._open[-1].children.append(data)


def _walk(node: Node) -> Iterator[Node]:
    yield node
    for child in node.children:
        if isinstance(child, Node):
            yield from _walk(child)


def _plain(node: Node) -> str:
    """All the text under a node, with whitespace collapsed (for headings)."""
    parts: list[str] = []

    def collect(n: Node) -> None:
        if n.tag in _SKIPPED:
            return
        for c in n.children:
            collect(c) if isinstance(c, Node) else parts.append(c)

    collect(node)
    return _tidy("".join(parts))


def _tidy(text: str) -> str:
    text = " ".join(text.replace("\xa0", " ").split())
    return re.sub(r"\s+([,.;:!?)])", r"\1", text)


def _is_external(href: str) -> bool:
    return href.startswith(("http://", "https://", "//"))


def article_title(href: str) -> str | None:
    """'/wiki/R%C3%ADo_Hondo,_Zacapa#History' -> 'Río_Hondo,_Zacapa'; None if it isn't an article."""
    if not href.startswith("/wiki/"):
        return None  # red links (/w/index.php?...&redlink=1), anchors, other sites
    title = unquote(href[len("/wiki/") :]).split("#", 1)[0].strip()
    if not title or title.partition(":")[0] in NON_ARTICLE_NAMESPACES:
        return None
    return title[:1].upper() + title[1:]


@dataclass
class _Own:
    """What a list item says itself, leaving out its nested sub-lists."""

    text: str
    titles: list[str]
    cited: bool  # it ends in, or has, a source link such as "(AP)"


def _own(li: Node) -> _Own:
    parts: list[str] = []
    titles: list[str] = []
    cited = False

    def collect(n: Node) -> None:
        nonlocal cited
        if n.tag in _SKIPPED:
            return
        if n.tag == "a":
            href = n.attrs.get("href", "")
            if _is_external(href):
                cited = True  # a source: "(AP)". Dropped from the text with its brackets.
                return
            title = article_title(href)
            if title and title not in titles:
                titles.append(title)
        for c in n.children:
            collect(c) if isinstance(c, Node) else parts.append(c)

    for child in li.children:
        if isinstance(child, str):
            parts.append(child)
        elif child.tag != "ul":
            collect(child)
    return _Own(_tidy("".join(parts)), titles, cited)


@dataclass
class Event:
    text: str
    titles: list[str]
    section: str
    topic: str
    topic_titles: list[str] = field(default_factory=list)


def _events_in(ul: Node, section: str, heads: list[_Own], out: list[Event]) -> None:
    for li in ul.elements("li"):
        own = _own(li)
        # A heading has sub-lists and no source; an unsourced item with no sub-list is an event
        # only if it reads like a sentence.
        sentence = not li.elements("ul") and len(own.text.split()) >= MIN_EVENT_WORDS
        is_event = bool(own.text) and (own.cited or sentence)
        if is_event:
            topic_titles = [t for h in heads for t in h.titles]
            out.append(
                Event(
                    text=own.text,
                    titles=own.titles,
                    section=section,
                    topic=" > ".join(h.text for h in heads if h.text),
                    topic_titles=list(dict.fromkeys(topic_titles)),
                )
            )
        # Sub-lists of an event hold more events; those of a heading hold what sits under it.
        below = heads if is_event else [*heads, own]
        for sub in li.elements("ul"):
            _events_in(sub, section, below, out)


def parse_events(html: str) -> list[Event]:
    """Every event bullet of one day's page, in page order. Empty if the page has no content block
    (the markup changed, or the day has no page)."""
    builder = _TreeBuilder()
    builder.feed(html)
    content = next(
        (
            n
            for n in _walk(builder.root)
            if n.tag == "div" and "current-events-content" in n.classes()
        ),
        None,
    )
    if content is None:
        return []
    events: list[Event] = []
    section = ""
    for child in content.children:
        if not isinstance(child, Node):
            continue
        if child.tag == "p":
            heading = _plain(child)
            section = heading or section
        elif child.tag == "ul":
            _events_in(child, section, [], events)
    return events


# Downloading ------------------------------------------------------------------------------------------


def _fetched_at(path: Path) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(json.loads(path.read_text(encoding="utf-8"))["fetched_at"])
    except (OSError, KeyError, ValueError):
        return None


def is_stale(path: Path, day: dt.date) -> bool:
    """A copy fetched before the page had settled, and not fetched again since."""
    fetched = _fetched_at(path)
    if fetched is None:
        return True
    return fetched.date() < day + dt.timedelta(days=SETTLE_DAYS) and _now().date() > fetched.date()


def download(client: WikiClient, day: dt.date, *, force: bool = False) -> str:
    """'cached', 'downloaded', 'refreshed' or 'no page'."""
    path = RAW_DIR / f"{day.isoformat()}.json"
    had = path.exists()
    if had and not force and not is_stale(path, day):
        return "cached"
    payload = client.get_json(
        ACTION,
        params={
            "action": "parse",
            "page": page_title(day),
            "prop": "text",
            "disableeditsection": 1,
            "disablelimitreport": 1,
            "format": "json",
            "formatversion": 2,
        },
    )
    if payload is not None and "error" in payload:
        if payload["error"].get("code") == "missingtitle":
            return "no page"
        raise RuntimeError(f"Wikipedia refused {page_title(day)}: {payload['error']}")
    if payload is None:
        return "no page"
    record = {
        "day": day.isoformat(),
        "page": payload["parse"]["title"],
        "fetched_at": _now().isoformat(),
        "html": payload["parse"]["text"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return "refreshed" if had else "downloaded"


# The table ----------------------------------------------------------------------------------------------


def day_rows(day: dt.date, html: str) -> list[dict]:
    return [
        {
            "id": f"{day.isoformat()}:{n}",
            "day": day,
            "text": e.text,
            "titles": e.titles,
            "section": e.section,
            "topic": e.topic,
            "topic_titles": e.topic_titles,
        }
        for n, e in enumerate(parse_events(html))
    ]


def build_table() -> tuple[pa.Table, list[dt.date]]:
    """The table from every raw file on disk, and the days whose page gave no events at all."""
    rows: list[dict] = []
    empty: list[dt.date] = []
    for path in sorted(RAW_DIR.glob("*.json")) if RAW_DIR.exists() else []:
        day = dt.date.fromisoformat(path.stem)
        found = day_rows(day, json.loads(path.read_text(encoding="utf-8"))["html"])
        rows += found
        if not found:
            empty.append(day)
    table = pa.Table.from_pylist(rows, schema=SCHEMA)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, OUT)
    return table, empty


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    add_date_args(p, default_days=7)
    p.add_argument(
        "--refetch", action="store_true", help="download the days again even if already on disk"
    )
    args = p.parse_args(argv)
    start, end = date_range(args)

    client = WikiClient()
    counts: dict[str, int] = {}
    for day in days_between(start, end):
        status = download(client, day, force=args.refetch)
        counts[status] = counts.get(status, 0) + 1
    print(f"{start}..{end}: {counts}")

    table, empty = build_table()
    days = len(set(table["day"].to_pylist()))
    print(f"{table.num_rows} events over {days} days -> {OUT}")
    if empty:
        shown = ", ".join(d.isoformat() for d in empty[:8])
        print(
            f"WARNING: {len(empty)} day(s) gave no events at all ({shown}"
            f"{'...' if len(empty) > 8 else ''}). Either the page is empty, or Wikipedia changed "
            "the page's markup and parse_events() needs a look."
        )


if __name__ == "__main__":
    main()
