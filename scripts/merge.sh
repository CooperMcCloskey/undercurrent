#!/bin/bash
# The final merge, in one command: every finished section's stories from the bucket -> forecast
# -> the globe export -> export-final.tgz in the bucket, to download for the web app.
#
#   bash ~/undercurrent/scripts/merge.sh start     start it in tmux (survives SSH closing)
#   bash ~/undercurrent/scripts/merge.sh status    how far it got, and the latest log lines
#
# Run it on the machine with the most CPUs that can read the bucket (a section server, or a
# Google Cloud VM: in us-central1 the download is fast and free). It needs `uv` and `gcloud`
# with read and write access to the bucket, and disk for every section's articles (it checks).
# Safe to run again: downloads only what changed, and redoes the forecast and export.
#
# A section is included once its `finish` has uploaded its own story days (status/*.done in the
# bucket). Sections still tracking are left out; run this again later to add them.
# Options (environment): BUCKET, MODELS (default "hgb"; "hgb gru" adds the net, best on a GPU),
# START (first day to export, default: the first merged day), TOP (stories a day, default 300).
set -uo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
SELF=$R/scripts/merge.sh
BUCKET=${BUCKET:-undercurrent-data-cooper}
MODELS=${MODELS:-hgb}
TOP=${TOP:-300}
M=$R/data/merged
L=$R/logs
export PATH=$HOME/google-cloud-sdk/bin:$HOME/.local/bin:$PATH
export CLOUDSDK_STORAGE_PROCESS_COUNT=${CLOUDSDK_STORAGE_PROCESS_COUNT:-4}
mkdir -p "$L" "$M"
cd "$R" || exit 1

case "${1:-}" in
start)
  command -v uv >/dev/null || { echo "uv is missing: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }
  command -v gcloud >/dev/null || { echo "gcloud is missing (section.sh setup installs it)"; exit 1; }
  gcloud storage ls "gs://$BUCKET/status/" >/dev/null 2>&1 ||
    { echo "Can't read gs://$BUCKET with this login. gcloud auth login, then ask Cooper for access."; exit 1; }
  tmux has-session -t merge 2>/dev/null && { echo "A merge is already running: bash $SELF status"; exit 1; }
  : >"$L/merge.log"
  tmux new -d -s merge -c "$R" "MODELS='$MODELS' TOP='$TOP' START='${START:-}' BUCKET='$BUCKET' bash $SELF _run >> $L/merge.log 2>&1"
  echo "Merge started. Watch it: bash $SELF status   (or: tail -f $L/merge.log)"
  ;;
_run)
  set -e
  T0=$(date +%s)
  step() { echo; echo "== $(date -u +%H:%M) UTC  $*"; }
  step "finished sections in the bucket"
  gcloud storage ls "gs://$BUCKET/status/" 2>/dev/null | sed 's#.*/##' | tr '\n' ' '; echo

  step "space check"
  NEED=$(gcloud storage du -s "gs://$BUCKET/processed/articles" | awk '{print $1}')
  FREE=$(df -B1 --output=avail "$M" | tail -1)
  echo "articles in the bucket: $((NEED / 1000000000)) GB; free here: $((FREE / 1000000000)) GB"
  [ "$FREE" -gt $((NEED + NEED / 5 + 20000000000)) ] ||
    { echo "Not enough disk: free up space or use a bigger machine."; exit 1; }

  step "download: story tables, articles, totals (only what changed)"
  git pull -q --ff-only || echo "(git pull failed: carrying on with this copy of the code)"
  uv sync -q
  for T in story_assignments stories_daily story_lineage articles gdelt_totals; do
    mkdir -p "$M/$T"
    gcloud storage rsync -r -q "gs://$BUCKET/processed/$T" "$M/$T" || { [ "$T" = story_lineage ] || exit 1; }
    echo "  $T: $(ls "$M/$T" | wc -l) files"
  done
  uv run python - "$M" <<'PY'
import datetime as dt, pathlib, sys
days = sorted(dt.date.fromisoformat(p.stem) for p in pathlib.Path(sys.argv[1], "stories_daily").glob("*.parquet"))
runs = []
for d in days:
    if runs and (d - runs[-1][1]).days == 1:
        runs[-1][1] = d
    else:
        runs.append([d, d])
print(f"  merged story days: {len(days)}, in {len(runs)} stretch(es):")
for a, b in runs:
    print(f"    {a} -> {b} ({(b - a).days + 1} days)")
PY

  step "forecast ($MODELS) on every merged story; the backtest prints model vs baseline"
  DEV=cpu; command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null 2>&1 && DEV=cuda
  OMP_NUM_THREADS=$(nproc) uv run python -m undercurrent.forecast.run --source stories \
    --processed "$M" --out "$M" --model $MODELS --device $DEV

  step "export (top $TOP a day, English titles, theme categories, junk left out)"
  rm -rf "$R/data/export.tmp"
  OPTS="--top $TOP"; [ -n "${START:-}" ] && OPTS="$OPTS --start $START"
  uv run python -m undercurrent.stories.export --processed "$M" --out "$R/data/export" --force $OPTS
  uv run pytest -q -p no:cacheprovider pipeline/tests/test_contracts.py

  step "upload export-final.tgz and the forecast outputs"
  tar czf "$R/data/export-final.tgz" -C "$R/data" export
  gcloud storage cp -q "$R/data/export-final.tgz" "gs://$BUCKET/export-final.tgz"
  for f in forecasts.parquet model_metrics.json forecast_report.json; do
    [ -f "$M/$f" ] && gcloud storage cp -q "$M/$f" "gs://$BUCKET/merged/$f"
  done
  step "DONE in $((($(date +%s) - T0) / 60)) min: export-final.tgz ($(du -h "$R/data/export-final.tgz" | cut -f1))"
  echo "Download it: https://console.cloud.google.com/storage/browser/$BUCKET (click the file, then Download)"
  echo "MERGE DONE" >"$L/MERGE_DONE"
  ;;
status)
  if tmux has-session -t merge 2>/dev/null; then echo "running"; elif [ -f "$L/MERGE_DONE" ]; then echo "done"; else echo "not running"; fi
  grep -E "^== " "$L/merge.log" 2>/dev/null | tail -3
  echo "--- latest lines:"
  tail -n 6 "$L/merge.log" 2>/dev/null | cut -c1-200
  ;;
*)
  sed -n '2,17p' "$SELF"
  exit 1
  ;;
esac
