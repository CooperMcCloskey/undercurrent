#!/usr/bin/env bash
# Check on or control this account's block VM from Cloud Shell, with nothing to fill in: it
# finds the VM (uc-gpu) and its zone itself.
#
#   bash ~/undercurrent/scripts/gcp/vm.sh COMMAND
#
#   status       progress: days pulled and embedded, what's running, latest log lines
#   speed        GPU use (5 samples), top CPU users and load: is the GPU kept busy?
#   embed2       switch a running VM to two embedders per GPU (safe; nothing is lost)
#   feeds        GKG rows per feed so far (the translated feed should be about 2x English)
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
  as_uc <<'EOF'
cd ~/undercurrent && source ~/block.env && export PATH=$HOME/.local/bin:$PATH
EXP=$(( ($(date -ud $OWN_END +%s) - $(date -ud $PULL_START +%s)) / 86400 + 1 ))
EMB=$((GPUS * 2))
for s in $(tmux ls -F '#{session_name}' 2>/dev/null | grep '^embed'); do tmux kill-session -t $s; done
for k in $(seq 0 $((EMB - 1))); do
  tmux new -d -s embed$k -c "$PWD" "while true; do CUDA_VISIBLE_DEVICES=$((k % GPUS)) UC_HALF=1 uv run python -m undercurrent.vectors.store --source news --start $PULL_START --end $OWN_END --shard $k/$EMB >> logs/embed-$k.log 2>&1; n=\$(find data/processed/vectors/news -name '*.parquet' | wc -l); [ \$n -ge $EXP ] && { echo \"all $EXP days embedded \$(date -u)\" > logs/EMBED_DONE; exit 0; }; sleep 60; done"
done
echo "now running: $(tmux ls -F '#{session_name}' | tr '\n' ' ')"
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
log)
  gcloud compute instances get-serial-port-output "$VM" --zone="$ZONE" 2>/dev/null | grep uc-startup | tail -20
  ;;
stop)
  gcloud compute instances stop "$VM" --zone="$ZONE"
  ;;
*)
  sed -n '2,17p' "$0"
  exit 1
  ;;
esac
