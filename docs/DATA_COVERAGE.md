# Data coverage: which sections are done, and which are free

GDELT, split into 6-month **sections**. Each person or agent takes a free section and runs it end to end (pull → embed → track). **Before starting one, put your name and "started" in its row, and push straight away**, so nobody else takes it.

Start a section from Cloud Shell, with its number in place of `N`:

```bash
rm -rf undercurrent && git clone -q https://github.com/CooperMcCloskey/undercurrent.git && bash undercurrent/scripts/gcp/create_vm.sh N
```

Results go to `gs://undercurrent-data-cooper`. Send Cooper your VM's service account (the script prints it) for write access.

| Section | Own days | Warm-up from | Who | Pulled | Embedded | Tracked | In the frontend |
|---|---|---|---|---|---|---|---|
| **1** | 2026-05-08 → 2026-11-06 | 2026-04-10 | **Cooper** (`uc-gpu`, us-central1-c) | ✅ done | ✅ done (211/211 at 01:52 UTC) | ✅ done: tracked on the 0.80 cutoffs and the language filter, uploaded with `finish` (by about 05:35 UTC) | ✅ all 183 own days (2026-05-08 → 2026-11-06), loaded on Cooper's laptop 2026-11-08 ~05:45 UTC: English titles, junk left out; no forecasts yet; categories from before the theme-based ones |
| 2 | 2025-11-07 → 2026-05-07 | 2025-10-10 | **ControlThePancake** (`uc-gpu`, us-central1-b, `1025880049110-compute@…`) | ✅ done (210/210) | ✅ done (210/210 at 03:00 UTC) | ✅ done: fully tracked and processed (210/210 days) | ⬜ |
| 3a | 2025-05-08 → 2025-08-06 (91 days) | 2025-04-10 | **yeah-course** (RunPod GPU, `scripts/standalone/section.sh`; its tracker stops at 2025-08-06) | ✅ done | ✅ done (194/211: the other 17 are the GDELT outage, which has no headlines) | ✅ done: tracked to 2025-08-06 on the 0.80 cutoffs, the outage days skipped without aging stories (`14e6f78`), uploaded with `finish` | ⬜ |
| 3b | 2025-08-07 → 2025-11-06 (92 days) | 2025-07-10 | **ControlThePancake** (tracking only: articles and vectors for 2025-07-10 → 2025-11-06 are in the bucket) | ✅ done (pulled once for all of 3a and 3b, on yeah-course's RunPod GPU) | ✅ in the bucket (embedded with 3a) | ✅ done: tracked 120/120 to 2025-11-06 on 6b's server `a8d5…` (07:05 → 07:53 UTC, cutoff 0.80, about 24 s a day), uploaded with `finish` and checked with `verify` (`status/block-3b.done`, 07:56 UTC). The earlier runs on `a0fb…` (lost in its reset) and `6da8…` (stopped after 1 day) were never uploaded | ⬜ |
| 4a | 2024-11-07 → 2025-03-28 (142 days) | 2024-10-10 | **yeah-course** (`uc-gpu`, us-west1-a: no L4s in us-central1, `851470465448-compute@…`; its tracker stops at 2025-03-28) | ✅ done (210/210, pulled once for all of 4a and 4b) | ✅ done (210/210) | ✅ done: tracked 170/170 to 2025-03-28 by 06:00 UTC (cutoff 0.80, about 40 s a day), uploaded with `vm.sh finish` (`status/block-4a.done`) | ⬜ |
| 4b | 2025-03-29 → 2025-05-07 (40 days) | 2025-03-01 | **ControlThePancake** (tracking only, from the bucket: `extra_section.sh 4b`, on the 2× L40S server `6da8…`. An earlier start on `f365…` (06:07 UTC) never tracked: its fetch stopped at 27 of 68 days. Don't run `track` or `finish` there) | ✅ done (with 4a) | ✅ in the bucket (with 4a) | ✅ done: tracked 68/68 to 2025-05-07 on `6da8…` (06:33 → 06:52 UTC, cutoff 0.80, about 17 s a day), uploaded with `finish` and checked with `verify` (`status/block-4b.done`, 06:54 UTC) | ⬜ |
| 5a | 2024-05-08 → 2024-08-06 (91 days) | 2024-04-10 | **Lucy** (2× A100 PCIe, 32 vCPU server, `scripts/standalone/section.sh`) | ✅ done (119/119) | ✅ done (119/119) | ✅ done: tracked on the 0.80 cutoffs, uploaded with `finish` and checked with `verify` (by 05:40 UTC) | ⬜ |
| 5b | 2024-08-07 → 2024-11-06 (92 days) | 2024-07-10 | **ControlThePancake** (6b's server `a8d5…`, in its own home: `extra_section.sh 5b`) | ✅ done (120/120, in the bucket) | ✅ done (120/120, in the bucket) | ✅ done: tracked 120/120 to 2024-11-06 on `a8d5…` (resumed 06:28 UTC; the move to `a0fb…` was lost with that server), uploaded with `finish` and checked with `verify` (`status/block-5b.done`, 07:16 UTC) | ⬜ |
| 6a | 2023-11-07 → 2024-02-06 | 2023-10-10 | **ControlThePancake** (extra GPU server, `scripts/standalone/section.sh`) | ✅ done | ✅ done | ✅ done: tracked on the 0.80 cutoffs, uploaded with `finish` and checked with `verify` (by 05:23 UTC) | ⬜ |
| 6b | 2024-02-07 → 2024-05-07 | 2024-01-10 | **ControlThePancake** (a GPU server outside Google Cloud, `scripts/standalone/section.sh`) | ✅ done (119/119) | ✅ done (119/119) | ✅ done: tracked on the 0.80 cutoffs, uploaded with `finish` and checked with `verify` (by 05:23 UTC) | ⬜ |
| 7a | 2023-05-08 → 2023-08-06 (91 days) | 2023-04-10 | **yeah-course** (second RunPod GPU, `scripts/standalone/section.sh`) | ✅ done | ✅ done | ✅ done (2023-08-06 at 05:06 UTC, cutoff 0.80) | ⬜ |
| 7b | 2023-08-07 → 2023-11-06 (92 days) | *none: continues 7a's timeline* | **yeah-course** (same RunPod GPU as 7a; taken over from ControlThePancake) | ✅ done (pulled once with 7a) | ✅ done (embedded with 7a) | ✅ done: tracked to 2023-11-06 at 06:02 UTC, carrying on from 7a's tracker state (no seam at 2023-08-07), uploaded with `finish` | ⬜ |
| 8 | 2022-11-07 → 2023-05-07 | 2022-10-10 | **ControlThePancake** (2× L40S server, 32 vCPU, `scripts/standalone/section.sh`) | ✅ done (210/210) | ✅ done (210/210) | ✅ done: tracked on the 0.80 cutoffs, uploaded with `finish` and checked with `verify` (by 05:23 UTC) | ⬜ |
| 9a | 2022-05-08 → 2022-08-06 (91 days) | 2022-04-10 | **ControlThePancake** (6a's server `f365…`, L4, in its own home: `extra_section.sh 9a`). Section 9 is split in two halves like 5, each pulled, embedded and tracked on its own; the earlier claim of all of 9 on `f365…` never ran (06:33: no section 9 there) | ⏳ pulling on `f365…` (48 CPUs, L4) since about 06:58 UTC: 34/119 at 07:11 (about 2.6 days a minute, so pulled by about 07:45) | ⏳ 14/119 at 07:11 | ⏳ tracks right behind the embedding (1/119 at 07:11); not ready for the 07:00 merge | ⬜ |
| 9b | 2022-08-07 → 2022-11-06 (92 days) | 2022-07-10 | **ControlThePancake** (Google Cloud `uc-gpu`, us-central1-b: block 2's VM, now free, as its own user `s9b` with `scripts/standalone/section.sh`; block 2's files there are untouched) | ⏳ pulling on `uc-gpu` (zip files, not BigQuery): 50 new days in the bucket by 07:22 UTC (2022-07-10 → 2022-08-28) | ⏳ 30 by 07:22 (2022-07-10 → 2022-08-08) | ⏳ tracks right behind the embedding; not ready for the 07:00 merge | ⬜ |
| 10 | 2021-11-07 → 2022-05-07 | 2021-10-10 | **ControlThePancake** (the 2× L40S server `6da8…`, in its own home: `extra_section.sh 10`. It was on the 32-core server `a0fb…`, and was lost with it when that server was reset after 06:00 UTC; nothing of 10 was in the bucket) | ⏳ pulling again from scratch on `6da8…` since 06:56 UTC (8 embedders on both GPUs): 79/210 at 07:27; 16 pull workers from 07:27 (was 8: about 320 days an hour, twice as fast), so pulled by about 07:55 | ⏳ right behind the pull (74/210 at 07:25) | ⏳ tracks right behind the embedding (67/210 at 07:25, about 15 s a day); not ready for the 07:00 merge | ⬜ |
| Pre-10 (pull only) | 2020-01-01 → 2021-10-09 (648 days: everything before section 10's first warm-up day) | | **ControlThePancake** (`ingest.gdelt_zip` in 30 chunks of 21–22 days, tmux `gdelt-01` … `gdelt-30`, each into its own `data/gdelt_chunks/NN/` and synced to the bucket) | ⏳ claimed 2026-11-08 07:25 UTC | ⬜ not planned yet | ⬜ not planned yet (tracking from 2020-01-01 would need warm-up days from 2019-12-04) | ⬜ |
| **Gap** (inside 3) | 2025-06-15 → 2025-07-01 (17 days), plus 2025-06-14 after 17:45 UTC and 2025-07-02 before about 02:15 UTC | | *nobody: a GDELT outage* | ❌ 0 articles. GDELT has no files for these days: `gdelt_files/<day>.json` lists 0 per feed, and the zip URLs return 404. Not recoverable from the zip files; BigQuery not checked | n/a | n/a | n/a |

**"In the frontend"** means that section's days are in an export loaded into the web app (`data/export/` on the demo laptop): ⬜ not yet, ◐ some days, ✅ all its own days. Exports come from `vm.sh frontend` (one block) or the merge (every finished section, with forecasts). Update it when you load a new export.


- **Tracked:** only tracking started after the cutoffs were pushed (2026-11-08 03:45 UTC, `2ab8939`) counts; anything tracked before then has to be redone with `track-fresh`. Each section tracks only its own days, on its own server: `docs/SECTION_RUNBOOK.md`, "What gets tracked".
- **Sections 1–4 are two years**, the plan for tonight. **Sections 5–10** reach back five years, for whoever has a free GPU.
- **Order:** newest free section first. Each extra section helps the forecast less than the one before.
- **Teammates already running:** two teammates' VMs have bucket access (`196306351948-compute@…` and `1025880049110-compute@…`, the second is section 2). If the first is yours, write your section in its row now. `docs/ONE_YEAR_EACH.md` (year-long blocks) is superseded: nobody started from it, and its dates would overlap these sections.
- **Section 6 is split in two** (6a and 6b, three months each), because 6a runs on a server outside Google Cloud. Start 6a, 6b or 5 on any GPU server with `scripts/standalone/section.sh` (`docs/PREVIOUS_YEAR.md`); `create_vm.sh 6` would redo 6a, so don't use it.
- **Section 3 is split for tracking only** (3a/3b), with two machines tracking in parallel from articles and vectors pulled and embedded once. Section 3b fetches from the bucket with `bash ~/undercurrent/scripts/standalone/extra_section.sh 3b`, starting at its own warm-up on 2025-07-10; it keeps fetching and tracking as days arrive (`docs/SECTION_RUNBOOK.md`, "Tracking-only sections"). Cooper: this adds a seam at 2025-08-07. **Section 7 now continues on one machine:** yeah-course finished 7a and is tracking 7b from the same saved state, with no seam at 2023-08-07. Do not start a separate 7b run on ControlThePancake's server.
- **Section 4 is split the same way** (4a/4b, 2026-11-08 ~05:40 UTC), because its VM tracks at about 41 s a day: yeah-course's VM stops at 2025-03-28, and ControlThePancake tracks 4b from the bucket, from its warm-up on 2025-03-01. `section.sh` and `extra_section.sh` don't know 4b yet: add `4b) echo "2025-03-01 2025-03-29 2025-05-07" ;;` to `days_of` in `section.sh`, and `4b` to the tracking-only checks (`^[37]b$` → `^[347]b$`) in both scripts. Cooper: this adds a seam at 2025-03-29.
- **The gap is missing data, not quiet news.** The day files exist but are empty, so code that counts articles will see every story drop to 0 for 17 days. Stories (B) and Forecast (C): treat 2025-06-14 → 2025-07-02 as missing, and don't train on targets or features whose 7-day windows touch it. `wiki_names` already skips empty days.
- **Own days never overlap.** The warm-up days (the 28 days before a section, used only to start its tracker) may overlap the next section's own days. That's expected.

## Next, in order (as of 2026-11-08 02:00 UTC)

1. **Sections 3 and 4 are claimed by yeah-course**, completing ownership of the two-year range. See the table above for progress; do not start duplicate pulls.
2. **Confirm that BigQuery has the translated feed.** It's about 73% of articles, and not checked yet for sections 1 and 2. On a section's VM: `bash ~/undercurrent/scripts/gcp/vm.sh feeds` (translated should be about 2× English). Or compare the bucket's 2026-11-05 (inside section 1) with the zip-file test day below: 276,486 articles, 73% translated.
3. **Cooper: "cutoffs pushed"** once a week of section 1 is embedded. Then every running section starts tracking (`vm.sh track`, or `section.sh track`).
4. **Wikipedia top-1000 for the whole range: running** on ControlThePancake's laptop (started about 01:10 UTC; 4 of 8 languages done at 01:55). **Nobody else run it:** Wikimedia's limit is shared by everyone running our code.
5. **Older data, once 1–4 are covered:** 6a and 6b are pulled and embedded, 5 is Cooper's, and 8–10 are ControlThePancake's (starting 03:00 UTC). **7 is now claimed by yeah-course** on a second RunPod GPU; no sections are free.
6. **When every section is tracked:** Cooper merges, trains and exports (`docs/TWO_YEAR_RUN.md`, "Cooper only"). Then the Wikipedia pageviews and edits for the stories' top entities (`README.md`, "Wikipedia"). Most of these will already be cached by Lucy's run (below), which picks entities from the articles as they land, so that step only fetches the leftovers, such as places.

**Check what's really in the bucket** (Cloud Shell). This prints each continuous range of pulled days; a gap is a section nobody has pulled. Swap `articles/` for `vectors/news/` (embedded days) or `stories_daily/` (tracked days).

```bash
gcloud storage ls gs://undercurrent-data-cooper/processed/articles/ | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}' | sort -u | python3 -c "import sys, datetime as d; ds = [d.date.fromisoformat(l.strip()) for l in sys.stdin]; r = []; [r.append([x, x]) if not r or (x - r[-1][1]).days > 1 else r[-1].__setitem__(1, x) for x in ds]; [print(a, '->', b, (b - a).days + 1, 'days') for a, b in r]"
```

## The final merge (one command)

**Schedule (2026-11-08, UTC), as it stands at 09:10.** Submission is at **12:00**. The merge (trial and final in one) is running on the 2× A100 pod.

| By | What | Who |
|---|---|---|
| **as soon as it's done** | `export-final.tgz` is in the bucket (`merge.sh status` says `done`). **Post "merge done" with the backtest table** | whoever runs the merge |
| **+10 min** | Cooper loads it: download it, then `bash scripts/load_export.sh` (prints the model card; keeps the old export as a fallback). A backup laptop does the same | Cooper and one other |
| **10:30: go / no-go** | If `export-final.tgz` isn't loaded and working on the demo laptop by 10:30, **present with block 1's export** (already loaded) and stop waiting. Re-shoot screenshots only if the new one is in | Cooper |
| 10:30 → 11:30 | Presentation, a short screen recording of the demo, Devpost. Rehearse once with wifi off: the app needs no internet | everyone |
| **12:00** | Submit | |

**If the export step fails, or is too big or slow:** don't redo the hour of download and training. `merge.sh export` redoes only the export and upload, in minutes, from what the merge already downloaded and trained. To export just the last year (smaller and faster, and what the globe's loading is tuned for):

```bash
git -C ~/undercurrent pull -q && START=2025-11-07 bash ~/undercurrent/scripts/merge.sh export
```

A failed contract check no longer stops the upload. `merge.sh status` flags it, and Cooper decides whether to load it.

**Fallback:** demo with the block 1 export already on Cooper's laptop. It has real stories (2026-05-08 → 11-06) and English titles, but no forecasts and the old categories. Undo a bad load with `rm -rf data/export && mv data/export.prev data/export`.

**Full instructions for whoever trains it** (machine, setup, what each step does, how to read the backtest table): **`docs/TRAINING.md`**.

**The command.** **One person** runs it on the machine with the most CPUs that can read the bucket (a 32-vCPU section server is ideal; a Google Cloud VM in us-central1 downloads fastest):

```bash
git -C ~/undercurrent pull -q && bash ~/undercurrent/scripts/merge.sh start
```

- It runs in tmux, so it survives SSH closing. Progress: `bash ~/undercurrent/scripts/merge.sh status`.
- **What it does:**
  1. downloads every finished section's story tables, articles and totals;
  2. trains the forecast on all of them, printing model vs baseline;
  3. exports the globe data (English titles, theme categories, junk left out, forecasts) and checks it against the contract;
  4. uploads **`export-final.tgz`** and the forecast files to the bucket.
- **Options:** `MODELS="hgb gru"` adds the neural net (on a GPU), and `START=2025-11-07` exports only the last year if the full span is too big for the web app.
- On Cooper's VM it's `bash ~/undercurrent/scripts/gcp/vm.sh merge` from Cloud Shell, but its 4 vCPUs make it slow.
- **For the web laptop:** download `export-final.tgz` from the [Storage browser](https://console.cloud.google.com/storage/browser/undercurrent-data-cooper), unpack it as `data/export/`, then `npm run dev`. It's the only export needed: it replaces every earlier one.
- **Running it twice is cheap:** the second run (07:00) downloads only the sections that finished since the trial, then redoes the forecast and export.

## Other data

| What | Range | Who | Where |
|---|---|---|---|
| GDELT zip-file test days: articles, `gdelt_totals`, `gdelt_files` (1,232,548 articles, 237 MB; 71–77% translated) | 2026-11-01 → 2026-11-05 | ControlThePancake | that laptop, not the bucket. Inside section 1's days, so it's a cross-check for the BigQuery pull |
| Wikipedia `wiki_top.parquet` (8 languages; replaces Lucy's earlier 2025-11-07 → 2026-11-06 copy) | 2024-10-10 → 2026-11-07 | **ControlThePancake** | ⏳ running on that laptop, started 2026-11-08 about 01:10 UTC (4 of 8 languages done at 01:55). Lands in `data/processed/wiki_top.parquet` there; not in the bucket yet |
| Wikipedia pageviews and edits for each week's top GDELT people and orgs | 2021-10-10 → 2026-11-06 | **Lucy** | ✅ **in the bucket for B** (06:18 UTC 2026-11-08): `wiki/wiki_selection.parquet`, `wiki/wiki_entity_daily.parquet`, `wiki/cache/` (see "Wikipedia files for B" below). 1,761 entities with every day 2021-10-10 → 2026-11-06; names counted for every pulled day (2022-10-10 → 2026-11-06). Sections 9 and 10 are out of scope for now |
| Wikipedia pageviews and edits for story entities not covered above (places, smaller names) | | | after tracking (step 6 above) |
| *(add new data here)* | | | |

## Wikipedia from Lucy's laptop

**What it collects:** for every person and organisation that makes a weekly top 50 in GDELT, how many people read their Wikipedia article each day, and (for the top 15) how many edits it got, from 2021-10-10 to 2026-11-06. That covers all ten sections. The forecast uses these as features (`ingest/WIKIPEDIA.md`).

**Why it can run now, before tracking:** the names come straight from the articles in the bucket, not from stories. One request fetches an article's whole five years, so a name is fetched once, as soon as it first appears, and never again.

**How it runs.** As new days land in the bucket, every few hours:

```bash
# 1. Count names in the new days (days already counted are skipped), then re-pick each week's
#    top 50 people and top 50 orgs. Top 15 of each = tier 1, the rest tier 2.
uv run python -m undercurrent.ingest.wiki_names --bucket gs://undercurrent-data-cooper
# 2. Fetch Wikipedia data for any name not fetched before:
#    tier 1: daily pageviews in 8 languages + English edits; tier 2: English pageviews only.
uv run python -m undercurrent.ingest.wiki_bulk fetch --no-top
# 3. Rebuild every table. `fetch` doesn't rewrite wiki_selection.parquet, so without this the
#    selection lags behind step 1's new picks.
uv run python -m undercurrent.ingest.wiki_bulk build
# 4. Upload the files B needs (overwrites the previous copy; see "Wikipedia files for B").
gcloud storage cp data/processed/wiki_selection.parquet data/processed/wiki_entity_daily.parquet \
    gs://undercurrent-data-cooper/wiki/
gcloud storage cp data/cache/wiki_resolve.json data/cache/wiki_links.json \
    gs://undercurrent-data-cooper/wiki/cache/
```

Don't upload `data/processed/wiki_top.parquet` from Lucy's laptop. It only covers 2025-11-07 → 2026-11-06 and would overwrite the bucket's longer copy.

- **Dates are fixed** at 2021-10-10 → 2026-11-06 (the defaults in `wiki_bulk.py`). Don't change them mid-run: the cache only counts as done for the exact range, so a different range refetches everything.
- **`--no-top`:** the top-1000 lists are ControlThePancake's job (step 4 above), so this run doesn't fetch them.
- **Weeks that are only partly pulled** get a provisional top 50. The next run corrects it. A name fetched early that later drops out stays in the cache but isn't in `wiki_selection.parquet`, so it does no harm.
- **Speed:** Wikimedia allows about 190 requests a minute per IP address. Expect 5–10k names and a day or more from one laptop. **To help**, use a machine on a **different network**: a phone hotspot or home broadband, not the same wifi as Lucy. Write your name here first, and tell Lucy so she switches to `fetch --no-top --shard 0/2`. Copy `gs://undercurrent-data-cooper/wiki/raw` into your `data/raw/wiki` (so names Lucy already fetched are skipped), then run the same commands with `fetch --no-top --shard 1/2`. Send Lucy `data/wiki_shard-1-of-2.tar.gz` and she runs `wiki_bulk merge`.
- **Bucket access:** Lucy's account (`mccluskeylucy30@gmail.com`) can write only under `gs://undercurrent-data-cooper/wiki/`. That's why step 4 uses `gcloud storage cp`: a folder-limited grant can't cover *listing* the bucket, which `rsync` needs. The raw per-article cache isn't uploaded (B doesn't need it). It's packed in `data/wiki_shard-0-of-1.tar.gz` on Lucy's laptop, which `wiki_bulk merge` can restore on any machine.
- **Done when:** every section is pulled, `wiki_names` has counted every day, and a final `fetch` reports nothing new. Then `wiki_bulk build` writes the final `wiki_entity_daily.parquet` and `wiki_selection.parquet` to the bucket. The forecast should only use an entity from its `first_week` onwards (point-in-time).

### Wikipedia files for B (and C)

What happens to these files after the bucket (story links, the point-in-time rules, the forecast features): `docs/ARCHITECTURE.md`, "Wikipedia features, end to end".

These four files are everything B needs from the Wikipedia run. They were checked on 2026-11-08 from a clean folder with the network blocked: `resolve_entities()` reproduces the selection's Wikidata ID for all 2,066 names, `forecast/panel.py`'s join runs, and a story's `pageviews_7d` adds up. `gs://undercurrent-data-cooper/wiki/MANIFEST.json` lists their row counts and checksums.

| Bucket path | Put it in | Used for |
|---|---|---|
| `wiki/wiki_selection.parquet` | `data/processed/` | GDELT name → `qid`. Columns `name, kind, tier, first_week, tier1_week, mentions, title_en, qid` (`tier1_week` added 2026-11-08). `name` is GDELT's lowercase name; `kind` is `persons` or `orgs`, matching the article columns. 2,066 names, 1,773 with an article (1,679 entities) |
| `wiki/wiki_entity_daily.parquet` | `data/processed/` | `qid, title_en, day, views, edits, n_langs, views_<lang>…, edits_<lang>…`. 1,761 entities, **every day 2021-10-10 → 2026-11-06**, complete rows only. `day` is a date, not a timestamp |
| `wiki/cache/wiki_resolve.json`, `wiki/cache/wiki_links.json` | `data/cache/` | Lets `wiki_resolve.resolve_entities()` answer every name already looked up without the network, e.g. for the leftover fetch after tracking |

```bash
gcloud storage cp gs://undercurrent-data-cooper/wiki/wiki_selection.parquet \
    gs://undercurrent-data-cooper/wiki/wiki_entity_daily.parquet data/processed/
gcloud storage cp gs://undercurrent-data-cooper/wiki/cache/wiki_resolve.json \
    gs://undercurrent-data-cooper/wiki/cache/wiki_links.json data/cache/
```

B's job with them (`PLAN.md`, C's "Needs from others"):

1. ✅ `story_wiki_entities.parquet` (`story_id, qid`): `uv run python -m undercurrent.stories.wiki_entities --processed <dir>` (`stories/wiki_entities.py`; how it stays point in time is in `PLAN.md`, B's notes). It joins names to `wiki_selection.name` (any kind), counts an entity only once its `first_week` has ended, and leaves out countries, capitals, media, platforms and bad matches. The exclusion list is `excluded()` in that module.
2. In the export, fill `Entity.wikipedia_title` (`title_en`), `pageviews_7d` / `edits_7d` (sum over the 7 days ending on the snapshot day), and `TimelinePoint.wiki_pageviews`.
3. ✅ `scripts/merge.sh` copies `wiki_selection.parquet` and `wiki_entity_daily.parquet` into `data/merged/` and runs `stories.wiki_entities --processed data/merged` before the forecast. If that fails, the forecast runs without Wikipedia features. `WIKI=0` skips it. `forecast/panel.py` joins only the days around each story, and uses 8 languages and edits only from `full_from` (`PLAN.md`, C's notes).

Not in these files:

- **Names before 2022-10-10:** sections 9 and 10 are out of scope for now, so no names are picked before then. The pageviews themselves go back to 2021-10-10.
- **Places:** GDELT locations aren't covered.
- **Names that never made a weekly top 50.** For those, run `wiki --titles-file` after tracking. It needs the network.

## Bucket access

Who can write to `gs://undercurrent-data-cooper` (project `hackathon-514022`; Cooper grants it). Add yourself here once you've been granted, so the list stays complete.

| Who | Account | Role | Where in the bucket | Granted |
|---|---|---|---|---|
| Section 1's VM (Cooper) | `972944720186-compute@developer.gserviceaccount.com` | owner's project editor | everything | from the start |
| Section 2's VM (ControlThePancake) | `1025880049110-compute@developer.gserviceaccount.com` | `roles/storage.objectAdmin` | everything | 2026-11-08 |
| A teammate's VM (which section? write it here) | `196306351948-compute@developer.gserviceaccount.com` | `roles/storage.objectAdmin` | everything | 2026-11-08 |
| Lucy (Wikipedia backup) | `user:mccluskeylucy30@gmail.com` | `roles/storage.objectUser`, condition `wiki-only` | only `wiki/` | 2026-11-08 |

**To check the real list** (Cloud Shell; the bucket is the truth, this table is the record):

```bash
gcloud storage buckets get-iam-policy gs://undercurrent-data-cooper --format=json
```
