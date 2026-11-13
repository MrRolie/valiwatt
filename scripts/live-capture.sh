#!/usr/bin/env bash
# Blind capture of Hydro-Québec's 15-minute demand feed (it keeps only ~48 h). Writes each response, gzipped, to
# data/live/capture/; nothing reads these files before the rules-frozen-live tag (docs/plan-live.md, "The live inputs").
set -euo pipefail
cd "$(dirname "$0")/.."
out=data/live/capture
mkdir -p "$out"
ts=$(date -u +%Y%m%dT%H%M%SZ)
url="https://donnees.hydroquebec.com/api/explore/v2.1/catalog/datasets/demande-electricite-quebec/exports/json?timezone=UTC"
tmp=$(mktemp)
if curl -fsS --max-time 60 "$url" -o "$tmp" && [ -s "$tmp" ]; then
  gzip -c "$tmp" > "$out/$ts.json.gz"
else
  echo "$ts fetch failed" >> "$out/failures.log"
fi
rm -f "$tmp"
