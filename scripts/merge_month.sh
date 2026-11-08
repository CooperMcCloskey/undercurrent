#!/bin/bash
# The FAST merge: scripts/merge.sh, but the forecast trains on one month of stories instead of
# every section's five years. Use it when there isn't time for the full merge. Same steps, on a
# short window that ends on the last tracked day in the bucket:
#   the window's files from the bucket -> Wikipedia -> forecast -> globe export -> export-final.tgz
#
#   bash ~/undercurrent/scripts/merge_month.sh plan      print the window's dates, download nothing
#   bash ~/undercurrent/scripts/merge_month.sh start     start it in tmux (survives SSH closing)
#   bash ~/undercurrent/scripts/merge_month.sh status    how far it got, and the latest log lines
#   bash ~/undercurrent/scripts/merge_month.sh stop      stop it (rerun `start` later: downloads are kept)
#
# The window, going backwards from the last tracked day (END):
#   test   TEST_DAYS (28)       the walk-forward test: the backtest table is scored on these days
#   cal    CAL_DAYS (14) + 14   calibrates the 80% ranges (+14: the longest horizon's outcome)
#   train  TRAIN_DAYS (30)      the month the model learns from
#   burn   21                   history before the first row (the features look back up to 21 days)
# That is 107 days (about 7 GB of articles), not five years (100-150 GB). Expect 10-20 minutes.
#
# What makes it fast, compared with merge.sh:
#   - downloads only the window's days, all five tables at once, into its own folder
#     (data/merged_month), so a half-finished full merge in data/merged is never read;
#   - one test window (--folds 0) instead of four, and a 14-day calibration block instead of 28;
#   - hgb (trees) only, no neural net, unless MODELS says otherwise;
#   - the export covers the window only; the tarball uses pigz when it's installed.
#
# Options (environment): BUCKET, END (last day, default: the last tracked day in the bucket),
# TRAIN_DAYS, TEST_DAYS, CAL_DAYS, MODELS (default "hgb"), TOP (stories a day, default 750),
# START (first day to export, default: the first day with a forecast row), WIKI=0 (skip the
# Wikipedia features), EXPORT_OPTS="--no-title-categories --no-episodes" (the two slowest export
# steps). It uploads export-final.tgz and merged/{forecasts.parquet,...}, the same names as merge.sh.
set -uo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
SELF=$R/scripts/merge_month.sh
BUCKET=${BUCKET:-undercurrent-data-cooper}
MODELS=${MODELS:-hgb}
TOP=${TOP:-750}
TRAIN_DAYS=${TRAIN_DAYS:-30}
TEST_DAYS=${TEST_DAYS:-28}
CAL_DAYS=${CAL_DAYS:-14}
BURN_IN=21 # forecast.settings.BURN_IN_DAYS: rows before this are dropped
MAX_H=14   # max(config.HORIZONS)
M=$R/data/merged_month
L=$R/logs
export PATH=$HOME/google-cloud-sdk/bin:$HOME/.local/bin:$PATH
export CLOUDSDK_STORAGE_PROCESS_COUNT=${CLOUDSDK_STORAGE_PROCESS_COUNT:-8}
mkdir -p "$L" "$M"
cd "$R" || exit 1

# Sets END, TEST_START, CAL_START, FIRST_ROW (first training row) and FIRST (first day downloaded).
window() {
  [ "$TEST_DAYS" -gt "$MAX_H" ] || { echo "TEST_DAYS must be more than $MAX_H, or the 14-day horizon is never scored"; exit 1; }
  if [ -z "${END:-}" ]; then
    END=$(gcloud storage ls "gs://$BUCKET/processed/stories_daily/" 2>/dev/null |
      sed -n 's#.*/\([0-9-]*\)\.parquet$#\1#p' | sort | tail -1)
  fi
  [ -n "$END" ] || { echo "No stories_daily in gs://$BUCKET (or no access): gcloud auth login"; exit 1; }
  TEST_START=$(date -ud "$END - $((TEST_DAYS - 1)) days" +%F)
  CAL_START=$(date -ud "$TEST_START - $((CAL_DAYS + MAX_H)) days" +%F)
  FIRST_ROW=$(date -ud "$CAL_START - $TRAIN_DAYS days" +%F)
  FIRST=$(date -ud "$FIRST_ROW - $BURN_IN days" +%F)
  NDAYS=$((($(date -ud "$END" +%s) - $(date -ud "$FIRST" +%s)) / 86400 + 1))
}

print_window() {
  echo "  download  $FIRST -> $END ($NDAYS days, about $((NDAYS * 70 / 1000)) GB of articles)"
  echo "  burn-in   $FIRST -> $(date -ud "$FIRST_ROW - 1 day" +%F) (history only, no rows)"
  echo "  train     $FIRST_ROW -> $(date -ud "$CAL_START - 1 day" +%F) ($TRAIN_DAYS days of rows)"
  echo "  calibrate $CAL_START -> $(date -ud "$TEST_START - 1 day" +%F)"
  echo "  test      $TEST_START -> $END ($TEST_DAYS days: the backtest table)"
  echo "  export    ${START:-$FIRST_ROW} -> $END"
}

case "${1:-}" in
plan)
  window
  print_window
  ;;
start)
  command -v uv >/dev/null || { echo "uv is missing: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }
  command -v gcloud >/dev/null || { echo "gcloud is missing (section.sh setup installs it)"; exit 1; }
  gcloud storage ls "gs://$BUCKET/status/" >/dev/null 2>&1 ||
    { echo "Can't read gs://$BUCKET with this login. gcloud auth login, then ask Cooper for access."; exit 1; }
  tmux has-session -t merge-month 2>/dev/null && { echo "A fast merge is already running: bash $SELF status"; exit 1; }
  rm -f "$L/MERGE_MONTH_DONE"
  : >"$L/merge_month.log"
  tmux new -d -s merge-month -c "$R" "EXPORT_OPTS='${EXPORT_OPTS:-}' MODELS='$MODELS' TOP='$TOP' START='${START:-}' END='${END:-}' TRAIN_DAYS='$TRAIN_DAYS' TEST_DAYS='$TEST_DAYS' CAL_DAYS='$CAL_DAYS' BUCKET='$BUCKET' WIKI='${WIKI:-1}' bash $SELF _run >> $L/merge_month.log 2>&1"
  echo "Fast merge started. Watch it: bash $SELF status   (or: tail -f $L/merge_month.log)"
  ;;
_run)
  set -e
  T0=$(date +%s)
  step() { echo; echo "== $(date -u +%H:%M) UTC (+$((($(date +%s) - T0) / 60)) min)  $*"; }

  step "the window"
  window
  print_window

  step "space check"
  FREE=$(df -B1 --output=avail "$M" | tail -1)
  NEED=$((NDAYS * 70000000 * 13 / 10 + 10000000000))
  echo "need about $((NEED / 1000000000)) GB; free here: $((FREE / 1000000000)) GB"
  [ "$FREE" -gt "$NEED" ] || { echo "Not enough disk: free up space or use a bigger machine."; exit 1; }

  step "code: git pull and uv sync"
  git pull -q --ff-only || echo "(git pull failed: carrying on with this copy of the code)"
  uv sync -q

  step "download the window's days only (all five tables at once)"
  # Files outside the window, from an earlier run with other dates, would be read too: drop them.
  shopt -s nullglob
  for f in "$M"/{stories_daily,story_assignments,story_lineage}/*.parquet; do
    d=$(basename "$f" .parquet)
    if [[ "$d" < "$FIRST" || "$d" > "$END" ]]; then rm -f "$f"; fi
  done
  for p in "$M"/{articles,gdelt_totals}/day=*; do
    d=${p##*day=}
    if [[ "$d" < "$FIRST" || "$d" > "$END" ]]; then rm -rf "$p"; fi
  done
  shopt -u nullglob
  DATES=""
  d=$FIRST
  while [[ ! "$d" > "$END" ]]; do
    DATES="$DATES${DATES:+|}$d"
    d=$(date -ud "$d + 1 day" +%F)
  done
  declare -A JOBS=()
  for T in story_assignments stories_daily story_lineage articles gdelt_totals; do
    mkdir -p "$M/$T"
    case $T in
    articles | gdelt_totals) KEEP="^(?!day=($DATES)/)" ;; # day=YYYY-MM-DD/part.parquet
    *) KEEP="^(?!($DATES)\.parquet\$)" ;;                 # YYYY-MM-DD.parquet
    esac
    gcloud storage rsync -r -q -x "$KEEP" "gs://$BUCKET/processed/$T" "$M/$T" &
    JOBS[$!]=$T
  done
  FAILED=""
  for pid in "${!JOBS[@]}"; do
    wait "$pid" || { [ "${JOBS[$pid]}" = story_lineage ] || FAILED="$FAILED ${JOBS[$pid]}"; }
  done
  [ -z "$FAILED" ] || { echo "Download failed:$FAILED. Run start again (it keeps what it got)."; exit 1; }
  for T in story_assignments stories_daily story_lineage articles gdelt_totals; do
    echo "  $T: $(ls "$M/$T" | wc -l) of $NDAYS days"
  done
  N=$(ls "$M/stories_daily" | wc -l)
  [ "$N" -ge $((NDAYS - BURN_IN)) ] || { echo "Only $N of $NDAYS story days in the window: is END right?"; exit 1; }

  # Wikipedia features: A's two tables from the bucket, then each story's entities (B). If any
  # of it fails, the forecast still runs, just without Wikipedia features. WIKI=0 skips it.
  rm -f "$M/story_wiki_entities.parquet"
  if [ "${WIKI:-1}" != 0 ]; then
    step "Wikipedia: A's tables, then each story's entities (stories.wiki_entities)"
    if gcloud storage cp -q "gs://$BUCKET/wiki/wiki_selection.parquet" \
         "gs://$BUCKET/wiki/wiki_entity_daily.parquet" "$M/" &&
       uv run python -m undercurrent.stories.wiki_entities --processed "$M"; then
      echo "  Wikipedia features on"
    else
      rm -f "$M/story_wiki_entities.parquet"
      echo "  Wikipedia step failed: the forecast runs without Wikipedia features"
    fi
  fi

  step "forecast ($MODELS) on one month; the backtest prints model vs baseline"
  DEV=cpu; command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null 2>&1 && DEV=cuda
  # --min-train-days is a floor, not the window: the window above already gives TRAIN_DAYS.
  OMP_NUM_THREADS=$(nproc) uv run python -m undercurrent.forecast.run --source stories \
    --processed "$M" --out "$M" --model $MODELS --device $DEV \
    --folds 0 --final-test-days "$TEST_DAYS" --cal-days "$CAL_DAYS" \
    --min-train-days $((TRAIN_DAYS / 2))

  step "export (top $TOP a day, from ${START:-$FIRST_ROW})"
  rm -rf "$R/data/export.tmp"
  uv run python -m undercurrent.stories.export --processed "$M" --out "$R/data/export" --force \
    --top "$TOP" --start "${START:-$FIRST_ROW}" ${EXPORT_OPTS:-}
  uv run pytest -q -p no:cacheprovider pipeline/tests/test_contracts.py

  step "upload export-final.tgz and the forecast outputs"
  if command -v pigz >/dev/null; then
    tar -I pigz -cf "$R/data/export-final.tgz" -C "$R/data" export
  else
    tar czf "$R/data/export-final.tgz" -C "$R/data" export
  fi
  gcloud storage cp -q "$R/data/export-final.tgz" "gs://$BUCKET/export-final.tgz"
  for f in forecasts.parquet model_metrics.json forecast_report.json; do
    [ -f "$M/$f" ] && gcloud storage cp -q "$M/$f" "gs://$BUCKET/merged/$f"
  done
  step "DONE in $((($(date +%s) - T0) / 60)) min: export-final.tgz ($(du -h "$R/data/export-final.tgz" | cut -f1))"
  echo "Download it: https://console.cloud.google.com/storage/browser/$BUCKET (click the file, then Download)"
  echo "MERGE MONTH DONE" >"$L/MERGE_MONTH_DONE"
  ;;
stop)
  tmux kill-session -t merge-month 2>/dev/null && echo "fast merge stopped" || echo "no fast merge running"
  ;;
status)
  if tmux has-session -t merge-month 2>/dev/null; then echo "running"; elif [ -f "$L/MERGE_MONTH_DONE" ]; then echo "done"; else echo "not running"; fi
  grep -E "^== " "$L/merge_month.log" 2>/dev/null | tail -3
  echo "--- latest lines:"
  tail -n 6 "$L/merge_month.log" 2>/dev/null | cut -c1-200
  ;;
*)
  sed -n '2,30p' "$SELF"
  exit 1
  ;;
esac
