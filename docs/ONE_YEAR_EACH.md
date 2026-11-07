# One year of GDELT each: the fast plan (4 accounts, 4 L4s)

**Goal:** four years of news (**2022-11-07 → 2026-11-06**) turned into stories in the shared bucket, as fast as possible. Each of us takes one year on our own Google Cloud account with one L4. After that, Cooper merges the blocks and trains.

**For each account:** a CPU VM pulls the year from GDELT's zip files into the bucket. The L4 VM takes each day from the bucket as soon as it lands, embeds it and uploads the vectors. Once the cutoffs are calibrated, the tracker follows the embedding day by day.

All three stages stream, oldest day first. So the whole run takes about as long as embedding alone, about 3–5 hours after setup. If each stage waited for the previous one to finish, it would take several hours longer.

This replaces the six-month split and the BigQuery pull in `docs/TWO_YEAR_RUN.md`. **Don't run `scripts/gcp/create_vm.sh` for this plan:** its VM pulls the old blocks from BigQuery at every boot. The tracking cutoffs and the merge ("Cooper only" in `docs/TWO_YEAR_RUN.md`) are unchanged.

## The split

Each block pulls, embeds and tracks from 28 days before its own days. That warm-up means the stories already running on its first day exist by then (`docs/TWO_YEAR_RUN.md` explains why). Only the own days count in the merge.

| Block | Owner | Own days | `PULL_START` | Days pulled, embedded and tracked |
|---|---|---|---|---|
| 1 | **Cooper** | 2025-11-07 → 2026-11-06 | 2025-10-10 | 393 |
| 2 | ____ | 2024-11-07 → 2025-11-06 | 2024-10-10 | 393 |
| 3 | ____ | 2023-11-07 → 2024-11-06 | 2023-10-10 | 394 |
| 4 | ____ | 2022-11-07 → 2023-11-06 | 2022-10-10 | 393 |

Put your name in the table and push it, so nobody takes the same block twice.

## Timeline (from "go"; estimates until the first hour's numbers are in)

| When | Who | What |
|---|---|---|
| 0:00 | everyone | Step 0: post your service account in the chat. Cooper creates the bucket and grants access |
| 0:05 | everyone | Step 1: create the L4 VM first (its driver takes longest), then the pull VM |
| 0:15 | everyone | Steps 2–3: the pull starts, at about 150–250 days an hour, so about 2 hours |
| 0:25 | everyone | Step 4: the L4 VM starts embedding the first pulled days. **Post titles a second from `gpu.sh status`** |
| ~0:45 | Cooper | A week of block 1 is embedded: label pairs, calibrate, push, post **"cutoffs pushed"** |
| ~1:30 | everyone | Step 5: `gpu.sh track`. It follows the embedding and is faster than it, so it finishes a few minutes after the last day is embedded |
| ~2:15 | everyone | Step 6: delete the pull VM once its block is in the bucket |
| ~3:30–5:30 | everyone | Embedding and tracking done: `gpu.sh finish`, post "block N done", stop the L4 VM |
| then | Cooper | Merge, forecast, export (`docs/TWO_YEAR_RUN.md`, "Cooper only") |

**Embedding is the long step:** about 100 million headlines per block on one L4. The table assumes 6–9k titles a second, with two embed processes sharing the GPU. Redo it with the real number.

## The machines (per account, all in `us-central1`)

| VM | Type | Disk | Runs | Lives | Price |
|---|---|---|---|---|---|
| `uc-pull` | `e2-standard-16`: 16 vCPUs, 64 GB, no GPU | 100 GB | `scripts/one_year/pull.sh`: GDELT zips → bucket | ~2 h, then deleted | ~$0.54/h |
| `uc-gpu` | `g2-standard-8`: 1 L4, 8 vCPUs, 32 GB | 250 GB (block 1: 400 GB, for the merge) | `scripts/one_year/gpu.sh`: bucket → embed → upload, then track | until the block is done | ~$0.85/h |

- **Why the pull gets its own machine:** it's download and CPU work. The L4 VM's 8 vCPUs are needed for feeding the GPU and for tracking.
- **Why highcpu types won't do:** building one day of articles takes about 3.5 GB of memory.
- **Quota:** 1 L4 (and *GPUs (all regions)* 1), plus 16 CPUs for the pull VM on top of the L4 VM's 8. That's 24 CPUs in `us-central1`.

## Rules

1. **Same code:** `git pull` on both VMs before starting.
2. **Same vectors:** `UC_HALF=1` (it's in `block.env`), and never set `UC_EMBEDDING_MODEL`. The vector store refuses to mix models (`ManifestMismatch`).
3. **Same cutoffs:** don't start tracking before "cutoffs pushed". If they change after you've started, use `gpu.sh track-fresh`.
4. **Never delete anything in the bucket.** The scripts only add files.
5. **Delete the pull VM when its block is in the bucket. Stop the L4 VM when it isn't working.**

## 0. Everyone, right away (Cloud Shell)

Your account must already be set up: paid billing, quota and a budget alert, as in `docs/TWO_YEAR_RUN.md` step 0. Open **Cloud Shell** in the console (the `>_` icon):

```bash
export PROJECT=$(gcloud config get-value project) ZONE=us-central1-a
gcloud services enable compute.googleapis.com storage.googleapis.com
gcloud compute project-info describe --format="value(defaultServiceAccount)"
```

**Post that last line (your service account) in the chat now.** Both of your VMs use it, so one grant covers both.

**Cooper only.** Create the bucket once, then grant each service account as it's posted:

```bash
gcloud storage buckets create gs://undercurrent-data-cooper --location=us-central1 --uniform-bucket-level-access
gcloud storage buckets add-iam-policy-binding gs://undercurrent-data-cooper --member=serviceAccount:THEIR_SERVICE_ACCOUNT --role=roles/storage.objectAdmin
```

## 1. Cloud Shell: create both VMs

**The L4 VM first.** It needs a Deep Learning VM image with CUDA 13, because PyTorch in `uv.lock` needs NVIDIA driver 580 or newer:

```bash
FAMILY=$(gcloud compute images list --project=deeplearning-platform-release --no-standard-images --format="value(family)" | grep -E '^common-cu1[3-9]' | sort -uV | tail -1); echo "image: $FAMILY"
```

If that prints nothing, use the newest `common-cu` family. Step 4 then upgrades the driver.

```bash
gcloud compute instances create uc-gpu --zone=$ZONE --machine-type=g2-standard-8 --maintenance-policy=TERMINATE --image-project=deeplearning-platform-release --image-family=$FAMILY --boot-disk-size=250GB --boot-disk-type=pd-balanced --metadata=install-nvidia-driver=True --scopes=cloud-platform
```

- **`ZONE_RESOURCE_POOL_EXHAUSTED`** means no L4s are free there: `export ZONE=us-central1-b` (or `-c`) and run it again. The zones that have L4s are listed by `gcloud compute accelerator-types list --filter="name=nvidia-l4 AND zone~us-central1"`.
- **Block 1 (Cooper):** use `--boot-disk-size=400GB`, since the merge lands on this disk too.

**Then the pull VM:**

```bash
gcloud compute instances create uc-pull --zone=$ZONE --machine-type=e2-standard-16 --image-family=debian-12 --image-project=debian-cloud --boot-disk-size=100GB --boot-disk-type=pd-balanced --scopes=cloud-platform
```

- `--scopes=cloud-platform` on both VMs is what lets them use the bucket.
- **CPU quota error on `uc-pull`:** use `e2-standard-8`, and put `PULL_WORKERS=12` in `block.env`. The pull is about half as fast, but still ahead of the embedding.

## 2. Your block's settings (the same file on both VMs)

On each VM, after `gcloud compute ssh uc-pull --zone=$ZONE` or `gcloud compute ssh uc-gpu --zone=$ZONE`, write `~/block.env`. This example is block 2; use your row from the table:

```bash
cat > ~/block.env <<'EOF'
export BLOCK=2 PULL_START=2024-10-10 OWN_START=2024-11-07 OWN_END=2025-11-06
export BUCKET=undercurrent-data-cooper
export UC_HALF=1 GPUS=1 EMBED_PROCS=2
EOF
```

`GPUS` and `EMBED_PROCS` matter only on the L4 VM.

## 3. Pull VM: set up and start

```bash
sudo apt-get update -q && sudo apt-get install -y -q git tmux
curl -LsSf https://astral.sh/uv/install.sh | sh && source $HOME/.local/bin/env
git clone https://github.com/CooperMcCloskey/undercurrent.git && cd undercurrent && uv sync -q
bash scripts/one_year/pull.sh start
```

- If `git clone` asks for a login, the repo is private: use `gh auth login` or a token.
- `pull.sh start` downloads the block oldest day first, and uploads each finished day to the bucket within seconds. Failed days and failed uploads are retried every minute until the whole block is in.
- Watch it with `tail -f ~/undercurrent/logs/pull.log`. Each day prints a line like `2024-10-10: 268,765 articles (en 96/96 files, tr 96/96), 55 MB, 38s | … days/h, ETA … h`. **Post the first one in the chat.**
- **Fewer than 96 files** on a day means a GDELT outage. It's recorded and listed by `pull.sh status`. It isn't an error.
- `403` in the log means Cooper hasn't granted your account yet. The pull carries on, and the uploads catch up once access works.

## 4. L4 VM: set up and start embedding

```bash
nvidia-smi        # lists the L4 and the driver version; right after boot the driver can take a few minutes
```

**If the driver version is below 580,** install the newest one. The VM reboots; SSH in again afterwards:

```bash
curl -fsSL https://storage.googleapis.com/compute-gpu-installation-us/installer/latest/cuda_installer.pyz -o cuda_installer.pyz && sudo python3 cuda_installer.pyz install_driver
```

Then:

```bash
sudo apt-get update -q && sudo apt-get install -y -q git tmux
curl -LsSf https://astral.sh/uv/install.sh | sh && source $HOME/.local/bin/env
git clone https://github.com/CooperMcCloskey/undercurrent.git && cd undercurrent && uv sync -q
uv run python -c "import torch; print('GPU ok' if torch.cuda.is_available() else 'NO GPU')"
bash scripts/one_year/gpu.sh start
```

`gpu.sh start` starts three kinds of loop in tmux, and each one stops by itself when the block is done:

- **fetch:** every 2 minutes, copies your block's newly pulled days from the bucket, and only those (other blocks stay there). Each day appears to the embedder whole.
- **embed:** two processes (`EMBED_PROCS`) share the L4, each embedding every other day as it arrives, oldest first.
- **upload:** every 10 minutes, copies the vectors to the bucket, as a backup and for later re-tracking.

Check on it any time:

```bash
bash ~/undercurrent/scripts/one_year/gpu.sh status
```

It shows fetched, embedded and tracked days, titles a second per embed process, GPU use, the last line of each log, and disk use. **Post the titles a second in the chat.** If GPU use stays under about 80%, try `EMBED_PROCS=3` in `~/block.env`, then `tmux kill-server` and `gpu.sh start`. Embedding resumes where it was.

## 5. L4 VM: track, after "cutoffs pushed"

```bash
bash ~/undercurrent/scripts/one_year/gpu.sh track
```

- It pulls the new cutoffs from `main`, then runs the tracker from `PULL_START`.
- When it reaches a day that isn't embedded yet, it waits 2 minutes and carries on, so it stays just behind the embedding.
- `tail -f ~/undercurrent/logs/track.log` prints a line per day.
- **If the cutoffs change** after you've started, run `gpu.sh track-fresh` (a new timeline).

## 6. Finish

**Pull VM.** `pull.sh status` shows `in the bucket: 393/393 days` (394 for block 3) and `PULL DONE`. Then delete it from Cloud Shell:

```bash
gcloud compute instances delete uc-pull --zone=$ZONE
```

**L4 VM.** Once `gpu.sh status` shows `tracked 393/393` and `TRACK_DONE`, run:

```bash
bash ~/undercurrent/scripts/one_year/gpu.sh finish
```

It uploads your own days' story tables and a last copy of the vectors, then writes `status/block-N.done` in the bucket. Post "block N done" in the chat, then stop the VM from Cloud Shell. Delete it after Cooper's merge has worked.

```bash
gcloud compute instances stop uc-gpu --zone=$ZONE
```

## When something goes wrong

| Problem | Fix |
|---|---|
| `403` in any log | Cooper hasn't granted your service account (step 0). Nothing is lost; the loops retry |
| `gpu.sh status` says `fetched 0` for a long time | Check the pull is uploading: `gcloud storage ls gs://$BUCKET/processed/articles/ \| tail -3` should show your days. Then check `logs/fetch.log` |
| `pull.log` keeps saying `some days … failed` | Read the `ERROR` lines just above. Network errors fix themselves; if the same day fails every time, post the error |
| `track.log` says `No news vectors for <day>` | Normal: that day isn't embedded yet, and the tracker carries on by itself |
| `ManifestMismatch` | `UC_EMBEDDING_MODEL` is set somewhere, or one VM skipped `git pull`. Don't delete anything; ask in the chat |
| `NO GPU` from the torch check | The driver is missing or too old: step 4's driver install, then reboot |
| A VM was rebooted or preempted | Run `pull.sh start` or `gpu.sh start` again (plus `gpu.sh track` if it was tracking). Everything resumes |
| L4 VM disk full | `gcloud compute disks resize uc-gpu --size=400GB --zone=$ZONE`, then reboot. A block's vectors are about 90 GB |
| Your block is done early | Say so in the chat, with every block's `embedded N/393` from `gpu.sh status`. Don't start on someone else's days without agreeing who does which |

## Cost per account (estimates)

| | |
|---|---|
| Pull VM, ~2 h | ~$1–1.50. Downloads from GDELT are free |
| L4 VM, ~4–6 h | ~$4–5 |
| Disks while they exist | ~$0.50 a day |
| Bucket: ~20 GB of articles and ~90 GB of vectors per block | ~$2 a month per block |

## About the pull (`ingest/gdelt_zip.py`)

- It writes exactly what `gdelt_bq.py` writes, through the same `gkg.py` (`articles/day=*/part.parquet` and `gdelt_totals/day=*/part.parquet`). Everything downstream reads it unchanged. It also writes `gdelt_files/<day>.json`, listing files present and missing per feed.
- **Measured on 2026-11-01..05 on a 12-core machine:**
  - 1.39 GB zipped a day (192 files, both feeds), about 341k GKG rows;
  - 248–281k articles after dedupe on weekdays, 156k on the Sunday;
  - 71–77% of articles from the translated feed. **Its titles are in the original language**, not English;
  - 50–57 MB of Parquet a day, so about 20 GB a year;
  - about 20 s a day with several days in flight (about 180 days an hour), limited by the download.
- **Safe to stop and restart:**
  - A day is done when its articles file exists. Each file is written in a scratch folder and renamed into place, articles last, so the bucket never gets half a day.
  - Files GDELT lists but never serves count as missing.
  - Records split by stray newlines are joined back together.
- `--sample-per-hour N` (the PLAN.md fallback: N of every 4 files, on both feeds alike) and `--shard k/n` (several machines on one block) are there if needed.
