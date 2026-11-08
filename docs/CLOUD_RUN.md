# Cloud run: GDELT on Google Cloud, fastest path

How to go from nothing to GDELT → vectors → stories → forecasts, on the $300 Google Cloud credit, as fast as possible. The *why* behind each choice is in `README.md` ("Scaling up"); this file is the *how*, in order.

- **Steps 0–6** are the one-year run on two small machines.
- **`docs/TWO_YEAR_RUN.md`** is the plan we're running: two years split across 3–4 people's accounts, one VM each.
- **"Multi-year: 5–10 years in parallel"** (further down) scales the same pipeline to 5–10 years on bigger machines. Steps 0–2 (accounts, quota, login, the one-day test) come first either way.

**Prices and sizes below are estimates** (list prices as we know them, unmeasured data). Check the real numbers at step 2 and write them in `PLAN.md` Notes (A).

## The shape

```
 laptop ──(test one day)──▶  BigQuery (GDELT, US)
                                  │
 CPU VM "pull"  ── gdelt_bq ──────┘ ──▶ bucket gs://$BUCKET/processed/articles/
                                              │
 GPU VM "work"  ◀─────────────────────────────┘
   embed (GPU) → vectors ──▶ bucket
   track (CPU process) → story tables ──▶ bucket
   forecast (CPU) → forecasts ──▶ bucket
   export → data/export/ ──▶ laptops (the only thing that leaves the cloud)
```

Two machines only because **GPU quota can take hours or days to approve**, and the pull doesn't need to wait for it. The pull machine is deleted once it's done. Everything else runs on one GPU machine (`README.md` explains why one is enough).

**Every stage is resumable** (one file per day, finished days skipped), so a crashed or restarted machine just carries on.

## 0. Right now: start the slow things (15 minutes)

These take time on Google's side, so do them first.

1. **Upgrade the free-trial account to a paid billing account.** Google's free trial has not allowed GPUs; upgrading keeps the remaining credit. Console → Billing → *Upgrade*. Check the current terms when you do.
2. **Request GPU quota** (Console → IAM & Admin → Quotas), for the project:
   - *GPUs (all regions)*: 1
   - *NVIDIA L4 GPUs* in `us-central1`: 1
3. **Set a budget alert** (Console → Billing → Budgets & alerts): alerts at $50, $150 and $250 of the $300.
4. **Enable the APIs** (from a laptop with `gcloud`, step 1):

   ```bash
   gcloud services enable bigquery.googleapis.com bigquerystorage.googleapis.com compute.googleapis.com storage.googleapis.com
   ```

## 1. Laptop: log in once

```bash
brew install --cask google-cloud-sdk
```

```bash
gcloud init                                   # pick the project and region us-central1
```

```bash
gcloud auth application-default login         # what the Python code uses
```

Put the project in the repo's `.env`: `GOOGLE_CLOUD_PROJECT=your-project-id`. Then set these in your shell for the commands below:

```bash
export PROJECT=your-project-id REGION=us-central1 ZONE=us-central1-a BUCKET=undercurrent-data-yourname
```

## 2. Laptop: test one day and price the year (30 minutes)

```bash
uv run python -m undercurrent.ingest.gdelt_bq --schema
uv run python -m undercurrent.ingest.gdelt_bq --start 2026-11-01 --dry-run
uv run python -m undercurrent.ingest.gdelt_bq --start 2026-11-01
uv run python -m undercurrent.ingest.gdelt_bq --start 2025-11-07 --end 2026-11-06 --dry-run   # the whole year: bytes and $
```

Do the three checks in `pipeline/undercurrent/ingest/GDELT.md` ("First run"): the translated feed is present, most rows have a headline, and days aren't short at the end. Write rows per day, articles per day and MB per day in `PLAN.md` Notes (A).

**Decide now, and don't change them later** (`README.md` checklist item 5): the embedding model, whether to use `UC_HALF=1` on the GPU (twice as fast; vectors differ slightly from full precision, so use it for every day or none), and the test period (the latest 3–6 months, never tuned on).

## 3. Bucket and pull machine (15 minutes, then it runs for hours)

```bash
gcloud storage buckets create gs://$BUCKET --location=$REGION --uniform-bucket-level-access
```

```bash
gcloud compute instances create pull --zone=$ZONE --machine-type=e2-standard-4 --image-family=debian-12 --image-project=debian-cloud --boot-disk-size=150GB --scopes=cloud-platform
```

```bash
gcloud compute ssh pull --zone=$ZONE
```

On the VM (it logs in to BigQuery and the bucket as its own service account, no keys needed):

```bash
sudo apt-get update && sudo apt-get install -y git tmux
curl -LsSf https://astral.sh/uv/install.sh | sh && source $HOME/.local/bin/env
git clone https://github.com/CooperMcCloskey/undercurrent.git && cd undercurrent   # private repo: use `gh auth login` or a token
uv sync
echo "GOOGLE_CLOUD_PROJECT=$(gcloud config get-value project)" > .env
tmux new -s pull
```

Inside tmux (detach with `Ctrl-b d`; the pull keeps running if SSH drops). Newest 60 days first, then the rest, as `PLAN.md` asks:

```bash
uv run python -m undercurrent.ingest.gdelt_bq --start 2026-09-07 --end 2026-11-06 && \
uv run python -m undercurrent.ingest.gdelt_bq --start 2025-11-07 --end 2026-09-06
```

In a second tmux window (`Ctrl-b c`), copy finished days to the bucket every 10 minutes:

```bash
BUCKET=undercurrent-data-yourname; while true; do gcloud storage rsync -r data/processed gs://$BUCKET/processed; sleep 600; done
```

**Meanwhile:** run the forecast's dry run on proxy stories as soon as the newest days exist. It needs articles only, no vectors or stories, and tells you early whether the features work on real data:

```bash
uv run python -m undercurrent.forecast.run --source proxy
```

## 4. GPU machine (once quota is approved)

Pick a current Deep Learning VM image family (it has the NVIDIA drivers):

```bash
gcloud compute images list --project=deeplearning-platform-release --no-standard-images --filter="family~common-cu" --format="value(family)" | sort -u
```

```bash
gcloud compute instances create work --zone=$ZONE --machine-type=g2-standard-8 --maintenance-policy=TERMINATE --image-project=deeplearning-platform-release --image-family=FAMILY_FROM_ABOVE --boot-disk-size=300GB --boot-disk-type=pd-balanced --metadata=install-nvidia-driver=True --scopes=cloud-platform
```

`g2-standard-8` is one L4 GPU, 8 vCPUs and 32 GB RAM. Same setup as the pull VM (uv, clone, `uv sync`, `.env`), then check the GPU: `nvidia-smi`.

### 4a. Embed, while the pull is still running

Start a tmux session. Each pass copies the newly pulled days down from the bucket, embeds every day that has articles and isn't done yet, and pushes the vectors back. Days that aren't pulled yet show as `no input file` and are picked up by a later pass.

```bash
BUCKET=undercurrent-data-yourname
while true; do
  gcloud storage rsync -r gs://$BUCKET/processed/articles data/processed/articles
  uv run python -m undercurrent.vectors.store --source news --start 2025-11-07 --end 2026-11-06
  gcloud storage rsync -r data/processed/vectors gs://$BUCKET/processed/vectors
  sleep 300
done
```

Add `UC_HALF=1` in front of the embed command if you chose it in step 2. Stop the loop once the pull has finished and a pass embeds nothing new.

### 4b. Track stories: newest 60 days first (for the demo), then the whole year

The tracker needs every day in order, in its own process (not the one that embeds). The newest 60 days are embedded first, so they can start as soon as they're done:

```bash
uv run python -m undercurrent.stories.track --start 2026-09-07 --end 2026-11-06 --fresh
```

**Before trusting the stories,** read 20 of them, and calibrate the cutoffs (`README.md`, "Clustering"; `python -m undercurrent.vectors.calibrate`). Bad stories make everything after them noisy. If you change the cutoffs, re-run this step; it takes minutes for 60 days.

When the whole year is embedded, run the full timeline (roughly 1.5–2 hours for a year, by the tracker's measurements on synthetic data):

```bash
uv run python -m undercurrent.stories.track --start 2025-11-07 --end 2026-11-06 --fresh
```

Copy the results up: `gcloud storage rsync -r data/processed gs://$BUCKET/processed`.

### 4c. Forecast

```bash
uv run python -m undercurrent.forecast.run --source stories                          # trees: minutes
uv run python -m undercurrent.forecast.run --source stories --model hgb gru --device cuda   # + the net, on the GPU
```

`pipeline/undercurrent/forecast/README.md` says when each model earns its place.

### 4d. Export to the globe

`stories/export.py` turns the tracker output, the articles and the forecast into `data/export/`: the top 750 stories of each day, with location, footprint, forecast and outcome, plus one detail file per story. It runs on the CPU in seconds to a minute or two (1.2 million assignments took 2 seconds), so run it after every tracker or forecast run:

```bash
uv run python -m undercurrent.stories.export                    # every tracked day
uv run pytest pipeline/tests/test_contracts.py                  # validates data/export/
```

Re-run the forecast after re-running the tracker, before exporting: story ids change between tracker runs, and the exporter counts any forecast whose actuals don't match the snapshots ("stale forecast?" in its output). Expect about 7 MB of JSON per 30 days exported; for a smaller demo export use `--start` and `--top`. Then:

```bash
gcloud storage rsync -r data/export gs://$BUCKET/export        # on the VM
gcloud storage rsync -r gs://$BUCKET/export data/export        # on each laptop; then npm run dev
```

## 5. Parallel lane: Wikipedia

> **Status (2026-11-08):** done from Lucy's laptop. The tables are in `gs://undercurrent-data-cooper/wiki/` (`docs/DATA_COVERAGE.md`), and `scripts/merge.sh` uses them (`docs/ARCHITECTURE.md`, "Wikipedia features, end to end").

Rate-limited, not compute-limited, so it doesn't need the GPU machine. As soon as there are stories, run tier 1 entities from a laptop or the pull VM (`README.md`, "Wikipedia: what the data agent (A) should do now"; `pipeline/undercurrent/ingest/WIKIPEDIA.md`). The top-1000 history needs no entities and can start at step 3.

## 6. Turn things off

```bash
gcloud compute instances delete pull --zone=$ZONE              # as soon as the pull is in the bucket
gcloud compute instances stop work --zone=$ZONE                # between sessions (the disk still bills, about $1/day)
gcloud compute instances delete work --zone=$ZONE              # when done: everything is in the bucket
```

**A GPU machine left running costs about $20 a day.** Stop it whenever nobody is using it.

## Multi-year: 5–10 years in parallel

GDELT's GKG starts in February 2015, so up to about 11.5 years exist. 10 years is roughly 3,650 days: maybe 400–700 million headlines after dedupe (guessing 100–200k a day; step 2 measures it). **Plan around the measured numbers, not these.**

### What limits the speed, and how each part is parallelised

| Stage | Can run in parallel? | How |
|---|---|---|
| Pull | Yes, any split | One machine, one process per year. Days are independent, and BigQuery runs many queries at once |
| Embed | Yes, any split | One process per GPU, each taking a share of the days (`--shard i/n`). Days are independent |
| Track | **Only between timelines** | Days within a timeline must run in order (about 25 seconds a day measured, so 10 years in one timeline would be ~25 hours). So: **one timeline per year, all running at once**, each with a 4-week warm-up (below) |
| Forecast | Within one run | Minutes for the trees on many cores; the net on a GPU |

### The machines

Three machines, **two running at any time**. The pull machine starts immediately and doesn't wait for GPU quota.

| Machine | Type (us-central1) | CPU / RAM / GPU | Disk | Runs | Rough price (on-demand) |
|---|---|---|---|---|---|
| `pull` | `n2-highmem-16` | 16 vCPU, 128 GB, no GPU | 600 GB pd-balanced | 10 pull processes, one per year | ~$1/hour |
| `gpu` | `g2-standard-48` | 48 vCPU, 192 GB, **4× L4** (24 GB each) | 1.5 TB pd-balanced | embedding (4 processes, one per GPU), then the 10 trackers, then training | ~$4/hour |
| *(optional, faster)* | `g2-standard-96` instead | 96 vCPU, 384 GB, **8× L4** | 1.5 TB | as above, with embedding about twice as fast | ~$8/hour |

**Why these sizes:**
- **`pull`:** each pull process holds about 3 days of GKG rows in memory (`--batch-days 3`), roughly 3–6 GB at peak. 10 processes fit in 128 GB with room to spare. The disk holds 10 years of compact articles, maybe 200–400 GB, until they're synced to the bucket.
- **`gpu`:** one machine with 4 GPUs rather than four 1-GPU machines. They share one disk, so nothing is copied between machines. The 48 vCPUs and 192 GB then run all 10 trackers at once (about 2.5–5 GB each; 2.3 GB was the measured peak at 200k items a day) without a third machine. The disk holds the articles plus about 0.8 KB of vector per headline (≈ 300–550 GB for 10 years).
- **L4, not A100.** The MiniLM model is small, so it can't keep a big GPU busy. L4s are the cheapest per headline embedded and the easiest to get quota for.
- **If no 4-GPU machine is available,** use 4× `g2-standard-8` (1 L4, 8 vCPU, 32 GB, 500 GB disk each), each given its own block of years with `--start/--end`. Then run the trackers on one `n2-standard-32` (32 vCPU, 128 GB, 1.5 TB disk) that pulls the vectors from the bucket.

### Quota to request in step 0 (new projects start low)

In `us-central1`:
- *GPUs (all regions)*: 4 (8 for the 96-core machine)
- *NVIDIA L4 GPUs*: 4 (or 8)
- *CPUs*: 64 (16 + 48; 112 with the 8-GPU machine)
- *N2 CPUs*: 16
- *SSD total GB*: 2,500. pd-balanced disks count here, and the default can be as low as 500 GB

Ask for these early. Requests for more than a few GPUs on a new account can be refused; then use whatever is granted and accept a longer embed.

### Phase 1: pull every year at once (machine `pull`)

Set up as in step 3, but with the bigger machine type and disk. Then start one process per year in the background. This example is 10 years ending 2026-11-06; years are calendar years, with the first and last cut to the range:

```bash
for Y in $(seq 2016 2026); do
  S=$([ $Y = 2016 ] && echo 2016-11-07 || echo $Y-01-01)
  E=$([ $Y = 2026 ] && echo 2026-11-06 || echo $Y-12-31)
  tmux new -d -s pull$Y "uv run python -m undercurrent.ingest.gdelt_bq --start $S --end $E --batch-days 3 > pull-$Y.log 2>&1"
done
```

Run the bucket sync loop from step 3 in another tmux window. Watch progress with `tail -f pull-2026.log`. **Dry-run the whole range first** (step 2) so you know the BigQuery bill before any of this starts.

### Phase 2: embed as years land (machine `gpu`)

Set up as in step 4 with `--machine-type=g2-standard-48`, then check that `nvidia-smi` shows 4 GPUs. One process per GPU, each taking every 4th day over the whole range. They share the disk, so there's no splitting of files. Embed one day first, so the vector store's `manifest.json` is written once before the four processes start:

```bash
uv run python -m undercurrent.vectors.store --source news --start 2026-11-06 --end 2026-11-06
```

Then the loop, which keeps going while the pull is still running. Days that aren't pulled yet are skipped and picked up by a later pass:

```bash
BUCKET=undercurrent-data-yourname
for i in 0 1 2 3; do
  tmux new -d -s embed$i "while true; do
    gcloud storage rsync -r gs://$BUCKET/processed/articles data/processed/articles >/dev/null 2>&1
    CUDA_VISIBLE_DEVICES=$i uv run python -m undercurrent.vectors.store --source news --start 2016-11-07 --end 2026-11-06 --shard $i/4
    sleep 300
  done > embed-$i.log 2>&1"
done
```

(Prefix the command with `UC_HALF=1` if you chose half precision in step 2.) The store prints how many titles a second it embeds. **Check that number in the first hour** and redo the time estimate below. Sync `data/processed/vectors` to the bucket every hour or so, and once at the end.

Two cautions:
- Only one `rsync` should write to the articles folder at a time. If the four loops' syncs collide, move the sync into a separate fifth loop.
- Stop the loops when the pull is finished and a pass embeds nothing new.

### Phase 3: one tracker per year, all at once (machine `gpu`, CPU only)

Each year gets its own folder, `data/blocks/<year>/`. Its `articles` and `vectors` are links to the shared ones, so nothing is copied, and each tracker keeps its own state there.

**Each block starts 28 days before its year** (a warm-up), so the stories already running on 1 January exist when the year starts. Only the year's own days are kept. Run this when the whole range is embedded:

```bash
for Y in $(seq 2016 2026); do
  B=data/blocks/$Y; mkdir -p $B
  ln -sfn ../../processed/articles $B/articles
  ln -sfn ../../processed/vectors $B/vectors
  S=$([ $Y = 2016 ] && echo 2016-11-07 || date -d "$Y-01-01 -28 days" +%F)
  E=$([ $Y = 2026 ] && echo 2026-11-06 || echo $Y-12-31)
  tmux new -d -s track$Y "OMP_NUM_THREADS=4 uv run python -m undercurrent.stories.track --processed $B --start $S --end $E --fresh > $B/track.log 2>&1"
done
```

Each block takes roughly 2.5–3 hours, and they all run at once. Then copy each year's own days, not the warm-up, into the shared tables:

```bash
for Y in $(seq 2016 2026); do
  for T in story_assignments stories_daily story_lineage; do
    mkdir -p data/processed/$T
    [ -d data/blocks/$Y/$T ] && find data/blocks/$Y/$T -name "$Y-*.parquet" -exec cp {} data/processed/$T/ \;
  done
done
gcloud storage rsync -r data/processed gs://$BUCKET/processed
```

**Calibrate before Phase 3,** not after. Run steps 4b–4c on the newest 60 days first, read 20 stories, and set the cutoffs. Ten parallel trackers with bad cutoffs waste the run.

**Year boundaries:** a story that runs across 1 January gets a new id in the new year, because each block is its own timeline. Ids come from the story's first article, so they never collide between blocks. Its first 4 weeks of the new year have a shorter history than usual, so treat the first 4 weeks of each block like the README's "drop the first 2–4 weeks" rule when training. Linking stories across the boundary (matching centroids on 31 December) is a possible later improvement.

### Phase 4: train and check that the older years help (machine `gpu`)

```bash
uv run python -m undercurrent.forecast.run --source stories --model hgb gru --device cuda
```

Then the learning curve from `README.md` ("One year first, then older years"). Train on the last 1, 2, 5 and 10 years and test on the same held-out months. **If older years don't lower the error, train on fewer years.** GDELT's mix of sources drifts, so more isn't automatically better. The trees fit in minutes even on 10 years of rows (tens of millions; the machine's 192 GB is plenty). The net is the slow part, at about an hour per full backtest on the GPUs.

### Timeline and cost for 10 years (estimates; redo them with the step 2 numbers)

| Phase | Wall clock | Machine-hours | Cost |
|---|---|---|---|
| 0–2. Accounts, quota, one-day test | ~1 hour, plus the quota wait | laptop | $0 |
| 1. Pull 10 years (10 processes) | ~3–6 hours | `pull` ~5 h | ~$5, plus BigQuery: roughly 1.5–3 TB scanned is ~$5–15 after the free TB (the dry run gives the exact figure) |
| 2. Embed 400–700M headlines on 4 L4s | ~6–12 hours (if one L4 does ~4–6k titles a second; overlaps the pull) | `gpu` ~10 h | ~$40 |
| 3. Track 11 blocks at once | ~3 hours | `gpu` ~3 h | ~$12 |
| 4. Train + learning curve | ~2–4 hours | `gpu` ~3 h | ~$12 |
| Disks while running (~2 days) | | | ~$10 |
| Bucket (articles + vectors, ~0.6–1 TB) | | | ~$12–20 a month |
| **Total** | **~16–24 hours of wall clock after quota is approved** | | **~$90–120**, leaving ~$180 for reruns |

- **With the 8-GPU machine,** embedding takes about half as long for the same money (twice the price per hour, half the hours).
- **Spot machines** cost roughly a third, and every stage resumes after a preemption. But the trackers lose their running hours each time one is interrupted, so use spot for the pull and the embedding only.
- **The biggest cost risk** is the `gpu` machine idling at ~$100 a day. Stop it between phases if nothing is running.
- **5 years instead of 10** halves phases 1–3 and saves about $40.

## Timeline and cost (estimates)

| Step | Wall clock | Cost |
|---|---|---|
| 0–2. Accounts, quota request, one-day test | ~1 hour (quota approval: hours to days) | ~$0 |
| 3. Pull 12 months | hours; depends on BigQuery speed, measure on day one | BigQuery: the first 1 TB a month is free, then ~$6.25/TB. VM: ~$0.15/hour |
| 4a. Embed tens of millions of headlines on one L4 | a few hours (measure: the store prints titles per second) | VM ~$0.85/hour |
| 4b. Track: 60 days, then the year | minutes, then ~2 hours | included above |
| 4c. Forecast | minutes (trees), ~1 hour (net on GPU) | included above |
| Storage: bucket ~100 GB, VM disks | for the hackathon | a few dollars |
| **Total** | **about a day of wall clock once quota is approved** | **roughly $20–60 of the $300** |

That leaves room to rerun the tracker with new cutoffs, try a bigger embedding model, or add a second year (`README.md`, "One year first, then older years").

## After the hackathon

Delete both VMs and keep the bucket (about $2 a month for ~100 GB; less in an archive storage class). Host the web app with the final `data/export/` on a free static host. Daily updates later would cost roughly $10–40 a month on a small CPU machine (no GPU needed for one day of headlines).
