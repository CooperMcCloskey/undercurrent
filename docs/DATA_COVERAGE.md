# Data coverage: which sections are done, and which are free

GDELT, split into 6-month **sections**. Each person or agent takes a free section and runs it end to end (pull → embed → track). **Before starting one, put your name and "started" in its row, and push straight away**, so nobody else takes it.

Start a section from Cloud Shell, with its number in place of `N`:

```bash
rm -rf undercurrent && git clone -q https://github.com/CooperMcCloskey/undercurrent.git && bash undercurrent/scripts/gcp/create_vm.sh N
```

Results go to `gs://undercurrent-data-cooper`. Send Cooper your VM's service account (the script prints it) for write access.

| Section | Own days | Warm-up from | Who | Pulled | Embedded | Tracked |
|---|---|---|---|---|---|---|
| **1** | 2026-05-08 → 2026-11-06 | 2026-04-10 | **Cooper** | ✅ done | ⏳ in progress (75/211 at 00:40 UTC) | ⬜ waits for the cutoffs |
| 2 | 2025-11-07 → 2026-05-07 | 2025-10-10 | **ControlThePancake** (`uc-gpu`, us-central1-b, `1025880049110-compute@…`) | ⏳ started 2026-11-08 | ⏳ | ⬜ waits for the cutoffs |
| **3** | 2025-05-08 → 2025-11-06 | 2025-04-10 | **Lucy** (VM `satanisreal`, us-central1-b) | ⏳ started | ⏳ started | ⬜ waits for the cutoffs |
| 4 | 2024-11-07 → 2025-05-07 | 2024-10-10 | **yeah-course** (`uc-gpu`, us-west1-a: no L4s in us-central1, `851470465448-compute@…`) | ⏳ started 2026-11-08 | ⏳ | ⬜ waits for the cutoffs |
| 5 | 2024-05-08 → 2024-11-06 | 2024-04-10 | *free* | | | |
| 6a | 2023-11-07 → 2024-02-06 | 2023-10-10 | **ControlThePancake** (extra GPU server, `scripts/standalone/section.sh`) | ⏳ starting | | |
| 6b | 2024-02-07 → 2024-05-07 | 2024-01-10 | **ControlThePancake** (a GPU server outside Google Cloud, `scripts/standalone/section.sh`) | ⏳ starting | | |
| 7 | 2023-05-08 → 2023-11-06 | 2023-04-10 | *free* | | | |
| 8 | 2022-11-07 → 2023-05-07 | 2022-10-10 | *free* | | | |
| 9 | 2022-05-08 → 2022-11-06 | 2022-04-10 | *free* | | | |
| 10 | 2021-11-07 → 2022-05-07 | 2021-10-10 | *free* | | | |

- **Sections 1–4 are two years**, the plan for tonight. **Sections 5–10** reach back five years, for whoever has a free GPU.
- **Order:** newest free section first. Each extra section helps the forecast less than the one before.
- **Teammates already running:** two teammates' VMs have bucket access (`196306351948-compute@…` and `1025880049110-compute@…`, the second is section 2). If the first is yours, write your section in its row now. `docs/ONE_YEAR_EACH.md` (year-long blocks) is superseded: nobody started from it, and its dates would overlap these sections.
- **Section 6 is split in two** (6a and 6b, three months each), because 6a runs on a server outside Google Cloud. Start 6a, 6b or 5 on any GPU server with `scripts/standalone/section.sh` (`docs/PREVIOUS_YEAR.md`); `create_vm.sh 6` would redo 6a, so don't use it.
- **Own days never overlap.** The warm-up days (the 28 days before a section, used only to start its tracker) may overlap the next section's own days. That's expected.

## Next, in order (as of 2026-11-08 02:00 UTC)

1. **Section 4 has no owner yet, and it finishes the two years** (section 3 is Lucy's). Take it now: `create_vm.sh 4` on Google Cloud, or `scripts/standalone/section.sh setup 4` on any other GPU server. It comes before 6a, 6b and 5.
2. **Confirm that BigQuery has the translated feed.** It's about 73% of articles, and not checked yet for sections 1 and 2. On a section's VM: `bash ~/undercurrent/scripts/gcp/vm.sh feeds` (translated should be about 2× English). Or compare the bucket's 2026-11-05 (inside section 1) with the zip-file test day below: 276,486 articles, 73% translated.
3. **Cooper: "cutoffs pushed"** once a week of section 1 is embedded. Then every running section starts tracking (`vm.sh track`, or `section.sh track`).
4. **Wikipedia top-1000 for the whole range: running** on ControlThePancake's laptop (started about 01:10 UTC; 4 of 8 languages done at 01:55). **Nobody else run it:** Wikimedia's limit is shared by everyone running our code.
5. **Older data, once 1–4 are covered:** 6a and 6b (ControlThePancake, starting), then 5 (`docs/PREVIOUS_YEAR.md`), then 7–10.
6. **When every section is tracked:** Cooper merges, trains and exports (`docs/TWO_YEAR_RUN.md`, "Cooper only"). Then the Wikipedia pageviews and edits for the stories' top entities (`README.md`, "Wikipedia").

**Check what's really in the bucket** (Cloud Shell). This prints each continuous range of pulled days; a gap is a section nobody has pulled. Swap `articles/` for `vectors/news/` (embedded days) or `stories_daily/` (tracked days).

```bash
gcloud storage ls gs://undercurrent-data-cooper/processed/articles/ | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}' | sort -u | python3 -c "import sys, datetime as d; ds = [d.date.fromisoformat(l.strip()) for l in sys.stdin]; r = []; [r.append([x, x]) if not r or (x - r[-1][1]).days > 1 else r[-1].__setitem__(1, x) for x in ds]; [print(a, '->', b, (b - a).days + 1, 'days') for a, b in r]"
```

## Other data

| What | Range | Who | Where |
|---|---|---|---|
| GDELT zip-file test days: articles, `gdelt_totals`, `gdelt_files` (1,232,548 articles, 237 MB; 71–77% translated) | 2026-11-01 → 2026-11-05 | ControlThePancake | that laptop, not the bucket. Inside section 1's days, so it's a cross-check for the BigQuery pull |
| Wikipedia `wiki_top.parquet` (8 languages) | 2026-09-08 → 2026-11-06 | A (Ingest) | *(add where it is)* |
| Wikipedia `wiki_top.parquet`, full range (8 languages) | 2024-10-10 → 2026-11-07 | **ControlThePancake** | ⏳ running on that laptop, started 2026-11-08 about 01:10 UTC (4 of 8 languages done at 01:55). Lands in `data/processed/wiki_top.parquet` there; not in the bucket yet |
| Wikipedia pageviews and edits for story entities | | | after tracking (step 6 above) |
| *(add new data here)* | | | |
