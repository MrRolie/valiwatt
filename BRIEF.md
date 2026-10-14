# valiwatt: founding brief

Dated founding record, 2026-10-13. Never edited; departures are listed in `AGENTS.md` with their ruling.

## What it is

valiwatt validates and monitors a forecast of Québec's hourly electricity demand, the way a model-risk team would.
The forecast is deliberately simple. The project is about the checks around it: how the model is tested before it is
trusted, how it is watched once it runs, and what it says when it should no longer be trusted.

## Why

- Most forecasting projects stop at an accuracy score. A model used for real decisions also needs validation under
  hard conditions, monitoring in production, and documentation a non-specialist can follow.
- Electricity demand is a good test bed: public hourly data, strong seasonality, weather sensors with real gaps, a
  real distribution shift (2020), and a record cold peak (February 2023).

## The data

- **Demand:** Hydro-Québec's open data "Historique de la demande d'électricité au Québec", the hourly average in MW,
  every hour of 2019 to 2024 (52,608 records, read from the portal's API on 2026-10-13). Licence CC BY-NC 4.0.
  Hydro-Québec describes this series as informational, not official data; every public surface says so.
- **Weather:** Environment and Climate Change Canada's hourly station observations, for example MONTREAL INTL A
  (climate ID 7025251), fetched from climate.weather.gc.ca. Its licence terms are split between ECCC's own data
  licence and the Open Government Licence; read both before anything is published.
- Data is fetched by script and never committed.

## The product (v1)

**In scope:**
- **Data quality and representativeness:** gaps, daylight-saving hours, outliers, and how well the chosen stations
  stand for province-wide demand.
- **A simple forecast:** a seasonal baseline and one explainable model. No model zoo.
- **Validation:**
  - walk-forward with an embargo (never random folds on a time series);
  - error analysis by season, hour and load level;
  - the 2020 shift and the February 2023 cold peak as test cases (to be confirmed in the data, not assumed);
  - prediction intervals by conformal prediction, with their measured coverage.
- **Monitoring:** 2024 replayed hour by hour as if it were production, with the model frozen at the end of training.
  - drift checks on inputs and predictions (Evidently);
  - performance decay against alert thresholds;
  - when a threshold trips, a written recommendation (investigate the data, recalibrate the intervals, or retrain)
    with its evidence. The monitor never retrains on its own.
- **Tracking:** every run in MLflow.
- **The validation report:**
  - in French first, with an English version derived from it;
  - a model card;
  - each method explained in plain words;
  - a table mapping each expectation of SR 11-7 (conceptual soundness, ongoing monitoring, outcomes analysis) and of
    the NIST AI RMF to the check that covers it, and naming what this project does not cover.
- **CI:** GitHub Actions runs the checks on every change, including the cases that must fail.

**Out of scope for v1:** a production service, a dashboard server, real-time ingestion, more than one forecast target,
anything that trades or prices on the data.

## The rails (these define the project)

1. **No lookahead.** A feature unavailable at forecast time is never used. If observed weather stands in for a weather
   forecast, every result that depends on it says so.
2. **A check that cannot fail is not a check.** Each monitor and gate carries a recorded break (injected drift, an
   injected sensor gap, a shifted input) that must make it fire, and CI runs those breaks.
3. **Numbers come from runs.** Every figure in the report and the README is generated from a tracked run, never typed
   by hand.
4. **Claims are scoped to the checks.** No public line claims more than a passing check shows.
5. **Authorship.** Built with Claude Code under the owner's direction. No surface implies he wrote or reviewed the
   code.

## The repository is public at his go, with its history

Commit nothing private:
- no credentials;
- no paths outside this repository;
- no agent run records (`docs/audits/`, `*.args.json`);
- no data files.

Private context for agent sessions on the owner's machine lives in `ESTATE.local.md`, which is gitignored.

## First tasks, in order

1. **Data and licences.** Fetch scripts for both sources, the data-quality and representativeness report, and both
   ECCC licence pages read and quoted with their URLs in `docs/data-licences.md`.
2. **Architecture and plan.** The design is the build session's call.
3. **The validation harness,** with the rails as tests from the first commit, then the forecast it validates.

## Open decisions (the owner's)

- **The code licence:** MIT is proposed.
- **The public home and timing.**
- **Whether the English report ships in v1** or follows.
