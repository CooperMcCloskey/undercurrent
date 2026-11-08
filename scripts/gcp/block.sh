#!/bin/bash
# One account's block of the two-year run (docs/TWO_YEAR_RUN.md), on its VM, as user `uc`:
#
#   sudo -u uc -H bash /home/uc/undercurrent/scripts/gcp/block.sh COMMAND
#
#   start        dry run (once; stops if over MAX_GB), then the pull, embed and upload loops
#   status       what's pulled, embedded and running, and the latest log lines
#   track        after "cutoffs pushed": git pull, then track the block (resumes if it stopped)
#   track-fresh  the same, starting the timeline again (after the cutoffs changed)
#   finish       upload this block's own story days, a last sync, and status/block-N.done
#   export [DAYS [LINK ASSIGN]]  track the newest DAYS days (default 60, plus 28 warm-up) in their own
#                folder and export them to gs://BUCKET/export. Runs detached; the block is untouched
#   export-log   the latest lines of the export's log     export-stop  stop a running export
#
# Settings come from ~/block.env (written by vm_startup.sh). Logs are in ~/undercurrent/logs/.
set -uo pipefail
SELF=$(cd "$(dirname "$0")" && pwd)/$(basename "$0")  # absolute, since we cd below
export PATH=$HOME/.local/bin:$PATH
source "$HOME/block.env"
R=$HOME/undercurrent
L=$R/logs
cd "$R" && mkdir -p "$L"

expected_days() { echo $((($(date -ud "$OWN_END" +%s) - $(date -ud "$PULL_START" +%s)) / 86400 + 1)); }
pulled_days() { find data/processed/articles -maxdepth 1 -name 'day=*' 2>/dev/null | wc -l; }
embedded_days() { find data/processed/vectors/news -name '*.parquet' 2>/dev/null | wc -l; }
running() { tmux ls -F '#{session_name}' 2>/dev/null | grep -c "^$1" || true; }
launch() { tmux has-session -t "$1" 2>/dev/null || tmux new -d -s "$1" -c "$R" "$2"; }

case "${1:-}" in
start)
  if [ ! -f "$L/dryrun.ok" ]; then
    uv run python -m undercurrent.ingest.gdelt_bq --start "$PULL_START" --end "$OWN_END" \
      --batch-days "$BATCH" --dry-run 2>&1 | tee "$L/dryrun.log"
    GB=$(grep -oE 'Total [0-9,.]+ GB' "$L/dryrun.log" | sed -E 's/Total ([0-9,.]+) GB/\1/; s/,//g')
    if [ -z "$GB" ] && ! grep -q "already done" "$L/dryrun.log"; then
      echo "Dry run failed (see $L/dryrun.log): not starting." | tee "$L/STOPPED"; exit 1
    fi
    if [ -n "$GB" ] && [ "${GB%.*}" -gt "$MAX_GB" ]; then
      echo "Dry run says $GB GB, over MAX_GB=$MAX_GB: not starting. To go ahead anyway:" \
        "sed -i 's/MAX_GB=.*/MAX_GB=99999/' ~/block.env && rm $L/STOPPED, then: block.sh start" | tee "$L/STOPPED"
      exit 1
    fi
    echo "${GB:-0}" >"$L/dryrun.ok"
  fi
  rm -f "$L/STOPPED"
  # The pull, split into PULLS date ranges (each skips the days it already has).
  i=0
  uv run python - <<'EOF' | while read -r a b; do
import datetime as dt, os
s, e = dt.date.fromisoformat(os.environ["PULL_START"]), dt.date.fromisoformat(os.environ["OWN_END"])
n, days = int(os.environ["PULLS"]), (e - s).days + 1
for i in range(n):
    print(s + dt.timedelta(days * i // n), s + dt.timedelta(days * (i + 1) // n - 1))
EOF
    launch "pull$i" "uv run python -m undercurrent.ingest.gdelt_bq --start $a --end $b --batch-days $BATCH >> $L/pull-$i.log 2>&1"
    i=$((i + 1))
  done
  # Two embed loops per GPU, each with its own share of the days: while one prepares a day on
  # the CPU (load, clean, dedupe), the other keeps the GPU busy. One loop alone leaves the GPU
  # idle more than half the time. They stop by themselves once every day is embedded.
  EMB=$((GPUS * 2))
  for k in $(seq 0 $((EMB - 1))); do
    launch "embed$k" "bash $SELF _embed-loop $((k % GPUS)) $k $EMB"
  done
  launch upload "bash $SELF _upload-loop"
  bash "$SELF" status
  ;;
_embed-loop)  # GPU, shard, shards
  while true; do
    CUDA_VISIBLE_DEVICES=$2 uv run python -m undercurrent.vectors.store --source news \
      --start "$PULL_START" --end "$OWN_END" --shard "$3/$4" >>"$L/embed-$3.log" 2>&1
    if [ "$(running pull)" = 0 ] && [ "$(embedded_days)" -ge "$(expected_days)" ]; then
      echo "all $(expected_days) days embedded $(date -u)" | tee -a "$L/embed-$3.log" >"$L/EMBED_DONE"
      exit 0
    fi
    sleep 120
  done
  ;;
_upload-loop)
  # A backup, so it must not starve the embedder: lowest CPU priority, and one gcloud process
  # (by default gcloud storage starts a copy process per CPU, which swamps a 4-vCPU VM).
  renice -n 19 $$ >/dev/null
  export CLOUDSDK_STORAGE_PROCESS_COUNT=1 CLOUDSDK_STORAGE_THREAD_COUNT=4
  while true; do
    last=no  # decided before syncing, so the final pass includes everything
    [ -f "$L/EMBED_DONE" ] && [ "$(running embed)" = 0 ] && last=yes
    for T in articles gdelt_totals vectors; do
      [ -d "data/processed/$T" ] && gcloud storage rsync -r "data/processed/$T" "gs://$BUCKET/processed/$T" -q
    done >>"$L/upload.log" 2>&1
    echo "synced $(date -u)" >>"$L/upload.log"
    [ $last = yes ] && { echo "final sync done" >>"$L/upload.log"; exit 0; }
    sleep 900
  done
  ;;
track | track-fresh)
  git pull -q --ff-only && uv sync -q
  FRESH=""
  { [ "$1" = track-fresh ] || [ ! -f data/processed/tracker_state/state.pkl ]; } && FRESH=--fresh
  [ "$(embedded_days)" -ge "$(expected_days)" ] ||
    echo "Note: only $(embedded_days)/$(expected_days) days embedded; the tracker stops at the first missing day (run 'track' again later to carry on)."
  tmux kill-session -t track 2>/dev/null
  launch track "OMP_NUM_THREADS=$(nproc) uv run python -m undercurrent.stories.track --start $PULL_START --end $OWN_END $FRESH >> $L/track.log 2>&1"
  echo "tracking ($FRESH); watch: tail -f $L/track.log"
  ;;
finish)
  shopt -s nullglob
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
  WANT=$((($(date -ud "$OWN_END" +%s) - $(date -ud "$OWN_START" +%s)) / 86400 + 1))
  [ "$N" = "$WANT" ] || { echo "stories_daily has $N of $WANT own days: is tracking finished? Not uploading."; exit 1; }
  for T in story_assignments stories_daily story_lineage; do
    gcloud storage rsync -r "$HOME/own/$T" "gs://$BUCKET/processed/$T" -q
  done
  for T in articles gdelt_totals vectors; do
    gcloud storage rsync -r "data/processed/$T" "gs://$BUCKET/processed/$T" -q
  done
  echo "block $BLOCK $OWN_START..$OWN_END done $(date -u)" | gcloud storage cp - "gs://$BUCKET/status/block-$BLOCK.done"
  echo "Uploaded. Post 'block $BLOCK done' in the chat, then stop the VM from Cloud Shell."
  ;;
export)
  # export [DAYS [LINK ASSIGN]]: the first real data for the globe. Runs in its own tmux session, so
  # it goes on if Cloud Shell closes. The block's own tracker state and story files are never touched.
  shift
  DAYS=${1:-60}
  [[ "$DAYS" =~ ^[0-9]+$ ]] && [ "$DAYS" -ge 8 ] || { echo "usage: block.sh export [DAYS [LINK ASSIGN]]" >&2; exit 1; }
  CUTS=""
  if [ $# -gt 1 ]; then
    [[ "${2:-}" =~ ^[01]\.[0-9]+$ && "${3:-}" =~ ^[01]\.[0-9]+$ ]] ||
      { echo "give both cutoffs: block.sh export $DAYS LINK ASSIGN   e.g. 0.80 0.85" >&2; exit 1; }
    CUTS="--link-sim $2 --assign-sim $3"
  fi
  tmux has-session -t export 2>/dev/null &&
    { echo "An export is already running. Watch it: vm.sh export-log. Stop it: vm.sh export-stop." >&2; exit 1; }
  git pull -q --ff-only || { echo "git pull failed; nothing started." >&2; exit 1; }
  uv sync -q
  read -r START WARM < <(uv run python -c "
import datetime as dt
end = dt.date.fromisoformat('$OWN_END')
start = end - dt.timedelta(days=$DAYS - 1)
print(start, start - dt.timedelta(days=28))")
  MISSING=$(uv run python - "$WARM" "$OWN_END" <<'PY'
import datetime as dt, pathlib, sys
first, last = (dt.date.fromisoformat(a) for a in sys.argv[1:3])
vectors = pathlib.Path("data/processed/vectors/news")
days = (first + dt.timedelta(i) for i in range((last - first).days + 1))
print(" ".join(str(d) for d in days if not (vectors / f"{d}.parquet").exists()))
PY
)
  if [ -n "$MISSING" ]; then
    echo "Not every day from $WARM to $OWN_END is embedded yet; missing: $(echo $MISSING | cut -c1-200)" >&2
    echo "The embedders go oldest to newest, so the newest days come last. Check: vm.sh status" >&2
    exit 1
  fi
  if [ -z "$CUTS" ] && ! uv run python -c "
import sys
from undercurrent import config
from undercurrent.vectors.thresholds import CALIBRATED
sys.exit(0 if (config.EMBEDDING_MODEL, ('news', 'news')) in CALIBRATED else 1)"; then
    echo "The news-news cutoff in thresholds.py is still the placeholder (nothing is in CALIBRATED). It was" >&2
    echo "tuned on the hand-written demo items, and on real headlines it makes a few giant stories." >&2
    echo "Give the cutoffs yourself:  vm.sh export $DAYS 0.80 0.85   (vm.sh preview shows what they do)," >&2
    echo "or push calibrated ones first." >&2
    exit 1
  fi
  : >"$L/export.log"
  launch export "bash $SELF _export-run $START $WARM $CUTS >> $L/export.log 2>&1"
  echo "Export started: $START to $OWN_END (the tracker warms up from $WARM). It keeps going if you close Cloud Shell."
  echo "Watch it: vm.sh export-log     Stop it: vm.sh export-stop"
  ;;
_export-run)  # START WARM [tracker options]
  START=$2 WARM=$3
  shift 3
  W=$HOME/export_work OUT=$HOME/export
  rm -rf "$W" "$OUT" && mkdir -p "$W"
  ln -s "$R/data/processed/vectors" "$W/vectors"
  ln -s "$R/data/processed/articles" "$W/articles"
  echo "[$(date -u +%H:%M:%S)] tracking $WARM to $OWN_END ($*)"
  OMP_NUM_THREADS=$(nproc) nice uv run python -m undercurrent.stories.track --processed "$W" --fresh \
    --start "$WARM" --end "$OWN_END" "$@" || { echo "EXPORT FAILED while tracking"; exit 1; }
  echo "[$(date -u +%H:%M:%S)] exporting $START to $OWN_END"
  uv run python -m undercurrent.stories.export --processed "$W" --out "$OUT" --start "$START" --end "$OWN_END" ||
    { echo "EXPORT FAILED while exporting"; exit 1; }
  gcloud storage rsync -r "$OUT" "gs://$BUCKET/export" -q || { echo "EXPORT FAILED while uploading"; exit 1; }
  echo "[$(date -u +%H:%M:%S)] EXPORT DONE: $START to $OWN_END is in gs://$BUCKET/export"
  echo "On your laptop (needs gcloud there):  gcloud storage rsync -r gs://$BUCKET/export data/export   then  npm run dev"
  ;;
export-log)
  tail -n 30 "$L/export.log" 2>/dev/null || echo "No export log yet: nothing has been started."
  tmux has-session -t export 2>/dev/null && echo "(still running)" || echo "(not running)"
  ;;
export-stop)
  tmux kill-session -t export 2>/dev/null && echo "export stopped" || echo "no export is running"
  ;;
status)
  echo "block $BLOCK: pulls $PULL_START..$OWN_END, owns $OWN_START..$OWN_END; $GPUS GPU(s), $PULLS pulls, bucket $BUCKET"
  echo "dry run: $(cat "$L/dryrun.ok" 2>/dev/null || echo 'not done') GB"
  echo "pulled $(pulled_days)/$(expected_days) days, embedded $(embedded_days)/$(expected_days)"
  [ -f "$L/STOPPED" ] && echo "STOPPED: $(cat "$L/STOPPED")"
  [ -f "$L/EMBED_DONE" ] && echo "embedding: $(cat "$L/EMBED_DONE")"
  echo "running: $(tmux ls -F '#{session_name}' 2>/dev/null | tr '\n' ' ')"
  for f in "$L"/*.log; do echo "--- $(basename "$f"): $(grep -v '^\s*$' "$f" | tail -1 | cut -c1-160)"; done
  df -h / | tail -1 | awk '{print "disk: " $3 " used of " $2}'
  ;;
*)
  sed -n '2,16p' "$SELF"
  exit 1
  ;;
esac
