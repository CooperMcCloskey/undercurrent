#!/bin/bash
# Load a downloaded export into the web app, on a laptop, in one step:
#
#   bash scripts/load_export.sh [ARCHIVE]
#
# ARCHIVE defaults to the newest *xport*.tgz in ~/Downloads or the repo root (export-final.tgz,
# "Export Block 1.tgz"...). It unpacks it as data/export/, keeps the previous export as
# data/export.prev/ (to go back: rm -rf data/export && mv data/export.prev data/export), prints
# what's in it, and checks it against the contract. `npm run dev` can stay running: reload the page.
# The contract check reads every file: minutes on a multi-year export. CHECK=0 skips it.
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
cd "$R"
A=${1:-$(ls -t ~/Downloads/*xport*.tgz "$R"/*xport*.tgz "$R"/data/*xport*.tgz 2>/dev/null | head -1 || true)}
if [ -z "$A" ] || [ ! -f "$A" ]; then
  echo "No export archive found. Download export-final.tgz from"
  echo "  https://console.cloud.google.com/storage/browser/undercurrent-data-cooper"
  echo "(click the file, then Download), or give its path: bash scripts/load_export.sh PATH"
  exit 1
fi
OUT=${OUT:-$R/data/export}
echo "== loading $A"
T=$(mktemp -d "${TMPDIR:-/tmp}/export.XXXXXX")
tar xzf "$A" -C "$T"
META=$(find "$T" -maxdepth 3 -name meta.json | head -1)
[ -n "$META" ] || { echo "$A has no meta.json: it isn't an export"; rm -rf "$T"; exit 1; }
if [ -d "$OUT" ]; then rm -rf "$OUT.prev" && mv "$OUT" "$OUT.prev" && echo "   previous export kept in $OUT.prev"; fi
mkdir -p "$(dirname "$OUT")"
mv "$(dirname "$META")" "$OUT"
rm -rf "$T"

python3 - "$OUT" <<'PY'
import collections, glob, json, os, sys
root = sys.argv[1]
meta = json.load(open(os.path.join(root, "meta.json")))
snaps = sorted(glob.glob(os.path.join(root, "snapshots", "*.json")))
print(f"   days: {len(meta['dates'])} ({meta['dates'][0]} -> {meta['latest']}), "
      f"story files: {len(glob.glob(os.path.join(root, 'stories', '*.json')))}")
print(f"   forecast horizons: {meta['horizons'] or 'none'}")
model = meta.get("model")
if model:
    print(f"   model card ({model['model_version']}, tested {model['test_start']} -> {model['test_end']}):")
    for h in model["horizons"]:
        better = "beats" if h["mae_log2"] < h["baseline_mae_log2"] else "does NOT beat"
        print(f"     {h['days']:>2}d: miss {h['mae_log2']:.3f} vs baseline {h['baseline_mae_log2']:.3f} "
              f"({better} it), range held {h['coverage']:.0%}")
cats, n, fc = collections.Counter(), 0, 0
for f in snaps:
    for s in json.load(open(f))["stories"]:
        n += 1; cats[s["category"]] += 1; fc += bool(s.get("forecast"))
print(f"   story-days: {n:,}, with a forecast: {fc / max(n, 1):.0%}")
print("   categories: " + ", ".join(f"{k} {v / n:.0%}" for k, v in cats.most_common()))
last = json.load(open(snaps[-1]))["stories"][:5]
print(f"   biggest stories on {meta['latest']}:")
for s in last:
    print(f"     {s['article_count_7d']:>6}  {s['category']:<10} {s['title'][:80]}")
PY

if [ "$OUT" = "$R/data/export" ] && [ "${CHECK:-1}" != 0 ]; then
  echo "== contract check (CHECK=0 skips it next time; it can take a few minutes on a big export)"
  uv run pytest -q -p no:cacheprovider pipeline/tests/test_contracts.py | tail -1
fi
echo "== done. If npm run dev is running, reload the page; otherwise: npm run dev"
