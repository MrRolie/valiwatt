#!/usr/bin/env bash
# Hourly snapshot of Hydro-Québec's production and interchange feeds (CC BY-NC 4.0), started 2026-11-22 during the
# demand feed's outage: they keep only 48 h, and the overlap with demand is the only way to test whether they can
# stand in for it. Snapshots only; no forecast reads them; any use is the owner's ruling.
set -euo pipefail
cd "$(dirname "$0")/.."
out=data/live/supply
mkdir -p "$out"
ts=$(date -u +%Y%m%dT%H%M%SZ)
for d in production-electricite-quebec importations-exportations-avec-transits; do
  url="https://donnees.hydroquebec.com/api/explore/v2.1/catalog/datasets/$d/exports/json"
  tmp=$(mktemp)
  if curl -fsS --max-time 60 "$url" -o "$tmp" && [ -s "$tmp" ]; then
    gzip -c "$tmp" > "$out/.$d-$ts.json.gz.part" && mv "$out/.$d-$ts.json.gz.part" "$out/$d-$ts.json.gz"
  else
    echo "$ts $d fetch failed" >> "$out/failures.log"
  fi
  rm -f "$tmp"
done
