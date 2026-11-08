#!/bin/bash
# One GDELT section end to end (pull -> embed -> track) on ANY Linux server with an NVIDIA GPU,
# inside or outside Google Cloud, saving to the shared bucket. See docs/PREVIOUS_YEAR.md.
#
#   bash ~/undercurrent/scripts/standalone/section.sh setup SECTION   once: tools, code, GPU check, settings
#   bash ~/undercurrent/scripts/standalone/section.sh start           pull + embed + upload loops (safe to rerun)
#   bash ~/undercurrent/scripts/standalone/section.sh status          progress, speed, GPU, logs, bucket access
#   bash ~/undercurrent/scripts/standalone/section.sh track [N]       after "cutoffs pushed": track, following the embedding
#   bash ~/undercurrent/scripts/standalone/section.sh track-fresh [N] the same, starting the timeline again (after the
#                                                                     cutoffs changed); the old story tables are moved aside
#   bash ~/undercurrent/scripts/standalone/section.sh finish          upload the section's own story days, mark it done
#   bash ~/undercurrent/scripts/standalone/section.sh verify          read-only: is everything this section made in the bucket?
#   bash ~/undercurrent/scripts/standalone/section.sh fetch           instead of `start`, for a section pulled and embedded
#                                                                     elsewhere (3b, 7b): download its articles and vectors
#                                                                     from the bucket, then `track` (it keeps fetching new days)
#
# SECTION: 3, 4, 5 (or 5a, 5b), 6a, 6b, 7-10 (9 as 9a, 9b), or the tracking halves 3a, 3b, 7a, 7b (docs/DATA_COVERAGE.md). The pull reads GDELT's 15-minute zip files
# (ingest/gdelt_zip.py), so no BigQuery or Google Cloud project is needed; only `gcloud` logged in
# with write access to the bucket. Everything resumes where it stopped: after a reboot, run `start`.
# Tracking uses every CPU the machine allows (less what this section's pull and embedders still
# need), unless N threads are given; its searches run on the GPU (stories/search.py).
# Quick reference for all of this: docs/SECTION_RUNBOOK.md.
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
    9a) echo "2022-04-10 2022-05-08 2022-08-06" ;; # section 9 split in two halves, like 5: each pulls,
    9b) echo "2022-07-10 2022-08-07 2022-11-06" ;; # embeds and tracks its own days (9b on Google Cloud)
    10) echo "2021-10-10 2021-11-07 2022-05-07" ;;
    # 3 and 7, split in two for tracking only (pulled and embedded whole): the a halves track on the
    # machine that has the data, the b halves anywhere, after `fetch` from the bucket.
    3a) echo "2025-04-10 2025-05-08 2025-08-06" ;;
    3b) echo "2025-07-10 2025-08-07 2025-11-06" ;;
    4b) echo "2025-03-01 2025-03-29 2025-05-07" ;; # 4 split for tracking: 4a on its VM, 4b from the bucket
    7a) echo "2023-04-10 2023-05-08 2023-08-06" ;;
    7b) echo "2023-07-10 2023-08-07 2023-11-06" ;;
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
# CPUs we may use: nproc, capped by a container's CPU quota (inside one, nproc often counts the host).
usable_cpus() {
  local n q p
  n=$(nproc)
  if [ -r /sys/fs/cgroup/cpu.max ]; then
    read -r q p </sys/fs/cgroup/cpu.max
  elif [ -r /sys/fs/cgroup/cpu/cpu.cfs_quota_us ]; then
    q=$(cat /sys/fs/cgroup/cpu/cpu.cfs_quota_us) p=$(cat /sys/fs/cgroup/cpu/cpu.cfs_period_us)
  fi
  if [[ "${q:-}" =~ ^[0-9]+$ && "${p:-0}" -gt 0 ]]; then
    q=$((q / p))
    [ "$q" -ge 1 ] || q=1
    [ "$q" -lt "$n" ] && n=$q
  fi
  echo "$n"
}
# Tracker threads: every usable CPU, less ~2 per embedder and 4 for the pull of this section still running.
track_threads() {
  local t=$(($(usable_cpus) - 2 * $(running embed) - 4 * $(running pull)))
  [ "$t" -ge 2 ] && echo "$t" || echo 2
}

case "${1:-}" in
setup)
  SECTION=${2:-}
  DAYS=$(days_of "$SECTION") || { echo "Unknown section '$SECTION': use 3, 4, 5, 5a, 5b, 6a, 6b, 7-10, 9a, 9b, 3a, 3b, 4b, 7a or 7b (docs/DATA_COVERAGE.md)"; exit 1; }
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
export PULL_WORKERS=$([ "$CPUS" -ge 12 ] && echo 8 || echo 4)
EOF
  echo "== settings (~/block.env):"; cat "$HOME/block.env"

  NEXT="bash $SELF start"
  [[ "$SECTION" =~ ^[347]b$ ]] && NEXT="bash $SELF fetch && bash $SELF track   (tracking only: its data is in the bucket)"
  echo "== bucket"
  if bucket_ok; then
    echo "gs://$BUCKET is reachable as $(gcloud config get-value account 2>/dev/null). Next: $NEXT"
  else
    echo "Can't reach gs://$BUCKET yet. Log in with a Google account Cooper has given write access:"
    echo "  gcloud auth login --no-launch-browser"
    echo "(Cooper: gcloud storage buckets add-iam-policy-binding gs://$BUCKET --member=user:EMAIL --role=roles/storage.objectAdmin)"
    echo "Then: $NEXT"
  fi
  ;;
fetch)
  # A section pulled and embedded on another machine: copy its articles and vectors, from its first
  # warm-up day to its last day, out of the bucket. rsync, so a rerun only fetches what's missing or
  # incomplete; `track` reruns it while it waits for days that aren't in the bucket yet.
  if [ ! -f "$L/FETCHED" ] && { [ -s "$L/pull.log" ] || running pull | grep -qv '^0$'; }; then
    echo "This server pulls and embeds section $BLOCK itself, so there's nothing to fetch: to track it here, run 'track'."
    echo "(fetch is for tracking a section on a different server from the one pulling it.)"
    exit 1
  fi
  bucket_ok || { echo "Can't reach gs://$BUCKET: gcloud auth login --no-launch-browser"; exit 1; }
  export CLOUDSDK_STORAGE_PROCESS_COUNT=4 CLOUDSDK_STORAGE_THREAD_COUNT=8 # nothing to starve here
  DATES=$(d=$PULL_START; while [[ ! "$d" > "$OWN_END" ]]; do printf '%s|' "$d"; d=$(date -ud "$d + 1 day" +%F); done)
  DATES=${DATES%|}
  mkdir -p data/processed/articles data/processed/vectors
  touch "$L/FETCHED" # before the copy: an interrupted fetch must still be retried by `track`
  gcloud storage rsync -r -q -x "^(?!day=($DATES)/)" "gs://$BUCKET/processed/articles" data/processed/articles &&
    gcloud storage rsync -r -q -x "^(?!manifest\.json\$|news/($DATES)\.(npy|parquet)\$)" \
      "gs://$BUCKET/processed/vectors" data/processed/vectors
  OK=$?
  echo "$(date -u) fetched from gs://$BUCKET: articles $(pulled)/$(expected) days, vectors $(embedded)/$(expected)"
  exit $OK
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
  N=${2:-auto}
  [[ "$N" = auto || "$N" =~ ^[1-9][0-9]*$ ]] || { echo "usage: section.sh $1 [THREADS]"; exit 1; }
  git pull -q --ff-only && uv sync -q
  tmux kill-session -t track 2>/dev/null
  for _ in $(seq 30); do # let the old tracker exit before touching its tables
    pgrep -f "undercurrent.stories.track --start $PULL_START " >/dev/null || break
    sleep 1
  done
  rm -f "$L/TRACK_DONE"
  if [ "$1" = track-fresh ]; then
    # Moved, not deleted: a new timeline must not be mixed with (or finished on) the old one's days.
    OLD=data/processed/_superseded/$(date -u +%Y%m%dT%H%M%S)
    for T in story_assignments stories_daily story_lineage tracker_state; do
      [ -d "data/processed/$T" ] && mkdir -p "$OLD" && mv "data/processed/$T" "$OLD/"
    done
    [ -d "$OLD" ] && echo "the previous story tables are in $R/$OLD (not deleted)"
  fi
  launch track "bash $SELF _track $([ "$1" = track-fresh ] && echo --fresh || echo -) $N"
  echo "tracking $PULL_START..$OWN_END, following the embedding; watch: tail -F $L/track.log"
  ;;
_track)
  F=${2:--} T=${3:-auto}
  [ "$F" = - ] && F=""
  # The tracker stops at the first day without vectors; carry on from there until the section is done.
  # The thread count is worked out again on each run, so it grows as the pull and embedders finish.
  until
    [ -f "$L/FETCHED" ] && bash "$SELF" fetch >>"$L/fetch.log" 2>&1 # days uploaded since the last try
    N=$([ "$T" = auto ] && track_threads || echo "$T")
    echo "$(date -u) tracking with $N threads" >>"$L/track.log"
    OMP_NUM_THREADS=$N uv run python -m undercurrent.stories.track --start "$PULL_START" --end "$OWN_END" $F >>"$L/track.log" 2>&1
  do
    F="" # --fresh only on the first run
    sleep 120
  done
  echo "$(date -u) TRACK DONE" | tee -a "$L/track.log" >"$L/TRACK_DONE"
  ;;
finish)
  [ -f "$L/TRACK_DONE" ] || { echo "Tracking hasn't finished (no $L/TRACK_DONE). Not uploading."; exit 1; }
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
  if [ -f "$L/FETCHED" ]; then
    echo "(articles and vectors came from the bucket: not uploading them again)"
  else
    for T in articles gdelt_totals gdelt_files; do
      [ -d "data/processed/$T" ] && gcloud storage rsync -r -q "data/processed/$T" "gs://$BUCKET/processed/$T"
    done
    gcloud storage rsync -r -q -x '.*\.tmp[0-9]+$' data/processed/vectors "gs://$BUCKET/processed/vectors"
  fi
  echo "section $BLOCK $OWN_START..$OWN_END done $(date -u)" | gcloud storage cp - "gs://$BUCKET/status/block-$BLOCK.done"
  echo "Uploaded. Mark section $BLOCK done in docs/DATA_COVERAGE.md and post it in the chat."
  echo
  bash "$SELF" verify
  ;;
verify)
  # Read-only: is everything this section made in the bucket? Its own days of the three story
  # tables (same size as here), the articles and vectors of every day (warm-up too), the done marker.
  bucket_ok || { echo "Can't reach gs://$BUCKET: gcloud auth login --no-launch-browser"; exit 1; }
  B=gs://$BUCKET/processed
  TMP=$(mktemp -d)
  trap 'rm -rf "$TMP"' EXIT
  for T in stories_daily story_assignments story_lineage; do # "size name" per object
    gcloud storage ls -l "$B/$T/" 2>/dev/null | awk '$3 ~ /^gs:/ {n = $3; sub(".*/", "", n); print n, $1}' | sort >"$TMP/$T"
  done
  gcloud storage ls "$B/vectors/news/" 2>/dev/null | sed 's|.*/||' | sort >"$TMP/vectors"
  for T in articles gdelt_totals; do
    gcloud storage ls "$B/$T/" 2>/dev/null | sed -n 's|.*/day=\([0-9-]*\)/$|\1|p' | sort >"$TMP/$T"
  done
  days() { d=$1; while [[ ! "$d" > "$2" ]]; do echo "$d"; d=$(date -ud "$d + 1 day" +%F); done; }
  WANT=$((($(date -ud "$OWN_END" +%s) - $(date -ud "$OWN_START" +%s)) / 86400 + 1))
  ALL=$(expected)
  BAD=0
  for T in stories_daily story_assignments story_lineage; do
    shopt -s nullglob
    tot=0 same=0
    for f in data/processed/$T/*.parquet; do
      d=$(basename "$f" .parquet)
      [[ "$d" < "$OWN_START" || "$d" > "$OWN_END" ]] && continue
      tot=$((tot + 1))
      [ "$(awk -v n="$d.parquet" '$1 == n {print $2}' "$TMP/$T")" = "$(stat -c %s "$f")" ] && same=$((same + 1))
    done
    need=$([ $T = story_lineage ] && echo "$tot" || echo "$WANT")
    state=ok
    [ "$tot" -lt "$need" ] && state="ONLY $tot OF $WANT OWN DAYS HERE: is tracking finished?"
    [ "$same" -lt "$tot" ] && state="$((tot - same)) MISSING OR DIFFERENT IN THE BUCKET: run finish"
    [ "$state" = ok ] || BAD=1
    printf '  %-18s %4d/%-4d own-day files in the bucket, same size  %s\n' "$T" "$same" "$tot" "$state"
  done
  missing=$(days "$PULL_START" "$OWN_END" | while read -r d; do
    grep -qx "$d.npy" "$TMP/vectors" && grep -qx "$d.parquet" "$TMP/vectors" || echo "$d"; done | wc -l)
  printf '  %-18s %4d/%-4d days (npy and parquet)                  %s\n' vectors $((ALL - missing)) "$ALL" "$([ "$missing" = 0 ] && echo ok || echo "MISSING: run finish")"
  [ "$missing" = 0 ] || BAD=1
  for T in articles gdelt_totals; do
    missing=$(days "$PULL_START" "$OWN_END" | grep -cvxFf "$TMP/$T")
    printf '  %-18s %4d/%-4d days                                     %s\n' "$T" $((ALL - missing)) "$ALL" "$([ "$missing" = 0 ] && echo ok || echo "MISSING: run finish")"
    [ "$missing" = 0 ] || BAD=1
  done
  if gcloud storage ls "gs://$BUCKET/status/block-$BLOCK.done" >/dev/null 2>&1; then
    echo "  done marker        status/block-$BLOCK.done                        ok"
  else
    echo "  done marker        status/block-$BLOCK.done                        MISSING: run finish"
    BAD=1
  fi
  if [ $BAD = 0 ]; then
    date -u >"$L/VERIFIED" # progress.sh stops listing the section once its data is deleted
    echo "Everything section $BLOCK made is in gs://$BUCKET. This server's copy is no longer needed:"
    echo "  rm -rf $R/data/processed $R/data/raw"
    echo "(or delete the server). If the cutoffs change again, 'fetch' brings the data back from the bucket."
  else
    echo "NOT everything is in the bucket yet: keep this server's data."
    exit 1
  fi
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
