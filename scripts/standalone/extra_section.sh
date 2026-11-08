#!/bin/bash
# Run another GDELT section on a server that already has one (e.g. 8 next to 6b, whose tracking
# still needs the server), fully separate: its own home (settings, gcloud login, uv), repo, data,
# logs and tmux. The first section's files and loops are never touched. Everything here runs at
# lower CPU priority, so the first section's tracking keeps the CPUs it needs.
#
#   bash ~/undercurrent/scripts/standalone/extra_section.sh SECTION            set up, start and track (safe to rerun); for
#                                                                            3b or 7b: fetch from the bucket and track
#   bash ~/undercurrent/scripts/standalone/extra_section.sh SECTION progress   every section on this server (progress.sh)
#   bash ~/undercurrent/scripts/standalone/extra_section.sh SECTION status     or track, track-fresh, finish
#
# Its files are in /workspace/uc-home-SECTION (or ~/uc-home-SECTION without /workspace). A copy of
# this script saved as /workspace/uc-section.sh works the same way. See docs/SECTION_RUNBOOK.md.
S=${1:?usage: extra_section.sh SECTION [progress|status|track|track-fresh|finish]}
CMD=${2:-go}
OLD=$HOME
BASE=/workspace
[ -d "$BASE" ] || BASE=$OLD
export HOME=$BASE/uc-home-$S
unset TMUX # its own tmux server, even when run from inside a tmux session
export TMUX_TMPDIR=/tmp/uc-tmux-$S
export PATH=$HOME/google-cloud-sdk/bin:$HOME/.local/bin:$OLD/google-cloud-sdk/bin:$OLD/.local/bin:$PATH
export UV_CACHE_DIR=$OLD/.cache/uv # reuse the packages already downloaded (torch etc.)
mkdir -p "$HOME" "$TMUX_TMPDIR"
SEC=$HOME/undercurrent/scripts/standalone/section.sh

case "$CMD" in
go)
  if [ ! -d "$HOME/.config/gcloud" ] && [ -d "$OLD/.config/gcloud" ]; then
    mkdir -p "$HOME/.config" && cp -r "$OLD/.config/gcloud" "$HOME/.config/" # the same Google login
  fi
  if [ -d "$HOME/undercurrent" ]; then # an older clone may not know this section yet
    git -C "$HOME/undercurrent" pull -q --ff-only || echo "(git pull failed; carrying on with this copy of the code)"
  else
    git clone -q https://github.com/CooperMcCloskey/undercurrent.git "$HOME/undercurrent" || exit 1
  fi
  nice -n 10 bash "$SEC" setup "$S" || exit 1
  gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | grep -q . ||
    gcloud auth login --no-launch-browser || exit 1
  if [[ "$S" =~ ^[37]b$ ]]; then # tracking only: the data is in the bucket
    nice -n 10 bash "$SEC" fetch # if some days aren't uploaded yet, `track` fetches them as they arrive
    tmux has-session -t track 2>/dev/null || nice -n 10 bash "$SEC" track # (a rerun leaves a running tracker alone)
  else
    nice -n 10 bash "$SEC" start
    tmux has-session -t track 2>/dev/null || nice -n 10 bash "$SEC" track # the cutoffs are out: track behind the embedding
  fi
  echo "Section $S is in $HOME/undercurrent. Watch it: bash $0 $S progress   (or status)"
  ;;
progress) # every section on this server, this one included
  HOME=$OLD bash "$(cd "$(dirname "$0")" && pwd)/progress.sh"
  ;;
*)
  nice -n 10 bash "$SEC" "$CMD" "${@:3}"
  ;;
esac
