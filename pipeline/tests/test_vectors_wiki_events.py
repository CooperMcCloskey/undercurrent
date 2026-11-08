"""Wikipedia "Current events": parsing a day's page, downloading it politely, and the table.

The page below is written by hand in the structure of the real one (checked against real pages from
2021 to 2026): bold section headings, then lists nested topic > sub-topic > event, where events end
in a source link like "(AP)". The Wikimedia API is a fake (httpx.MockTransport): no network.
"""

import datetime as dt
import json
from collections import Counter

import httpx
import pyarrow.parquet as pq
import pytest

from undercurrent.vectors import wiki_events as we
from undercurrent.vectors.store import day_inputs

D = dt.date(2026, 11, 6)

PAGE = """
<div class="mw-content-ltr mw-parser-output" lang="en" dir="ltr">
<style>.mw-parser-output .current-events-content{padding:0 0.3em}</style>
<div class="current-events"><div role="region" class="current-events-main vevent">
<div class="current-events-heading plainlinks">
  <ul class="current-events-navbar editlink noprint">
    <li><a class="external text" href="//en.wikipedia.org/w/index.php?title=x&action=edit">edit</a></li>
  </ul>
</div>
<div class="current-events-content description">
<p><b>Armed conflicts and attacks</b> </p>
<ul><li><a href="/wiki/Kivu_conflict">Kivu conflict</a>
  <ul><li><a href="/wiki/Allied_Democratic_Forces_insurgency">Allied Democratic Forces insurgency</a>
    <ul><li>The death toll from an attack by <a href="/wiki/Allied_Democratic_Forces">suspected militants</a>
      in <a href="/wiki/North_Kivu">North Kivu</a>, <a href="/wiki/Democratic_Republic_of_Congo">DR&nbsp;Congo</a>,
      has risen to&nbsp;33. <a class="external text" href="https://www.aa.com.tr/en/africa/x">(AA)</a></li></ul>
  </li></ul>
</li>
<li>Politician Julio Portillo is killed in <a href="/wiki/R%C3%ADo_Hondo,_Zacapa#History">Rio Hondo</a>,
  <a href="/wiki/Guatemala">Guatemala</a>, by gunmen; <a href="/wiki/Guatemala">Guatemala</a> mourns.
  <a href="https://apnews.com/x">(AP)</a> <a href="https://lemonde.fr/y">(<i>Le Monde</i>)</a></li>
</ul>
<p><b>Business and economy</b> </p>
<ul>
<li><a href="/wiki/Tesla,_Inc.">Tesla</a> ends the <a href="/w/index.php?title=Cybertruck_x&amp;redlink=1">Cybertruck</a>
  line, <a href="/wiki/File:Cybertruck.jpg">pictured</a> in <a href="/wiki/Category:Trucks">trucks</a>
  and the <a href="/wiki/Wikipedia:Sandbox">sandbox</a>.<sup class="reference">[1]</sup>
  <a href="https://apnews.com/z">(AP)</a></li>
</ul>
<p><b>Disasters and accidents</b> </p>
<ul>
<li><a href="/wiki/Aftermath_of_the_October_2021_Sudanese_coup">Aftermath of the October 2021 Sudanese coup d'etat</a>
  <ul><li>Protests continue nationwide and an intensive civil disobedience movement begins. <a href="https://x.test/a">(Reuters)</a></li></ul>
</li>
<li>A bus crash on a motorway near Lyon leaves two people dead and sixteen injured, officials said.</li>
<li><a href="/wiki/Short_heading">Short heading with no children</a></li>
</ul>
</div>
<div class="current-events-nav"><a href="/wiki/Portal:Current_events/2026_November_5">Previous</a></div>
</div></div></div>
"""


def events() -> list[we.Event]:
    return we.parse_events(PAGE)


# Parsing ----------------------------------------------------------------------------------------


def test_finds_every_event_and_only_events() -> None:
    texts = [e.text for e in events()]
    assert len(texts) == 5
    # The navbar link, the three headings and the childless short heading are not events.
    assert not any("edit" == t or "Kivu conflict" == t or "Short heading" in t for t in texts)
    assert not any(t.startswith("Aftermath of the October 2021") for t in texts)


def test_sections_and_topics() -> None:
    by_text = {e.text[:20]: e for e in events()}
    adf = by_text["The death toll from "]
    assert adf.section == "Armed conflicts and attacks"
    assert adf.topic == "Kivu conflict > Allied Democratic Forces insurgency"
    assert adf.topic_titles == ["Kivu_conflict", "Allied_Democratic_Forces_insurgency"]
    direct = by_text["Politician Julio Por"]
    assert direct.topic == "" and direct.topic_titles == []
    assert by_text["Tesla ends the Cyber"].section == "Business and economy"


def test_text_is_clean() -> None:
    adf = events()[0]
    # Source citation gone with its brackets, non-breaking spaces turned into spaces, no gaps
    # before punctuation.
    assert adf.text == (
        "The death toll from an attack by suspected militants in North Kivu, DR Congo, "
        "has risen to 33."
    )
    two_sources = events()[1]
    assert two_sources.text.endswith("Guatemala mourns.")
    assert "AP" not in two_sources.text and "Monde" not in two_sources.text


def test_titles_are_articles_only_decoded_and_deduplicated() -> None:
    assert events()[0].titles == [
        "Allied_Democratic_Forces",
        "North_Kivu",
        "Democratic_Republic_of_Congo",
    ]
    # percent-decoded, "#History" dropped, a title linked twice listed once
    assert events()[1].titles == ["Río_Hondo,_Zacapa", "Guatemala"]
    # red link, File:, Category:, Wikipedia: and the reference superscript are all left out
    tesla = events()[2]
    assert tesla.titles == ["Tesla,_Inc."]
    assert "[1]" not in tesla.text


def test_unsourced_item_is_an_event_only_if_it_is_a_sentence_without_sub_lists() -> None:
    texts = [e.text for e in events()]
    assert any(t.startswith("A bus crash on a motorway near Lyon") for t in texts)
    assert not any(t == "Short heading with no children" for t in texts)
    # ... and an unsourced heading with sub-lists is a topic even when it is long (9 words)
    sudan = next(e for e in events() if e.text.startswith("Protests continue"))
    assert sudan.topic == "Aftermath of the October 2021 Sudanese coup d'etat"
    assert sudan.topic_titles == ["Aftermath_of_the_October_2021_Sudanese_coup"]


def test_page_without_the_content_block_gives_no_events() -> None:
    assert we.parse_events("<div><p>Page layout changed</p></div>") == []
    assert we.parse_events("") == []


def test_article_title_rules() -> None:
    assert we.article_title("/wiki/Tesla,_Inc.") == "Tesla,_Inc."
    assert we.article_title("/wiki/star_Wars:_X") == "Star_Wars:_X"  # a colon is not a namespace
    assert we.article_title("/wiki/Talk:Tesla") is None
    assert we.article_title("/wiki/User_talk:Bob") is None
    assert we.article_title("/w/index.php?title=X&redlink=1") is None
    assert we.article_title("https://example.org/wiki/X") is None
    assert we.article_title("/wiki/") is None


def test_page_titles_have_no_zero_padding() -> None:
    assert we.page_title(dt.date(2026, 11, 6)) == "Portal:Current_events/2026_November_6"
    assert we.page_title(dt.date(2024, 2, 29)) == "Portal:Current_events/2024_February_29"
    assert we.page_title(dt.date(2021, 12, 31)) == "Portal:Current_events/2021_December_31"


# Downloading ------------------------------------------------------------------------------------


class FakeWikipedia:
    """Serves PAGE for some days, 'missingtitle' for the rest, and records every request."""

    def __init__(self, days: set[dt.date]) -> None:
        self.pages = {we.page_title(d): PAGE for d in days}
        self.requests: Counter[str] = Counter()
        self.user_agents: set[str] = set()
        self.error: dict | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.user_agents.add(request.headers["user-agent"])
        q = request.url.params
        assert request.url.host == "en.wikipedia.org" and q["action"] == "parse"
        assert q["prop"] == "text" and q["formatversion"] == "2"
        title = q["page"]
        self.requests[title] += 1
        if self.error:
            return httpx.Response(200, json={"error": self.error})
        if title not in self.pages:
            return httpx.Response(200, json={"error": {"code": "missingtitle", "info": "no page"}})
        return httpx.Response(
            200, json={"parse": {"title": title.replace("_", " "), "text": self.pages[title]}}
        )


NOW = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.UTC)


@pytest.fixture
def wiki(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """A fake Wikipedia that has pages for D and D+1, with every file under tmp_path."""
    server = FakeWikipedia({D, D + dt.timedelta(1)})
    transport = httpx.MockTransport(server)
    real = we.WikiClient
    monkeypatch.setattr(we, "WikiClient", lambda: real(max_rps=1e6, transport=transport))
    monkeypatch.setattr(we, "RAW_DIR", tmp_path / "raw" / "wiki" / "current_events")
    monkeypatch.setattr(we, "OUT", tmp_path / "processed" / "wiki_events.parquet")
    monkeypatch.setattr(we, "_now", lambda: NOW)
    server.client = we.WikiClient  # type: ignore[attr-defined]
    return server


def raw_path(day: dt.date):
    return we.RAW_DIR / f"{day.isoformat()}.json"


def test_download_saves_the_page_and_the_second_run_is_cached(wiki) -> None:
    client = wiki.client()
    assert we.download(client, D) == "downloaded"
    saved = json.loads(raw_path(D).read_text(encoding="utf-8"))
    assert saved["day"] == "2026-11-06" and saved["page"] == "Portal:Current events/2026 November 6"
    assert saved["html"] == PAGE and saved["fetched_at"] == NOW.isoformat()
    assert we.download(client, D) == "cached"
    assert wiki.requests[we.page_title(D)] == 1
    assert len(wiki.user_agents) == 1 and "undercurrent" in next(iter(wiki.user_agents))


def test_a_day_with_no_page_is_reported_and_not_saved(wiki) -> None:
    assert we.download(wiki.client(), dt.date(2026, 11, 18)) == "no page"
    assert not raw_path(dt.date(2026, 11, 18)).exists()


def test_other_api_errors_are_loud(wiki) -> None:
    wiki.error = {"code": "ratelimited", "info": "slow down"}
    with pytest.raises(RuntimeError, match="ratelimited"):
        we.download(wiki.client(), D)


def test_a_page_fetched_before_it_settled_is_fetched_again_later(wiki) -> None:
    client = wiki.client()
    we.download(client, D)
    # Pretend that copy was taken the day after (D + 1 < D + 3), and today is later.
    saved = json.loads(raw_path(D).read_text(encoding="utf-8"))
    saved["fetched_at"] = dt.datetime(2026, 11, 7, 9, tzinfo=dt.UTC).isoformat()
    raw_path(D).write_text(json.dumps(saved), encoding="utf-8")
    assert we.download(client, D) == "refreshed"
    assert wiki.requests[we.page_title(D)] == 2
    assert we.download(client, D) == "cached"  # the new copy is 14 days after the day


def test_a_fresh_early_copy_is_not_refetched_the_same_day(wiki, monkeypatch) -> None:
    day = dt.date(2026, 11, 19)  # yesterday, as of NOW
    wiki.pages[we.page_title(day)] = PAGE
    client = wiki.client()
    assert we.download(client, day) == "downloaded"
    monkeypatch.setattr(we, "_now", lambda: NOW + dt.timedelta(hours=3))
    assert we.download(client, day) == "cached"  # still the same calendar day
    monkeypatch.setattr(we, "_now", lambda: NOW + dt.timedelta(days=1))
    assert we.download(client, day) == "refreshed"  # next day: the page may have grown


def test_force_downloads_again(wiki) -> None:
    client = wiki.client()
    we.download(client, D)
    assert we.download(client, D, force=True) == "refreshed"
    assert wiki.requests[we.page_title(D)] == 2


# The table --------------------------------------------------------------------------------------


def test_table_has_the_agreed_columns_and_ids(wiki) -> None:
    client = wiki.client()
    for d in (D, D + dt.timedelta(1)):
        we.download(client, d)
    table, empty = we.build_table()
    assert empty == []
    assert table.schema == we.SCHEMA and table.num_rows == 10
    ids = table["id"].to_pylist()
    assert ids[:5] == [f"2026-11-06:{n}" for n in range(5)]
    assert ids[5] == "2026-11-07:0" and len(set(ids)) == 10
    read = pq.read_table(we.OUT)
    assert read.schema == we.SCHEMA
    assert read["titles"].to_pylist()[0] == [
        "Allied_Democratic_Forces",
        "North_Kivu",
        "Democratic_Republic_of_Congo",
    ]
    assert read["day"].to_pylist()[0] == D


def test_the_table_accumulates_from_every_raw_file(wiki) -> None:
    client = wiki.client()
    we.download(client, D)
    assert we.build_table()[0].num_rows == 5
    we.download(client, D + dt.timedelta(1))
    assert we.build_table()[0].num_rows == 10


def test_an_empty_table_still_has_the_schema(wiki) -> None:
    table, empty = we.build_table()
    assert table.num_rows == 0 and table.schema == we.SCHEMA and empty == []


def test_the_vector_store_reads_the_table(wiki, tmp_path) -> None:
    we.download(wiki.client(), D)
    we.build_table()
    ids, texts = day_inputs("wiki_events", tmp_path / "processed", D)  # type: ignore[misc]
    assert ids == [f"2026-11-06:{n}" for n in range(5)]
    assert texts[0].startswith("The death toll from an attack by suspected militants")
    assert all(t == t.strip() and t for t in texts)


def test_main_downloads_builds_and_reports(wiki, capsys) -> None:
    we.main(["--start", "2026-11-06", "--end", "2026-11-08"])
    out = capsys.readouterr().out
    assert "'downloaded': 2" in out and "'no page': 1" in out
    assert "10 events over 2 days" in out and "WARNING" not in out
    we.main(["--start", "2026-11-06", "--end", "2026-11-07"])
    assert "'cached': 2" in capsys.readouterr().out
    assert wiki.requests[we.page_title(D)] == 1


def test_main_warns_when_a_page_gives_no_events(wiki, capsys) -> None:
    wiki.pages[we.page_title(D)] = "<div class='something-new'><ul><li>changed</li></ul></div>"
    we.main(["--start", "2026-11-06", "--end", "2026-11-07"])
    out = capsys.readouterr().out
    assert "WARNING: 1 day(s) gave no events at all (2026-11-06)" in out
    assert "5 events over 1 days" in out
