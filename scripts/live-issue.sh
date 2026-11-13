#!/usr/bin/env bash
# Valiwatt Live daily issue (docs/plan-live.md): run every 10 minutes; live.daily acts only between the origin + 25 min
# and the deadline (end of D's first hour), and logs a missed day after it. Capture first so the newest readings are in.
set -uo pipefail
cd "$(dirname "$0")/.."
scripts/live-capture.sh
uv run --quiet python -m live.daily --issue >> data/live/issue.log 2>&1
