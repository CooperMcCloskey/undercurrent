#!/bin/bash
# One GDELT section end to end (pull -> embed -> track) on ANY Linux server with an NVIDIA GPU,
# inside or outside Google Cloud, saving to the shared bucket. See docs/PREVIOUS_YEAR.md.
#
#   bash ~/undercurrent/scripts/standalone/section.sh setup SECTION   once: tools, code, GPU check, settings
#   bash ~/undercurrent/scripts/standalone/section.sh start           pull + embed + upload loops (safe to rerun)
#   bash ~/undercurrent/scripts/standalone/section.sh status          progress, speed, GPU, logs, bucket access
#   bash ~/undercurrent/scripts/standalone/section.sh track           after "cutoffs pushed": track, following the embedding
#   bash ~/undercurrent/scripts/standalone/section.sh track-fresh     the same, starting the timeline again
#   bash ~/undercurrent/scripts/standalone/section.sh finish          upload the section's own story days, mark it done
#
# SECTION: 3, 4, 5 (or 5a, 5b), 6a, 6b or 7-10 (docs/DATA_COVERAGE.md). The pull reads GDELT's 15-minute zip files
# (ingest/gdelt_zip.py), so no BigQuery or Google Cloud project is needed; only `gcloud` logged in
# with write access to the bucket. Everything resumes where it stopped: after a reboot, run `start`.
set -uo pipefail
R=$(cd "$(dirname "$0")/../.." && pwd)
L=$R/logs
SELF=$R/scripts/standalone/section.sh
BUCKET=${BUCKET:-undercurrent-data-cooper}
export PATH=$HOME/google-cloud-sdk/bin:$HOME/.local/bin:$PATH
# One gcloud copy process: by default it starts one per CPU, which starves embedding on a small VM.
export CLOUDSDK_STORAGE_PROCESS_COUNT=1 CLOUDSDK_STORAGE_THREAD_COUNT=4
mkdir -p "$L"
cd "$R" || exit 1

# section -> pull start (28-day warm-up), own start, own end. Keep in step with docs/DATA_COVERAGE.md.
days_of() {
  case "$1" in
    3) echo "2025-04-10 2025-05-08 2025-11-06" ;;
    4) echo "2024-10-10 2024-11-07 2025-05-07" ;;
    5) echo "2024-04-10 2024-05-08 2024-11-06" ;;
    5a) echo "2024-04-10 2024-05-08 2024-08-06" ;; # section 5 split in two halves, one per GPU
    5b) echo "2024-07-10 2024-08-07 2024-11-06" ;;
    6a) echo "2023-10-10 2023-11-07 2024-02-06" ;;
    6b) echo "2024-01-10 2024-02-07 2024-05-07" ;;
    7) echo "2023-04-10 2023-05-08 2023-11-06" ;;
    8) echo "2022-10-10 2022-11-07 2023-05-07" ;;
    9) echo "2022-04-10 2022-05-08 2022-11-06" ;;
    10) echo "2021-10-10 2021-11-07 2022-05-07" ;;
    *) return 1 ;; # 1 and 2 are running on Google Cloud; 6 is split into 6a and 6b
  esac
}

if [ "${1:-}" != setup ]; then
  [ -f "$HOME/block.env" ] || { echo "Run: bash $SELF setup SECTION   (first)"; exit 1; }
  source "$HOME/block.env"
fi
expected() { echo $((($(date -ud "$OWN_END" +%s) - $(date -ud "$PULL_START" +%s)) / 86400 + 1)); }
in_range() { awk -v s="$PULL_START" -v e="$OWN_END" '$1 >= s && $1 <= e' | wc -l; }
pulled() { ls data/processed/articles 2>/dev/null | sed -n 's/^day=//p' | in_range; }
embedded() { ls data/processed/vectors/news 2>/dev/null | sed -n 's/\.parquet$//p' | in_range; }
tracked() { ls data/processed/stories_daily 2>/dev/null | sed -n 's/\.parquet$//p' | in_range; }
running() { tmux ls -F '#{session_name}' 2>/dev/null | grep -c "^$1" || true; }
launch() { tmux has-session -t "$1" 2>/dev/null || tmux new -d -s "$1" -c "$R" "$2"; }
bucket_ok() { gcloud storage ls "gs://$BUCKET/processed/" >/dev/null 2>&1; }

case "${1:-}" in
setup)
  SECTION=${2:-}
  DAYS=$(days_of "$SECTION") || { echo "Unknown section '$SECTION': use 3, 4, 5, 5a, 5b, 6a, 6b or 7-10 (docs/DATA_COVERAGE.md)"; exit 1; }
  read -r PS OS OE <<<"$DAYS"
  echo "== section $SECTION: pulls $PS..$OE, owns $OS..$OE, into gs://$BUCKET"
  if ! command -v tmux >/dev/null || ! command -v git >/dev/null || ! command -v curl >/dev/null; then
    sudo apt-get update -q && sudo apt-get install -y -q git tmux curl
  fi
  command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
  if ! command -v gcloud >/dev/null; then
    echo "== installing the Google Cloud CLI into ~/google-cloud-sdk"
    curl -sSL https://sdk.cloud.google.com | bash -s -- --disable-prompts --install-dir="$HOME" >/dev/null
  fi
  git pull -q --ff-only || echo "(git pull failed; carrying on with this copy of the code)"
  uv sync -q || { echo "uv sync failed"; exit 1; }

  echo "== GPU"
  nvidia-smi -L || { echo "No NVIDIA GPU or driver found (nvidia-smi failed)."; exit 1; }
  DRIVER=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)
  if [ "${DRIVER%%.*}" -lt 580 ]; then
    echo "Driver $DRIVER is too old: the PyTorch in uv.lock (CUDA 13) needs 580 or newer."
    echo "On Ubuntu: sudo apt-get install -y nvidia-driver-580-server && sudo reboot   (or pick your provider's newer driver image)"
    echo "Then run this setup again."
    exit 1
  fi
  uv run python -c 'import torch; assert torch.cuda.is_available(); print("torch", torch.__version__, "sees", torch.cuda.device_count(), "GPU(s)")' ||
    { echo "PyTorch can't use the GPU (driver $DRIVER)."; exit 1; }
  GPUS=$(nvidia-smi -L | wc -l)
  CPUS=$(nproc)
  GPU_GB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1 | tr -dc 0-9)
  GPU_GB=$((GPU_GB / 1024))
  # Sized to the machine: each embedder uses ~2 CPUs between GPU bursts, so CPUs decide how many
  # can keep the GPU busy (2 per GPU on a small VM, 4 with 12+ CPUs); big GPUs take bigger batches.
  PER_GPU=$([ "$CPUS" -ge 12 ] && echo 4 || echo 2)
  FREE_GB=$(df -BG --output=avail "$R" | tail -1 | tr -dc 0-9)
  [ "$FREE_GB" -ge 60 ] || echo "WARNING: only ${FREE_GB} GB free; a section needs about 60 GB (vectors are ~230 MB a day)"

  cat >"$HOME/block.env" <<EOF
export BLOCK=$SECTION PULL_START=$PS OWN_START=$OS OWN_END=$OE
export BUCKET=$BUCKET
export UC_HALF=1 GPUS=$GPUS EMBED_PROCS=$((GPUS * PER_GPU)) BATCH_SIZE=$([ "$GPU_GB" -ge 40 ] && echo 1024 || echo 512)
export PULL_WORKERS=$([ "$CPUS" -ge 12 ] && echo 8 || echo 4) TRACK_THREADS=$([ "$CPUS" -ge 12 ] && echo 6 || echo 2)
EOF
  echo "== settings (~/block.env):"; cat "$HOME/block.env"

  echo "== bucket"
  if bucket_ok; then
    echo "gs://$BUCKET is reachable as $(gcloud config get-value account 2>/dev/null). Next: bash $SELF start"
  else
    echo "Can't reach gs://$BUCKET yet. Log in with a Google account Cooper has given write access:"
    echo "  gcloud auth login --no-launch-browser"
    echo "(Cooper: gcloud storage buckets add-iam-policy-binding gs://$BUCKET --member=user:EMAIL --role=roles/storage.objectAdmin)"
    echo "Then: bash $SELF start"
  fi
  ;;
start)
  bucket_ok || echo "WARNING: gs://$BUCKET isn't reachable yet; work carries on locally and uploads retry."
  launch pull "bash $SELF _pull"
  for k in $(seq 0 $((EMBED_PROCS - 1))); do launch "embed$k" "bash $SELF _embed $k"; done
  launch upload "bash $SELF _upload"
  bash "$SELF" status
  ;;
_pull)
  # Oldest day first, so embedding and tracking can follow right behind. Low priority: the pull
  # is far ahead of the embedding anyway, and the embedders need the CPUs to keep the GPU busy.
  until nice -n 10 uv run python -m undercurrent.ingest.gdelt_zip --start "$PULL_START" --end "$OWN_END" \
    --oldest-first --workers "$PULL_WORKERS" --sync-to "gs://$BUCKET/processed" >>"$L/pull.log" 2>&1; do
    echo "$(date -u) some days or the upload failed; retrying in 60 s" >>"$L/pull.log"
    sleep 60
  done
  echo "$(date -u) PULL DONE: $(expected) days" | tee -a "$L/pull.log" >"$L/PULL_DONE"
  ;;
_embed)
  # EMBED_PROCS embedders share the GPU(s), each with its own share of the days: while one
  # prepares a day on the CPU, another keeps the GPU busy.
  k=$2
  while true; do
    CUDA_VISIBLE_DEVICES=$((k % GPUS)) uv run python -m undercurrent.vectors.store --source news \
      --start "$PULL_START" --end "$OWN_END" --shard "$k/$EMBED_PROCS" --batch-size "$BATCH_SIZE" >>"$L/embed-$k.log" 2>&1
    if [ -f "$L/PULL_DONE" ] && [ "$(embedded)" -ge "$(expected)" ]; then
      echo "$(date -u) all $(expected) days embedded" | tee -a "$L/embed-$k.log" >"$L/EMBED_DONE"
      exit 0
    fi
    sleep 60
  done
  ;;
_upload)
  renice -n 19 $$ >/dev/null
  while true; do
    last=no # decided before syncing, so the final pass includes everything
    [ -f "$L/EMBED_DONE" ] && [ "$(running embed)" = 0 ] && last=yes
    gcloud storage rsync -r -q -x '.*\.tmp[0-9]+$' data/processed/vectors "gs://$BUCKET/processed/vectors" >>"$L/upload.log" 2>&1 &&
      echo "$(date -u) vectors synced" >>"$L/upload.log"
    [ $last = yes ] && { echo "final sync done" >>"$L/upload.log"; exit 0; }
    sleep 600
  done
  ;;
track | track-fresh)
  git pull -q --ff-only && uv sync -q
  tmux kill-session -t track 2>/dev/null
  launch track "bash $SELF _track $([ "$1" = track-fresh ] && echo --fresh)"
  echo "tracking $PULL_START..$OWN_END, following the embedding; watch: tail -f $L/track.log"
  ;;
_track)
  F=${2:-}
  # The tracker stops at the first day without vectors; carry on from there until the section is done.
  until OMP_NUM_THREADS=$TRACK_THREADS uv run python -m undercurrent.stories.track --start "$PULL_START" --end "$OWN_END" $F >>"$L/track.log" 2>&1; do
    F="" # --fresh only on the first run
    sleep 120
  done
  echo "$(date -u) TRACK DONE" | tee -a "$L/track.log" >"$L/TRACK_DONE"
  ;;
finish)
  shopt -s nullglob
  WANT=$((($(date -ud "$OWN_END" +%s) - $(date -ud "$OWN_START" +%s)) / 86400 + 1))
  rm -rf "$HOME/own"
  for T in story_assignments stories_daily story_lineage; do
    mkdir -p "$HOME/own/$T"
    for f in data/processed/$T/*.parquet; do
      d=$(basename "$f" .parquet)
      [[ "$d" < "$OWN_START" || "$d" > "$OWN_END" ]] || cp "$f" "$HOME/own/$T/"
    done
    echo "$T: $(ls "$HOME/own/$T" | wc -l) own days"
  done
  N=$(ls "$HOME/own/stories_daily" | wc -l)
  [ "$N" = "$WANT" ] || { echo "stories_daily has $N of $WANT own days: is tracking finished? Not uploading."; exit 1; }
  for T in story_assignments stories_daily story_lineage; do
    gcloud storage rsync -r -q "$HOME/own/$T" "gs://$BUCKET/processed/$T"
  done
  for T in articles gdelt_totals gdelt_files; do
    [ -d "data/processed/$T" ] && gcloud storage rsync -r -q "data/processed/$T" "gs://$BUCKET/processed/$T"
  done
  gcloud storage rsync -r -q -x '.*\.tmp[0-9]+$' data/processed/vectors "gs://$BUCKET/processed/vectors"
  echo "section $BLOCK $OWN_START..$OWN_END done $(date -u)" | gcloud storage cp - "gs://$BUCKET/status/block-$BLOCK.done"
  echo "Uploaded. Mark section $BLOCK done in docs/DATA_COVERAGE.md and post it in the chat."
  ;;
status)
  echo "section $BLOCK: $PULL_START..$OWN_END (owns from $OWN_START), bucket $BUCKET, $EMBED_PROCS embedder(s) on $GPUS GPU(s)"
  echo "pulled $(pulled)/$(expected), embedded $(embedded)/$(expected), tracked $(tracked)/$(expected)"
  for f in "$L"/embed-*.log; do
    [ -f "$f" ] || continue
    # titles a second over this embedder's last 5 days ("... N embedded  S.Ss")
    grep ' embedded ' "$f" | tail -5 | awk -v f="$(basename "$f")" '
      {for (i = 2; i <= NF; i++) if ($i == "embedded") {x = $(i - 1); gsub(",", "", x); n += x}
       t = $NF; sub("s$", "", t); s += t}
      END {if (s > 0) printf "%s: %.0f titles/s\n", f, n / s}'
  done
  for flag in PULL_DONE EMBED_DONE TRACK_DONE; do [ -f "$L/$flag" ] && echo "$flag: $(cat "$L/$flag")"; done
  bucket_ok && echo "bucket: reachable" || echo "bucket: NOT reachable (gcloud auth login, and ask Cooper for access)"
  echo "running: $(tmux ls -F '#{session_name}' 2>/dev/null | tr '\n' ' ')"
  for f in "$L"/*.log; do
    [ -f "$f" ] && echo "--- $(basename "$f"): $(grep -v '^\s*$' "$f" | tail -1 | cut -c1-160)"
  done
  nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader 2>/dev/null | sed 's/^/GPU use, memory: /'
  df -h "$R" | tail -1 | awk '{print "disk: " $3 " used of " $2}'
  ;;
*)
  sed -n '2,15p' "$SELF"
  exit 1
  ;;
esac
