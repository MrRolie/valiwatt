# valiwatt: rules for every session and build agent

valiwatt validates and monitors a forecast of Québec's hourly electricity demand, the way a model-risk team would.

`BRIEF.md` is the dated founding record and is never edited. This file is the operational home of its rules. Each
departure from the brief is listed below with its ruling. A disagreement that is not listed means the brief wins, and
the gap goes to the owner as a question.

## Rules (from the brief)

- **No lookahead.**
  - Splits are walk-forward with an embargo. Never random folds on a time series.
  - A feature unavailable at forecast time is never used. Observed weather standing in for a weather forecast is
    labelled on every result that depends on it.
- **A check that cannot fail is not a check.** Each monitor and gate has a recorded break that must make it fire.
  CI runs every break.
- **Numbers come from runs.** Report and README figures are generated from tracked MLflow runs, never typed.
- **Anyone can re-run it.** Data and run outputs stay out of git, so one command fetches the data and regenerates
  every figure in the report and the README. Stored figures are never the source of truth.
- **Claims are scoped to the checks.** No public line claims more than a passing check shows.
- **The monitor recommends; it never acts.** A tripped threshold produces a written recommendation with its evidence.
  Nothing retrains on its own.
- **The model stays simple.** A seasonal baseline and one explainable model. A new model family needs the owner's
  ruling.
- **Data never enters git.** Scripts fetch it into the gitignored `data/`. Hydro-Québec's series is CC BY-NC 4.0 and
  "informational, not official": attribute it and say so on every public surface. Nothing here is used commercially.
- **Authorship.** Built with Claude Code under the owner's direction. Never imply he wrote or reviewed code.

## This repository goes public with its history

- Before each commit, check it holds nothing private: credentials, paths outside the repo, agent run records or data.
  `uv run valiwatt safety` scans what git tracks and stages; it does not know the private names in `ESTATE.local.md`,
  so grep the staged diff for those by hand.
- Commits use this repository's configured author identity, a GitHub noreply address. Never change `user.email`.
- No remote until the owner rules on the public home.
- If `ESTATE.local.md` exists, read it: it holds private context for sessions on the owner's machine. Never commit it,
  quote it or reference its contents in a tracked file.

## Departures from the brief

None yet.
