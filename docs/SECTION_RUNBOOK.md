# Section runbook: start, watch and finish a GDELT section on a GPU server

The commands for running one section of `docs/DATA_COVERAGE.md` end to end (pull → embed → track → finish) on a GPU server outside Google Cloud, with `scripts/standalone/section.sh`, plus the matching commands for the Google Cloud block VMs. Background: `docs/PREVIOUS_YEAR.md` (the standalone script) and `docs/TWO_YEAR_RUN.md` (the plan and the Google Cloud VMs).

> **2026-11-08 03:45 UTC: cutoffs pushed** (`2ab8939`). News-news is now 0.80, and the tracker leaves out languages the embedding model can't tell apart. **Any tracking started before this has to be redone:** run `track-fresh` (section 5). Embedding doesn't need redoing. The section below explains which tracking counts.

## What gets tracked, and what needs redoing

**Nobody tracks the whole database.** Tracking is done one section at a time, on the server that pulled and embedded that section:
- **What it covers:** the section's 28 warm-up days, then its own days, in order.
- **What it reads:** only that section's vectors and articles, on that server's disk. It never needs another section's data.
- **What `finish` uploads:** only the section's own days of story tables.

When every section is done, Cooper merges the sections' own days into one set of story tables. The forecast trains on those, and the globe is exported from them (`docs/TWO_YEAR_RUN.md`, "Cooper only").

So the 11 sections (1–10, with 6 split into 6a and 6b) are tracked in parallel, each on its own server. A story that crosses from one section into the next gets a new id there. That's expected.

**Which tracking counts:** only tracking that started **after the cutoffs were pushed** (2026-11-08 03:45 UTC). Anything tracked before then used the placeholder cutoff (0.42), with no language filter. Its stories are wrong, however far it got.

| A section's state | What to do |
|---|---|
| Embedded, never tracked | `track`. It can start while embedding is still running and follows behind it |
| Tracked, any amount, with a run started before 03:45 UTC | `track-fresh`. The old tables are moved to `data/processed/_superseded/` |
| A tracking run started after 03:45 UTC | Leave it. If it stopped, `track` carries on from the last finished day |
| Cooper changes the cutoffs again | Everyone runs `track-fresh` again |

**How to tell which kind of run a server has:**
```bash
grep -n "tracking with\|PLACEHOLDER" ~/undercurrent/logs/track.log | tail -3
```
- **A `PLACEHOLDER` line after the last `tracking with` line,** or no `tracking with` line at all: the run is from before the cutoffs. Run `track-fresh`.
- **Otherwise, a `tracking with N threads` line with a time after 03:45:** the run is current. Leave it.

**What never needs redoing:** the pull and the embedding. The cutoffs and the language filter only change tracking.

**Already uploaded:** story tables reach the bucket only through `finish`. If a section is re-tracked and runs `finish` again, the new upload replaces its earlier files day by day. The exception is `story_lineage`: there's a file only on days with a merge, so an old one can stay behind on a day where the new run merged nothing. Tell Cooper if you re-finish a section.

## For agents

- Use the commands in this file. Don't improvise other ways to pull, embed, track or upload.
- **Never delete anything** in `gs://undercurrent-data-cooper`, and never upload `tracker_state/`.
- **Ask your human** before creating, stopping or deleting a server or VM, and before `finish`.
- Claim a section in `docs/DATA_COVERAGE.md` and push **before** starting it.
- Don't run `track` or `track-fresh` again while the tracker is running: each run restarts it.
- Don't set `UC_EMBEDDING_MODEL`. Every section must use the same vectors.
- Report numbers from `status` or `progress`, not guesses. Section 8 below lists the messages that look like errors but aren't.

## 1. Before you start

- **Claim the section.** Put your name and "started" in its row in `docs/DATA_COVERAGE.md`, and push straight away.
- **One section per home folder.** A second section on a server that already has one goes through `extra_section.sh` (section 6). Never run `setup` for a second section in the same home: it overwrites the first section's `~/block.env`, and the first one still needs it to track and finish.
- **The server needs:**
  - an NVIDIA driver 580 or newer;
  - about 70 GB of free disk for a six-month section;
  - as many CPUs as you can get. Each embedder needs about 2 to keep its GPU busy, and the pull and the tracker need CPUs too.

## 2. Set up and start (one line)

Put your section number in `S=` (3–10, 6a or 6b; see `docs/DATA_COVERAGE.md`):

```bash
S=8 W=/workspace; if [ -f ~/block.env ] && ! grep -q "BLOCK=$S " ~/block.env; then echo "This server already has section $(grep -o "BLOCK=[^ ]*" ~/block.env | cut -d= -f2) on it. Use another server for section $S."; else P=$([ "$(id -u)" = 0 ] || echo sudo); { command -v git && command -v tmux && command -v curl; } >/dev/null || { $P apt-get update -q && $P apt-get install -y -q git tmux curl; }; if [ ! -e ~/undercurrent ]; then if [ -d $W ]; then [ -d $W/uc-$S ] || git clone -q https://github.com/CooperMcCloskey/undercurrent.git $W/uc-$S; ln -sfn $W/uc-$S ~/undercurrent; else git clone -q https://github.com/CooperMcCloskey/undercurrent.git ~/undercurrent; fi; fi; grep -q google-cloud-sdk/bin ~/.bashrc 2>/dev/null || echo "export PATH=\$HOME/google-cloud-sdk/bin:\$HOME/.local/bin:\$PATH" >> ~/.bashrc; bash ~/undercurrent/scripts/standalone/section.sh setup $S && { ~/google-cloud-sdk/bin/gcloud auth list --filter=status:ACTIVE --format="value(account)" | grep -q . || ~/google-cloud-sdk/bin/gcloud auth login --no-launch-browser; } && bash ~/undercurrent/scripts/standalone/section.sh start; fi
```

**What it does:**
- installs git, tmux and curl if they're missing;
- clones the repo into `/workspace/uc-N` (which survives a container restart) and links `~/undercurrent` to it;
- puts gcloud on your PATH;
- runs `setup`: the driver and GPU check, and settings sized to the machine;
- logs you in to Google if needed;
- starts the pull, embed and upload loops.

**After a restart,** paste the same line again: everything carries on where it stopped. It refuses to run on a server that already has a different section.

**Check the settings it prints,** for example `GPUS=2 EMBED_PROCS=8 BATCH_SIZE=1024 PULL_WORKERS=8`.

**On a box with 2 GPUs and 16 or more CPUs,** the pull becomes the slow part. Give it more workers. This restarts only the pull, which carries on where it was:

```bash
sed -i 's/PULL_WORKERS=[0-9]*/PULL_WORKERS=16/' ~/block.env && tmux kill-session -t pull && bash ~/undercurrent/scripts/standalone/section.sh start
```

Check the `days/h` figure at the end of `tail -1 ~/undercurrent/logs/pull.log`: one L40S with 8 workers managed about 220. If the log shows repeated download errors, go back down to 12.

## 3. Google login

- **When the setup line prints a link:** open it, sign in with the Google account Cooper gave write access to the bucket, and paste the code back.
- **If you need access:** send Cooper your Google email. Cooper runs:
  ```bash
  gcloud storage buckets add-iam-policy-binding gs://undercurrent-data-cooper --member=user:EMAIL --role=roles/storage.objectAdmin
  ```
- **If `gcloud: command not found`:** run `source ~/.bashrc`. The setup line adds gcloud to your PATH, but only for new shells.
- **To check you're logged in:** `gcloud config get-value account` should print your email.
- **To check uploads really work:** `bash ~/undercurrent/scripts/standalone/section.sh status` should show `bucket: reachable`. That only proves the bucket can be listed. The proof that uploads work is a recent `vectors synced` in `~/undercurrent/logs/upload.log`, and no `sync failed` lines in `pull.log` after the time you logged in. The pull only logs failures.

## 4. Progress

**One command for every section (job) on a server:** the main one and any started with `extra_section.sh`. It shows each one's pull, embedding and tracking, what's running for it right now, and what to run next:

```bash
git -C ~/undercurrent pull -q                                    # once, to get the script
bash ~/undercurrent/scripts/standalone/progress.sh
watch -n 60 bash ~/undercurrent/scripts/standalone/progress.sh   # refreshes every minute; Ctrl-C stops it
```

```
progress of each section on a0fb64a8b64f, 05:10 UTC (cutoffs pushed 2026-11-08 03:45:00 UTC)
8    pulled 210/210  embedded 210/210  tracked  57/210  27% [#####               ]
     running: track | tracking since 04:20 UTC | last day 2022-12-05, 24 s/day, ~1.0 h left
3b   pulled 120/120  embedded 120/120  tracked   0/120   0% [                    ]
     running: nothing | not tracking yet | last day -, ? s/day
     next: bash ~/undercurrent/scripts/standalone/extra_section.sh 3b track
```

**To make `progress` run it,** paste this once per server. It replaces the older `progress` function, which counted leftover files from runs before the cutoffs and so looked frozen:
```bash
echo 'progress() { bash ~/undercurrent/scripts/standalone/progress.sh; }' >> ~/.bashrc && source ~/.bashrc
```

**How to read it:**
- **`tracked`** counts only the current timeline: the days written since its first day. An old run's leftover files, if they weren't moved aside, don't count.
- **The speed** is from when the last 10 days' files were written.
- **`running`** lists the processes this section has right now: `pull`, `embed xN`, `track`, `loops waiting` (section.sh's loops between tries), or `nothing`.
- **The tracking state** is one of:
  - `tracking since …`;
  - `waiting …`: the loop retries every 2 minutes. "No news vectors for …" means that day isn't embedded or fetched yet;
  - `tracking stopped: <the log's last line>`;
  - `tracking done`;
  - `not tracking yet`;
  - `timeline began …, BEFORE the cutoffs`: needs `track-fresh`.
- **`next:`** appears only when something needs doing. It's the exact command to run.

**On a Google Cloud block VM,** run it from Cloud Shell, after `git -C ~/undercurrent pull -q`:
```bash
Z=$(gcloud compute instances list --filter=name=uc-gpu --format='value(zone.basename())')
gcloud compute ssh uc-gpu --zone=$Z --command='sudo -u uc -H bash -s' < ~/undercurrent/scripts/standalone/progress.sh
```

`section.sh status` still shows the raw details: each embedder's titles a second, GPU use, bucket access, and the last line of every log. Its `tracked` count includes leftover files from older runs.

## 5. Commands

Each of these starts with `bash ~/undercurrent/scripts/standalone/section.sh`:

| Command | What it does |
|---|---|
| `status` | Everything: days pulled, embedded and tracked; titles a second per embedder; GPU use; bucket access; the last line of each log |
| `track [N]` | After "cutoffs pushed": updates the code and tracks, following the embedding if it isn't finished. It resumes from the last finished day |
| `track-fresh [N]` | Tracks again from the start, after the cutoffs or the tracker changed. The old story tables move to `data/processed/_superseded/`; nothing is deleted |
| `finish` | Only once `status` shows `TRACK_DONE`: uploads the section's own story days and a last copy of everything else, and marks the section done in the bucket. It refuses to run without `TRACK_DONE` |

**How tracking uses the machine:**
- **The CPUs:** it uses every CPU the server allows, less what this section's pull and embedders still need. Inside a container that's the container's real limit, not the host's count. Give `N` to choose the number yourself. Each run's first line in `track.log` says how many threads it got.
- **The GPU:** its two heavy searches run on the GPU (`stories/search.py`), which is idle once embedding is done. Set `UC_SEARCH=cpu` to keep it on the CPUs.

**Logs and markers**, all in `~/undercurrent/logs/`:
- **Follow a log:** `tail -F ~/undercurrent/logs/pull.log`. The others are `embed-0.log` (one per embedder), `upload.log` and `track.log`.
- **What's running:** `tmux ls`. Each stage's sessions disappear when it finishes: `pull`, then `embed0`…, then `upload`.
- **What's finished:** `ls ~/undercurrent/logs/*_DONE`. The markers are `PULL_DONE`, `EMBED_DONE` and `TRACK_DONE`.

## 6. A second section on the same server

Use this when a server's first section is still tracking but its GPU is free:

```bash
git -C ~/undercurrent pull -q
bash ~/undercurrent/scripts/standalone/extra_section.sh 9              # set up and start section 9 (safe to rerun)
bash ~/undercurrent/scripts/standalone/extra_section.sh 9 progress
bash ~/undercurrent/scripts/standalone/extra_section.sh 9 status       # and track, track-fresh, finish
```

**How it stays separate:** the second section gets its own home, `/workspace/uc-home-9`, holding its settings, Google login (copied from yours), repo, data and logs. It also has its own tmux. It runs at lower CPU priority, so the first section's tracking isn't slowed down.

**Don't run two new sections on one GPU at the same time:** they only split it. Start the next one when the current one's embedding is done.

## 6b. Tracking-only sections (3b, 7b): from the bucket

Sections 3 and 7 were pulled and embedded whole on one machine each, then split in two for tracking. The a halves track where the data is. The b halves track anywhere, from the bucket's copy:

```bash
git -C ~/undercurrent pull -q
bash ~/undercurrent/scripts/standalone/extra_section.sh 3b            # or 7b; safe to rerun
bash ~/undercurrent/scripts/standalone/extra_section.sh 3b progress
```

**What it does:** sets up the section in its own home, downloads its articles and vectors (`section.sh fetch`: only the half's 28 warm-up days and its own days), then starts `track`.

**If some days aren't in the bucket yet,** as with 7b while section 7 is still embedding, the tracker stops at the first missing day. It then fetches again every 2 minutes and carries on as days arrive.

**`finish`** uploads only the story tables, since the articles and vectors are already in the bucket.

**Size and cost:** a half is about 35 GB on disk (120 days of vectors and articles). Downloading it to a server outside Google Cloud costs Cooper's project about $4 in egress.

**Tracking is a good job to add to a busy server:** each tracker keeps about one core busy, and only briefly uses the GPU.

## 7. Google Cloud block VMs (`create_vm.sh`)

Run these from Cloud Shell. First update Cloud Shell's copy of the repo with `git -C ~/undercurrent pull -q`, then run `bash ~/undercurrent/scripts/gcp/vm.sh <command>`. It finds the VM and its zone itself.

| Command | What it does |
|---|---|
| `status` | Days pulled and embedded, what's running, the last line of each log |
| `speed` | GPU use (5 samples) and load |
| `track`, `track-fresh` | Like the standalone ones. The VM tracks with all of its CPUs and its GPU |
| `finish` | Uploads the block's own story days and marks it done |
| `stop` | Stops the VM. Only after `finish` |

**A `progress` command for Cloud Shell:** it runs `progress.sh` on the VM. Paste it once:

```bash
echo 'progress() { git -C ~/undercurrent pull -q; local z=$(gcloud compute instances list --filter=name=uc-gpu --format="value(zone.basename())" | head -1); gcloud compute ssh uc-gpu --zone="$z" --command="sudo -u uc -H bash -s" < ~/undercurrent/scripts/standalone/progress.sh; }' >> ~/.bashrc && source ~/.bashrc
```

**Unlike `section.sh`, the VM's `track-fresh` leaves the old story tables in place,** so `tracked` counts them, and `finish` would accept them. After a `track-fresh` there, run `finish` only once `track.log` ends with a new `done: … items …` line.

## 8. Messages that look like errors but aren't

| Message | Why it's fine |
|---|---|
| `FileNotFoundError: No articles…` in `embed-*.log` right after `start` | The pull hasn't written a day yet. The embedders retry every minute |
| `Did not find existing container at: data/processed/vectors` in `upload.log` | Nothing is embedded yet. It retries every 10 minutes |
| `sync failed … no active account` in `pull.log` | This happened before you logged in. Only new lines of it matter |
| `filter keys were not present … status` | The login check ran before you'd logged in |
| `faiss … AVX512 / AVX2 … ModuleNotFoundError` | faiss tries its fastest builds first. `Successfully loaded faiss` means it works (with AVX2, through dynamic dispatch) |
| `PLACEHOLDER cutoff …` in `track.log` | The tracker is running on code from before the cutoffs were pushed. Run `track-fresh` |
| GPU at 0% while tracking | Normal: the tracker uses the GPU only for its two searches each day, for a few seconds; the rest of each day runs on the CPUs |
| CPU at a few % on a big server while tracking | Normal: one tracker works through the days in order, and most of each day is single-threaded Python, so it keeps about one core busy. To use the rest of the machine, run another section's pull and embedding next to it (section 6) |

## 9. The order, start to finish

1. **Claim and set up** (sections 1–2). Pulling and embedding a six-month section takes about 1–1.5 hours on one L40S with about 27 CPUs.
2. **Wait for "cutoffs pushed"** (done: see the note at the top). Then `track`, or `track-fresh` if you'd already tracked. This takes hours, depending mostly on the CPUs.
3. **When `status` shows `TRACK_DONE`:** run `finish`.
4. **Mark the section done** in `docs/DATA_COVERAGE.md`, and post it in the chat.
5. **Shut the server down** if you're paying for it by the hour.

## Why we track

The pull and the embedding give us millions of separate headlines, each turned into a vector: a list of numbers that captures what the headline is about. Headlines about the same event get similar vectors. But nothing yet says which headlines belong together.

**Tracking turns those headlines into stories.** It goes through the days in order, and for each headline it either:
- joins it to a story that's already running, if their vectors are close enough (the cutoff), or
- starts a new story from headlines that match each other.

Along the way it merges stories that turn out to be the same, and retires stories that have gone quiet.

**What it writes, for each day:**
- how big each story was that day (`stories_daily`);
- which story each article belongs to (`story_assignments`);
- which stories merged (`story_lineage`).

**Why the project needs it:**
- **The forecast** learns from story sizes over time, to predict how big a story will get in the next days.
- **The globe** shows those stories.

Without tracking, there's nothing to forecast and nothing to show.

**Why each server tracks its own section:**
- **It must go day by day, in order,** carrying forward the stories still running. So it runs where the section's vectors are.
- **The 28 warm-up days** mean stories that were already running when the section starts exist by then. Only the section's own days are uploaded.

**Why every section uses the same cutoffs:** the cutoff defines what counts as "the same story". With different cutoffs, the stories from different sections wouldn't be comparable. That's why every section tracks again with `track-fresh` when the cutoffs change.
