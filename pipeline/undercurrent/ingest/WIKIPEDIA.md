# Wikipedia data

**Owner:** A (Ingest) · **Code:** `pipeline/undercurrent/ingest/wiki*.py` · **Tests:** `pipeline/tests/test_wiki*.py`

> **Scale-up:** this doc is sized for a few thousand entities and 60 days. For 12+ months of stories, per-entity calls are too slow. At that scale use entity tiers, bulk pageviews (`wiki_bulk.py`) and a long top-1000 history (`wiki_top.py`).

## In one paragraph

When something is about to become big news, people start looking it up. They read the Wikipedia articles on the people, places and organisations involved, and editors start updating those articles. This code collects that signal. For each **entity** in our stories (a person, an organisation, a place), it records **how many people read its Wikipedia article each day, and how many edits it got**. It does this in the 8 most-read Wikipedia languages, and it also saves each language's daily list of its 1,000 most-read articles. Reading and editing often pick up a few days before a story peaks in the news, and that early warning is what the forecast is looking for.

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
| **Wikidata ID (QID)** | A language-independent ID for a topic. `Q76` is Barack Obama in every language. We use it to find the same article in Japanese (`バラク・オバマ`), Russian (`Обама,_Барак`), and so on. |
| **Entity** | One topic (one QID), with its articles in all 8 languages added together. |
| **Day** | A UTC calendar day. |

**Languages:** `en` English, `ja` Japanese, `de` German, `fr` French, `ru` Russian, `es` Spanish, `it` Italian, `zh` Chinese. These were the 8 most-read Wikipedias in October 2026. The list is `LANGUAGES` in `wiki_client.py`, and every command accepts `--langs` to use a different set.

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
        │  3. fetch: daily pageviews and edits for each of the 8 articles
        ▼
wiki_pageviews.parquet / wiki_edits.parquet     one row per language, article and day
        │  4. combine: add the languages together
        ▼
wiki_entity_daily.parquet                        one row per entity and day
```

Separately, and with no names needed, `wiki_top` downloads each language's top-1000 list for every day.

### Step 1: name → English article (`wiki_resolve.py`)

GDELT gives us names like `barack obama`, `un` or `john smith`. For each one:

1. **Try the obvious titles first:** `Barack Obama`, and `UN` for short names. If the title is a redirect, follow it (`UN` → `United_Nations`). About 16 names are checked in a single request.
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

One row per entity per day, all languages added together. Only complete days are included. The examples are a real row (Barack Obama, 4 November 2026).

| Column | Example | Meaning |
|---|---|---|
| `qid` | `Q76` | Wikidata ID. If an article has no Wikidata entry, it's `en:<title>` instead. |
| `title_en` | `Barack_Obama` | English title |
| `day` | `2026-11-04` | |
| `views` | `111251` | readers across all languages |
| `edits` | `3` | edits across all languages |
| `n_langs` | `8` | how many languages we have pageviews for: those with an article on this entity, or 1 for a `wiki_bulk` tier-2 entity (English only) |
| `views_en` … `views_zh` | `75834` (en) | readers in each language (empty if there's no article in that language) |
| `edits_en` … `edits_zh` | `0` (en) | edits in each language |

### `data/processed/wiki_pageviews.parquet` and `wiki_edits.parquet`

The per-language detail: `lang, title, day, views` (or `edits`), plus `complete`. The same title can exist in several languages (`Barack_Obama` is the title in English, German, French, Spanish and Italian), so always filter by `lang` as well as `title`.

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
#    The names file has one name per line, e.g. the top people/orgs of each story.
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

resolve("barack obama")  # "Barack_Obama"
resolve_entities(["barack obama"])  # {"barack obama": {"qid": "Q76", "title": "Barack_Obama",
#   "titles": {"en": "Barack_Obama", "ja": "バラク・オバマ", ...}}}
wiki.fetch(["Barack_Obama"], start, end)  # downloads, then rebuilds the three tables
```

---

## Two years, on several machines (`wiki_names` + `wiki_bulk`)

For the two-year GDELT run, entity names come from the articles themselves, not from stories (which don't exist until tracking runs), and the work is split across machines.

**Why split it:** Wikimedia's limit is about 200 requests a minute **per client**, and anonymous clients are told apart by IP address. Machines on **different networks** (home broadband, a phone hotspot, a cloud VM) each get their own budget. Machines behind the **same router or the same hackathon wifi share one budget** and gain nothing. Use one machine there, or set `UC_WIKI_REQUESTS_PER_MINUTE=47` on each of 4.

```bash
# 1. ONE machine: pick names. Counts each day once (resumable), then picks each ISO week's
#    top 50 people and top 50 orgs by mentions (weighted by dup_count). The top 15 of each are tier 1.
#    Run it again as more days land; only new days are counted.
uv run python -m undercurrent.ingest.wiki_names --bucket gs://undercurrent-data-cooper   # needs gcloud + read access
uv run python -m undercurrent.ingest.wiki_names                                          # or: local data/processed/articles
#    -> data/processed/wiki_names.parquet (small). Copy it to the same path on every machine.

# 2. EVERY machine i of n (0-based). Resumable: just run it again if it stops.
uv run python -m undercurrent.ingest.wiki_bulk fetch --shard 0/4
#    -> data/wiki_shard-0-of-4.tar.gz

# 3. ONE machine: merge everyone's tarballs and build every table (no network).
uv run python -m undercurrent.ingest.wiki_bulk merge wiki_shard-*-of-4.tar.gz
```

What each machine fetches (`fetch --shard i/n`). The default range is five years, 2021-10-10 (section 10's warm-up) to 2026-11-06 (the last GDELT day). It's a fixed date, not "yesterday": an article's cache is reused only if it covers the whole requested range, so a moving end date (or a start moved earlier later on) would refetch every cached article on every run. Keep the same `--start`/`--end` for the whole run.

| Step | What | Requests |
|---|---|---|
| Top-1000 | 8 languages × 2 lists, on the days where `day.toordinal() % n == i` | about 12,000 in total for two years, split n ways |
| Resolve | Its names (`sha1(name) % n == i`), linked to all 8 languages | about 0.5 per name |
| Tier 1 | Pageviews in all 8 languages, then English edits | about 6 + 3 per entity |
| Tier 2 | English pageviews only | 1 per entity |

`wiki_names` prints an estimate of the total for the names it picked. Change `--top` / `--tier1` to trade coverage for time. One pageviews request covers the whole two years, so the cost is per entity, not per day.

**Extra outputs:**

- `data/processed/wiki_names.parquet`: `week, kind, name, mentions, rank, tier`.
- `data/processed/wiki_selection.parquet` (written by `merge` / `build`): `name, kind, tier, first_week, tier1_week, mentions, title_en, qid`. `tier1_week` is the first week the name was tier 1 (empty if never). Its 8 languages and edits only count from the end of that week, because `tier` is the best over every week, including later ones.

**Point-in-time (C):** a name is chosen by one week's mentions, never by what happened later. But its pageviews are fetched for the whole range, including weeks before it was chosen. So "this entity has data" quietly says "it got big at some point". For a forecast made on day t, use only entities whose `first_week` ended by t.

**Not covered:** places (GDELT `locations`), and names that never reach a weekly top 50. When stories exist, their top entities can be fetched the usual way (`wiki --titles-file`). Everything already cached is skipped.

---

## How the other workstreams should use it

### B: Stories

**Done: `stories/wiki_entities.py`** links each story to its Wikipedia entities and writes `story_wiki_entities.parquet` (`story_id, qid, full_from`, plus columns for people). `scripts/merge.sh` runs it before the forecast. What it does and why each rule is there: `docs/ARCHITECTURE.md`, "Wikipedia features, end to end". In short:

1. It reads `wiki_selection.parquet` and `wiki_entity_daily.parquet` from `gs://undercurrent-data-cooper/wiki/`. It needs no network.
2. It matches the GDELT names in a story's first articles to `wiki_selection.name`, normalised the same way `wiki_names` counts them (`normalise_name`: lower case, single spaces).
3. It keeps the 5 entities named in the most articles, each in at least 2. An entity counts only once its `first_week` has ended.
4. It leaves out countries, capitals, dateline cities, seats of government, media and platforms, and generic or wrong matches (`excluded()`). They're read so much anyway (`United_States`, `Reuters`) that they drown out the story's own signal.

**Still to do: the exporter.** Fill these from `story_wiki_entities` and `wiki_entity_daily.parquet`:

- `Entity.wikipedia_title`: `title_en`. Leave it empty when there's no match; never guess.
- `Entity.pageviews_7d` / `edits_7d`: the sum of `views` / `edits` over the 7 days ending on the snapshot day.
- `TimelinePoint.wiki_pageviews`: the sum of `views` over the story's entities for that day, or empty if there's no data.

Use the same `full_from` rule as the forecast, or the globe will show numbers the model wasn't allowed to see.

**Names outside the selection** (places, smaller names): resolve them with `resolve_entities(names)`, write the titles to `data/processed/wiki_titles.txt`, then run `wiki --titles-file` (step 3 above; about 5 seconds per entity). Don't send every GDELT name: there are hundreds of thousands. These are for the globe only. `stories/wiki_entities.py` uses only names in the selection, because those have a `first_week` to check. An entity fetched this way would need its own point-in-time rule before the forecast could use it.

### C: Forecast

**Done.** `forecast/panel.py` (`wiki_sql`) sums each story's entities per day, and `forecast/features.py` turns that into five features: `wiki_log_views7`, `wiki_growth_7d`, `wiki_edit_growth_7d`, `wiki_non_en_share`, `wiki_views_per_article`. The rules:

1. **Read only the parquet files**, never the internet, so training is repeatable and works offline.
2. **No peeking into the future.** A forecast made on day t uses pageviews up to t − 1 (they're published about a day late) and edits up to t (`settings.WIKI_VIEWS_LAG_DAYS`, `WIKI_EDITS_LAG_DAYS`).
3. **8 languages and edits only from `full_from`.** A fetches 8 languages and edits for the whole history of any entity that was ever a weekly top 15 (tier 1), so before the week it got there they'd give away its later prominence. Before `full_from` the panel uses English views (`views_en`, fetched the same way for every entity) and edits are unknown. This means `wiki_views` jumps when an entity goes tier 1: the jump is real information on that day, not a leak.
4. **Only the days around the story** are joined (43 before its first article, 7 after its last). No feature looks back further, and it keeps the full merge to about 10M rows instead of 300M.
5. **Missing isn't zero.** A story with no entities, or a day with no rows, is NaN. The gradient boosting model handles NaN natively.

Not built yet, and worth trying: the number of languages with rising views (from the `views_<lang>` columns, only after `full_from`), and top-1000 presence (`wiki_top`, bots removed).

### D: Web and API

- **The web app never talks to Wikipedia.** Everything arrives through B's export, so the demo works offline.
- Link to an entity's article with `https://en.wikipedia.org/wiki/<wikipedia_title>`.
- The Wikipedia numbers can be empty (`null`); show them as "–".

### A: Ingest (looking after this code)

- Keep column names stable. Adding columns is fine; tell B and C before renaming or removing anything.
- Tests: `uv run pytest pipeline/tests/test_wiki*.py`. That's 30 tests; they run offline in about 1 second using a fake Wikimedia server.

---

## Known limitations

- **Redirects are counted separately.** Someone who reads `UN` (a redirect to `United_Nations`) is counted under `UN`, and we don't add those views in. Totals are a bit low for entities that are often reached through a short name.
- **Renamed articles lose their history.** Views are recorded under the title at the time, so if an article was renamed recently, its earlier views sit under the old title.
- **New articles start at zero.** An article written after an event (common in non-English Wikipedias, which often follow English) shows no views before it was created. This is correct, but it means `n_langs` counts languages that have an article *today*, not on each day.
- **Only desktop bots are caught.** Automated traffic disguised as mobile (for example `Hanuman_Ansh`) isn't flagged.
- **The name matcher prefers "don't know" to a wrong answer.** Vague names and some acronyms come back empty. It can still occasionally choose the wrong article for a single common word, so check the top stories by eye.
- **Big topics dominate totals.** `Donald_Trump` gets far more views than most entities. Compare growth rates, not raw counts.
- **Recent days arrive late.** Pageviews for the last 1–2 days, and yesterday's desktop top-1000, may not be published yet. Running again the next day fills them in.
- **8 languages only.** Portuguese, Arabic, Persian, Ukrainian and others matter for geopolitics but aren't in the top 8 by readership. Add them with `--langs` if needed. Each one adds about 2 requests per entity.
