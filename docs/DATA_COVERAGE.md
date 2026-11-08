# Data coverage: what's collected, and who is pulling what

**One file for everyone (people and agents), so no two machines pull the same days.** The bucket is the truth; this file is the claims and the plan. Check them against each other with the command at the end.

## How to use this file

1. **Before pulling anything,** add a row with status `claimed`, then push it straight away: `git pull --rebase`, commit, push. If someone else's row already owns any of your days, don't pull them. Talk first.
2. **"Own days" must never overlap** between rows. Warm-up days (the 28 days before a block, used only to start its tracker) may overlap someone else's own days. They're pulled and embedded twice, but only the owner uploads story tables for them.
3. **Update your row** as it moves through the statuses, with the time in UTC, and push each time.
4. **Statuses:** `claimed` → `pulling` → `pulled` → `embedding` → `embedded` → `tracking` → `done`. `done` means uploaded, with `status/block-N.done` in the bucket. Add `failed: <why>` if it stops.

Everything goes to **`gs://undercurrent-data-cooper`**, in project `hackathon-514022`. Its owner, Cooper, grants each VM's service account write access.

## GDELT news → vectors → stories

Bucket paths: `processed/articles/day=…`, `processed/gdelt_totals/day=…`, `processed/vectors/news/…`, and, own days only, `processed/story_assignments/`, `stories_daily/` and `story_lineage/`.

| Block | Own days | Pulled from (warm-up) | Who | Account / VM | How | Status (UTC) |
|---|---|---|---|---|---|---|
| 1 | **2026-05-08 → 2026-11-06** (183 days) | 2026-04-10 | Cooper | `hackathon-514022`, `uc-gpu` (`g2-standard-4`, 1× L4, `us-central1-c`) | BigQuery, `scripts/gcp/create_vm.sh 1` | **pulled** 211/211 (00:40). **embedding**: 75/211 at 00:40, two embedders since 00:55. Uploading every 15 min. Tracking waits for the cutoffs |
| 2 | 2025-11-07 → 2026-05-07 (182) | 2025-10-10 | ? | ? | ? | **unclaimed**: owner, fill this in |
| 3 | 2025-05-08 → 2025-11-06 (183) | 2025-04-10 | ? | ? | ? | **unclaimed** |
| 4 | 2024-11-07 → 2025-05-07 (182) | 2024-10-10 | ? | ? | ? | **unclaimed** |

**Bucket access granted so far** (as of 2026-11-08 ~00:50): two teammates' VMs, `196306351948-compute@developer.gserviceaccount.com` and `1025880049110-compute@developer.gserviceaccount.com`. Whoever owns each: put your block number next to it in your row.

**Two plans disagree on the blocks; settle it here.**
- **6-month blocks (the table above):** `docs/deployGDELT.md` and `docs/TWO_YEAR_RUN.md` use these, and so does `scripts/gcp/create_vm.sh N`. They match what block 1 is actually running.
- **Year-long blocks:** `docs/ONE_YEAR_EACH.md` has its block 1 as 2025-11-07 → 2026-11-06, which overlaps block 2 above.

If you've already started from `ONE_YEAR_EACH.md`, write your real dates in your row now. Whichever table wins, block 1's own days stay 2026-05-08 → 2026-11-06, because they're already pulled.

## Articles only (no vectors, no stories)

For the forecast's proxy pretraining (`forecast.run --pretrain proxy` reads theme × country series from articles alone). It needs a CPU VM only, not a GPU.

| Own days | Who | Account / VM | How | Status (UTC) |
|---|---|---|---|---|
| 2021-11-07 → 2024-11-06 (3 years) | nobody yet | | BigQuery (about 0.6 GB a day, so about 660 GB) or the zip files | **proposed** (2026-11-08 00:50): claim it before starting |

## Other data

| What | Range | Who | Where | Notes |
|---|---|---|---|---|
| GDELT zip-file test days | 2026-11-01 → 2026-11-05 | ControlThePancake | that laptop | Measured: about 341k GKG rows and 248–281k articles a weekday; 50–57 MB of articles a day (`ingest/GDELT.md`) |
| Wikipedia `wiki_top.parquet` (8 languages) | 2026-09-08 → 2026-11-06 | A (Ingest) | not recorded: add it | From `PLAN.md` Notes (A) |
| *(add new data here)* | | | | |

## Check what's really in the bucket

In Cloud Shell. This prints each continuous range of article days and its length; a gap between ranges is a block nobody has pulled.

```bash
gcloud storage ls gs://undercurrent-data-cooper/processed/articles/ | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}' | sort -u | python3 -c "import sys, datetime as d; ds = [d.date.fromisoformat(l.strip()) for l in sys.stdin]; r = []; [r.append([x, x]) if not r or (x - r[-1][1]).days > 1 else r[-1].__setitem__(1, x) for x in ds]; [print(a, '->', b, (b - a).days + 1, 'days') for a, b in r]"
```

Swap `processed/articles/` for `processed/vectors/news/` (to see embedded days) or `processed/stories_daily/` (tracked days), and `gcloud storage ls gs://undercurrent-data-cooper/status/` lists the finished blocks.
