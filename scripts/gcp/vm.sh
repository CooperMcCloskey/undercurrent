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
#   pairs [N [TOP [PER]]]  sample headline pairs from N embedded days (default 4) and print them numbered, without
#                scores, to label "same story?" (1 or 0). Writes ~uc/pairs_unlabelled.csv on the VM.
#                TOP = pairs per band from similarity 0.75 up (default 12; 30 puts the labels where a cutoff's
#                precision is decided), PER = pairs per lower band (default 12; 6 keeps the total down).
#   calibrate "LABELS"  measure the "same story" cutoff from your labels, one digit per pair in order,
#                e.g. "1011 0010 ..." (spaces ignored). Prints the cutoff to put in thresholds.py.
#   languages [N]  which scripts the embedding model can't tell apart (any two Malayalam headlines look alike),
#                measured on N embedded days (default 3) from the vectors already made; no GPU needed
#   preview [LINK ASSIGN [START END]]  try a cutoff on real days without touching the block: tracks
#                START..END (default 2026-10-24..2026-11-06) with LINK/ASSIGN (default 0.80 0.85) into
#                ~uc/preview, then prints story sizes and about 20 stories to read. Takes a while.
#   export [DAYS [LINK ASSIGN]]  the first real data for the globe, without waiting for the whole block:
#                tracks the newest DAYS days (default 60, plus 28 to warm up) in their own folder and
#                exports them to gs://BUCKET/export. Runs detached on the VM (it survives Cloud Shell
#                closing). Needs those days embedded, and cutoffs: give them (0.80 0.85) until the
#                calibrated ones are pushed. The block is untouched.
#   export-log   the latest lines of the export's log (and whether it is still running)
#   export-stop  stop a running export
#   section S [status|stop]  use the idle GPU for another section S (5, 6a or 6b; claim it in
#                docs/DATA_COVERAGE.md first): pull, embed, upload, as its own user (sS) at low priority.
#                `stop` pauses it (carry on with `section S`). Stop it before track or export: 4 CPUs, 16 GB.
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
status | track | track-fresh | finish | export-log | export-stop)
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
    N=${2:-4} TOP=${3:-} PER=${4:-}
    [[ "$N" =~ ^[0-9]+$ && "${TOP:-0}" =~ ^[0-9]+$ && "${PER:-0}" =~ ^[0-9]+$ ]] ||
      { echo "usage: vm.sh pairs [days [pairs per top band [pairs per lower band]]]   e.g. vm.sh pairs 4 30 6" >&2; exit 1; }
    OPTS=""
    [ -n "$TOP" ] && OPTS="--per-bin-top $TOP"
    [ -n "$PER" ] && OPTS="$OPTS --per-bin $PER"
    { echo "N=$N"; echo "OPTS='$OPTS'"; echo "B64=$(base64 <"$SRC" | tr -d '\n')"; cat; } <<'EOF' | as_uc
cd ~/undercurrent && export PATH=$HOME/.local/bin:$PATH
echo "$B64" | base64 -d > "$HOME/sample_pairs.py"
nice -n 19 uv run python "$HOME/sample_pairs.py" --days "$N" $OPTS --out "$HOME/pairs_unlabelled.csv" --print
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
languages)
  # Sent to the VM like pairs and calibrate, for the same reason (see there): no git pull on the VM.
  DAYS=${2:-3}
  [[ "$DAYS" =~ ^[0-9]+$ ]] || { echo "usage: vm.sh languages [number of days]" >&2; exit 1; }
  SRC="$(cd "$(dirname "$0")/../.." && pwd)/pipeline/undercurrent/vectors/language_check.py"
  [ -f "$SRC" ] || { echo "Can't find $SRC. Run git pull in Cloud Shell's ~/undercurrent first." >&2; exit 1; }
  { echo "DAYS=$DAYS"; echo "B64=$(base64 <"$SRC" | tr -d '\n')"; cat; } <<'EOF' | as_uc
cd ~/undercurrent && export PATH=$HOME/.local/bin:$PATH
echo "$B64" | base64 -d > "$HOME/language_check.py"
nice -n 19 uv run python "$HOME/language_check.py" --days "$DAYS"
EOF
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
export)
  DAYS=${2:-60}
  [[ "$DAYS" =~ ^[0-9]+$ ]] && [ "$DAYS" -ge 8 ] ||
    { echo "usage: vm.sh export [DAYS [LINK ASSIGN]]   e.g. vm.sh export 60 0.80 0.85" >&2; exit 1; }
  ARGS=$DAYS
  if [ -n "${3:-}" ]; then
    [[ "$3" =~ ^[01]\.[0-9]+$ && "${4:-}" =~ ^[01]\.[0-9]+$ ]] ||
      { echo "give both cutoffs: vm.sh export $DAYS LINK ASSIGN   e.g. vm.sh export 60 0.80 0.85" >&2; exit 1; }
    ARGS="$DAYS $3 $4"
  fi
  # Pull first: the VM's copy of block.sh may not know `export` yet. (By now the embedders have
  # finished with the newest days, which is when `track` pulls too.) Only digits and dots get this far.
  {
    echo 'cd ~/undercurrent && git pull -q --ff-only || { echo "git pull failed on the VM; nothing started." >&2; exit 1; }'
    echo "bash ~/undercurrent/scripts/gcp/block.sh export $ARGS"
  } | as_uc
  ;;
section)
  SEC=${2:-} ACTION=${3:-start}
  [[ "$SEC" =~ ^(5|6a|6b)$ && "$ACTION" =~ ^(start|status|stop)$ ]] ||
    { echo "usage: vm.sh section SECTION [start|status|stop]   SECTION is 5, 6a or 6b (claim it in docs/DATA_COVERAGE.md first)" >&2; exit 1; }
  # Its own user (s5...) with its own copy of the repo, settings, tmux and data, so the block's files and
  # loops (user uc) are never touched. Everything runs at low CPU priority, but the VM has only 4 CPUs
  # and 16 GB: stop the section (`section S stop`) before the block's `track` or `export`.
  { echo "SEC=$SEC ACTION=$ACTION"; cat; } <<'EOF' | gcloud compute ssh "$VM" --zone="$ZONE" --command='sudo bash -s' -- -o ServerAliveInterval=30
U=s$SEC
if [ "$ACTION" = start ] && ! id "$U" >/dev/null 2>&1; then adduser --disabled-password --gecos "" "$U" >/dev/null; fi
id "$U" >/dev/null 2>&1 || { echo "Section $SEC has not been started on this VM."; exit 1; }
case "$ACTION" in
start)
  sudo -u "$U" -H nice -n 10 bash -s "$SEC" <<'INNER'
cd ~ && { [ -d undercurrent ] || git clone -q https://github.com/CooperMcCloskey/undercurrent.git </dev/null; }
cd undercurrent && git pull -q --ff-only </dev/null
bash scripts/standalone/section.sh setup "$1" </dev/null && bash scripts/standalone/section.sh start </dev/null
INNER
  ;;
status)
  sudo -u "$U" -H bash "/home/$U/undercurrent/scripts/standalone/section.sh" status
  ;;
stop)
  sudo -u "$U" -H tmux kill-server 2>/dev/null || true
  echo "Section $SEC paused (nothing is lost). To carry on: bash ~/undercurrent/scripts/gcp/vm.sh section $SEC"
  ;;
esac
EOF
  ;;
log)
  gcloud compute instances get-serial-port-output "$VM" --zone="$ZONE" 2>/dev/null | grep uc-startup | tail -20
  ;;
stop)
  gcloud compute instances stop "$VM" --zone="$ZONE"
  ;;
*)
  sed -n '2,37p' "$0"
  exit 1
  ;;
esac
