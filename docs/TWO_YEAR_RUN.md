# Two years of GDELT, split across 3–4 Google Cloud accounts

> **Status, 2026-11-07 23:23 UTC: block 1 (Cooper) is running exactly as this file describes.**
> - **VM:** `uc-gpu`, a `g2-standard-4` (1× L4, 4 vCPU, 16 GB RAM, 400 GB disk) in `us-central1-c`, created with the quick start below (`create_vm.sh 1`).
> - **Pull:** from BigQuery, 2026-04-10 → 2026-11-06. Block 1 owns 2026-05-08 → 2026-11-06.
> - **Bucket:** `gs://undercurrent-data-cooper`.
>
> **Still to settle for blocks 2–4:** ControlThePancake is preparing an alternative: one year per person, pulled from GDELT's zip files on a separate CPU VM (`docs/ONE_YEAR_EACH.md` and `ingest/gdelt_zip.py`; **neither is on `main` yet**). Both pulls write identical files, so blocks can be pulled either way. Whichever split the others use:
> - its newest block must **own the days up to 2026-05-07** and stop there, because block 1 owns everything after;
> - every block keeps the rules below: same `UC_HALF=1`, same cutoffs, and only its own days of story tables uploaded.

**Goal:** by the end of the hackathon, one shared bucket holds two years of news (**2024-11-07 → 2026-11-06**, 730 days) turned into stories, ready for the forecast to train on, plus the forecast and the globe export built from them.

**Why split it:** each account brings its own GPU quota, its own $300 credit and its own free 1 TB of BigQuery a month. New accounts get only 1–2 GPUs, so four accounts embed and track about four times faster than one.

**How:** each account owns one contiguous block of days and runs the whole pipeline on it, on a single VM of its own (pull → embed → track). Nothing moves between accounts except finished results in the bucket. Cooper then merges the blocks and trains.

Background: `docs/CLOUD_RUN.md` (the one-account version, which has more detail on each step) and `pipeline/undercurrent/ingest/GDELT.md`.

## What we produce

Everything goes to Cooper's bucket, **`gs://undercurrent-data-cooper`** (`create_vm.sh` uses it by default):

| Path in the bucket | What | Written by | Rough size for 2 years* |
|---|---|---|---|
| `processed/articles/day=YYYY-MM-DD/part.parquet` | One row per distinct headline: url, domain, title, places, persons, orgs, themes, tone, language (`docs/WORKPLAN.md` 3.1) | every block | ~40 GB |
| `processed/gdelt_totals/day=YYYY-MM-DD/part.parquet` | GKG rows per feed and country before filtering (for normalising volume) | every block | < 1 GB |
| `processed/vectors/news/YYYY-MM-DD.npy` + `.parquet`, `manifest.json` | One 384-d float16 vector per headline, plus the text that was embedded | every block | ~150–200 GB |
| `processed/story_assignments/YYYY-MM-DD.parquet` | Which story each article belongs to, and its similarity to the story | every block, **own days only** | 2–5 GB |
| `processed/stories_daily/YYYY-MM-DD.parquet` | Per story and day: size, first seen, label, category | every block, **own days only** | < 1 GB |
| `processed/story_lineage/YYYY-MM-DD.parquet` | Merges: old story id → surviving id | every block, **own days only** | tiny |
| `status/block-N.done` | "Block N is finished and uploaded" | every block | |
| `merged/forecasts.parquet`, `model_metrics.json`, `forecast_report.json` | The forecast trained on all blocks | Cooper | < 1 GB |
| `export/` | The globe's JSON (`meta.json`, `snapshots/`, `stories/`) | Cooper | ~200 MB |

\*From ControlThePancake's measurement of 2026-11-01..05 (zip files, both feeds): 248–281k articles a day on weekdays and 156k on a Sunday, after dedupe, with 50–57 MB of articles Parquet a day. About 73% of articles come from the translated feed, with titles in the original language. The vectors are 768 bytes a headline plus the text.

**"Ready for training"** means `uv run python -m undercurrent.forecast.run --source stories --processed data/merged` runs on the merged tables (the last section).

## The split

### With 4 accounts (about 6 months each)

| Block | Owner | Owns (its own days) | Pulls, embeds and tracks from (28-day warm-up) | Days |
|---|---|---|---|---|
| 1 | **Cooper** (1× L4, **running**) | 2026-05-08 → 2026-11-06 | 2026-04-10 | 183 + 28 |
| 2 | ____ | 2025-11-07 → 2026-05-07 | 2025-10-10 | 182 + 28 |
| 3 | ____ | 2025-05-08 → 2025-11-06 | 2025-04-10 | 183 + 28 |
| 4 | ____ | 2024-11-07 → 2025-05-07 | 2024-10-10 | 182 + 28 |

### With 3 accounts (about 8 months each)

| Block | Owner | Owns | From | Days |
|---|---|---|---|---|
| 1 | **Cooper** | 2026-03-09 → 2026-11-06 | 2026-02-09 | 243 + 28 |
| 2 | ____ | 2025-07-08 → 2026-03-08 | 2025-06-10 | 244 + 28 |
| 3 | ____ | 2024-11-07 → 2025-07-07 | 2024-10-10 | 243 + 28 |

**Why whole blocks, not every 4th day:** the story tracker has to process days in order on one machine. Whole blocks let each account pull, embed and track locally, so no vectors are copied between accounts.

**Why the 28-day warm-up:** stories that are already running on a block's first day should already exist when that day starts. Each block re-does the 28 days before it, then keeps only its own days. That costs 12–15% extra pulling and embedding, and saves waiting on the previous block.

**Block boundaries:** a story that crosses one gets a new id after the boundary. Ids can't collide between blocks. C: treat the first 4 weeks of each block like the first weeks of the data when training.

**Cooper has the newest block,** because it's the demo period and the cutoffs are calibrated on it. If time or quota runs out, the older blocks only add training data.

## Rules for everyone

1. **Same code.** Run `git pull` before you start, and again before you track.
2. **Same vectors:** `UC_HALF=1` on every GPU, and don't set `UC_EMBEDDING_MODEL`. Vectors made with a different model or precision can't be compared across blocks.
3. **Same story cutoffs.** Don't track until Cooper posts **"cutoffs pushed"**: they go into `pipeline/undercurrent/vectors/thresholds.py` on `main`. Then `git pull` and track. If nothing is posted by the deadline in the timeline below, track with what is on `main`.
4. **Upload only your own days** of `story_assignments`, `stories_daily` and `story_lineage` (the script below does this). Articles, totals and vectors are uploaded in full: a warm-up day uploaded by two blocks is the same file.
5. **Never delete in the bucket** (no `rsync --delete-unmatched-destination-objects`), and never upload `tracker_state/`.
6. **Everything in `us-central1`.** Transfers to the bucket are then free.
7. **Stop or delete your VM when you're done.** It costs about $1–2 an hour while it runs.

## Timeline (from when your VM is up; estimates)

Block 1 started at 2026-11-07 23:23 UTC. The times below are for a block of about 210 days at about 250k headlines a day, so roughly 50 million headlines. Redo them with the embed log's titles-a-second number.

| Time | What | Notes |
|---|---|---|
| 0:00–0:20 | The VM sets itself up and dry-runs the block | `create_vm.sh` does all of it. The dry run is free and gives the exact BigQuery bill |
| 0:20–~3:00 | Pull the block (2–3 processes) | Embedding runs alongside it on the GPU |
| until ~3:00–5:30 | Embedding finishes | 2–3 hours of GPU time with 2 L4s, 3–5 hours with 1 L4 (block 1's VM has 1 L4 and 4 vCPUs). Embedding stops by itself when every day is done |
| by **4:00** | **Deadline:** Cooper posts "cutoffs pushed" | Otherwise track with what is on `main` |
| after embedding, ~1.5–3 h | Track the block | 13–37 seconds a day for 200k items, measured on synthetic data, so more at 250k and on 4 vCPUs |
| then | `finish` (uploads your own days and marks the block done), then stop the VM | |
| when every block is done, ~1 h | Cooper: merge, forecast, export | |

## Quick start: one command per account (recommended)

After step 0 below (billing upgrade, quota), open **Cloud Shell** in the console (the `>_` icon), check the project with `gcloud config get-value project`, and run, with **your block number** in place of `2`:

```bash
rm -rf undercurrent && git clone -q https://github.com/CooperMcCloskey/undercurrent.git && bash undercurrent/scripts/gcp/create_vm.sh 2
```

- **With 3 accounts,** add `3` after the block number. Cooper is block `1`, which also creates the bucket.
- **Creating the VM** (`scripts/gcp/create_vm.sh`): it tries every `us-central1` zone and the biggest L4 machine your quota allows, and retries for about 12 minutes if L4s are out of stock.
- **The VM then sets itself up** (`scripts/gcp/vm_startup.sh`, at every boot): it checks the driver and PyTorch, installs the code, sizes `~/block.env` to the machine, runs the dry run (and stops if it's over 2,500 GB), then starts the pull, embed and upload loops. Embedding and uploading stop by themselves when the block is done.
- **At the end it prints** the VM's service account: send it to Cooper for bucket access.

From then on, everything is one command from Cloud Shell, with nothing to fill in. `scripts/gcp/vm.sh` finds your VM and its zone itself. Update the copy in Cloud Shell first:

```bash
git -C ~/undercurrent pull -q
```

| When | Command |
|---|---|
| Any time: progress | `bash ~/undercurrent/scripts/gcp/vm.sh status` |
| Is the GPU kept busy? | `bash ~/undercurrent/scripts/gcp/vm.sh speed` |
| VM started before 2026-11-08 01:15 UTC | `bash ~/undercurrent/scripts/gcp/vm.sh embed2`: two embedders per GPU, about 1.5–2× faster, nothing lost |
| Is the translated feed there? | `bash ~/undercurrent/scripts/gcp/vm.sh feeds` (it should be about 2× English) |
| Cooper only: calibrate the cutoffs | `vm.sh pairs`, then `vm.sh calibrate "1011 0010 ..."` (see "Calibrate the cutoffs" below) |
| After "cutoffs pushed" | `bash ~/undercurrent/scripts/gcp/vm.sh track` (resumes if it stops; `track-fresh` starts again) |
| Tracking finished | `bash ~/undercurrent/scripts/gcp/vm.sh finish`: uploads your own days and writes `status/block-N.done` |
| Then | `bash ~/undercurrent/scripts/gcp/vm.sh stop` |
| Setup went wrong | `bash ~/undercurrent/scripts/gcp/vm.sh log` (the setup log, no SSH) |

The steps below are what the scripts do, by hand: use them to debug, or if the scripts fail.

## Runbook for each account (by hand)

### 0. The account (do it now: approvals take time)

1. Billing → **Upgrade** the free trial to a paid account. The trial allows no GPUs and only 8 vCPUs; upgrading keeps the $300.
2. IAM & Admin → **Quotas** (`docs/CLOUD_RUN.md` step 0). In `us-central1`: *NVIDIA L4 GPUs* 1–2, *Preemptible NVIDIA L4 GPUs* 1–2, *CPUs* 24, *Persistent Disk SSD (GB)* 1000. Globally: *GPUs (all regions)* 1–2.
3. Billing → **Budgets**: alerts at $50 and $150.
4. On your laptop: `gcloud init` (your project, region `us-central1`), then:

```bash
gcloud services enable bigquery.googleapis.com bigquerystorage.googleapis.com compute.googleapis.com storage.googleapis.com
```

### 1. Laptop: create the VM

Pick the machine for the GPUs you were granted: 1 L4 → `g2-standard-8` (8 vCPU, 32 GB) with a 250 GB disk; 2 L4 → `g2-standard-24` (24 vCPU, 96 GB). Cooper's VM is a `g2-standard-4` (1 L4, 4 vCPU, 16 GB; the bigger ones weren't available) with 400 GB, since the merge also lands on that disk. `create_vm.sh` picks the biggest of the three your quota and stock allow, and sizes the settings to it.

```bash
export PROJECT=your-project-id ZONE=us-central1-a MACHINE=g2-standard-8 DISK=250GB
```

The newest Deep Learning VM image family with CUDA (it comes with the NVIDIA driver):

```bash
gcloud compute images list --project=deeplearning-platform-release --no-standard-images --filter="family~common-cu" --format="value(family)" | sort -u
```

```bash
export FAMILY=paste-the-newest-family-here
```

```bash
gcloud compute instances create uc-gpu --project=$PROJECT --zone=$ZONE --machine-type=$MACHINE --maintenance-policy=TERMINATE --image-project=deeplearning-platform-release --image-family=$FAMILY --boot-disk-size=$DISK --boot-disk-type=pd-balanced --metadata=install-nvidia-driver=True --scopes=cloud-platform
```

- `--scopes=cloud-platform` matters. With the console's "default access", the VM can't query BigQuery or write to the bucket.
- **If the zone has no L4s,** try `us-central1-b` or `-c`. To see which zones have them:

```bash
gcloud compute accelerator-types list --filter="name=nvidia-l4 AND zone~us-central1"
```

**Send Cooper your VM's service account** (it's what writes to the bucket):

```bash
gcloud compute instances describe uc-gpu --project=$PROJECT --zone=$ZONE --format="value(serviceAccounts[0].email)"
```

### 2. On the VM: set up

```bash
gcloud compute ssh uc-gpu --project=$PROJECT --zone=$ZONE
```

```bash
nvidia-smi                                   # should list your L4s; the driver can take a few minutes after first boot
sudo apt-get update && sudo apt-get install -y git tmux
curl -LsSf https://astral.sh/uv/install.sh | sh && source $HOME/.local/bin/env
git clone https://github.com/CooperMcCloskey/undercurrent.git && cd undercurrent   # private repo: gh auth login, or a token
uv sync
echo "GOOGLE_CLOUD_PROJECT=$(gcloud config get-value project)" > .env
```

### 3. Your block's settings

Put your row from the split table in `~/block.env`. This example is block 2 of 4:

```bash
cat > ~/block.env <<'EOF'
export BLOCK=2 PULL_START=2025-10-10 OWN_START=2025-11-07 OWN_END=2026-05-07
export BUCKET=the-bucket-name-from-chat
export UC_HALF=1
export GPUS=1        # how many L4s this VM has
export PULLS=2       # parallel pull processes: 2 on g2-standard-8, 3 on g2-standard-24
EOF
source ~/block.env
```

Run `source ~/block.env` again in every new SSH session.

### 4. Dry run, then pull

```bash
uv run python -m undercurrent.ingest.gdelt_bq --start $PULL_START --end $OWN_END --dry-run
```

**Post the total GB in the team chat.** Up to about 1 TB is free on each billing account; beyond that it's about $6.25 per TB. Then start the pull: your block is split into `$PULLS` pieces, one tmux session each.

```bash
uv run python - <<'EOF' > ~/ranges.txt
import datetime as dt, os
s, e = dt.date.fromisoformat(os.environ["PULL_START"]), dt.date.fromisoformat(os.environ["OWN_END"])
n, days = int(os.environ["PULLS"]), (e - s).days + 1
for i in range(n):
    print(s + dt.timedelta(days * i // n), s + dt.timedelta(days * (i + 1) // n - 1))
EOF
cat ~/ranges.txt
```

```bash
i=0; while read a b; do tmux new -d -s pull$i -c "$PWD" "uv run python -m undercurrent.ingest.gdelt_bq --start $a --end $b --batch-days 3 > pull-$i.log 2>&1"; i=$((i+1)); done < ~/ranges.txt
```

Watch it with `tail -f pull-0.log`. Each finished day prints `YYYY-MM-DD: N GKG rows -> M articles`. A killed or restarted pull skips the days it already has, so just start it again.

### 5. Embed (start it straight away; it follows the pull)

One loop per GPU. Each pass embeds every pulled day not yet embedded (`--shard i/n` splits the days between GPUs), then waits 5 minutes and looks again:

```bash
for i in $(seq 0 $((GPUS - 1))); do tmux new -d -s embed$i -c "$PWD" "while true; do CUDA_VISIBLE_DEVICES=$i UC_HALF=1 uv run python -m undercurrent.vectors.store --source news --start $PULL_START --end $OWN_END --shard $i/$GPUS; sleep 300; done > embed-$i.log 2>&1"; done
```

The log prints titles a second: **post that number in the chat in the first hour**, so the timeline can be redone. When the pulls have finished, check that every day is embedded. The two numbers must match:

```bash
uv run python -c "import datetime as d, os; print((d.date.fromisoformat(os.environ['OWN_END']) - d.date.fromisoformat(os.environ['PULL_START'])).days + 1)"; ls data/processed/vectors/news/*.parquet | wc -l
```

Then stop the embed loops: `tmux kill-session -t embed0` (and `embed1`).

### 6. Upload as you go (backup, and so Cooper can see progress)

```bash
tmux new -d -s upload -c "$PWD" "while true; do for T in articles gdelt_totals vectors; do gcloud storage rsync -r data/processed/\$T gs://$BUCKET/processed/\$T; done; sleep 900; done > upload.log 2>&1"
```

### 7. Track (after "cutoffs pushed", or the deadline)

```bash
git pull && tmux new -d -s track -c "$PWD" "OMP_NUM_THREADS=8 uv run python -m undercurrent.stories.track --start $PULL_START --end $OWN_END --fresh > track.log 2>&1"
```

- `tail -f track.log` prints a line per day (items, stories joined, new stories).
- **If it stops** (the VM restarted, or a day wasn't embedded yet), run the same command **without `--fresh`** to carry on where it stopped.
- **If the cutoffs change after you've started,** run it again with `--fresh`.

### 8. Upload your own days and mark the block done

```bash
shopt -s nullglob; rm -rf ~/own && for T in story_assignments stories_daily story_lineage; do
  mkdir -p ~/own/$T
  for f in data/processed/$T/*.parquet; do d=$(basename "$f" .parquet); [[ "$d" < "$OWN_START" || "$d" > "$OWN_END" ]] || cp "$f" ~/own/$T/; done
  echo "$T: $(ls ~/own/$T | wc -l) days"
  gcloud storage rsync -r ~/own/$T gs://$BUCKET/processed/$T
done
```

`story_assignments` and `stories_daily` must each show exactly your own number of days (182, 183, 243 or 244). `story_lineage` only has files on days with a merge. Then do a last sync of the rest, mark the block done, and post in the chat:

```bash
tmux kill-session -t upload; for T in articles gdelt_totals vectors; do gcloud storage rsync -r data/processed/$T gs://$BUCKET/processed/$T; done
echo "block $BLOCK $OWN_START..$OWN_END done $(date -u)" | gcloud storage cp - gs://$BUCKET/status/block-$BLOCK.done
```

### 9. Stop the VM (from your laptop)

```bash
gcloud compute instances stop uc-gpu --project=$PROJECT --zone=$ZONE
```

Delete it once the merge has worked (`instances delete`). A stopped VM still pays for its disk, about $0.30–0.50 a day.

## Cooper only

**Before anyone uploads:** create the bucket, then give each teammate's VM service account write access:

```bash
gcloud storage buckets create gs://$BUCKET --location=us-central1 --uniform-bucket-level-access
```

```bash
gcloud storage buckets add-iam-policy-binding gs://$BUCKET --member=serviceAccount:THEIR_VM_SERVICE_ACCOUNT --role=roles/storage.objectAdmin
```

**Calibrate the cutoffs (the gate for everyone's step 7, deadline 4:00).**
1. Once a few days of block 1 are embedded, in Cloud Shell: `git pull` (Cloud Shell's copy, not the VM's), then `bash ~/undercurrent/scripts/gcp/vm.sh pairs`. It prints about 150 numbered pairs of real headlines: random headlines and their nearest neighbours that day, spread across similarity bands, **without their scores** so the labels come from the text alone. It also saves them as `pairs_unlabelled.csv` in `uc`'s home on the VM. The script is sent to the VM, so nothing is pulled there.
2. Label each pair **1** if both headlines report the same specific event or story (the same incident, decision, match or announcement, in any wording or language), **0** if they are the same kind of thing but a different event or place, or merely related. When unsure, 0. Write the labels as one string in order: `1011 0010 ...`.
3. `bash ~/undercurrent/scripts/gcp/vm.sh calibrate "1011 0010 ..."` prints a table of precision by cutoff and the cutoff for 90% precision, with a range for how far another sample could move it. A string of the wrong length is refused, so a missed digit can't shift the labels.
4. Put the cutoff in `vectors/thresholds.py` (and add the pair to `CALIBRATED`), commit, push, and post **"cutoffs pushed"**.
5. Track the newest 60 days first and read 20 stories before telling anyone to go.

**Merge, train, export** (when every `status/block-N.done` exists, `gcloud storage ls gs://$BUCKET/status/`):

```bash
mkdir -p data/merged && for T in articles gdelt_totals story_assignments stories_daily story_lineage; do gcloud storage rsync -r gs://$BUCKET/processed/$T data/merged/$T; done
```

```bash
uv run python -m undercurrent.forecast.run --source stories --processed data/merged --out data/merged --model hgb gru --device cuda
```

```bash
uv run python -m undercurrent.stories.export --processed data/merged && uv run pytest pipeline/tests/test_contracts.py
```

```bash
for T in forecasts.parquet model_metrics.json forecast_report.json; do gcloud storage cp data/merged/$T gs://$BUCKET/merged/; done; gcloud storage rsync -r data/export gs://$BUCKET/export
```

Vectors aren't downloaded for the merge: the forecast and the export only need the articles and the story tables. Teammates get the globe data with `gcloud storage rsync -r gs://$BUCKET/export data/export`, then `npm run dev`.

## When something goes wrong

| Problem | Fix |
|---|---|
| **No GPU quota granted** | Pull your block anyway on a CPU VM (`e2-standard-8`, no GPU quota needed) and upload the articles. Post in the chat: an account with spare GPUs runs the embed loop on your days with `--start/--end` set to your block, then uploads the vectors. Then sync them down and track on your CPU VM |
| BigQuery `403` / access denied | Give the VM's service account *BigQuery Job User* and *BigQuery Read Session User* in your project (IAM page), or check that you created the VM with `--scopes=cloud-platform` |
| `ZONE_RESOURCE_POOL_EXHAUSTED` when creating the VM | No L4s free in that zone: try `us-central1-b` or `-c`. Same quota |
| Bucket `403` on upload | Cooper hasn't added your VM's service account yet (Cooper only, above) |
| The VM restarted | Start the same tmux commands again: pull and embed skip finished days, and the tracker carries on without `--fresh` |
| `ManifestMismatch` from the vector store | A different model was used. Don't set `UC_EMBEDDING_MODEL`. Ask in the chat before deleting anything |
| Disk full | `df -h`. Make sure step 6 has run, then delete `data/processed/vectors` only **after** tracking has finished |

## Cost per account (estimates)

- **VM:** `g2-standard-8` about $0.85 an hour, `g2-standard-24` about $2 an hour, so roughly $6–15 for a 7-hour run.
- **BigQuery:** the dry run gives the exact figure. Likely under the free TB per account; at most a few dollars more.
- **Disk:** about $0.50 a day while it exists.
- **Bucket:** about $5 a month for everything (Cooper's account).

Well inside each $300.
