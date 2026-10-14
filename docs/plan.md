# Architecture and plan

How v1 is built, in the order it is built. `BRIEF.md` says what v1 is; `AGENTS.md` holds the rules and the owner's
rulings. This page fixes the design choices the brief left to the build. It quotes no result: results live in the
reports, generated from runs. It was revised on 2026-10-14 after a fresh-context review; the changes are in git.

## 1. The forecast

- **Target:** Hydro-Québec's hourly demand, each value the mean over the hour ending at its UTC timestamp.
- **Issue time:** day-ahead. The forecast for local-standard-time day D is issued at its origin, the end of day D−1,
  when every hour of D−1 has been observed. It covers the 24 hours of D.
- **Inputs, and when each is known:**

| Input | Known at the origin? |
|---|---|
| Demand at the same hour one day earlier (lag 24 h) | yes |
| Demand at the same hour one week earlier (lag 168 h) | yes |
| Hour of day on the local clock, day type (weekday, Saturday, Sunday or Québec statutory holiday) | yes |
| Temperature at the same hour of D−1 (*persistence weather*) | yes |
| Temperature at the target hour (*observed weather*) | **no**: stands in for a weather forecast |

- "Known at the origin" is judged on timestamps, with the values as fetched in 2026: no publication delay and no
  revisions are modelled, because no real-time vintage of either source is available. Every result says so.
- **Two weather scenarios, two separate fits.** Each scenario is its own model, fitted on its own temperature input,
  with its own out-of-sample errors, intervals and thresholds.
  - *Persistence weather* is the production scenario: the only one a forecaster could run at the origin. The frozen
    model, the intervals, the thresholds, the 2024 replay and every recommendation use it.
  - *Observed weather* is an optimistic bound, reported in validation only and always labelled ("météo observée" /
    "observed weather"). It issues no recommendation.
  - The distance between the two bounds the part of the skill that a weather forecast would add.
- **Missing data:** a training row with a missing target or input is dropped. At issue time, an hour whose inputs are
  missing is not predicted, and the data-quality monitor records why. Nothing is interpolated.

## 2. Two models, fixed before validation

- **Seasonal baseline:** demand at the same hour one week earlier.
- **One explainable model:** ordinary least squares on named features: hour-of-day by day-type cells, heating and
  cooling degree-hours (base 18 °C) for each of five fixed time-of-day blocks (night 0–5 h, morning 6–9 h, day
  10–15 h, evening 16–20 h, late 21–23 h), and the lag-24 and lag-168 demand.
- **The configuration is fixed now, not chosen on the test folds.** The temperature source is Montréal's station, the
  primary station the brief names. The six-station mean and any other variant are reported as sensitivity runs and are
  never used to choose. Choosing on the same folds that produce the scores would make them in-sample for that choice.
- **What the coefficients mean.** They are partial effects: each says what a feature adds *given* the lagged demand.
  The report shows them next to an explanatory fit without the lags, used for interpretation only. No OLS standard
  error or p-value is reported (the errors are autocorrelated); comparisons use the week-block bootstrap.
- **Accepted risk: level drift.** The data-quality report shows weather-adjusted demand rising from 2021. The lags carry
  part of the level; the rest shows up as bias. The model card states it, validation measures it by year with a
  bootstrap interval, and the monitor's two-sided bias rule watches it. No trend term is added.

## 3. Validation, on 2019–2023 only

- **Walk-forward.** Monthly test folds from January 2020 to December 2023. Each fold's model is refitted on the hours
  before the fold, minus the last 7 days. Every training hour precedes the fold and the lags only look back, so no
  information crosses the boundary. The 7-day embargo, which the brief requires, makes each fold model at least a week
  old at its first forecast, so the scores are conservative next to a model refitted up to the origin. The embargo
  removes training rows only; test rows still read their lags from those hours.
- **Error analysis:** mean absolute percentage error (demand never nears zero), mean absolute error, and signed bias;
  overall, by season, by hour of day, by year, and by load decile. Load deciles are taken on the *forecast*, with cut
  points fixed on the training folds, so the grouping does not condition on the outcome. Every figure by group carries
  a week-block bootstrap interval, and any "A beats B" sentence needs one that excludes zero.
- **Test cases, with criteria fixed in advance.**
  - *The 2020 shift:* the spring 2020 months' error and bias against the same months of the other years. Intervals
    cannot cover 2020 (section below), and the report says so.
  - *The February 2023 cold peak:* the busiest hours of the design years (the data-quality run already finds them in
    February 2023), reported as one event: hours covered out of hours, with each hour's error. Not a rate.
- **Conformal prediction intervals (persistence weather).** Nominal 90 %. The score is the *relative* error
  |actual − forecast| / forecast, because demand errors grow with the load; the half-width is that score's conformal
  quantile times the forecast. Each month is calibrated on the out-of-sample scores of the previous 12 monthly folds,
  so intervals start in January 2021 and 2020 has none. The absolute-error form is a sensitivity run. Coverage is
  measured overall, by season, by hour group and by forecast load decile, each with a bootstrap interval. Conformal
  coverage assumes calibration errors resemble future errors; under drift it may not hold, and the measured coverage,
  not the nominal one, is the result.
- **Is a frozen model like a refitted one?** Fold models are refitted monthly; the production model is frozen for a
  year. A *pseudo-replay* answers it on design years only: freeze at the end of 2022 (embargo kept), replay 2023 with
  intervals and thresholds from folds up to 2022, and compare its errors, coverage and alarm days with the refitted
  2023 folds. It is also where the monitoring rules are tried before 2024 exists for them (section 5).

## 4. Freezing, and where every threshold comes from

- **Frozen model:** the persistence-weather model refitted on 2019–2023 with the fixed configuration, logged to MLflow
  with the hash of its coefficients, never refitted during the replay. Its calibration scores are the out-of-sample
  scores of the 2023 folds.
- **Performance thresholds (persistence weather), from validation, per season.** The monitor reads *weekly* statistics
  (Monday to Sunday, local standard time): weekly MAPE, weekly signed relative bias, weekly interval coverage. For
  each meteorological season, the thresholds are quantiles of the same weekly statistics over the 2021–2023 validation
  folds of that season: MAPE above its 95th percentile; bias outside its 2.5th–97.5th percentiles (two-sided);
  coverage below its 5th percentile. With per-season thresholds, winter is not mistaken for decay.
- **Drift thresholds, also from validation.** Each week's temperature and forecast distributions are compared, with
  Evidently's normed Wasserstein distance, to a reference of the same calendar weeks (±2 weeks) in earlier years only.
  The alarm level is the 95th percentile of that weekly statistic over the design-year weeks, each computed against
  its own earlier years. Evidently's default thresholds are not used.
- **Day-level data-quality criteria,** for the replay's monitor: a day fails when an hour is missing or duplicated
  outside the documented fall-back shape, a value is outside the data-quality layer's bounds, or the primary station
  is silent for more than 6 of the day's hours. A documented fall-back night is recorded as *expected*, not a failure.
- All thresholds, quantiles and windows are logged as parameters of the validation run. The replay reads them from
  that run.
- **Freeze before 2024.** The monitoring rules (statistics, windows, quantiles, the decision table of section 5, the
  Evidently test) may be tuned on the 2023 pseudo-replay. They are then frozen in a named commit before the 2024 replay
  ever runs, and the report cites that commit. Any later change to them is listed in `AGENTS.md` as a departure,
  labelled post hoc in the report, and the earlier replay is shown beside it.

## 5. Monitoring: 2024 replayed as production

- **The clock advances hour by hour.** At each origin the replay issues the next day's forecast with the frozen
  persistence model; each hour is evaluated when its demand arrives. Every read goes through an accessor that refuses
  any timestamp past the clock. The replay works from the raw fetched records, never from the full-window status
  columns of the data-quality layer, and scores a spike only once the following hour has arrived.
- **Three monitors:** data quality (day-level criteria, section 4); drift (weekly, section 4); performance (weekly,
  section 4). The weekly monitors stay quiet until the replay has a full week; they never borrow 2023 errors.
- **An ordered decision table.** Each week the replay emits at most one recommendation, with all evidence listed:
  1. A data-quality failure that week → *investigate the data*; the performance rules are suppressed for that week.
  2. MAPE above threshold, or bias outside its band, for two consecutive weeks → *retrain*.
  3. Coverage below threshold for two consecutive weeks while bias stays inside its band → *recalibrate the intervals*.
  4. Input or forecast drift beyond its alarm level for two consecutive weeks, with performance inside its thresholds →
     *investigate the data* (the inputs changed; check them before trusting the forecast).
  Otherwise: no recommendation. A single bad day never yields *retrain*.
- **The monitor never acts.** The frozen model's coefficient hash and the interval half-widths are checked before and
  after the replay; the replay fails if either changed.

## 6. Breaks: every gate and monitor is seen to fire

Each guard lands with a break that must make it fire and a clean case that must stay quiet, on the day the guard is
built:

- *Lookahead:* a feature reading demand after its origin is refused; a training row inside the embargo or after the
  fold start is refused; design code given a 2024 row is refused; thresholds derived from replay data are refused.
- *Clock:* the replay reading past its clock raises.
- *Monitors:* injected temperature drift fires the drift monitor (the seasonal reference stays quiet on clean data);
  an injected sensor gap fires data quality; a shifted demand level fires *retrain*; deflated intervals fire
  *recalibrate*; two rules at once yield one recommendation with both pieces of evidence; a one-day shock does not
  yield *retrain*.
- *Never acts:* a replay that writes to the model or the half-widths fails.
- *Render:* a report whose runs do not chain, or whose code changed since its runs, is refused.

CI has no data, so it runs every break on synthetic frames. `valiwatt all` also runs the same break suite as a tracked
`breaks` run; the report renders the break table from it and refuses to render if any break did not fire.

## 7. Tracking and reports

- **Run lineage.** Each MLflow run tags the runs it consumed: validation → data quality (and the data hashes); frozen
  model → validation; pseudo-replay and replay → frozen model and validation; breaks → code hash. The renderer starts
  from the replay, follows the chain, and refuses a missing or mismatched link or a code change since the runs.
- **The validation report,** French first and English derived, rendered from that chain and committed under
  `docs/report/` with its run IDs and code hash (owner's ruling). Contents:
  - a model card: intended use; out-of-scope uses (no operational dispatch, no trading or pricing, informational
    data); data and dates; metrics and both test cases; the weather-scenario caveat; limitations; "Built with Claude
    Code under the owner's direction";
  - each method in plain words;
  - validation results, test cases, coverage, the pseudo-replay, the 2024 replay and its recommendations;
  - the break table;
  - a table mapping SR 11-7's expectations (conceptual soundness, ongoing monitoring with process verification and
    benchmarking, outcomes analysis with back-testing, data quality, documentation) and the NIST AI RMF categories the
    checks touch (MAP, MEASURE, MANAGE) to the checks that cover them, quoting the documents with section identifiers.
    The quotations go under the same live check as the licence quotes, with a PDF text extractor. The "not covered"
    column names at least: independent validation and effective challenge (the build and the checks come from one AI
    agent session under one owner, with no independent human review of the code), governance, policies and controls,
    the model inventory, periodic review, override analysis, and use in decisions; on the NIST side, most of GOVERN.
- The README quotes no result figure; it points to the committed report.

## 8. Milestones

| Date | Done when |
|---|---|
| 2026-10-14 | Lookahead, embargo and holdout breaks; walk-forward validation, error analysis, both scenarios and the sensitivity runs, tracked with lineage |
| 2026-10-15 | Conformal intervals and coverage; thresholds logged; frozen model; the 2023 pseudo-replay; SR 11-7 / NIST mapping draft |
| 2026-10-16 | Monitoring rules frozen in a named commit; the 2024 replay with its monitors, decision table and breaks; breaks run |
| 2026-10-17 | Validation report (FR, EN) with the model card, committed under `docs/report/`; CI green on the private remote |
| 2026-10-18 | Full fresh-context review of the tree and every public line; fixes |
| 2026-10-19 | Buffer |
| 2026-10-20 | v1 ready for the owner's review |
