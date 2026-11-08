# Data coverage: which sections are done, and which are free

GDELT, split into 6-month **sections**. Each person or agent takes a free section and runs it end to end (pull → embed → track). **Before starting one, put your name and "started" in its row, and push straight away**, so nobody else takes it.

Start a section from Cloud Shell, with its number in place of `N`:

```bash
rm -rf undercurrent && git clone -q https://github.com/CooperMcCloskey/undercurrent.git && bash undercurrent/scripts/gcp/create_vm.sh N
```

Results go to `gs://undercurrent-data-cooper`. Send Cooper your VM's service account (the script prints it) for write access.

| Section | Own days | Warm-up from | Who | Pulled | Embedded | Tracked |
|---|---|---|---|---|---|---|
| **1** | 2026-05-08 → 2026-11-06 | 2026-04-10 | **Cooper** (`uc-gpu`, us-central1-c) | ✅ done | ✅ done (211/211 at 01:52 UTC) | ⏳ tracking on the 0.80 cutoffs and the language filter: at 2026-06-11 (63/211 days) around 05:15 UTC, about 35 s a day, so done about 06:45 UTC |
| 2 | 2025-11-07 → 2026-05-07 | 2025-10-10 | **ControlThePancake** (`uc-gpu`, us-central1-b, `1025880049110-compute@…`) | ✅ done (210/210) | ✅ done (210/210 at 03:00 UTC) | ⬜ redo with `track-fresh`: its tracking started before the cutoffs |
| ~~3~~ | ~~2025-05-08 → 2025-11-06~~ | | split into 3a and 3b below (2026-11-08), so the tracking runs on two machines. Pulled and embedded once, on yeah-course's RunPod GPU; everything is in the bucket | ✅ done (211/211) | | |
| 3a | 2025-05-08 → 2025-08-06 (91 days) | 2025-04-10 | **yeah-course** (RunPod GPU, `scripts/standalone/section.sh`; its tracker stops at 2025-08-06) | ✅ done | ⏳ (194/211 at 03:30 UTC) | ⏳ tracking (cutoff 0.80) |
| 3b | 2025-08-07 → 2025-11-06 (92 days) | 2025-07-10 | **ControlThePancake** (tracking only: articles and vectors for 2025-07-10 → 2025-11-06 are in the bucket) | ✅ done (from section 3) | ✅ in the bucket (from section 3) | ⬜ `track`, from 2025-07-10 |
| 4 | 2024-11-07 → 2025-05-07 | 2024-10-10 | **yeah-course** (`uc-gpu`, us-west1-a: no L4s in us-central1, `851470465448-compute@…`) | ✅ done (210/210) | ✅ done (210/210) | ⏳ tracking (cutoff 0.80) |
| ~~5~~ | ~~2024-05-08 → 2024-11-06~~ | | split into 5a and 5b below (2026-11-08 ~05:15 UTC). Cooper's run on block 1's VM pulled only 5 days and is stopped: no more GPU quota in that project | | | |
| 5a | 2024-05-08 → 2024-08-06 (91 days) | 2024-04-10 | **Lucy** (2× A100 PCIe, 32 vCPU server, 5a and 5b side by side: `scripts/standalone/multi.sh`) | ⏳ claimed 2026-11-08 04:35 UTC | | |
| 5b | 2024-08-07 → 2024-11-06 (92 days) | 2024-07-10 | **Lucy** (2× A100 PCIe, 32 vCPU server, 5a and 5b side by side: `scripts/standalone/multi.sh`) | ⏳ claimed 2026-11-08 04:35 UTC | | |
| 6a | 2023-11-07 → 2024-02-06 | 2023-10-10 | **ControlThePancake** (extra GPU server, `scripts/standalone/section.sh`) | ✅ done | ✅ done | ⬜ redo with `track-fresh`: its tracking started before the cutoffs |
| 6b | 2024-02-07 → 2024-05-07 | 2024-01-10 | **ControlThePancake** (a GPU server outside Google Cloud, `scripts/standalone/section.sh`) | ✅ done (119/119) | ✅ done (119/119) | ⬜ redo with `track-fresh`: its tracking started before the cutoffs |
| ~~7~~ | ~~2023-05-08 → 2023-11-06~~ | | split into 7a and 7b below (2026-11-08), so the tracking runs on two machines. Pulled and embedded once, on yeah-course's second RunPod GPU | ⏳ (82/211 at 03:30 UTC) | ⏳ (76/211 at 03:30 UTC) | |
| 7a | 2023-05-08 → 2023-08-06 (91 days) | 2023-04-10 | **yeah-course** (second RunPod GPU, `scripts/standalone/section.sh`; its tracker stops at 2023-08-06) | ⏳ | ⏳ | ⏳ tracking (cutoff 0.80) |
| 7b | 2023-08-07 → 2023-11-06 (92 days) | 2023-07-10 | **ControlThePancake** (tracking only, from section 7's articles and vectors in the bucket, once they're all uploaded) | ⏳ (from section 7) | ⏳ (from section 7) | ⬜ `track`, from 2023-07-10, once section 7 shows `EMBED_DONE` |
| 8 | 2022-11-07 → 2023-05-07 | 2022-10-10 | **ControlThePancake** (2× L40S server, 32 vCPU, `scripts/standalone/section.sh`) | ⏳ started 2026-11-08 03:28 UTC | | ⬜ cutoffs are out: `track` |
| 9 | 2022-05-08 → 2022-11-06 | 2022-04-10 | **ControlThePancake** (on 6a's GPU server, alongside it in its own home `/workspace/uc-home-9`) | ⏳ starting 2026-11-08 03:00 UTC | | ⬜ cutoffs are out: `track` |
| 10 | 2021-11-07 → 2022-05-07 | 2021-10-10 | **ControlThePancake** (GPU server, `scripts/standalone/section.sh`) | ⏳ starting 2026-11-08 03:00 UTC | | ⬜ cutoffs are out: `track` |
| **Gap** (inside 3) | 2025-06-15 → 2025-07-01 (17 days), plus 2025-06-14 after 17:45 UTC and 2025-07-02 before about 02:15 UTC | | *nobody: a GDELT outage* | ❌ 0 articles. GDELT has no files for these days: `gdelt_files/<day>.json` lists 0 per feed, and the zip URLs return 404. Not recoverable from the zip files; BigQuery not checked | n/a | n/a |

- **Tracked:** only tracking started after the cutoffs were pushed (2026-11-08 03:45 UTC, `2ab8939`) counts; anything tracked before then has to be redone with `track-fresh`. Each section tracks only its own days, on its own server: `docs/SECTION_RUNBOOK.md`, "What gets tracked".
- **Sections 1–4 are two years**, the plan for tonight. **Sections 5–10** reach back five years, for whoever has a free GPU.
- **Order:** newest free section first. Each extra section helps the forecast less than the one before.
- **Teammates already running:** two teammates' VMs have bucket access (`196306351948-compute@…` and `1025880049110-compute@…`, the second is section 2). If the first is yours, write your section in its row now. `docs/ONE_YEAR_EACH.md` (year-long blocks) is superseded: nobody started from it, and its dates would overlap these sections.
- **Section 6 is split in two** (6a and 6b, three months each), because 6a runs on a server outside Google Cloud. Start 6a, 6b or 5 on any GPU server with `scripts/standalone/section.sh` (`docs/PREVIOUS_YEAR.md`); `create_vm.sh 6` would redo 6a, so don't use it.
- **Sections 3 and 7 are split for tracking only** (3a/3b, 7a/7b), so two machines track each in parallel. They were pulled and embedded once, as whole sections. The b halves track from the bucket's copy, starting at their own warm-up (2025-07-10 and 2023-07-10), so they don't depend on the a halves. `section.sh` knows 3a, 3b, 7a and 7b. **A b half on any server:** `bash ~/undercurrent/scripts/standalone/extra_section.sh 3b` (or `7b`) downloads its articles and vectors from the bucket (`section.sh fetch`) and tracks them; while some days aren't uploaded yet, it keeps fetching and tracking as they arrive (`docs/SECTION_RUNBOOK.md`, "Tracking-only sections"). Cooper: the merge has one more seam in each, at 2025-08-07 and 2023-08-07.
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

## Other data

| What | Range | Who | Where |
|---|---|---|---|
| GDELT zip-file test days: articles, `gdelt_totals`, `gdelt_files` (1,232,548 articles, 237 MB; 71–77% translated) | 2026-11-01 → 2026-11-05 | ControlThePancake | that laptop, not the bucket. Inside section 1's days, so it's a cross-check for the BigQuery pull |
| Wikipedia `wiki_top.parquet` (8 languages) | 2025-11-07 → 2026-11-06 | Lucy | Lucy's laptop, `data/processed/wiki_top.parquet`. Not in the bucket. The full-range run below replaces it |
| Wikipedia `wiki_top.parquet`, full range (8 languages) | 2024-10-10 → 2026-11-07 | **ControlThePancake** | ⏳ running on that laptop, started 2026-11-08 about 01:10 UTC (4 of 8 languages done at 01:55). Lands in `data/processed/wiki_top.parquet` there; not in the bucket yet |
| Wikipedia pageviews and edits for each week's top GDELT people and orgs | 2021-10-10 → 2026-11-06 | **Lucy** | ⏳ setting up on Lucy's laptop (2026-11-08). See "Wikipedia from Lucy's laptop" below. Copied to `gs://undercurrent-data-cooper/wiki/` as it goes (Lucy can write to `wiki/` only; see "Bucket access") |
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
# 3. Back up to the bucket.
gcloud storage rsync -r data/raw/wiki gs://undercurrent-data-cooper/wiki/raw
gcloud storage cp data/processed/wiki_*.parquet gs://undercurrent-data-cooper/wiki/
```

- **Dates are fixed** at 2021-10-10 → 2026-11-06 (the defaults in `wiki_bulk.py`). Don't change them mid-run: the cache only counts as done for the exact range, so a different range refetches everything.
- **`--no-top`:** the top-1000 lists are ControlThePancake's job (step 4 above), so this run doesn't fetch them.
- **Weeks that are only partly pulled** get a provisional top 50. The next run corrects it. A name fetched early that later drops out stays in the cache but isn't in `wiki_selection.parquet`, so it does no harm.
- **Speed:** Wikimedia allows about 190 requests a minute per IP address. Expect 5–10k names and a day or more from one laptop. **To help**, use a machine on a **different network**: a phone hotspot or home broadband, not the same wifi as Lucy. Write your name here first, and tell Lucy so she switches to `fetch --no-top --shard 0/2`. Copy `gs://undercurrent-data-cooper/wiki/raw` into your `data/raw/wiki` (so names Lucy already fetched are skipped), then run the same commands with `fetch --no-top --shard 1/2`. Send Lucy `data/wiki_shard-1-of-2.tar.gz` and she runs `wiki_bulk merge`.
- **Bucket access:** Lucy's account (`mccluskeylucy30@gmail.com`) can write only under `gs://undercurrent-data-cooper/wiki/`. A folder-limited grant can't cover *listing* the bucket, so `gcloud storage rsync` (step 3's first line) needs read access to the whole bucket as well (`roles/storage.objectViewer`). Without it, use `gcloud storage cp -r data/raw/wiki gs://undercurrent-data-cooper/wiki/raw` instead.
- **Done when:** every section is pulled, `wiki_names` has counted every day, and a final `fetch` reports nothing new. Then `wiki_bulk build` writes the final `wiki_entity_daily.parquet` and `wiki_selection.parquet` to the bucket. The forecast should only use an entity from its `first_week` onwards (point-in-time).

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
