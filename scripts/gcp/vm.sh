#!/usr/bin/env bash
# Check on or control this account's block VM from Cloud Shell, with nothing to fill in: it
# finds the VM (uc-gpu) and its zone itself.
#
#   bash ~/undercurrent/scripts/gcp/vm.sh COMMAND
#
#   status       progress: days pulled and embedded, what's running, latest log lines
#   speed        GPU use (5 samples), top CPU users and load: is the GPU kept busy?
#   embed2 [B]   switch a running VM to two embedders per GPU (safe; nothing is lost);
#                B = batch size (default 256; 512 or 1024 keeps the GPU busier)
#   feeds        GKG rows per feed so far (the translated feed should be about 2x English)
#   pairs [N]    sample headline pairs from N embedded days (default 4) and print them numbered, without
#                scores, to label "same story?" (1 or 0). Writes ~uc/pairs_unlabelled.csv on the VM.
#   calibrate "LABELS"  measure the "same story" cutoff from your labels, one digit per pair in order,
#                e.g. "1011 0010 ..." (spaces ignored). Prints the cutoff to put in thresholds.py.
#   preview [LINK ASSIGN [START END]]  try a cutoff on real days without touching the block: tracks
#                START..END (default 2026-10-24..2026-11-06) with LINK/ASSIGN (default 0.80 0.85) into
#                ~uc/preview, then prints story sizes and about 20 stories to read. Takes a while.
#   track        after "cutoffs pushed": track the block (resumes if it stopped)
#   track-fresh  track from the start again (after the cutoffs changed)
#   finish       upload this block's own story days and mark the block done
#   log          the VM's setup log (no SSH)
#   stop         stop the VM (only once `finish` has run)
set -euo pipefail
VM=${VM:-uc-gpu}
ZONE=$(gcloud compute instances list --filter="name=$VM" --format="value(zone.basename())" 2>/dev/null | head -1)
if [ -z "$ZONE" ]; then
  echo "No VM called $VM in project $(gcloud config get-value project 2>/dev/null)." >&2
  echo "Wrong project? gcloud config set project YOUR-PROJECT-ID" >&2
  exit 1
fi
as_uc() { gcloud compute ssh "$VM" --zone="$ZONE" --command='sudo -u uc -H bash -s'; }

case "${1:-status}" in
status | track | track-fresh | finish)
  echo "bash ~/undercurrent/scripts/gcp/block.sh $1" | as_uc
  ;;
speed)
  gcloud compute ssh "$VM" --zone="$ZONE" --command='echo "GPU use, 5 samples:"; for i in 1 2 3 4 5; do nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader; sleep 2; done; uptime; top -bn1 -o %CPU | sed -n "7,12p"'
  ;;
embed2)
  B=${2:-256}
  [[ "$B" =~ ^[0-9]+$ ]] || { echo "batch size must be a number" >&2; exit 1; }
  { echo "B=$B"; cat; } <<'EOF' | as_uc
cd ~/undercurrent && source ~/block.env && export PATH=$HOME/.local/bin:$PATH
EXP=$(( ($(date -ud $OWN_END +%s) - $(date -ud $PULL_START +%s)) / 86400 + 1 ))
EMB=$((GPUS * 2))
for s in $(tmux ls -F '#{session_name}' 2>/dev/null | grep '^embed'); do tmux kill-session -t $s; done
for k in $(seq 0 $((EMB - 1))); do
  tmux new -d -s embed$k -c "$PWD" "while true; do CUDA_VISIBLE_DEVICES=$((k % GPUS)) UC_HALF=1 uv run python -m undercurrent.vectors.store --source news --start $PULL_START --end $OWN_END --shard $k/$EMB --batch-size $B >> logs/embed-$k.log 2>&1; n=\$(find data/processed/vectors/news -name '*.parquet' | wc -l); [ \$n -ge $EXP ] && { echo \"all $EXP days embedded \$(date -u)\" > logs/EMBED_DONE; exit 0; }; sleep 60; done"
done
echo "batch size $B; now running: $(tmux ls -F '#{session_name}' | tr '\n' ' ')"
EOF
  ;;
feeds)
  as_uc <<'EOF'
cd ~/undercurrent && ~/.local/bin/uv run python - <<'PY'
import glob, pandas as pd
d = pd.concat(pd.read_parquet(f) for f in glob.glob("data/processed/gdelt_totals/*/*.parquet"))
print(d.groupby("feed")[["records", "with_title"]].sum())
PY
EOF
  ;;
pairs | calibrate)
  # These two scripts are sent to the VM rather than pulled there: a running block's loops use the
  # copy of the repo cloned at boot, so don't `git pull` on the VM. (Update Cloud Shell's copy.)
  SCRIPT=pipeline/undercurrent/vectors/sample_pairs.py
  [ "$1" = calibrate ] && SCRIPT=pipeline/undercurrent/vectors/calibrate.py
  SRC="$(cd "$(dirname "$0")/../.." && pwd)/$SCRIPT"
  [ -f "$SRC" ] || { echo "Can't find $SRC. Run git pull in Cloud Shell's ~/undercurrent first." >&2; exit 1; }
  if [ "$1" = pairs ]; then
    N=${2:-4}
    [[ "$N" =~ ^[0-9]+$ ]] || { echo "usage: vm.sh pairs [number of days]" >&2; exit 1; }
    { echo "N=$N"; echo "B64=$(base64 <"$SRC" | tr -d '\n')"; cat; } <<'EOF' | as_uc
cd ~/undercurrent && export PATH=$HOME/.local/bin:$PATH
echo "$B64" | base64 -d > "$HOME/sample_pairs.py"
nice -n 19 uv run python "$HOME/sample_pairs.py" --days "$N" --out "$HOME/pairs_unlabelled.csv" --print
EOF
  else
    LABELS=${2:-}
    [ -n "$LABELS" ] || { echo 'usage: vm.sh calibrate "1011 0010 ..."  (one digit per pair, in order)' >&2; exit 1; }
    { echo "L64=$(printf %s "$LABELS" | base64 | tr -d '\n')"; echo "B64=$(base64 <"$SRC" | tr -d '\n')"; cat; } <<'EOF' | as_uc
cd ~/undercurrent && export PATH=$HOME/.local/bin:$PATH
echo "$B64" | base64 -d > "$HOME/calibrate.py"
LABELS=$(echo "$L64" | base64 -d)
uv run python "$HOME/calibrate.py" --pairs "$HOME/pairs_unlabelled.csv" --labels "$LABELS"
EOF
  fi
  ;;
preview)
  LINK=${2:-0.80} ASSIGN=${3:-0.85} START=${4:-2026-10-24} END=${5:-2026-11-06}
  [[ "$LINK" =~ ^[01]\.[0-9]+$ && "$ASSIGN" =~ ^[01]\.[0-9]+$ ]] ||
    { echo "usage: vm.sh preview [LINK ASSIGN [START END]]   e.g. vm.sh preview 0.80 0.85" >&2; exit 1; }
  [[ "$START" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ && "$END" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] ||
    { echo "dates look like 2026-10-24" >&2; exit 1; }
  # Its own copy of the output in ~uc/preview (the vectors and articles are linked, not copied), so
  # the block's tracker state and story files are never touched. The VM does pull the code: by now
  # the embedders have finished, which is when block.sh track pulls too.
  { echo "LINK=$LINK ASSIGN=$ASSIGN START=$START END=$END"; cat; } <<'EOF' | gcloud compute ssh "$VM" --zone="$ZONE" --command='sudo -u uc -H bash -s' -- -o ServerAliveInterval=30
cd ~/undercurrent && export PATH=$HOME/.local/bin:$PATH
git pull -q --ff-only || { echo "git pull failed on the VM; nothing started." >&2; exit 1; }
uv sync -q
P=$HOME/preview
rm -rf "$P" && mkdir -p "$P"
ln -s "$PWD/data/processed/vectors" "$P/vectors"
ln -s "$PWD/data/processed/articles" "$P/articles"
OMP_NUM_THREADS=$(nproc) nice uv run python -m undercurrent.stories.track --processed "$P" --fresh \
  --start "$START" --end "$END" --link-sim "$LINK" --assign-sim "$ASSIGN" || exit 1
uv run python -m undercurrent.stories.preview --processed "$P" --day "$END"
EOF
  ;;
log)
  gcloud compute instances get-serial-port-output "$VM" --zone="$ZONE" 2>/dev/null | grep uc-startup | tail -20
  ;;
stop)
  gcloud compute instances stop "$VM" --zone="$ZONE"
  ;;
*)
  sed -n '2,23p' "$0"
  exit 1
  ;;
esac
