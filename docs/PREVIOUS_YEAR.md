# The previous year: 2023-11-07 → 2024-11-06

The two-year plan (sections 1–4 in `docs/DATA_COVERAGE.md`) covers 2024-11-07 → 2026-11-06. This is the year before it, as extra training data. It's split into three pieces, each run end to end (pull → embed → track) into the same bucket, `gs://undercurrent-data-cooper`:

| Piece | Own days | Warm-up from | Status |
|---|---|---|---|
| **6a** | 2023-11-07 → 2024-02-06 (the first quarter) | 2023-10-10 | **Running** on ControlThePancake's extra GPU server |
| **6b** | 2024-02-07 → 2024-05-07 | 2024-01-10 | **Starting** on another of ControlThePancake's GPU servers, outside Google Cloud |
| **5** | 2024-05-08 → 2024-11-06 | 2024-04-10 | **Next step:** free |

## Next steps: get the rest of the year

6a and 6b are under way. **What's still needed is the rest of the year: section 5.**

1. **Claim it first.** Put your name and "started" in the piece's row in `docs/DATA_COVERAGE.md`, and push straight away, so nobody runs the same days twice.
2. **Sections 3 and 4 come first if they're still free.** They complete the two-year plan for tonight, and newer data helps the forecast more than older data.
3. **Start it:**
   - **Section 5 on Google Cloud:** from Cloud Shell, the usual one command:

     ```bash
     rm -rf undercurrent && git clone -q https://github.com/CooperMcCloskey/undercurrent.git && bash undercurrent/scripts/gcp/create_vm.sh 5
     ```

   - **6b, or 5 on any other GPU server:** use the script below. `create_vm.sh` doesn't know 6b. **Don't run `create_vm.sh 6`**: it would redo 6a.
   - The same script also runs sections **3 and 4** (`section.sh setup 3`). Take one of those first if it's still free.
4. **When it's finished,** mark it done in `docs/DATA_COVERAGE.md`.

## Run a piece on any GPU server: `scripts/standalone/section.sh`

This works on any Linux server with an NVIDIA GPU, inside or outside Google Cloud.
- It pulls from GDELT's 15-minute zip files (`ingest/gdelt_zip.py`), so it needs no BigQuery and no Google Cloud project.
- It writes exactly the files the BigQuery pull writes, to the same bucket.
- It sizes itself to the machine: more embedders and pull workers with more CPUs, and bigger batches on a big GPU.

**You need:**
- Ubuntu (or similar) with `sudo`;
- an NVIDIA driver **580 or newer** (`nvidia-smi` shows it; the PyTorch we use needs CUDA 13);
- about **60 GB of free disk**;
- a Google account with write access to the bucket (step 2).

**Which machine:** CPUs matter as much as the GPU. Each embedder needs about 2 CPUs to keep the GPU busy, and the pull needs CPUs too.
- An A100 with 16 vCPUs will do a 3-month piece in roughly 40 minutes.
- An L4 with 6 vCPUs runs only 2 embedders, so it takes roughly 1.5–2 hours.
- RAM above about 32 GB doesn't help.

These times are estimates.

### 1. Set up (once)

```bash
sudo apt-get update -q && sudo apt-get install -y -q git
git clone -q https://github.com/CooperMcCloskey/undercurrent.git ~/undercurrent
bash ~/undercurrent/scripts/standalone/section.sh setup 6b      # or 6a, or 5
```

`setup` does all of this:
- installs `tmux`, `uv` and the Google Cloud CLI;
- installs the code's dependencies;
- checks the driver and that PyTorch can use the GPU;
- writes `~/block.env` with the piece's dates and settings sized to the machine;
- checks bucket access.

If the driver is too old, it tells you how to upgrade it.

### 2. Bucket access (once, if `setup` says the bucket isn't reachable)

Log in with your Google account:

```bash
gcloud auth login --no-launch-browser
```

It prints a link. Open it, sign in, and paste the code back. Then **send Cooper your Google email**. Cooper runs:

```bash
gcloud storage buckets add-iam-policy-binding gs://undercurrent-data-cooper --member=user:YOUR_EMAIL --role=roles/storage.objectAdmin
```

On a Google Cloud VM made with `--scopes=cloud-platform`, the VM's own service account is used instead. Send Cooper that (`…-compute@developer.gserviceaccount.com`).

### 3. Start

```bash
bash ~/undercurrent/scripts/standalone/section.sh start
```

This starts, in tmux:
- **the pull:** oldest day first, at low priority, uploading each finished day;
- **the embedders**, which follow right behind the pull;
- **a vector upload** every 10 minutes.

Each stops by itself when the piece is done. It's safe to run `start` again at any time, for example after a reboot: everything resumes where it stopped.

### 4. Watch

```bash
bash ~/undercurrent/scripts/standalone/section.sh status
```

It shows days pulled, embedded and tracked, titles a second per embedder, GPU use, bucket access and the last line of each log. Logs are in `~/undercurrent/logs/`, for example `tail -f ~/undercurrent/logs/pull.log`.

### 5. Track, after Cooper posts "cutoffs pushed"

```bash
bash ~/undercurrent/scripts/standalone/section.sh track
```

It updates the code (for the new cutoffs), then tracks from the warm-up's first day. When it reaches a day that isn't embedded yet, it waits and carries on. If the cutoffs change later, use `track-fresh`.

### 6. Finish

When `status` shows `TRACK_DONE`:

```bash
bash ~/undercurrent/scripts/standalone/section.sh finish
```

It uploads the piece's own story days, a last copy of everything else, and `status/block-<piece>.done` to the bucket. Then mark the piece done in `docs/DATA_COVERAGE.md`, and shut the server down if you're paying for it by the hour.

## Two sections on one server: `scripts/standalone/multi.sh`

On a server with 2+ GPUs and 24+ vCPUs, run two sections at once (for example 5a and 5b). It runs `section.sh` once per section, each in its own clone (`~/uc-<S>/undercurrent`), its own home (so its own `block.env` and tmux sessions) and an equal share of the CPUs. The GPUs are shared.

```bash
sudo apt-get update -q && sudo apt-get install -y -q git
git clone -q https://github.com/CooperMcCloskey/undercurrent.git ~/undercurrent
bash ~/undercurrent/scripts/standalone/multi.sh setup 5a 5b && bash ~/undercurrent/scripts/standalone/multi.sh start
```

Then `multi.sh progress` (or `status`), `multi.sh track`, `multi.sh finish`: the same steps as above (`docs/SECTION_RUNBOOK.md`), run for every section. Also `multi.sh login` (gcloud, once for all sections), `multi.sh logs 5a` and `multi.sh attach 5b pull`.

## If something goes wrong

| Problem | Fix |
|---|---|
| `Driver … is too old` | Upgrade the driver to 580 or newer (`setup` prints the Ubuntu command), reboot, and run `setup` again |
| `bucket: NOT reachable` | Step 2. Work carries on locally, and uploads catch up once access works |
| `pull.log` keeps saying `some days … failed` | Read the `ERROR` lines above it. Network errors fix themselves; post anything that repeats |
| `track.log` says `No news vectors for <day>` | Normal: that day isn't embedded yet, and the tracker carries on by itself |
| `ManifestMismatch` | `UC_EMBEDDING_MODEL` is set somewhere. Unset it; every section must use the same model |
| The server rebooted | `section.sh start` (and `track` if it was tracking) |
