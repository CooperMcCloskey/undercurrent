#!/bin/bash
# Progress of every section (job) on this server, what each is running right now, and what to run
# next: the main section (~/block.env) and any started with extra_section.sh (/workspace/uc-home-*,
# ~/uc-home-*).
#
#   bash ~/undercurrent/scripts/standalone/progress.sh           this server, in detail
#   watch -n 60 bash ~/undercurrent/scripts/standalone/progress.sh
#   bash ~/undercurrent/scripts/standalone/progress.sh check     one verdict per section: is its tracking
#                                                                 the right one (started after the cutoffs)?
#   bash ~/undercurrent/scripts/standalone/progress.sh publish   copy this server's progress to the bucket
#                                                                 every 5 minutes (tmux session uc-report)
#   bash ~/undercurrent/scripts/standalone/progress.sh all       every server that publishes, from anywhere
#                                                                 gcloud is logged in (Cloud Shell too)
#
# A Google Cloud block VM, from Cloud Shell (after `git -C ~/undercurrent pull -q`); add `check` after `-s`:
#   Z=$(gcloud compute instances list --filter=name=uc-gpu --format='value(zone.basename())')
#   gcloud compute ssh uc-gpu --zone=$Z --command='sudo -u uc -H bash -s' < ~/undercurrent/scripts/standalone/progress.sh
#
# Tracked counts only the current timeline: the story files written since its first day (a fresh
# run rewrites the first day first, then the days in order), so an old run's leftover files don't
# make it look stuck. A timeline that began before the cutoffs were pushed is flagged (track-fresh).
# Tracking speed is from the files' write times over the last 10 days. A process belongs to the
# section whose folder it runs in, so sections side by side on one server are told apart.
CUTOFFS="2026-11-08 03:45:00 UTC" # 2ab8939: news-news 0.80 and the language filter
CUT=$(date -ud "$CUTOFFS" +%s)
MAIN=$HOME
MODE=${1:-report}
BUCKET=${BUCKET:-undercurrent-data-cooper}
REPORTS=gs://$BUCKET/status/progress
export PATH=$HOME/google-cloud-sdk/bin:$PATH

bar() { awk -v t="$1" -v n="$2" 'BEGIN {w = int(20 * t / n); b = sprintf("%*s", w, ""); gsub(/ /, "#", b); printf "[%-20s]", b}'; }
# python processes running module $1 in folder $2
procs() {
  local q c=0
  for q in $(pgrep -f "undercurrent.$1"); do
    [[ "$(ps -o comm= -p "$q")" == python* ]] && [ "$(readlink -f "/proc/$q/cwd" 2>/dev/null)" = "$2" ] && c=$((c + 1))
  done
  echo "$c"
}
loops() { # section.sh / block.sh loops (bash) in folder $1
  local q c=0
  for q in $(pgrep -f "(section|block)\.sh _"); do
    [ "$(readlink -f "/proc/$q/cwd" 2>/dev/null)" = "$1" ] && c=$((c + 1))
  done
  echo "$c"
}

one() { # $1 = a home folder with block.env and undercurrent/
  local h=$1
  (
    cd "$h/undercurrent" 2>/dev/null && source "$h/block.env" 2>/dev/null || exit 0
    # finished, checked and its data deleted (verify's rm line): nothing left to show
    [ ! -d data/processed ] && { [ -f logs/TRACK_DONE ] || [ -f logs/VERIFIED ]; } && exit 0
    real=$(readlink -f .)
    gcp=no
    [ -f logs/dryrun.ok ] || [ -n "${PULLS:-}" ] && gcp=yes
    if [ $gcp = yes ]; then
      how="(from Cloud Shell) bash ~/undercurrent/scripts/gcp/vm.sh"
    elif [ "$h" = "$MAIN" ]; then
      how="bash ~/undercurrent/scripts/standalone/section.sh"
    else
      how="bash ~/undercurrent/scripts/standalone/extra_section.sh $BLOCK"
    fi
    n=$((($(date -ud "$OWN_END" +%s) - $(date -ud "$PULL_START" +%s)) / 86400 + 1))
    inr() { awk -v s="$PULL_START" -v e="$OWN_END" '$1 >= s && $1 <= e' | wc -l; }
    pulled=$(ls data/processed/articles 2>/dev/null | sed -n 's/^day=//p' | inr)
    embedded=$(ls data/processed/vectors/news 2>/dev/null | sed -n 's/\.parquet$//p' | inr)

    # tracking: the current timeline only
    D=data/processed/stories_daily t=0 t0="" last="-" secs=""
    if [ -f "$D/$PULL_START.parquet" ]; then
      t0=$(stat -c %Y "$D/$PULL_START.parquet")
      mapfile -t rows < <(find "$D" -maxdepth 1 -name '*.parquet' -printf '%T@ %f\n' |
        awk -v s="$PULL_START" -v e="$OWN_END" -v t0="$t0" '{d = $2; sub(/\.parquet$/, "", d)
          if (d >= s && d <= e && $1 + 0 >= t0) print d, $1}' | sort)
      t=${#rows[@]}
      last=${rows[t - 1]%% *}
      if [ "$t" -ge 2 ]; then
        k=$((t > 10 ? 10 : t - 1))
        secs=$(awk -v a="${rows[t - 1 - k]#* }" -v b="${rows[t - 1]#* }" -v k="$k" 'BEGIN {printf "%.0f", (b - a) / k}')
      fi
    fi

    # what is running for this section right now
    np=$(procs "ingest.gdelt_" "$real") ne=$(procs "vectors.store" "$real") nt=$(procs "stories.track" "$real")
    nl=$(loops "$real")
    tp="" # when the running tracker process started
    for q in $(pgrep -f "undercurrent.stories.track"); do
      [[ "$(ps -o comm= -p "$q")" == python* ]] && [ "$(readlink -f "/proc/$q/cwd" 2>/dev/null)" = "$real" ] &&
        { tp=$(date -d "$(ps -o lstart= -p "$q")" +%s 2>/dev/null); break; }
    done
    newcode=no
    grep -q '("news", "news"): 0.80' pipeline/undercurrent/vectors/thresholds.py 2>/dev/null && newcode=yes

    if [ "$MODE" = check ]; then
      at() { date -ud "@$1" +%H:%M; }
      if [ -n "$t0" ] && [ "$t0" -lt "$CUT" ]; then
        v="WRONG: its timeline began $(at "$t0") UTC, before the cutoffs (03:45 UTC). Fix: $how track-fresh"
      elif [ $newcode = no ]; then
        v="WRONG: the code here still has the old cutoffs. Fix: $how track-fresh"
      elif [ -n "$tp" ] && [ "$tp" -lt "$CUT" ]; then
        v="WRONG: the running tracker started $(at "$tp") UTC, before the cutoffs. Fix: $how track-fresh"
      elif [ -z "$t0" ] && [ -z "$tp" ]; then
        v="NOT STARTED: nothing tracked yet. Start: $how track"
      elif [ -z "$t0" ]; then
        v="RIGHT: tracking its first day (tracker started $(at "$tp") UTC, after the cutoffs, 0.80 code)"
      else
        v="RIGHT: timeline began $(at "$t0") UTC, after the cutoffs, with the 0.80 code; $t/$n days so far"
      fi
      printf "%-4s %s\n" "$BLOCK" "$v"
      exit 0
    fi
    now=()
    [ "$np" -gt 0 ] && now+=("pull")
    [ "$ne" -gt 0 ] && now+=("embed x$ne")
    [ "$nt" -gt 0 ] && now+=("track")
    [ ${#now[@]} -eq 0 ] && [ "$nl" -gt 0 ] && now+=("loops waiting")
    [ ${#now[@]} -eq 0 ] && now+=("nothing")
    lastlog=$(grep -v '^\s*$' logs/track.log 2>/dev/null | tail -1 | cut -c1-80)
    since=${t0:+$(date -ud "@$t0" +%H:%M) UTC}

    # tracking state and the next command
    next=""
    other="" # another server tracks this section (status/tracking/S.txt, written by extra_section.sh)
    if [ "$nt" -eq 0 ] && [ ! -f logs/TRACK_DONE ] && [ ! -f logs/VERIFIED ]; then
      other=$(CLOUDSDK_CONFIG=$h/.config/gcloud timeout 20 gcloud storage cat "gs://$BUCKET/status/tracking/$BLOCK.txt" 2>/dev/null | awk '{print $1}')
      [ "$other" = "$(hostname)" ] && other=""
    fi
    if [ -n "$t0" ] && [ "$t0" -lt "$CUT" ]; then
      ts="timeline began $since, BEFORE the cutoffs"
      next="$how track-fresh"
    elif [ $newcode = no ]; then
      ts="the code here has the old cutoffs"
      next="$how track-fresh"
    elif [ "$nt" -gt 0 ]; then
      ts="tracking${since:+ since $since}"
      [ -z "$t0" ] && ts="tracking its first day"
    elif [ -f logs/VERIFIED ] && [ ! logs/TRACK_DONE -nt logs/VERIFIED ]; then
      # finished and checked: don't suggest finish again (a second finish only re-uploads)
      ts="FINISHED and verified $(date -ud "@$(stat -c %Y logs/VERIFIED)" +%H:%M) UTC: all in the bucket"
      next="nothing. To free the disk: rm -rf $real/data/processed $real/data/raw"
    elif [ "$t" -ge "$n" ] || [ -f logs/TRACK_DONE ]; then
      ts="tracking done"
      next="$how finish"
    elif [ -n "$other" ]; then
      ts="tracked on $other, not here (status/tracking/$BLOCK.txt): don't track or finish it here"
    elif [ -f logs/track.log ] && [ "$nl" -gt 0 ] && pgrep -f "section.sh _track" >/dev/null; then
      ts="waiting (retries every 2 min): $lastlog"
    elif [ -n "$t0" ] || [ -s logs/track.log ]; then
      ts="tracking stopped: $lastlog"
      next="$how track"
    else
      ts="not tracking yet"
      next="$how track"
    fi
    if [ "$pulled" -lt "$n" ] && [ "$np" -eq 0 ] && [ ! -f logs/FETCHED ] && [ $gcp = no ] && [ "$nl" -eq 0 ]; then
      next="$( [ "$h" = "$MAIN" ] && echo "bash ~/undercurrent/scripts/standalone/section.sh start" || echo "bash ~/undercurrent/scripts/standalone/extra_section.sh $BLOCK")   (the pull isn't running)"
    fi

    left=""
    [ -n "$secs" ] && [ "$t" -lt "$n" ] && left=$(awk -v d=$((n - t)) -v s="$secs" 'BEGIN {printf ", ~%.1f h left", d * s / 3600}')
    printf "%-4s pulled %3d/%-3d  embedded %3d/%-3d  tracked %3d/%-3d %3d%% %s\n" \
      "$BLOCK" "$pulled" "$n" "$embedded" "$n" "$t" "$n" $((100 * t / n)) "$(bar "$t" "$n")"
    printf "     running: %s | %s | last day %s, %s s/day%s\n" "${now[*]}" "$ts" "$last" "${secs:-?}" "$left"
    [ -n "$next" ] && echo "     next: $next"
  )
}

sections() { # every section on this server
  local h
  for h in "$HOME" /workspace/uc-home-* "$HOME"/uc-home-*; do
    [ -f "$h/block.env" ] && one "$h"
  done
}

case "$MODE" in
report)
  echo "progress of each section on $(hostname), $(date -u +%H:%M) UTC (cutoffs pushed $CUTOFFS)"
  sections
  ;;
check)
  echo "is each section's tracking the right one? $(hostname), $(date -u +%H:%M) UTC"
  sections
  ;;
publish)
  SELF=$(readlink -f "$0" 2>/dev/null)
  [ -f "$SELF" ] || { echo "Run publish from the file: bash ~/undercurrent/scripts/standalone/progress.sh publish"; exit 1; }
  if tmux has-session -t uc-report 2>/dev/null; then
    echo "already publishing to $REPORTS/$(hostname).txt (tmux session uc-report)"
    exit 0
  fi
  OUT=/tmp/uc-progress-$(hostname).txt
  tmux new -d -s uc-report "export PATH=$PATH; while true; do bash $SELF > $OUT 2>&1; gcloud storage cp -q $OUT $REPORTS/$(hostname).txt; sleep 300; done"
  echo "publishing this server's progress to $REPORTS/$(hostname).txt every 5 minutes (tmux session uc-report)"
  echo "see every server's: bash ~/undercurrent/scripts/standalone/progress.sh all"
  ;;
all)
  urls=$(gcloud storage ls "$REPORTS/" 2>/dev/null | grep '\.txt$')
  [ -n "$urls" ] || { echo "No server publishes yet. On each server: bash ~/undercurrent/scripts/standalone/progress.sh publish"; exit 1; }
  echo "every publishing server, $(date -u +%H:%M) UTC now (each report says when it was made; they refresh every 5 minutes)"
  for u in $urls; do
    echo
    gcloud storage cat "$u"
  done
  ;;
*)
  sed -n '2,20p' "$0"
  exit 1
  ;;
esac
exit 0
