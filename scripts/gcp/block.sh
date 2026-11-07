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
#
# Settings come from ~/block.env (written by vm_startup.sh). Logs are in ~/undercurrent/logs/.
set -uo pipefail
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
  # One embed loop per GPU; it stops by itself once every day is embedded and the pull is over.
  for g in $(seq 0 $((GPUS - 1))); do
    launch "embed$g" "bash $0 _embed-loop $g"
  done
  launch upload "bash $0 _upload-loop"
  bash "$0" status
  ;;
_embed-loop)
  while true; do
    CUDA_VISIBLE_DEVICES=$2 uv run python -m undercurrent.vectors.store --source news \
      --start "$PULL_START" --end "$OWN_END" --shard "$2/$GPUS" >>"$L/embed-$2.log" 2>&1
    if [ "$(running pull)" = 0 ] && [ "$(embedded_days)" -ge "$(expected_days)" ]; then
      echo "all $(expected_days) days embedded $(date -u)" | tee -a "$L/embed-$2.log" >"$L/EMBED_DONE"
      exit 0
    fi
    sleep 300
  done
  ;;
_upload-loop)
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
  sed -n '2,13p' "$0"
  exit 1
  ;;
esac
