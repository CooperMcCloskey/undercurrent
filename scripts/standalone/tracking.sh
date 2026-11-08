#!/bin/bash
# Tracking progress of every section on this server, since the cutoffs were pushed: the main one
# (~/block.env) and any started with extra_section.sh (/workspace/uc-home-*, ~/uc-home-*).
#
#   bash ~/undercurrent/scripts/standalone/tracking.sh
#   watch -n 60 bash ~/undercurrent/scripts/standalone/tracking.sh
#
# A Google Cloud block VM, from Cloud Shell (ZONE from `gcloud compute instances list`):
#   gcloud compute ssh uc-gpu --zone=ZONE --command='sudo -u uc -H bash -s' < ~/undercurrent/scripts/standalone/tracking.sh
#
# Per section: days tracked since the cutoffs / all days, the last day done, seconds a day over the
# last 10 days, roughly how long is left, and whether the tracker is running. Only story files
# written after the cutoffs were pushed are counted, so a run from before them shows as 0 and is
# flagged: it needs track-fresh.
CUTOFFS="2026-11-08 03:45:00 UTC" # 2ab8939: news-news 0.80 and the language filter
CUT=$(date -ud "$CUTOFFS" +%s)

one() { # $1 = a home folder with block.env and undercurrent/
  local h=$1 n t p started state last secs left
  (
    cd "$h/undercurrent" 2>/dev/null && source "$h/block.env" 2>/dev/null || exit 0
    n=$((($(date -ud "$OWN_END" +%s) - $(date -ud "$PULL_START" +%s)) / 86400 + 1))
    t=$(find data/processed/stories_daily -name '*.parquet' -newermt "$CUTOFFS" 2>/dev/null |
      sed 's|.*/||; s|\.parquet$||' | awk -v s="$PULL_START" -v e="$OWN_END" '$1 >= s && $1 <= e' | wc -l)
    # the last day lines of the tracker: "<day>  ... active  41.0s (match ...)"
    read -r last secs < <(grep -E '^20[0-9]{2}-[0-9]{2}-[0-9]{2} ' logs/track.log 2>/dev/null | tail -10 |
      awk '{for (i = NF; i > 1; i--) if ($i ~ /^[0-9.]+s$/) {s += $i + 0; k++; break}; d = $1}
           END {if (k) printf "%s %.0f\n", d, s / k}')
    p=""
    for q in $(pgrep -f "undercurrent.stories.track --start $PULL_START "); do # the python process,
      [[ "$(ps -o comm= -p "$q")" == python* ]] && { p=$q; break; }          # not a shell quoting it
    done
    if [ -n "$p" ]; then
      started=$(date -d "$(ps -o lstart= -p "$p")" +%s 2>/dev/null || echo 0)
      state="running"
      [ "$started" -lt "$CUT" ] && state="OLD RUN (started before the cutoffs): track-fresh"
    elif [ -f logs/TRACK_DONE ] || tail -1 logs/track.log 2>/dev/null | grep -q '^done:'; then
      state="done"
    elif [ -f logs/track.log ]; then
      state="stopped: $(grep -v '^\s*$' logs/track.log | tail -1 | cut -c1-70)"
    else
      state="not started"
    fi
    grep -q '("news", "news"): 0.80' pipeline/undercurrent/vectors/thresholds.py 2>/dev/null ||
      state="$state; code has the OLD cutoffs: track-fresh"
    left=""
    [ -n "${secs:-}" ] && [ "$t" -lt "$n" ] && left=$(awk -v d=$((n - t)) -v s="$secs" 'BEGIN {printf "~%.1f h left", d * s / 3600}')
    awk -v b="$BLOCK" -v t="$t" -v n="$n" -v last="${last:--}" -v secs="${secs:-?}" -v left="$left" -v st="$state" 'BEGIN {
      w = int(20 * t / n); bar = sprintf("%*s", w, ""); gsub(/ /, "#", bar)
      printf "%-4s %4d/%-4d %3d%% [%-20s]  last %s, %s s/day %s  %s\n", b, t, n, 100 * t / n, bar, last, secs, left, st }'
  )
}

echo "tracking since the cutoffs ($CUTOFFS), $(date -u +%H:%M) UTC"
for h in "$HOME" /workspace/uc-home-* "$HOME"/uc-home-*; do
  [ -f "$h/block.env" ] && one "$h"
done
