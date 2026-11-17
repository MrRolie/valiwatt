#!/usr/bin/env bash
# Valiwatt Live line 3, the updates (docs/plan-live.md): run every 10 minutes, offset from the midnight issue's step.
# live.updates acts only when an update is due (an hour complete, +25 min, before its deadline) and never touches the
# midnight issue or its log. It reads the capture that the issue step (scripts/live-issue.sh) takes every 10 minutes;
# this step runs 5 minutes after it.
set -uo pipefail
cd "$(dirname "$0")/.."
uv run --quiet python -m live.updates --run >> data/live/updates.log 2>&1
