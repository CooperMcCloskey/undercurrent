# Wikipedia data

**Owner:** A (Ingest) · **Code:** `pipeline/undercurrent/ingest/wiki*.py` · **Tests:** `pipeline/tests/test_wiki*.py`

## In one paragraph

When something is about to become big news, people start looking it up. They read the Wikipedia articles on the people, places and organisations involved, and editors start updating those articles. This code collects that signal. For each **entity** in our stories (a person such as Benjamin Netanyahu, or an organisation such as NATO), it records **how many people read its Wikipedia article each day, and how many edits it got**. It does this in the 8 most-read Wikipedia languages, and it also saves each language's daily list of its 1,000 most-read articles.

Here is the 2026 Israeli election as an example. Three days before polling day, reading of the story's articles (Netanyahu, Bennett, Lapid, the Knesset…) had already doubled, and edits were up almost 3×. That's the kind of early warning the forecast is looking for.

## Why it's useful to the project

1. **Forecast features (C).** Wikipedia keeps years of daily history, so we can backtest with it honestly. Bluesky posts can't be backtested.
2. **Things to show (B → D).** The story panel can show a line of Wikipedia reading over time and a list of the story's people and organisations with links to their articles.
3. **Not only English.** A story often starts in one country's language before English-language readers notice. Counting 8 languages makes those stories visible, and "attention spreading to more languages" is a signal in itself.

---

## Key ideas, in plain words

| Term | Meaning |
|---|---|
| **Pageviews** | How many times people read an article on a given day. We count human readers only; Wikimedia filters out known bots and crawlers. |
| **Edits** | How many times an article was changed on a given day. A burst of edits usually means something is happening. |
| **Top-1000** | Wikipedia's own daily list of its 1,000 most-read articles, per language. |
| **Title** | An article's name in a URL, with underscores: `Barack_Obama`. |
| **Wikidata ID (QID)** | A language-independent ID for a topic. `Q76` is Barack Obama in every language. We use it to find the same article in Japanese (`バラク・オバマ`), German, Russian, and so on. |
| **Entity** | One topic (one QID), with its articles in all 8 languages added together. |
| **Day** | A UTC calendar day. |

**Languages:** `en` English, `ja` Japanese, `de` German, `fr` French, `ru` Russian, `es` Spanish, `it` Italian, `zh` Chinese. These were the 8 most-read Wikipedias in October 2026. The list is `LANGUAGES` in `wiki_client.py`, and every command accepts `--langs` to use a different set.

---

## How it works, step by step

```
"benjamin netanyahu"            a name, as GDELT gives it
        │  1. resolve: find the English article
        ▼
Benjamin_Netanyahu              English Wikipedia title
        │  2. link: look it up on Wikidata
        ▼
Q43723  →  en: Benjamin_Netanyahu   ja: ベンヤミン・ネタニヤフ   de: Benjamin_Netanjahu
           fr: Benyamin_Netanyahou  ru: Нетаньяху,_Биньямин  es: … it: … zh: …
        │  3. fetch: daily pageviews and edits for each of the 8 articles
        ▼
wiki_pageviews.parquet / wiki_edits.parquet     one row per language, article and day
        │  4. combine: add the languages together
        ▼
wiki_entity_daily.parquet                        one row per entity and day
```

Separately, and with no names needed, `wiki_top` downloads each language's top-1000 list for every day.

### Step 1: name → English article (`wiki_resolve.py`)

GDELT gives us names like `barack obama`, `idf` or `john smith`. For each one:

1. **Try the obvious titles first:** `Barack Obama`, and `IDF` for short names. If the title is a redirect, follow it (`IDF` → `Israel_Defense_Forces`). About 16 names are checked in a single request.
2. **If the obvious title is a "disambiguation" page** (a page listing several things with the same name, as for `John Smith`), the name is too vague. We return **nothing** instead of guessing.
3. **If no obvious title exists, search** Wikipedia. Take the first result that isn't a disambiguation page and actually resembles the name. `zelensky` finds `Volodymyr_Zelenskyy`; `asdfgh qwerty` finds nothing.
4. **Remember every answer** in `data/cache/wiki_resolve.json`, including "nothing found", so no name is looked up twice. Network errors are not remembered, so those names are retried next time.

### Step 2: English article → the same article in other languages (`wiki_resolve.py`)

The English title is looked up on Wikidata, which says what the article is called in each language. 50 titles take two requests. Some topics have no article in some languages; the entity then simply has fewer languages. Answers are remembered in `data/cache/wiki_links.json`.

### Step 3: daily numbers for each article (`wiki.py`)

- **Pageviews:** one request per article covers the whole date range.
- **Edits:** we ask the article's own language site for its revision history in the date range and count edits per day. Very active articles need more than one request.
- **Days with nothing are recorded as 0.** But Wikimedia publishes pageviews about a day late, so for the last day or two a missing number might just mean "not published yet". Those rows have `complete = False` and are fetched again on the next run.
- **Everything is saved per article** under `data/raw/wiki/`. Running again only fetches what's missing or not yet complete, so you can stop a long run and start it again.

### Step 4: combine languages (`wiki.py`)

Complete rows are added together by Wikidata ID, so `wiki_entity_daily.parquet` has one row per entity and day: total views, total edits, and a column for each language.

### Top-1000 lists (`wiki_top.py`)

For each language and day we download two lists, all devices and desktop only, and clean them:

- **Remove pages that aren't articles:** the main page, search pages, help pages and so on. Each language names these differently (`Special:`, `特別:`, `Spezial:`…), so we read the names from each site rather than hard-coding English ones.
- **Flag likely bots.** Real readers are mostly on phones. A page whose readers are 90%+ on desktop is almost always being hit by an automated script, as with `JSON-LD` or `List_of_S&P_500_companies`. Those rows get `suspect_bot = True`. About 0.4–1.6% of rows are flagged, depending on the language. **Rows are flagged, not deleted.**

### Being polite to Wikimedia (`wiki_client.py`)

Since 2026, Wikimedia allows clients like ours (no login, identified by a User-Agent) **200 requests per minute in total, across all its sites, with at most 3 at the same time**. Their [rate limit page](https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits) has the details. The client stays just under this:

- 190 requests/minute, shared by everything (configurable with the `UC_WIKI_REQUESTS_PER_MINUTE` environment variable);
- at most 3 requests at once;
- if Wikimedia says "too many requests", everything pauses for as long as it asks (at least 5 seconds);
- every request identifies us with `config.WIKIMEDIA_USER_AGENT`.

**What this means for timing:**

| Job | Requests | Time |
|---|---|---|
| Top-1000, 8 languages, 60 days | about 960 | about 5 minutes |
| Resolve 1,000 names | about 60–400 (depends how many need a search) | 1–3 minutes |
| Pageviews and edits, 1 entity, 8 languages | about 16 | about 5 seconds |
| …1,000 entities | about 16,000 | **about 1.5 hours** |
| …3,000 entities | about 48,000 | **about 4.5 hours** |

So **choose entities carefully**, start the big fetch early in the background, and use `--langs` or `--what pageviews` to cut the work if time is short.

### Why we don't download Wikipedia's bulk files

Wikimedia publishes complete daily pageview files, but one day across all languages is about 700 MB, so 60 days is about 42 GB. That's too much for hackathon wifi, when we only need a few thousand articles. Asking for exactly the articles we need, and keeping them on disk, gives us the same data in a few MB. **Once fetched, everything works offline**, including training and the demo.

---

## What you get (files)

Everything lives in `data/`, which is not in git. Share it through the team drive if needed.

### `data/processed/wiki_entity_daily.parquet`: **start here**

One row per entity per day, all languages added together. Only complete days are included.

| Column | Example | Meaning |
|---|---|---|
| `qid` | `Q43723` | Wikidata ID. If an article has no Wikidata entry, it's `en:<title>` instead. |
| `title_en` | `Benjamin_Netanyahu` | English title |
| `day` | `2026-10-28` | |
| `views` | `176981` | readers across all languages |
| `edits` | `9` | edits across all languages |
| `n_langs` | `8` | how many of the 8 languages have an article on this entity |
| `views_en` … `views_zh` | `124772` | readers in each language (empty if there's no article in that language) |
| `edits_en` … `edits_zh` | `2` | edits in each language |

### `data/processed/wiki_pageviews.parquet` and `wiki_edits.parquet`

The per-language detail: `lang, title, day, views` (or `edits`), plus `complete`. Note that the same title can exist in several languages (`Knesset` is the same in en, de, fr and it), so always filter by `lang` as well as `title`.

### `data/processed/wiki_top.parquet`

`lang, day, rank, title, views, desktop_share, suspect_bot`. Use `df[~df.suspect_bot]` to drop likely bots.

### Caches (you don't normally need these)

| File | What |
|---|---|
| `data/cache/wiki_resolve.json` | name → English title, or `null` |
| `data/cache/wiki_links.json` | English title → Wikidata ID and the titles in each language |
| `data/cache/wiki_siteinfo/<lang>.json` | each language's main page and non-article prefixes |
| `data/raw/wiki/top/<lang>/<all-access or desktop>/<day>.json` | downloaded top-1000 lists |
| `data/raw/wiki/{pageviews,edits}/<hash>.json` | downloaded numbers per article |

To start again from scratch, delete the relevant cache and run the command again.

---

## How to run it

All commands run from the repo root. Each one is safe to stop and run again.

```bash
# 1. Top-1000 lists. Needs no names. Run any time; it only downloads days it doesn't have.
uv run python -m undercurrent.ingest.wiki_top --days 60

# 2. Names → English titles (also links them to the other languages).
#    names.txt: one name per line, e.g. the top people/orgs of each story.
uv run python -m undercurrent.ingest.wiki_resolve --names-file data/processed/story_entities.txt \
    --out-titles data/processed/wiki_titles.txt

# 3. Daily pageviews and edits in all 8 languages, then the combined entity table.
uv run python -m undercurrent.ingest.wiki --titles-file data/processed/wiki_titles.txt --days 60
```

Useful options (every command has `--help`):

- `--days N`, or `--start YYYY-MM-DD --end YYYY-MM-DD`. The default is the last 7 days, ending yesterday.
- `--langs en de fr` to use fewer or other languages.
- `--what pageviews` (in `wiki`) to skip edits and roughly halve the time.
- `--retry-misses` (in `wiki_resolve`) to look up names that previously found nothing.

Or from Python:

```python
from undercurrent.ingest.wiki_resolve import resolve, resolve_entities
from undercurrent.ingest import wiki

resolve("barack obama")                 # "Barack_Obama"
resolve_entities(["barack obama"])      # {"barack obama": {"qid": "Q76", "title": "Barack_Obama",
                                        #   "titles": {"en": "Barack_Obama", "ja": "バラク・オバマ", ...}}}
wiki.fetch(["Barack_Obama"], start, end)   # downloads, then rebuilds the three tables
```

---

## How the other workstreams should use it

### B: Stories

1. **For each story, pick its top entities** (for example the 5 most-mentioned people and organisations) and resolve them with `resolve_entities(names)`. Use the Python call, not the CLI: it's cached and fast after the first time.
2. Put the English title in `Entity.wikipedia_title` in the export. Leave it empty when nothing was found; never guess.
3. **Write the resolved titles to `data/processed/wiki_titles.txt`** and run step 3 above, or ask A to. Don't send every GDELT name. There are hundreds of thousands, and at about 5 seconds per entity it would take days.
4. When exporting, read `wiki_entity_daily.parquet`:
   - `Entity.pageviews_7d` / `edits_7d`: the sum of `views` / `edits` over the 7 days ending on the snapshot day.
   - `TimelinePoint.wiki_pageviews`: the sum of `views` over the story's entities for that day, or empty if there's no data.
5. Be careful with countries and capital cities: they're read so much anyway (`United_States`, `London`) that they drown out the signal. Leave them out of story totals, or give them low weight.

### C: Forecast

1. **Read only the parquet files**, never the internet, so your training is repeatable and works offline.
2. **No peeking into the future.** For a forecast made on day t, use only rows with `day <= t`. Strictly, pageviews for day t aren't published until about day t+1, so the most honest choice is `day <= t - 1` for pageviews. Edits are known straight away. Pick one rule, use it everywhere, and write it in your Notes in `PLAN.md`.
3. **Suggested features**, per story, from `wiki_entity_daily`:
   - **growth:** `log2((views over the last 7 days + 1) / (views over the 7 days before + 1))`, and the same for edits;
   - **acceleration:** this week's growth minus last week's;
   - **spread:** how many languages show rising views (from the `views_<lang>` columns);
   - **non-English share:** views outside English divided by total views; a rising share suggests the story is spreading internationally;
   - **top-1000 presence:** how many of the story's entities are in a top-1000 list that day (`wiki_top`, with bots removed).
4. **Missing isn't zero.** An entity with no rows was never fetched; treat it as missing (NaN). The gradient boosting model handles NaN natively.

### D: Web and API

- **The web app never talks to Wikipedia.** Everything arrives through B's export, so the demo works offline.
- Link to an entity's article with `https://en.wikipedia.org/wiki/<wikipedia_title>`.
- The Wikipedia numbers can be empty (`null`); show them as "–".

### A: Ingest (looking after this code)

- Keep column names stable. Adding columns is fine; tell B and C before renaming or removing anything.
- Tests: `uv run pytest pipeline/tests/test_wiki*.py`. That's 30 tests; they run offline in about 1 second using a fake Wikimedia server.

---

## Known limitations

- **Redirects are counted separately.** Someone who reads `Netanyahu` (a redirect to `Benjamin_Netanyahu`) is counted under `Netanyahu`, and we don't add those views in. Totals are a bit low for entities that are often reached through a short name.
- **Renamed articles lose their history.** Views are recorded under the title at the time, so if an article was renamed recently, its earlier views sit under the old title.
- **New articles start at zero.** For example, the Japanese article on the 2026 Israeli election was only created the day after the vote. This is correct, but it means `n_langs` counts languages that have an article *today*, not on each day.
- **Only desktop bots are caught.** Automated traffic disguised as mobile (for example `Hanuman_Ansh`) isn't flagged.
- **The name matcher prefers "don't know" to a wrong answer.** Vague names and some acronyms come back empty. It can still occasionally choose the wrong article for a single common word, so check the top stories by eye.
- **Big topics dominate totals.** `Donald_Trump` gets far more views than most entities. Compare growth rates, not raw counts.
- **Recent days arrive late.** Pageviews for the last 1–2 days, and yesterday's desktop top-1000, may not be published yet. Running again the next day fills them in.
- **8 languages only.** Arabic, Persian, Ukrainian, Hebrew and others matter for geopolitics but aren't in the top 8 by readership. Add them with `--langs` if needed. Each one adds about 2 requests per entity.
