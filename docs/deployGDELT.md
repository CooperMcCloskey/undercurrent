# Deploy GDELT: start your block

What each of us runs to start processing our block of the two-year plan (`docs/TWO_YEAR_RUN.md`). One command creates an L4 VM on your Google Cloud account. The VM then pulls your days from BigQuery, embeds them, and uploads everything to the shared bucket on its own.

**You don't need a separate CPU VM.** BigQuery does the heavy part of the pull, and the L4 VM's CPUs handle the rest alongside embedding. The bill is about 0.55 GB a day, so about 155 GB for a block: inside the free 1 TB a month each account gets.

**Who is doing which section, and what's next:** `docs/DATA_COVERAGE.md` is the source of truth. The table below can be out of date, and the blocks here are its sections 1–4.

**Day-to-day commands:** `bash ~/undercurrent/scripts/gcp/vm.sh status | speed | embed2 | feeds | track | finish | stop`, from Cloud Shell. It finds your VM and zone by itself; run `git -C ~/undercurrent pull -q` first. Steps 4–5 below are the same commands written out.

## The blocks

| Block | Owner | Own days | Pulls from (28-day warm-up) |
|---|---|---|---|
| 1 | **Cooper** (running since 2026-11-07 23:23 UTC) | 2026-05-08 → 2026-11-06 | 2026-04-10 |
| 2 | ____ | 2025-11-07 → 2026-05-07 | 2025-10-10 |
| 3 | ____ | 2025-05-08 → 2025-11-06 | 2025-04-10 |
| 4 | ____ | 2024-11-07 → 2025-05-07 | 2024-10-10 |

**Agree in the chat who takes 2, 3 and 4 before anyone starts**, so no block is run twice.

## 0. One check, once (whoever is free, in Cloud Shell)

This confirms that BigQuery's GDELT table has the translated-language articles. In GDELT's raw files they're about two-thirds of all articles. It reads only a few MB, so it's free.

```bash
gcloud services enable bigquery.googleapis.com
bq query --nouse_legacy_sql 'SELECT COUNTIF(STRPOS(GKGRECORDID, "-T") > 0) AS translated, COUNT(*) AS total FROM `gdelt-bq.gdeltv2.gkg_partitioned` WHERE _PARTITIONTIME = TIMESTAMP("2026-11-05")'
```

- **Expected:** roughly 225k `translated` out of roughly 341k `total`. Those are the counts in GDELT's raw files for that day; the table's partitions won't match them exactly.
- **If `translated` is 0,** post it in the chat and don't start step 2. The BigQuery pull would miss most articles, and block 1 would need redoing. The fallback then is to pull from GDELT's zip files on a CPU VM (ControlThePancake has this ready).

## 1. Your account (console, about 5 minutes; do it now, approvals can take a while)

1. **Billing → Upgrade.** The free trial allows no GPUs. Upgrading keeps the $300 credit.
2. **IAM & Admin → Quotas**, in `us-central1`:
   - *NVIDIA L4 GPUs*: 1
   - *CPUs*: 24
   - *Persistent Disk SSD (GB)*: 1000

   And globally:
   - *GPUs (all regions)*: 1
3. **Billing → Budgets & alerts:** alerts at $50 and $150.

## 2. Start your block (Cloud Shell)

Open **Cloud Shell** in the console (the `>_` icon at the top right). Check that it's the right project:

```bash
gcloud config get-value project
```

Then run this, with **your block number** in place of `2`:

```bash
rm -rf undercurrent && git clone -q https://github.com/CooperMcCloskey/undercurrent.git && bash undercurrent/scripts/gcp/create_vm.sh 2
```

- **What it does:** it tries every `us-central1` zone and the biggest L4 machine your quota allows. If L4s are out of stock, it retries for about 12 minutes.
- **What the VM does next:** it sets itself up (driver, code, model), dry-runs the BigQuery bill, then starts the pull, embed and upload loops. Nothing needs typing on the VM.
- **At the end** the script prints two things you need:
  - the **zone** (`us-central1-a`, `-b` or `-c`), for the commands below;
  - the VM's **service account** (`…@developer.gserviceaccount.com`), for step 3.

## 3. Bucket access

**Post your service account in the chat.** Cooper then runs this for each one:

```bash
gcloud storage buckets add-iam-policy-binding gs://undercurrent-data-cooper --member=serviceAccount:THEIR_SERVICE_ACCOUNT --role=roles/storage.objectAdmin
```

Until that's done, uploads fail and retry every 15 minutes; nothing is lost.

## 4. Watch it (Cloud Shell)

Replace `ZONE` with the zone from step 2:

```bash
gcloud compute ssh uc-gpu --zone=ZONE --command='sudo -u uc -H bash /home/uc/undercurrent/scripts/gcp/block.sh status'
```

It shows days pulled and embedded, what's running, the last line of each log, and disk use.

**In the first hour, post the last embed line in the chat** (the headlines embedded for a day, and the seconds it took). That number sets everyone's timeline.

If setup seems stuck, its log is readable without SSH:

```bash
gcloud compute instances get-serial-port-output uc-gpu --zone=ZONE 2>/dev/null | grep uc-startup | tail -20
```

## 5. Track, finish, stop

1. **After Cooper posts "cutoffs pushed":** run the step 4 command with `track` in place of `status`. If it stops at a day that isn't embedded yet, run `track` again later. It carries on where it stopped.
2. **When tracking has finished:** run it with `finish`. That uploads your own days' story tables and marks the block done in the bucket. Post "block N done" in the chat.
3. **Stop the VM:**

   ```bash
   gcloud compute instances stop uc-gpu --zone=ZONE
   ```

   A running L4 VM costs about $0.70–2 an hour. Delete it once Cooper's merge has worked.

## Timeline (estimates; redo them with the step 4 numbers)

| From start | What |
|---|---|
| 0:00–0:20 | The VM sets itself up and dry-runs the block |
| 0:20–~3:00 | The pull runs, with embedding alongside |
| until ~3:00–5:30 | Embedding finishes (about 50 million headlines per block on one L4) |
| after "cutoffs pushed" | Tracking, about 1.5–3 hours |
| then | `finish`, stop the VM. Cooper merges, trains and exports when every block is done |

## Optional: let Claude Code run it for you

Claude Code in your Cloud Shell is already logged in to your Google account, so it can create the VM, watch it and run the steps above.

```bash
curl -fsSL https://claude.ai/install.sh | bash
cd ~/undercurrent && claude
```

Then tell it something like:

> I'm block 2. Follow the quick start in docs/TWO_YEAR_RUN.md and docs/deployGDELT.md. Check `block.sh status` every 15 minutes, and tell me when I need to do something: bucket access, "cutoffs pushed", or errors. Never delete anything in the bucket, and ask before creating or deleting any VM.

Cloud Shell ends idle sessions after about an hour. That doesn't matter: the VM keeps running by itself. Just start `claude` again to check in.

## Rules (from `docs/TWO_YEAR_RUN.md`)

- Don't set `UC_EMBEDDING_MODEL`. The VM sets `UC_HALF=1` itself. Every block must use the same vectors.
- Don't track before "cutoffs pushed". If the cutoffs change later, use `track-fresh`.
- Never delete anything in `gs://undercurrent-data-cooper`.
- Everything stays in `us-central1`.
- Stop your VM when it isn't working.
