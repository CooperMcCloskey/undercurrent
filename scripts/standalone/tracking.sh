#!/bin/bash
# Renamed to progress.sh, which shows the same and more (each section's pull and embedding too).
# Kept so the old command still works.
exec bash "$(cd "$(dirname "$0")" && pwd)/progress.sh" "$@"
