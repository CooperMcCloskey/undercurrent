#!/bin/bash
# The L4 VM of docs/ONE_YEAR_EACH.md: embeds this block's days as they reach the bucket, then tracks.
#
#   bash ~/undercurrent/scripts/one_year/gpu.sh start        fetch, embed and upload loops in tmux (safe to rerun)
#   bash ~/undercurrent/scripts/one_year/gpu.sh status       fetched / embedded / tracked days, speed, logs
#   bash ~/undercurrent/scripts/one_year/gpu.sh track        after "cutoffs pushed": git pull, then track,
#                                                            following the embedding day by day
#   bash ~/undercurrent/scripts/one_year/gpu.sh track-fresh  the same, starting the timeline again
#   bash ~/undercurrent/scripts/one_year/gpu.sh finish       upload the block's own story days, mark it done
#
# Settings come from ~/block.env (EMBED_PROCS embed processes, default 2, share the GPUs). Logs are in
# ~/undercurrent/logs/. Every loop resumes where it stopped, so after a reboot just run `start` again.
set -uo pipefail
export PATH=$HOME/.local/bin:$PATH
source "$HOME/block.env"
R=$HOME/undercurrent
L=$R/logs
cd "$R" && mkdir -p "$L"
P=${EMBED_PROCS:-2}
G=${GPUS:-1}

expected() { echo $((($(date -ud "$OWN_END" +%s) - $(date -ud "$PULL_START" +%s)) / 86400 + 1)); }
in_range() { awk -v s="$PULL_START" -v e="$OWN_END" '$1 >= s && $1 <= e' | wc -l; }
fetched() { ls data/processed/articles 2>/dev/null | sed -n 's/^day=//p' | in_range; }
embedded() { ls data/processed/vectors/news 2>/dev/null | sed -n 's/\.parquet$//p' | in_range; }
tracked() { ls data/processed/stories_daily 2>/dev/null | sed -n 's/\.parquet$//p' | in_range; }
running() { tmux ls -F '#{session_name}' 2>/dev/null | grep -c "^$1" || true; }
launch() { tmux has-session -t "$1" 2>/dev/null || tmux new -d -s "$1" -c "$R" "$2"; }

# A gcloud --exclude pattern for every day outside this block's months: other blocks stay in the bucket.
not_mine() {
  uv run python -c '
import datetime as dt, os
s, e = (dt.date.fromisoformat(os.environ[k]) for k in ("PULL_START", "OWN_END"))
months = sorted({(s + dt.timedelta(i)).strftime("%Y-%m") for i in range((e - s).days + 1)})
print("^(?!day=(" + "|".join(months) + ")-)")'
}

# Moves fully downloaded days from the bucket mirror into data/processed/articles. Each day folder
# appears there whole, in one rename: the vector store takes an existing day folder as "pulled", so
# it must never see one that gcloud is still writing into.
promote() {
  mkdir -p data/processed/articles data/incoming/.tmp
  for d in data/incoming/articles/day=*; do
    [ -f "$d/part.parquet" ] || continue
    n=$(basename "$d")
    [ -e "data/processed/articles/$n" ] && continue
    rm -rf "data/incoming/.tmp/$n" && mkdir "data/incoming/.tmp/$n" &&
      ln "$d/part.parquet" "data/incoming/.tmp/$n/part.parquet" &&
      mv "data/incoming/.tmp/$n" "data/processed/articles/$n"
  done
}

case "${1:-}" in
start)
  launch fetch "bash $0 _fetch"
  for i in $(seq 0 $((P - 1))); do launch "embed$i" "bash $0 _embed $i"; done
  launch upload "bash $0 _upload"
  bash "$0" status
  ;;
_fetch)
  X=$(not_mine)
  while true; do
    gcloud storage rsync -r -q -x "$X" "gs://$BUCKET/processed/articles" data/incoming/articles >>"$L/fetch.log" 2>&1
    promote
    echo "$(date -u) fetched $(fetched)/$(expected) days" >>"$L/fetch.log"
    [ "$(fetched)" -ge "$(expected)" ] && { echo "all days fetched" >>"$L/fetch.log"; exit 0; }
    sleep 120
  done
  ;;
_embed)
  i=$2
  while true; do
    CUDA_VISIBLE_DEVICES=$((i % G)) uv run python -m undercurrent.vectors.store --source news \
      --start "$PULL_START" --end "$OWN_END" --shard "$i/$P" --batch-size "${BATCH_SIZE:-1024}" >>"$L/embed-$i.log" 2>&1
    if [ "$(fetched)" -ge "$(expected)" ] && [ "$(embedded)" -ge "$(expected)" ]; then
      echo "$(date -u) all $(expected) days embedded" | tee -a "$L/embed-$i.log" >"$L/EMBED_DONE"
      exit 0
    fi
    sleep 60
  done
  ;;
_upload)
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
  launch track "bash $0 _track $([ "$1" = track-fresh ] && echo --fresh)"
  echo "tracking $PULL_START..$OWN_END, following the embedding; watch: tail -f $L/track.log"
  ;;
_track)
  F=${2:-}
  # The tracker stops at the first day without vectors; carry on from there until the block is done.
  until OMP_NUM_THREADS=4 uv run python -m undercurrent.stories.track --start "$PULL_START" --end "$OWN_END" $F >>"$L/track.log" 2>&1; do
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
  gcloud storage rsync -r -q -x '.*\.tmp[0-9]+$' data/processed/vectors "gs://$BUCKET/processed/vectors"
  echo "block $BLOCK $OWN_START..$OWN_END done $(date -u)" | gcloud storage cp - "gs://$BUCKET/status/block-$BLOCK.done"
  echo "Uploaded. Post 'block $BLOCK done' in the chat, then stop the VM."
  ;;
status)
  echo "block $BLOCK: $PULL_START..$OWN_END (owns from $OWN_START), bucket $BUCKET, $P embed process(es) on $G GPU(s)"
  echo "fetched $(fetched)/$(expected), embedded $(embedded)/$(expected), tracked $(tracked)/$(expected)"
  for f in "$L"/embed-*.log; do
    [ -f "$f" ] || continue
    # titles a second over this process's last 5 embedded days ("... N embedded  S.Ss")
    grep ' embedded ' "$f" | tail -5 | awk -v f="$(basename "$f")" '
      {for (i = 2; i <= NF; i++) if ($i == "embedded") {x = $(i - 1); gsub(",", "", x); n += x}
       t = $NF; sub("s$", "", t); s += t}
      END {if (s > 0) printf "%s: %.0f titles/s\n", f, n / s}'
  done
  for flag in EMBED_DONE TRACK_DONE; do [ -f "$L/$flag" ] && echo "$flag: $(cat "$L/$flag")"; done
  echo "running: $(tmux ls -F '#{session_name}' 2>/dev/null | tr '\n' ' ')"
  for f in "$L"/*.log; do echo "--- $(basename "$f"): $(grep -v '^\s*$' "$f" | tail -1 | cut -c1-160)"; done
  nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader 2>/dev/null | sed 's/^/GPU: /'
  df -h / | tail -1 | awk '{print "disk: " $3 " used of " $2}'
  ;;
*)
  sed -n '2,12p' "$0"
  exit 1
  ;;
esac
