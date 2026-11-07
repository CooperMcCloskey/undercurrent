# Wikipedia data

**Owner:** A (Ingest) · **Code:** `pipeline/undercurrent/ingest/wiki*.py` · **Tests:** `pipeline/tests/test_wiki*.py`

This file is the guide to the Wikipedia data and the plan for it. It follows the team's 12-month plan ("Wikipedia: what the data agent (A) should do now" in the main `README.md`) and adds the details: what's built, what's measured, and the order of work.

## In one paragraph

When something is about to become big news, people start looking it up. They read the Wikipedia articles on the people, places and organisations involved, and editors start updating those articles. This code collects that signal. For each **entity** in our stories (a person, an organisation, a place), it records **how many people read its Wikipedia article each day, and how many edits it got, over the 12-month range**. The most important entities are covered in the 8 most-read Wikipedia languages, and the rest in English. It also keeps each language's daily list of its 1,000 most-read articles for the same 12 months. Reading and editing often pick up a few days before a story peaks in the news, and that early warning is what the forecast is looking for.

## Status at a glance

| | What | State |
|---|---|---|
| ✅ | Name → article resolver, Wikidata links to 8 languages | built, tested |
| ✅ | Daily pageviews and edits per article, cached and resumable | built, tested |
| ✅ | Per-entity table, languages summed (`wiki_entity_daily.parquet`) | built, tested |
| ✅ | Top-1000 lists, 8 languages, cleaned and bot-flagged | built; **12 months downloaded** (2025-11-07 to 2026-11-06) |
| ✅ | Rate limiting that follows Wikimedia's 2026 rules | built, tested |
| ⏳ | Entity tier selection (which entities get fetched, and how much) | next, see "The plan" |
| ⏳ | Bulk pageview dumps (one-day test) | to evaluate, on the cloud machine |
| ⏳ | Per-entity fetch for real stories | waits only for parsed GDELT articles |
| ⏳ | Scheduled events → `wiki_scheduled.parquet` | stretch |

## Why it's useful to the project

1. **Forecast features (C).** Wikipedia keeps years of daily history, so we can backtest with it honestly. Bluesky posts can't be backtested.
2. **Things to show (B → D).** The story panel can show a line of Wikipedia reading over time and a list of the story's people and organisations with links to their articles.
3. **Not only English.** A story often starts in one country's language before English-language readers notice. Counting 8 languages makes those stories visible, and "attention spreading to more languages" is a signal in itself.

---

## Key ideas, in plain words

| Term | Meaning |
|---|---|
| **Pageviews** | How many times people read an article **on a given day** (one number per day, never an all-time total). We count human readers only; Wikimedia filters out known bots and crawlers. |
| **Edits** | How many times an article was changed on a given day. A burst of edits usually means something is happening. |
| **Top-1000** | Wikipedia's own daily list of its 1,000 most-read articles, per language. |
| **Title** | An article's name in a URL, with underscores: `Barack_Obama`. |
| **Wikidata ID (QID)** | A language-independent ID for a topic. `Q76` is Barack Obama in every language. We use it to find the same article in Japanese (`バラク・オバマ`), Russian (`Обама,_Барак`), and so on. |
| **Entity** | One topic (one QID), with its articles in all languages added together. |
| **Tier** | How much data we fetch for an entity: tier 1 gets everything, tier 2 a little, the rest nothing. See "The plan". |
| **Day** | A UTC calendar day. |

**Languages:** `en` English, `ja` Japanese, `de` German, `fr` French, `ru` Russian, `es` Spanish, `it` Italian, `zh` Chinese. These were the 8 most-read Wikipedias in October 2026. The list is `LANGUAGES` in `wiki_client.py`, and every command accepts `--langs` to use a different set.

---

## Who waits on whom

**Wikipedia does not wait for stories, clustering, vectors or the full GDELT backfill.** It only needs entity names, and those come straight from the `persons` and `orgs` columns of the parsed GDELT articles (`articles.parquet`, built by the GDELT side of A).

- **Can run now, with no GDELT at all:** the top-1000 lists, the bulk-dump test, the tier selection code (tested on fake counts), and the scheduled-events scraper.
- **Needs only parsed articles:** the per-entity fetch. Parsed days arrive in stages (about 1 day at H+2, 7 days at H+5, the newest 60 at H+10). Start tier 1 on the first parsed week and keep adding as more days arrive. **Fetching is the long pole**, so waiting for the whole backfill would waste hours.
- **The dependency runs the other way:** Stories (B) need entity titles for `Entity.wikipedia_title`, and Forecast (C) needs `wiki_entity_daily.parquet` for its features. They wait on Wikipedia, not the reverse.
- **Not ours:** the Wikipedia "Current events" bullets (`wiki_events.parquet`) and new event articles belong to the Vectors workstream (V). They don't depend on this code.

---

## The plan: which entities get fetched

Wikimedia allows us about 190 requests a minute in total (see "Being polite to Wikimedia"). Over 12 months of stories there are far more entities than we can fetch in full, so **entities are fetched in tiers**:

| Tier | Which entities | What we fetch | Requests per entity |
|---|---|---|---|
| **1** | The top ~5 entities of the biggest stories each week | Pageviews in all 8 languages + **English** edits | about 8.5 (about 2.7 s) |
| **2** | The top entities of the next-biggest stories | **English** pageviews only | 1 (about 0.3 s) |
| **Everything else** | | Nothing. Their features are **missing (NaN), never 0** | 0 |

Rules for picking them:

1. **People, organisations and places, but not countries or capital cities.** `United_States` or `London` are read so much anyway that they drown out the signal.
2. **Not news outlets.** GDELT lists the agencies from bylines (`Associated Press`, `Reuters`, `Postmedia`) as organisations in almost every article. In a test on a real GDELT file they were among the most-mentioned "entities" of every story, while having nothing to do with the story.
3. **Rank by how specific an entity is to a story, not only by how often it's mentioned:** frequent in the story, rare elsewhere that week. Otherwise every story's top entities are the same handful of global names.
4. **The same rule for training and test days.**
5. **No peeking.** Which entities are chosen must never depend on how the story later did. Selection for a week uses only data up to that week.
6. **Before stories exist** (B's tracker isn't ready), approximate "top entities of the biggest stories" with **the most-mentioned entities of the previous week**: the previous week, not the same week, so the choice doesn't use what happened later. Switch to the real rule when stories arrive.
7. **Lock the rule before year 1 is processed.** Changing it later means refetching (main `README.md`, checklist item 5).

The selection will be a function over a table of entity mention counts (planned: `ingest/wiki_tiers.py`), which writes the tier 1 and tier 2 title lists that the fetch commands below read. It can be built and tested now on fake counts.

### What tiers cost

| Job | Requests | Time | Disk |
|---|---|---|---|
| 3,000 tier 1 entities, 12 months | about 25,500 | **about 2.2 hours** | about 300 MB |
| 10,000 tier 2 entities, 12 months | about 10,000 | **about 55 minutes** | about 100 MB |
| Resolving 1,000 names to articles | about 60–400 | 1–3 minutes | under 1 MB |

Measured on 89 real entities over a year: on average an entity has articles in 7.1 of the 8 languages, and its English article gets about 230 edits a year, so 1.2 edit requests. A pageview request covers the whole 12 months in one go, so **the number of entities sets the time, not the length of history**. Everything in this section shares the same 190-per-minute budget, so run big jobs one after another, not side by side.

### The timing rule (decided)

For a forecast made on day t:

- **pageviews up to day t − 1** (Wikimedia publishes them about a day late);
- **edits up to day t** (they're known straight away);
- **top-1000 lists up to day t − 1** (they're built from pageviews).

Apply it everywhere: features, backtests and the live view.

---

## How it works, step by step

```
"barack obama"                  a name, as GDELT gives it
        │  1. resolve: find the English article
        ▼
Barack_Obama                    English Wikipedia title
        │  2. link: look it up on Wikidata
        ▼
Q76  →  en: Barack_Obama   ja: バラク・オバマ   ru: Обама,_Барак   zh: 巴拉克·奧巴馬
        de / fr / es / it: Barack_Obama
        │  3. fetch: daily pageviews (and edits) for the entity's tier
        ▼
wiki_pageviews.parquet / wiki_edits.parquet     one row per language, article and day
        │  4. combine: add the languages together
        ▼
wiki_entity_daily.parquet                        one row per entity and day
```

Separately, and with no names needed, `wiki_top` downloads each language's top-1000 list for every day of the 12 months.

### Step 1: name → English article (`wiki_resolve.py`)

GDELT gives us names like `barack obama`, `un` or `john smith`. For each one:

1. **Try the obvious titles first:** `Barack Obama`, and `UN` for short names. If the title is a redirect, follow it (`UN` → `United_Nations`). About 16 names are checked in a single request.
2. **If the obvious title is a "disambiguation" page** (a page listing several things with the same name, as for `John Smith`), the name is too vague. We return **nothing** instead of guessing.
3. **If no obvious title exists, search** Wikipedia. Take the first result that isn't a disambiguation page and actually resembles the name. `zelensky` finds `Volodymyr_Zelenskyy`; `asdfgh qwerty` finds nothing.
4. **Remember every answer** in `data/cache/wiki_resolve.json`, including "nothing found", so no name is looked up twice. Network errors are not remembered, so those names are retried next time.

On the 80 most-mentioned names in a real GDELT file, 65 resolved (in 9 seconds) and about 6 of those were wrong or doubtful (for example `pope pope leo` → `Pope_Leo_X`). Most misses were names GDELT had garbled, or ambiguous names like `democratic party`.

### Step 2: English article → the same article in other languages (`wiki_resolve.py`)

The English title is looked up on Wikidata, which says what the article is called in each language. 50 titles take two requests. Some topics have no article in some languages; the entity then simply has fewer languages. Answers are remembered in `data/cache/wiki_links.json`.

### Step 3: daily numbers for each article (`wiki.py`)

**History: 12 months per article** (`HISTORY_DAYS = 365` in `wiki.py`). That covers the backfill range and gives the forecast a long baseline: how unusual this week is compared with the entity's normal level.

- **Pageviews:** we ask for "daily views of this article from day X to day Y". One request returns **one number per day** for the whole 12 months.
- **Edits:** we ask the article's own language site for its revision history in the date range and count edits per day. Very active articles need one extra request per 500 edits. The plan fetches edits for **English, tier 1 only**, because they're a weaker signal than pageviews. The code can do all 8 languages if that changes.
- **Days with nothing are recorded as 0.** But Wikimedia publishes pageviews about a day late, so for the last day or two a missing number might just mean "not published yet". Those rows have `complete = False` and are fetched again on the next run.
- **Everything is saved per article** under `data/raw/wiki/`. Running again only fetches what's missing or not yet complete, so you can stop a long run and start it again.

### Step 4: combine languages (`wiki.py`)

Complete rows are added together by Wikidata ID, so `wiki_entity_daily.parquet` has one row per entity and day: total views, total edits, and a column for each language. It always covers every language that has data, so running edits for English only doesn't drop the other languages' pageviews.

### Top-1000 lists (`wiki_top.py`)

For each language and day we download two lists, all devices and desktop only, and clean them. **12 months are kept** (a rolling window: each run deletes lists older than 365 days). 8 languages × 2 lists × 365 days is about 5,800 requests, about 30 minutes, the first time; after that, a daily top-up takes seconds.

**What we have:** 2025-11-07 to 2026-11-06, 2.87 million rows, 441 MB of raw lists and a 49 MB table. **4 language-days are missing** because Wikimedia itself has no list for them (still 404 after repeated tries): `ja` 2026-07-25, `fr` 2026-08-02, `zh` 2026-06-25 and 2026-08-14. Treat those days as missing, not as "nobody read anything". The `ja` desktop list for 2026-08-15 is also missing, so that day has no bot flags.

**Watch out for one-off 404s.** While downloading, Wikimedia sometimes answered "404 not found" for a list it does have; about 1 request in 400 got this and worked on a later try. So for days older than 2 days, a 404 is retried twice (5 seconds apart) before it's believed. The same applies to per-article pageviews, where a wrongly believed 404 would otherwise record a whole year of zeros.

- **Remove pages that aren't articles:** the main page, search pages, help pages and so on. Each language names these differently (`Special:`, `特別:`, `Spezial:`…), so we read the names from each site rather than hard-coding English ones.
- **Flag likely bots.** Real readers are mostly on phones. A page whose readers are 90%+ on desktop is almost always being hit by an automated script, as with `JSON-LD` or `List_of_S&P_500_companies`. Those rows get `suspect_bot = True`. About 0.4–1.6% of rows are flagged, depending on the language. **Rows are flagged, not deleted.**

### Bulk pageview dumps (to evaluate)

Instead of asking for each entity's pageviews one by one, we could download Wikimedia's daily pageview files, which cover every article in every language, and keep only the titles we need. That would remove the rate limit from pageviews entirely; edits would still use the API.

- **Checked:** a human-only version exists (`dumps.wikimedia.org/other/pageview_complete/<YYYY>/<YYYY-MM>/pageviews-<YYYYMMDD>-user.bz2`), separate from the automated-traffic file. It's about 670–710 MB per day (October 2026), so about 250 GB for 12 months. **Not for laptops or event wifi**: only on the cloud machine.
- **Not yet checked:** the file layout, how long one day takes to download and decompress, and how big the filtered result is.
- **Plan:** test one day on the cloud machine. Look at the layout, time it, and filter to the tier 1 and tier 2 titles in our 8 languages. Then decide how to store it. One option is to keep daily views for **every title above a view threshold**, so later entity lookups are plain joins with nothing to wait for. That table could get big, so size it on the test day before committing.

### Being polite to Wikimedia (`wiki_client.py`)

Since 2026, Wikimedia allows clients like ours (no login, identified by a User-Agent) **200 requests per minute in total, across all its sites, with at most 3 at the same time**. Their [rate limit page](https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits) has the details. The client stays just under this:

- 190 requests/minute, shared by everything (configurable with the `UC_WIKI_REQUESTS_PER_MINUTE` environment variable);
- at most 3 requests at once;
- if Wikimedia says "too many requests", everything pauses for as long as it asks (at least 5 seconds);
- every request identifies us with `config.WIKIMEDIA_USER_AGENT`.

The bulk dumps aren't part of these APIs, so they don't count against the 200 per minute.

---

## What you get (files)

Everything lives in `data/`, which is not in git. In the cloud, the bucket is the source of truth (main `README.md`).

### `data/processed/wiki_entity_daily.parquet`: **start here**

One row per entity per day, all languages added together. Only complete days are included. The examples are a real row (Barack Obama, 4 November 2026).

| Column | Example | Meaning |
|---|---|---|
| `qid` | `Q76` | Wikidata ID. If an article has no Wikidata entry, it's `en:<title>` instead. |
| `title_en` | `Barack_Obama` | English title |
| `day` | `2026-11-04` | |
| `views` | `111251` | readers across all fetched languages |
| `edits` | `3` | edits across all fetched languages (empty if none were fetched) |
| `n_langs` | `8` | how many languages have an article on this entity |
| `views_en` … `views_zh` | `75834` (en) | readers in each language (empty if not fetched, or no article in that language) |
| `edits_en` … | `0` (en) | edits in each language that was fetched |

Tier 2 entities only have `views_en`, so their `views` total is English only. Compare tier 1 and tier 2 entities with `views_en`, or with growth rates, not with raw totals.

### `data/processed/wiki_pageviews.parquet` and `wiki_edits.parquet`

The per-language detail: `lang, title, day, views` (or `edits`), plus `complete`. The same title can exist in several languages (`Barack_Obama` is the title in English, German, French, Spanish and Italian), so always filter by `lang` as well as `title`.

### `data/processed/wiki_top.parquet`

`lang, day, rank, title, views, desktop_share, suspect_bot`, for 12 months. Use `df[~df.suspect_bot]` to drop likely bots.

### Caches (you don't normally need these)

| File | What |
|---|---|
| `data/cache/wiki_resolve.json` | name → English title, or `null` |
| `data/cache/wiki_links.json` | English title → Wikidata ID and the titles in each language |
| `data/cache/wiki_siteinfo/<lang>.json` | each language's main page and non-article prefixes |
| `data/raw/wiki/top/<lang>/<all-access or desktop>/<day>.json` | downloaded top-1000 lists (441 MB for 12 months) |
| `data/raw/wiki/{pageviews,edits}/<hash>.json` | downloaded numbers per article |

To start again from scratch, delete the relevant cache and run the command again.

---

## How to run it

All commands run from the repo root. Each one is safe to stop and run again. **Run them one at a time**: they share Wikimedia's 190-per-minute budget.

```bash
# 1. Top-1000 lists for the 12 months. Needs no names; run it first.
uv run python -m undercurrent.ingest.wiki_top --days 365
#    ...then daily, to add new days (and drop days older than 12 months):
uv run python -m undercurrent.ingest.wiki_top

# 2. Names → English titles (also links them to the other languages).
#    One name per line, from the tier selection.
uv run python -m undercurrent.ingest.wiki_resolve --names-file data/processed/wiki_tier1_names.txt \
    --out-titles data/processed/wiki_tier1.txt
uv run python -m undercurrent.ingest.wiki_resolve --names-file data/processed/wiki_tier2_names.txt \
    --out-titles data/processed/wiki_tier2.txt

# 3. Tier 1: pageviews in all 8 languages, then English edits.
uv run python -m undercurrent.ingest.wiki --titles-file data/processed/wiki_tier1.txt --days 365 --what pageviews
uv run python -m undercurrent.ingest.wiki --titles-file data/processed/wiki_tier1.txt --days 365 --what edits --langs en

# 4. Tier 2: English pageviews only.
uv run python -m undercurrent.ingest.wiki --titles-file data/processed/wiki_tier2.txt --days 365 --what pageviews --langs en
```

The tier name files will come from the tier selection (planned, `ingest/wiki_tiers.py`); until then, write them by hand or from a quick count of `persons`/`orgs`.

Useful options (every command has `--help`):

- `--days N`, or `--start YYYY-MM-DD --end YYYY-MM-DD`, ending yesterday by default. **Pass `--days 365`** for the 12-month range: the defaults are short (7 days), because the team rule is that no command downloads months of data unless asked.
- `--langs en de fr` to use fewer or other languages.
- `--what pageviews` or `--what edits` (in `wiki`) to fetch only one kind.
- `--keep-days N` (in `wiki_top`) to change the 12-month window; `0` keeps everything.
- `--retry-misses` (in `wiki_resolve`) to look up names that previously found nothing.

Or from Python:

```python
import datetime as dt

from undercurrent.ingest import wiki
from undercurrent.ingest.wiki_resolve import resolve, resolve_entities

# "Barack_Obama"
resolve("barack obama")

# {"barack obama": {"qid": "Q76", "title": "Barack_Obama",
#                   "titles": {"en": "Barack_Obama", "ja": "バラク・オバマ", ...}}}
resolve_entities(["barack obama"])

# 12 months of daily pageviews in every language; downloads what's missing, then rebuilds the tables
end = dt.date.today() - dt.timedelta(days=1)
wiki.fetch(["Barack_Obama"], end - dt.timedelta(days=wiki.HISTORY_DAYS - 1), end, ["pageviews"])
```

---

## How the other workstreams should use it

### B: Stories

1. Each week, give A (or the tier selection) **the top entities of your biggest stories**, following the rules in "The plan". Leave out countries, capitals and news outlets.
2. Resolve names with `resolve_entities(names)`. Use the Python call, not the CLI: it's cached and fast after the first time. Put the English title in `Entity.wikipedia_title` in the export. Leave it empty when nothing was found; never guess.
3. When exporting, read `wiki_entity_daily.parquet`:
   - `Entity.pageviews_7d` / `edits_7d`: the sum of `views` / `edits` over the 7 days ending on the snapshot day (empty for entities that weren't fetched).
   - `TimelinePoint.wiki_pageviews`: the sum of `views` over the story's entities for that day, or empty if there's no data.

### C: Forecast

1. **Read only the parquet files**, never the internet, so your training is repeatable and works offline.
2. **Follow the timing rule:** pageviews and top-1000 up to t − 1, edits up to t.
3. **Suggested features**, per story, from `wiki_entity_daily`:
   - **growth:** `log2((views over the last 7 days + 1) / (views over the 7 days before + 1))`, and the same for edits;
   - **acceleration:** this week's growth minus last week's;
   - **unusualness:** this week's views compared with the entity's own past year (for example, how many standard deviations above its 12-month average). Big topics are always busy, so this is fairer than raw counts;
   - **spread** (tier 1 only): how many languages show rising views (from the `views_<lang>` columns);
   - **non-English share** (tier 1 only): views outside English divided by total views; a rising share suggests the story is spreading internationally;
   - **top-1000 presence:** how many of the story's entities are in a top-1000 list that day (`wiki_top`, with bots removed). It covers the full 12 months, so it works for backtests.
4. **Missing isn't zero.** Entities outside tiers 1 and 2 have no rows; treat that as missing (NaN). The gradient boosting model handles NaN natively. Add a feature for the story's tier, so the model knows how much Wikipedia data it has.

### V: Vectors

- The "Current events" bullets and new event articles are yours (`wiki_events.parquet`, `wiki_new.parquet`). They use the same Wikimedia rate limit; coordinate big runs with A so they don't overlap.

### D: Web and API

- **The web app never talks to Wikipedia.** Everything arrives through B's export, so the demo works offline.
- Link to an entity's article with `https://en.wikipedia.org/wiki/<wikipedia_title>`.
- The Wikipedia numbers can be empty (`null`); show them as "–".

---

## Order of work (A, Wikipedia)

1. ✅ **Top-1000 for the 12 months.** Done: 2.87 million rows, 2025-11-07 to 2026-11-06. It needed no names and finished before any entity fetches compete for the rate limit.
2. **Write the timing rule in `PLAN.md` Notes** (A's section).
3. **Tier selection** (`ingest/wiki_tiers.py`): a function from a table of entity mention counts to tier 1 and tier 2 lists, following the rules above (no countries, capitals or news outlets; specific to the story; previous week only). Test it on fake counts.
4. **Bulk-dump test day** on the cloud machine, then decide between dumps and per-entity calls for pageviews.
5. **As soon as the first parsed GDELT week exists:** run the tier selection on it, then fetch tier 1, then tier 2. Keep adding weeks as more parsed days arrive.
6. **Resolver improvements** from the real-GDELT test: use the story's country to pick between same-named articles (`democratic party` → `Democratic_Party_(United_States)`), and drop names GDELT has clearly garbled.
7. **Stretch: scheduled events.** Scrape Wikipedia pages that list dated upcoming events (elections, summits, finals) into `wiki_scheduled.parquet` (`date, title, kind`). Scheduled events are the main thing that can make a 14+ day forecast better than "it fades".

---

## Looking after this code (A)

- Keep column names stable. Adding columns is fine; tell B and C before renaming or removing anything.
- Tests: `uv run pytest pipeline/tests/test_wiki*.py`. That's 37 tests; they run offline in about 1 second using a fake Wikimedia server.

## Known limitations

- **Tier 2 is English only,** so stories that start outside English-language readership only show up in Wikipedia for tier 1 entities.
- **Redirects are counted separately.** Someone who reads `UN` (a redirect to `United_Nations`) is counted under `UN`, and we don't add those views in. Totals are a bit low for entities that are often reached through a short name.
- **Renamed articles lose their history.** Views are recorded under the title at the time, so if an article was renamed recently, its earlier views sit under the old title.
- **New articles start at zero.** An article written after an event (common in non-English Wikipedias, which often follow English) shows no views before it was created. This is correct, but it means `n_langs` counts languages that have an article *today*, not on each day.
- **Only desktop bots are caught.** Automated traffic disguised as mobile (for example `Hanuman_Ansh`) isn't flagged.
- **The name matcher prefers "don't know" to a wrong answer**, but about 1 in 10 matches is still doubtful. Check the top stories by eye.
- **Big topics dominate totals.** `Donald_Trump` gets far more views than most entities. Compare growth rates, not raw counts.
- **Recent days arrive late.** Pageviews for the last 1–2 days, and yesterday's desktop top-1000, may not be published yet. Running again the next day fills them in.
- **8 languages only.** Portuguese, Arabic, Persian, Ukrainian and others matter for geopolitics but aren't in the top 8 by readership. Add them with `--langs` if needed. Each one adds about 1 request per tier 1 entity.
