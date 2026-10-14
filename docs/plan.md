# Architecture and plan

How v1 is built, in the order it is built. `BRIEF.md` says what v1 is; `AGENTS.md` holds the rules. This page fixes the
design choices the brief left to the build. No result is quoted here: results live in the reports, generated from
runs.

## 1. The forecast

- **Target:** Hydro-Québec's hourly demand, each value the mean over the hour ending at its UTC timestamp.
- **Issue time:** day-ahead. Each forecast is issued when all of day D−1 (local standard time) has been observed, for
  the 24 hours of day D. The origin is therefore the last hour of D−1.
- **Inputs, and when each is known:**

| Input | Known at the origin? | Note |
|---|---|---|
| Demand at the same hour one day earlier (lag 24 h) | yes | every target hour of D maps to an hour of D−1 |
| Demand at the same hour one week earlier (lag 168 h) | yes | |
| Hour of day, day of week, Québec statutory holiday | yes | calendar from the `holidays` package (Canada, QC) |
| Temperature at the target hour | **no** | stands in for a weather forecast; see the two scenarios below |

- **Two weather scenarios, both always reported:**
  - *observed weather:* the temperature actually observed at the target hour. It stands in for a perfect weather
    forecast, so every result that uses it says so ("météo observée" / "observed weather");
  - *persistence weather:* the temperature observed at the same hour of D−1, which is known at the origin.
  The distance between the two is the part of the forecast's skill that depends on a weather forecast this project
  does not have. It is reported as a model risk, not hidden.
- **Missing data:** a training row with a missing target or input is dropped. At issue time, a forecast hour whose
  inputs are missing is not predicted (no value) and the monitor reads that as a data problem. Nothing is interpolated,
  as in the data-quality layer.

## 2. Two models, no more

- **Seasonal baseline:** demand at the same hour one week earlier.
- **One explainable model:** ordinary least squares on features a reader can name: hour-of-day by day-type (weekday,
  Saturday, Sunday or holiday), heating and cooling degree-hours from the panel or the primary station (the source is
  chosen in validation, on design years only), their interaction with hour-of-day blocks, and the lag-24 and lag-168
  demand. The coefficient table is the explanation. A new model family needs the owner's ruling.

## 3. Validation, on 2019–2023 only

- **Walk-forward with an embargo.** Monthly test folds from January 2020 to December 2023. For each fold, the model is
  refitted on every earlier hour except the last 7 days before the fold (the embargo): the hours just before a test
  month resemble it most, and training right up to them would flatter the score. 2020 is out of sample, so the 2020
  shift is a test case and not something the model was fitted to.
- **Error analysis:** mean absolute percentage error, mean absolute error, bias; by season, hour of day, and load
  decile; skill against the baseline. Any "A beats B" sentence needs the week-block bootstrap behind it.
- **Test cases, confirmed in the data, not assumed:** the spring 2020 months and the February 2023 cold peak hours,
  with their errors set against the rest of the period.
- **Conformal prediction intervals.** Nominal 90 %. For each month, the interval half-width is the conformal quantile
  of the absolute *out-of-sample* errors of the previous 12 monthly folds (errors on the training data would make the
  intervals too narrow). Intervals therefore start in January 2021. Coverage is measured overall, by season, by hour
  group, by load decile, and at the February 2023 peak hours. Conformal coverage assumes errors that resemble the
  calibration errors; the data-quality report already shows demand drifting, so the measured coverage, not the nominal
  one, is the result.

## 4. Freezing, and the alert thresholds

- **Frozen model:** refitted on all of 2019–2023 with the configuration validation chose, then logged to MLflow and
  never refitted during the replay. Its calibration errors are the out-of-sample errors of the 2023 folds.
- **Thresholds come from validation, never from 2024.** The monitor's alert levels (rolling error, rolling bias,
  rolling interval coverage) are set from the distribution of those quantities across the validation folds and logged
  as parameters of the validation run. The replay reads them from that run. They are written before a single 2024
  forecast exists.

## 5. Monitoring: 2024 replayed as production

- The replay walks through 2024 one day at a time. Each day it issues the forecast with the frozen model, then, once
  the day's demand arrives, evaluates it. It never reads data past its clock.
- **Three monitors:**
  - *data quality:* the day's data through the same gates as the data-quality layer (missing hours, duplicates, sensor
    gaps, bounds);
  - *drift (Evidently):* inputs and predictions over the trailing 28 days against a reference made of the same calendar
    weeks in the design years, so that the turn of the seasons is not mistaken for drift;
  - *performance:* rolling 28-day error, bias and interval coverage against the validation thresholds.
- **Recommendations, never actions.** When a monitor trips, the replay writes a recommendation with its evidence (the
  numbers, the dates, the threshold and where it came from):
  - a data-quality failure: *investigate the data*;
  - coverage short of its threshold while bias stays inside its own: *recalibrate the intervals*;
  - bias or error beyond threshold for two consecutive weeks, or confirmed input drift with growing error: *retrain*.
  Nothing retrains or recalibrates on its own.

## 6. Breaks: every monitor and gate must be seen to fire

CI has no data (data never enters git), so every monitor is a pure function over data frames, and CI runs each against
a synthetic break that must make it fire, next to a clean case that must stay quiet: injected drift (temperature
shifted), an injected sensor gap, a shifted input level, deflated intervals. The drift the data-quality report found
in the real data is the real-data demonstration; the synthetic breaks are what CI runs.

## 7. Tracking and reports

- MLflow experiments: data quality, validation, frozen model, monitoring. Each report states the runs it was rendered
  from and refuses to render when the code changed since those runs.
- **Validation report**, French first and English derived: the model card; each method in plain words; the error
  analysis, test cases and coverage; the monitoring replay and its recommendations; and a table mapping each
  expectation of SR 11-7 (conceptual soundness, ongoing monitoring, outcomes analysis) and of the NIST AI RMF to the
  check that covers it, quoting the documents themselves with section identifiers, and naming what the project does
  not cover.
- **One command:** `valiwatt all` fetches, checks, validates, freezes, replays and renders.

## 8. Milestones

| Date | Done when |
|---|---|
| 2026-10-14 | Features, both models, walk-forward validation and error analysis tracked in MLflow, with their tests |
| 2026-10-15 | Conformal intervals and coverage; thresholds derived and logged; frozen model logged |
| 2026-10-16 | Monitoring replay with its three monitors, recommendations, and the CI breaks |
| 2026-10-17 | Validation report (FR, EN), model card, SR 11-7 / NIST AI RMF mapping |
| 2026-10-18 | Full fresh-context review of the tree and every public line; fixes |
| 2026-10-19 | Buffer |
| 2026-10-20 | v1 ready for the owner's review |
