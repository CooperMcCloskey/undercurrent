#!/bin/bash
# Run another GDELT section on a server that already has one (e.g. 8 next to 6b, whose tracking
# still needs the server), fully separate: its own home (settings, gcloud login, uv), repo, data,
# logs and tmux. The first section's files and loops are never touched. Everything here runs at
# lower CPU priority, so the first section's tracking keeps the CPUs it needs.
#
#   bash ~/undercurrent/scripts/standalone/extra_section.sh SECTION            set up, start and track (safe to rerun); for
#                                                                            3b, 4b or 7b: fetch from the bucket and track
#   bash ~/undercurrent/scripts/standalone/extra_section.sh SECTION progress   every section on this server (progress.sh)
#   bash ~/undercurrent/scripts/standalone/extra_section.sh SECTION status     or track, track-fresh, finish
#   bash ~/undercurrent/scripts/standalone/extra_section.sh SECTION track-only on another server than the one pulling
#                     and embedding it: fetch it from the bucket and track it here, following the uploads. Only
#                     one server may track a section: stop the other one's tracker (`... SECTION stop-track`).
#   bash ~/undercurrent/scripts/standalone/extra_section.sh SECTION stop-track stop this section's tracker here
#   bash ~/undercurrent/scripts/standalone/extra_section.sh SECTION stop       stop everything of this section here
#                     (pull, embed, upload, track); its files stay. `extra_section.sh SECTION` carries on.
#
# Its files are in /workspace/uc-home-SECTION (or ~/uc-home-SECTION without /workspace). A copy of
# this script saved as /workspace/uc-section.sh works the same way. See docs/SECTION_RUNBOOK.md.
S=${1:?usage: extra_section.sh SECTION [track-only|progress|status|track|track-fresh|finish|stop-track|stop]}
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
go | track-only)
  if [ "$CMD" = track-only ] && [ -s "$HOME/undercurrent/logs/pull.log" ] && [ ! -f "$HOME/undercurrent/logs/FETCHED" ]; then
    echo "This server pulls and embeds section $S itself: track-only is for a DIFFERENT server."
    echo "  To track it here instead: bash $0 $S track"
    exit 1
  fi
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
  WHO=gs://${BUCKET:-undercurrent-data-cooper}/status/tracking/$S.txt # which server tracks this section
  if [ "$CMD" = track-only ] || [[ "$S" =~ ^[347]b$ ]]; then # tracking only: the data is in the bucket
    echo "$(hostname) since $(date -u)" | gcloud storage cp -q - "$WHO"
    nice -n 10 bash "$SEC" fetch # if some days aren't uploaded yet, `track` fetches them as they arrive
    tmux has-session -t track 2>/dev/null || nice -n 10 bash "$SEC" track # (a rerun leaves a running tracker alone)
  else
    if [ ! -s "$HOME/undercurrent/logs/pull.log" ]; then
      # A new home (a new server, or one that lost its disk): start from whatever of this section is
      # already in the bucket, so only the missing days are pulled and embedded again.
      nice -n 10 bash "$SEC" fetch
      rm -f "$HOME/undercurrent/logs/FETCHED" # this server pulls the rest: finish must upload them
    fi
    nice -n 10 bash "$SEC" start
    OTHER=$(gcloud storage cat "$WHO" 2>/dev/null | awk '{print $1}')
    if [ -n "$OTHER" ] && [ "$OTHER" != "$(hostname)" ]; then
      echo "Section $S is tracked on $OTHER ($WHO): pulling and embedding here only."
    else
      tmux has-session -t track 2>/dev/null || nice -n 10 bash "$SEC" track # the cutoffs are out: track behind the embedding
    fi
  fi
  echo "Section $S is in $HOME/undercurrent. Watch it: bash $0 $S progress   (or status)"
  ;;
stop-track)
  tmux kill-session -t track 2>/dev/null && echo "Section $S: tracker stopped (its tables stay; 'track' carries on)." ||
    echo "Section $S: no tracker running here."
  ;;
stop)
  tmux kill-server 2>/dev/null && echo "Section $S: stopped here (its files stay in $HOME; 'bash $0 $S' carries on)." ||
    echo "Section $S: nothing running here."
  ;;
progress) # every section on this server, this one included
  HOME=$OLD bash "$(cd "$(dirname "$0")" && pwd)/progress.sh"
  ;;
*)
  nice -n 10 bash "$SEC" "$CMD" "${@:3}"
  ;;
esac
