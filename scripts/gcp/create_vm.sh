#!/usr/bin/env bash
# Creates this account's GPU VM for the two-year run (docs/TWO_YEAR_RUN.md) and starts its block.
# Run it in Cloud Shell (console.cloud.google.com, the >_ icon), in the project that should pay:
#
#   git clone -q https://github.com/CooperMcCloskey/undercurrent.git && bash undercurrent/scripts/gcp/create_vm.sh BLOCK [ACCOUNTS]
#
#   BLOCK     1..ACCOUNTS. 1 is the newest (Cooper's); see the table in docs/TWO_YEAR_RUN.md
#   ACCOUNTS  how many accounts share the two years: 4 (default) or 3
#
# Optional environment: BUCKET (the shared bucket), MAX_GB (refuse to pull if the dry run says
# more than this; default 2500), VM (default uc-gpu).
#
# The VM sets itself up when it boots (scripts/gcp/vm_startup.sh): driver check, code, a dry run,
# then the pull, embed and upload loops. Nothing else needs to be typed on it.
set -euo pipefail

BLOCK=${1:?usage: create_vm.sh BLOCK [ACCOUNTS]   (BLOCK 1 is the newest; see docs/TWO_YEAR_RUN.md)}
ACCOUNTS=${2:-4}
BUCKET=${BUCKET:-undercurrent-data-cooper}
MAX_GB=${MAX_GB:-2500}
VM=${VM:-uc-gpu}
REGION=us-central1
HERE=$(cd "$(dirname "$0")" && pwd)

# block -> pull start (28-day warm-up), own start, own end. Same table as docs/TWO_YEAR_RUN.md.
case "$ACCOUNTS:$BLOCK" in
  4:1) DAYS="2026-04-10 2026-05-08 2026-11-06" ;;
  4:2) DAYS="2025-10-10 2025-11-07 2026-05-07" ;;
  4:3) DAYS="2025-04-10 2025-05-08 2025-11-06" ;;
  4:4) DAYS="2024-10-10 2024-11-07 2025-05-07" ;;
  3:1) DAYS="2026-02-09 2026-03-09 2026-11-06" ;;
  3:2) DAYS="2025-06-10 2025-07-08 2026-03-08" ;;
  3:3) DAYS="2024-10-10 2024-11-07 2025-07-07" ;;
  *) echo "No block $BLOCK with $ACCOUNTS accounts (blocks are 1..3 or 1..4)" >&2; exit 1 ;;
esac
read -r PULL_START OWN_START OWN_END <<<"$DAYS"

PROJECT=$(gcloud config get-value project 2>/dev/null)
[ -n "$PROJECT" ] || { echo "No project set: gcloud config set project YOUR-PROJECT-ID" >&2; exit 1; }
echo "Project $PROJECT, block $BLOCK of $ACCOUNTS: pulls $PULL_START..$OWN_END, owns $OWN_START..$OWN_END"
echo "Bucket gs://$BUCKET"

echo "== Enabling APIs (harmless if already on)"
gcloud services enable compute.googleapis.com bigquery.googleapis.com \
  bigquerystorage.googleapis.com storage.googleapis.com

if [ "$BLOCK" = 1 ] && ! gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1; then
  echo "== Creating the shared bucket"
  gcloud storage buckets create "gs://$BUCKET" --location=$REGION --uniform-bucket-level-access
fi

# An existing VM of that name: start it if it's stopped, otherwise leave it alone.
EXISTING=$(gcloud compute instances list --filter="name=$VM" --format="value(zone.basename(),status)")
if [ -n "$EXISTING" ]; then
  read -r ZONE STATUS <<<"$EXISTING"
  echo "== $VM already exists in $ZONE ($STATUS)"
  [ "$STATUS" = TERMINATED ] && gcloud compute instances start "$VM" --zone="$ZONE"
else
  # PyTorch in uv.lock is built for CUDA 13, which needs NVIDIA driver 580+: prefer a CUDA 13 image.
  FAMILIES=$(gcloud compute images list --project=deeplearning-platform-release --no-standard-images \
    --filter="family~^common-cu1" --format="value(family)" | sort -uV)
  FAMILY=$(echo "$FAMILIES" | grep '^common-cu13' | tail -1)
  [ -n "$FAMILY" ] || { FAMILY=$(echo "$FAMILIES" | tail -1); echo "No CUDA 13 image; using $FAMILY (the VM upgrades its driver if it's too old)"; }
  [ -n "$FAMILY" ] || { echo "Couldn't find a Deep Learning VM image with CUDA" >&2; exit 1; }
  ZONES=$(gcloud compute accelerator-types list --filter="name=nvidia-l4 AND zone~^$REGION" \
    --format="value(zone.basename())" | sort -u)
  ZONES=${ZONES:-"$REGION-a $REGION-b $REGION-c"}
  DISK=$([ "$BLOCK" = 1 ] && echo 400GB || echo 250GB)  # block 1 also holds the merge
  echo "== Creating $VM (image $FAMILY, disk $DISK); trying zones: $(echo $ZONES)"
  ZONE=""
  for round in 1 2 3 4 5 6; do
    for M in g2-standard-24 g2-standard-8 g2-standard-4; do  # 2, 1, 1 L4s; quota decides
      for Z in $ZONES; do
        echo "-- $M in $Z"
        if gcloud compute instances create "$VM" --zone="$Z" --machine-type="$M" \
          --maintenance-policy=TERMINATE \
          --image-project=deeplearning-platform-release --image-family="$FAMILY" \
          --boot-disk-size="$DISK" --boot-disk-type=pd-balanced --scopes=cloud-platform \
          --metadata-from-file=startup-script="$HERE/vm_startup.sh" \
          --metadata="install-nvidia-driver=True,uc-block=$BLOCK,uc-pull-start=$PULL_START,uc-own-start=$OWN_START,uc-own-end=$OWN_END,uc-bucket=$BUCKET,uc-max-gb=$MAX_GB" \
          2>"${TMPDIR:-/tmp}/uc-create.err"; then
          ZONE=$Z; break 3
        fi
        grep -m1 -E "ERROR|Quota|QUOTA|unavailable|exhausted" "${TMPDIR:-/tmp}/uc-create.err" || true
      done
    done
    echo "== No L4 machine available right now (round $round of 6); trying again in 2 minutes"
    sleep 120
  done
  [ -n "$ZONE" ] || { echo "Gave up. Check your GPU quota (docs/TWO_YEAR_RUN.md step 0), then run this again." >&2; exit 1; }
  echo "== Created $VM in $ZONE"
fi

SA=$(gcloud compute instances describe "$VM" --zone="$ZONE" --format="value(serviceAccounts[0].email)")
cat <<EOF

Done. The VM now sets itself up: about 10-15 minutes (driver, code, model), then a dry run, then
the pull, embed and upload loops. Check on it from here:

  gcloud compute ssh $VM --zone=$ZONE --command='sudo -u uc -H bash /home/uc/undercurrent/scripts/gcp/block.sh status'

Its setup log, without SSH:

  gcloud compute instances get-serial-port-output $VM --zone=$ZONE 2>/dev/null | grep uc-startup | tail -20

The VM writes to the bucket as: $SA
EOF
if [ "$BLOCK" != 1 ]; then
  cat <<EOF
Send that line to Cooper, who runs (in their own Cloud Shell):

  gcloud storage buckets add-iam-policy-binding gs://$BUCKET --member=serviceAccount:$SA --role=roles/storage.objectAdmin

Until then uploads fail and are retried every 15 minutes, so nothing is lost.
EOF
fi
