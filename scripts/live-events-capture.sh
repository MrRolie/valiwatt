#!/usr/bin/env bash
# Hourly snapshot of Hydro-Québec's winter peak-events feed (evenements-pointe, CC BY-NC 4.0), owner's ruling
# 2026-11-17: learn when each event first appears in the public feed, so a peak-event input can be designed and frozen
# for the next winter on verified availability. Snapshots only; nothing reads them for a forecast this winter.
set -euo pipefail
cd "$(dirname "$0")/.."
out=data/live/events
mkdir -p "$out"
ts=$(date -u +%Y%m%dT%H%M%SZ)
url="https://donnees.hydroquebec.com/api/explore/v2.1/catalog/datasets/evenements-pointe/exports/json"
tmp=$(mktemp)
if curl -fsS --max-time 60 "$url" -o "$tmp" && [ -s "$tmp" ]; then
  gzip -c "$tmp" > "$out/.$ts.json.gz.part" && mv "$out/.$ts.json.gz.part" "$out/$ts.json.gz"
else
  echo "$ts fetch failed" >> "$out/failures.log"
fi
rm -f "$tmp"
