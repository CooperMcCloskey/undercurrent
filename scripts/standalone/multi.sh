#!/bin/bash
# Several GDELT sections side by side on ONE multi-GPU server, each run by section.sh exactly as on
# its own machine. Made for 5a + 5b on 2x A100 with 32 vCPUs. See docs/PREVIOUS_YEAR.md.
#
#   bash multi.sh setup 5a 5b        once: tools, Google login, one clone + settings per section, GPU check
#   bash multi.sh login              log gcloud in again (shared by every section)
#   bash multi.sh start              pull + embed + upload loops for every section (safe to rerun)
#   bash multi.sh progress           % pulled, embedded, tracked, per section
#   bash multi.sh status             section.sh status for each section
#   bash multi.sh track [N]          after "cutoffs pushed": track every section, following the embedding
#   bash multi.sh track-fresh [N]    the same, starting the timelines again (after the cutoffs changed)
#   bash multi.sh finish             upload each section's own story days and mark it done
#   bash multi.sh attach S SESSION   watch one tmux session, e.g. `attach 5b pull` (Ctrl-b d to leave)
#   bash multi.sh logs S             tail every log of section S
#
# How the sections are kept apart (section.sh assumes one section per machine):
# - each section has its own clone, ~/uc-<S>/undercurrent: its own data/, logs/ and .venv;
# - each has its own HOME, ~/uc-<S>/home, so its own ~/block.env and ~/own, and its own tmux server
#   (TMUX_TMPDIR), so the sessions (pull, embed0, ..., track) don't clash. gcloud's login, uv and
#   the caches are symlinked to the real home, so they are installed and logged in once;
# - the CPUs are split evenly: OMP_NUM_THREADS makes `nproc` report each section's share, so
#   section.sh sizes its pull and its tracker threads to that share. All GPUs are shared, and the
#   embedders are split so the total is what section.sh would run for the whole machine.
# A section's 28 warm-up days are pulled by that section even if another one owns them (5b's warm-up
# is 5a's last 28 days): the same as on separate machines, and it keeps the runs independent.
set -uo pipefail
REPO_URL=${REPO_URL:-https://github.com/CooperMcCloskey/undercurrent.git}
SELF=$(cd "$(dirname "$0")" && pwd)/$(basename "$0")
LIST=$HOME/uc-sections.env # SECTIONS="5a 5b" SHARE=<CPUs per section>
export PATH=$HOME/google-cloud-sdk/bin:$HOME/.local/bin:$PATH
unset TMUX # inside a tmux session, tmux would otherwise talk to that session's server

base() { echo "$HOME/uc-$1"; }
# CPUs we may use: nproc, capped by a container's CPU quota (same rule as section.sh).
usable_cpus() {
  local n q p
  n=$(nproc)
  if [ -r /sys/fs/cgroup/cpu.max ]; then
    read -r q p </sys/fs/cgroup/cpu.max
  elif [ -r /sys/fs/cgroup/cpu/cpu.cfs_quota_us ]; then
    q=$(cat /sys/fs/cgroup/cpu/cpu.cfs_quota_us) p=$(cat /sys/fs/cgroup/cpu/cpu.cfs_period_us)
  fi
  if [[ "${q:-}" =~ ^[0-9]+$ && "${p:-0}" -gt 0 ]]; then
    q=$((q / p))
    [ "$q" -ge 1 ] || q=1
    [ "$q" -lt "$n" ] && n=$q
  fi
  echo "$n"
}
# Run a command for section S: its clone as the working directory, its home, its tmux server, its CPU share.
in_section() {
  local S=$1 B
  shift
  B=$(base "$S")
  (
    export HOME=$B/home TMUX_TMPDIR=$B/home/tmux OMP_NUM_THREADS=$SHARE
    cd "$B/undercurrent" && "$@"
  )
}
each() { # run `section.sh ARGS` for every section, one after the other
  local S
  for S in $SECTIONS; do
    echo "=================== section $S"
    in_section "$S" bash scripts/standalone/section.sh "$@"
  done
}

if [ "${1:-}" != setup ]; then
  [ -f "$LIST" ] || { echo "Run: bash $SELF setup SECTION [SECTION ...]   (first)"; exit 1; }
  source "$LIST"
fi

case "${1:-}" in
setup)
  shift
  [ $# -ge 1 ] || { echo "usage: multi.sh setup SECTION [SECTION ...], e.g. setup 5a 5b"; exit 1; }
  for S in "$@"; do
    [[ "$S" =~ ^(3|4|5|5a|5b|6a|6b|7|8|9|10)$ ]] || { echo "Unknown section '$S' (docs/DATA_COVERAGE.md)"; exit 1; }
  done
  # Tools, once, in the real home (section.sh would otherwise install them into each section's home).
  SUDO=$([ "$(id -u)" = 0 ] && echo "" || echo sudo)
  if [ "$(id -u)" = 0 ] && ! command -v sudo >/dev/null; then # section.sh calls sudo; root on a bare VPS may lack it
    apt-get update -q && apt-get install -y -q sudo
  fi
  if ! command -v tmux >/dev/null || ! command -v git >/dev/null || ! command -v curl >/dev/null; then
    $SUDO apt-get update -q && $SUDO apt-get install -y -q git tmux curl
  fi
  command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
  if ! command -v gcloud >/dev/null; then
    echo "== installing the Google Cloud CLI into ~/google-cloud-sdk"
    curl -sSL https://sdk.cloud.google.com | bash -s -- --disable-prompts --install-dir="$HOME" >/dev/null
  fi

  # Fail fast on the GPU, before downloading PyTorch once per section.
  echo "== GPU"
  nvidia-smi -L || { echo "No NVIDIA GPU or driver found (nvidia-smi failed)."; exit 1; }
  DRIVER=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)
  if [ "${DRIVER%%.*}" -lt 580 ]; then
    echo "Driver $DRIVER is too old: the PyTorch in uv.lock (CUDA 13) needs 580 or newer."
    echo "On Ubuntu: sudo apt-get install -y nvidia-driver-580-server && sudo reboot   (or pick your provider's newer driver image)"
    echo "Then run this setup again."
    exit 1
  fi
  GPUS=$(nvidia-smi -L | wc -l)
  CPUS=$(usable_cpus)
  SHARE=$((CPUS / $#))
  [ "$SHARE" -ge 1 ] || SHARE=1
  # The embedders section.sh would run for the whole machine (4 a GPU with 12+ CPUs, else 2), split
  # between the sections; at least one per GPU, so every section uses every GPU.
  TOTAL=$((GPUS * ($([ "$CPUS" -ge 12 ] && echo 4 || echo 2))))
  EMBED=$((TOTAL / $#))
  [ "$EMBED" -ge "$GPUS" ] || EMBED=$GPUS
  NEED=$((40 * $# + 20)) # docs/SECTION_RUNBOOK.md: ~70 GB for six months, so ~40 for a three-month half
  FREE_GB=$(df -BG --output=avail "$HOME" | tail -1 | tr -dc 0-9)
  [ "$FREE_GB" -ge "$NEED" ] || echo "WARNING: only ${FREE_GB} GB free in $HOME; $# sections need about $NEED GB"
  echo "$GPUS GPU(s), $CPUS CPUs: $# sections, each with $SHARE CPUs and $EMBED embedders, on every GPU"
  echo "SECTIONS=\"$*\" SHARE=$SHARE" >"$LIST"
  SECTIONS="$*"

  grep -q google-cloud-sdk/bin ~/.bashrc 2>/dev/null ||
    echo "export PATH=\$HOME/google-cloud-sdk/bin:\$HOME/.local/bin:\$PATH" >>~/.bashrc
  gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | grep -q . ||
    gcloud auth login --no-launch-browser || echo "(not logged in to Google: run bash $SELF login later)"
  mkdir -p "$HOME/.config" "$HOME/.cache" "$HOME/.local"
  for S in $SECTIONS; do
    B=$(base "$S")
    echo "=================== section $S in $B"
    mkdir -p "$B/home/tmux"
    for x in .config .cache .local google-cloud-sdk .gitconfig; do
      [ -e "$HOME/$x" ] && [ ! -e "$B/home/$x" ] && ln -s "$HOME/$x" "$B/home/$x"
    done
    if [ -d "$B/undercurrent/.git" ]; then
      git -C "$B/undercurrent" pull -q --ff-only || echo "(git pull failed; carrying on with this copy)"
    else
      git clone -q "$REPO_URL" "$B/undercurrent" || { echo "git clone failed"; exit 1; }
    fi
    in_section "$S" bash scripts/standalone/section.sh setup "$S" || { echo "section.sh setup $S failed"; exit 1; }
    echo "export EMBED_PROCS=$EMBED # multi.sh: this section's share of the embedders" >>"$B/home/block.env"
  done
  echo "=================== next"
  if gcloud storage ls gs://undercurrent-data-cooper/processed/ >/dev/null 2>&1; then
    echo "Bucket reachable. Start everything: bash $SELF start"
  else
    echo "Bucket not reachable yet: bash $SELF login, and ask Cooper for write access to the whole bucket."
    echo "You can start anyway (bash $SELF start): work carries on locally and the uploads catch up."
  fi
  ;;
login)
  gcloud auth login --no-launch-browser && echo "Logged in as $(gcloud config get-value account 2>/dev/null)"
  ;;
start | status | finish)
  each "$1"
  ;;
progress)
  for S in $SECTIONS; do
    echo "section $S"
    in_section "$S" bash scripts/standalone/section.sh status | grep -oE '(pulled|embedded|tracked) [0-9]+/[0-9]+' |
      awk -F'[ /]' '{b=sprintf("%*s", int(20*$2/$3), ""); gsub(/ /, "#", b); printf "  %-9s %4d/%-4d %3d%%  [%-20s]\n", $1, $2, $3, 100*$2/$3, b}'
  done
  ;;
track | track-fresh)
  each "$@"
  ;;
attach)
  [ $# -eq 3 ] || { echo "usage: multi.sh attach SECTION SESSION   (sessions: pull, embed0.., upload, track)"; exit 1; }
  in_section "$2" tmux attach -t "$3"
  ;;
logs)
  [ $# -eq 2 ] || { echo "usage: multi.sh logs SECTION"; exit 1; }
  tail -F "$(base "$2")"/undercurrent/logs/*.log
  ;;
*)
  sed -n '2,14p' "$SELF"
  exit 1
  ;;
esac
