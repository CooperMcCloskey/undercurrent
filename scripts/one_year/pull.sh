#!/bin/bash
# The CPU pull VM of docs/ONE_YEAR_EACH.md: GDELT zip files -> this block's articles in the bucket.
#
#   bash ~/undercurrent/scripts/one_year/pull.sh start    start the pull in tmux (resumes; safe to rerun)
#   bash ~/undercurrent/scripts/one_year/pull.sh status   days done locally and in the bucket, gaps, last log lines
#
# Settings come from ~/block.env. The log is ~/undercurrent/logs/pull.log.
set -uo pipefail
export PATH=$HOME/.local/bin:$PATH
source "$HOME/block.env"
R=$HOME/undercurrent
L=$R/logs
cd "$R" && mkdir -p "$L"

expected() { echo $((($(date -ud "$OWN_END" +%s) - $(date -ud "$PULL_START" +%s)) / 86400 + 1)); }
in_bucket() {
  gcloud storage ls "gs://$BUCKET/processed/articles/" 2>/dev/null |
    sed -n 's|.*/day=\([0-9-]*\)/$|\1|p' | awk -v s="$PULL_START" -v e="$OWN_END" '$1 >= s && $1 <= e' | wc -l
}

case "${1:-}" in
start)
  if tmux has-session -t pull 2>/dev/null; then
    echo "already running; watch: tail -f $L/pull.log"
    exit 0
  fi
  tmux new -d -s pull -c "$R" "bash $0 _loop"
  echo "pulling $PULL_START..$OWN_END, oldest day first; watch: tail -f $L/pull.log"
  ;;
_loop)
  # Exits non-zero if a day or the upload failed: retry until the whole block is in the bucket.
  until uv run python -m undercurrent.ingest.gdelt_zip --start "$PULL_START" --end "$OWN_END" \
    --oldest-first --workers "${PULL_WORKERS:-24}" --sync-to "gs://$BUCKET/processed" >>"$L/pull.log" 2>&1; do
    echo "$(date -u) some days or the upload failed; retrying in 60 s" >>"$L/pull.log"
    sleep 60
  done
  echo "$(date -u) PULL DONE: $(expected) days" | tee -a "$L/pull.log" >"$L/PULL_DONE"
  ;;
status)
  echo "block $BLOCK: pulls $PULL_START..$OWN_END ($(expected) days) into gs://$BUCKET"
  uv run python -m undercurrent.ingest.gdelt_zip --status --start "$PULL_START" --end "$OWN_END"
  echo "in the bucket: $(in_bucket)/$(expected) days"
  [ -f "$L/PULL_DONE" ] && cat "$L/PULL_DONE"
  echo "running: $(tmux ls -F '#{session_name}' 2>/dev/null | tr '\n' ' ')"
  grep -v '^\s*$' "$L/pull.log" 2>/dev/null | tail -3 | cut -c1-200
  df -h / | tail -1 | awk '{print "disk: " $3 " used of " $2}'
  ;;
*)
  sed -n '2,7p' "$0"
  exit 1
  ;;
esac
