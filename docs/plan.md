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
  revisions are modelled, because no real-time vintage of either source is available. Every result says so. One
  consequence: design code (validation, the frozen models) reads the data-quality layer's full-window series, whose
  one misplaced-record repair fills 2022-01-01 05:00 UTC with a record stamped a year later; the replay, which rebuilds
  from the raw records cut at its clock, cannot see that repair before the record arrives.
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
  points from the out-of-sample forecasts of all folds (forecasts only, never the outcome), so the grouping does not
  condition on the outcome. Every figure by group carries
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

- **Frozen model:** the persistence-weather model refitted on 2019–2023 with the fixed configuration, keeping the
  7-day embargo before 2024 so that the pseudo-replay (frozen the same way at the end of 2022) rehearses exactly the
  same step. It is logged to MLflow with the hash of its coefficients and half-width, registered as a model version
  with the alias `replay-2024`, and never refitted during the replay. Its half-width is the conformal quantile of the
  out-of-sample relative scores of the 12 folds just before the replayed year (2023 for the replay, 2022 for the
  pseudo-replay): the validation rule applied to one more month.
- **Performance thresholds (persistence weather), from validation, per season.** The monitor reads *weekly* statistics
  (Monday to Sunday, local standard time): weekly MAPE, weekly signed relative bias, weekly interval coverage. For
  each meteorological season, the thresholds are quantiles of the same weekly statistics over the 2021–2023 validation
  folds of that season: MAPE above its 95th percentile; bias outside its 2.5th–97.5th percentiles (two-sided);
  coverage below its 5th percentile. With per-season thresholds, winter is not mistaken for decay. Each replay has
  its own set: the 2024 replay reads the 2021–2023 weeks, the 2023 pseudo-replay the 2021–2022 weeks only.
- **Drift thresholds, also from validation.** Each week's temperature and forecast distributions are compared, with
  Evidently's normed Wasserstein distance, to a reference of the same calendar weeks (±2 weeks) in earlier years only.
  The alarm level is the 95th percentile of that weekly statistic over the design-year weeks, each computed against
  its own earlier years. Evidently's default thresholds are not used.
- **Day-level data-quality criteria,** for the replay's monitor: a day fails when an hour's raw record is missing,
  blank or duplicated outside the documented fall-back shape, a demand or temperature value is outside the
  data-quality layer's bounds (the value is then unusable), or the primary station is silent for more than 6 of the
  day's hours. A documented fall-back night is recorded as *expected*, not a failure. Spikes are recorded, not failed.
- All thresholds, quantiles and windows are logged as parameters of the validation run. The replay reads them from
  that run.
- **Freeze before 2024.** The monitoring rules (statistics, windows, quantiles, the decision table of section 5, the
  Evidently test) may be tuned on the 2023 pseudo-replay. They are then frozen in a named commit, the `rules-frozen`
  tag, before the 2024 replay ever runs, and the report cites that commit. The rule files are every file of the
  package and its configuration except those that only render, scan or dispatch (`monitor/rules.py` lists them); a
  file added later counts too. The 2024 replay refuses to run when any rule file differs from the tag, and refuses a
  tag moved since an earlier 2024 replay (deleted runs included) unless `AGENTS.md` records that move by commit and
  the run is asked for with `--departure`; such a run names the replays it departs from. Every run, the data-quality
  run included, carries the rules hash, and runs made under other rules are refused. Any later change to the rules is listed in
  `AGENTS.md` as a departure, labelled post hoc in the report, and the earlier replay is shown beside it.

## 5. Monitoring: 2024 replayed as production

- **The clock advances hour by hour.** At each origin the replay issues the next day's forecast with the frozen
  persistence model; each hour is evaluated when its demand arrives. Every read goes through an accessor that refuses
  any timestamp past the clock. The replay works from the raw fetched records, never from the full-window status
  columns of the data-quality layer: the canonical series, spike scores, lags and drift references are rebuilt from
  the records cut at the clock. Each day is scored, and its data quality judged, one hour after its last hour arrives,
  since the next record is what shows whether the last hour was a spike or half of a fall-back pair. The exception is
  the replay's last day, when the data end with it (2024 data never enter the pseudo-replay; 2025 is outside the
  window): it is judged at the end of the data, its last hour's spike stays unscored, and its record says so.
- **Thresholds.** The replay refuses thresholds computed on any week of the year it replays.
- **Three monitors:** data quality (day-level criteria, section 4); drift (weekly, section 4); performance (weekly,
  section 4). The weekly monitors stay quiet on a partial week (the first and last of a replay); they never borrow
  2023 errors.
- **An ordered decision table.** Each week the replay emits at most one recommendation, with all evidence listed:
  1. A data-quality failure that week → *investigate the data*; the performance rules are suppressed for that week.
  2. The same performance failure two weeks running (MAPE above its threshold both weeks, or bias beyond the same
     side of its band both weeks), broad in each week; or the bias beyond the same side of its band three weeks
     running → *retrain*.
  3. Coverage below its threshold two weeks running, broad in each week, while bias stays inside its band both weeks
     → *recalibrate the intervals*.
  4. The same input (temperature, or forecast) drifting beyond its alarm level two weeks running, with performance
     inside its thresholds both weeks → *investigate the data* (the inputs changed; check them before trusting the
     forecast).
  Otherwise: no recommendation. *Broad* means the week stays beyond the threshold when its two most extreme days, in
  the direction of the failure, are set aside. Why: the forecast reuses demand from one day and one week before, so
  one bad day of demand also spoils the next day's forecast and the same day's a week later; that never puts more
  than two spoiled days into one week, and with two days set aside the statistic is no further out than the mean of
  the five untouched days. So one bad day can make a week broad only when the week's other days already average
  beyond the threshold: it never yields *retrain* or *recalibrate* without failing days the weeks already held. A
  bad Monday-to-Sunday week comes back the next week through the week-old lag as an echo of the opposite sign, so a
  MAPE pair whose two weeks have their bias flagged on opposite sides does not count. Trimming costs power against a
  slow, sustained shift, hence the three-week arm of rule 2: a bad stretch of up to a week lies in at most two
  calendar weeks and puts only its opposite-signed echo into the week after, so it cannot put three weeks on the same
  side unless a neighbouring week was already out of band on that side. A week with a data-quality failure or fewer
  than 120 scored hours has no performance statistics and breaks any streak. The first wording of rule 2 (MAPE or
  bias out of band two weeks running, in any mix) is logged beside the decisions as a sensitivity; it never
  recommends.
- **Detection power.** Each replay measures the rules' power on its own hours: from the first full week of each
  season, a sustained gap of ±2 to ±8 % is put between forecast and demand (weeks until *retrain*), or the
  intervals' half-width is multiplied by 0.9 down to 0.5 (weeks until *recalibrate*). The report states these
  detection limits.
- **What the rules cannot tell apart, measured.** Trimming bounds what two spoiled days can do, and no more. Through
  the day-old lag a stretch of n bad days spoils n + 1 days, and through the week-old lag n days of the following
  week, so from three days on a stretch can make both its own week and that echo week broad on the same failure with
  no failing days of their own. The echo exclusion then holds only when both weeks' biases are flagged on opposite
  sides; when one stays inside its band (a band wide on that side, or a week that also holds days of the stretch,
  as when the stretch crosses a Monday), the pair counts and rule 2 says *retrain*. A bad week beside a week already
  out of band on the same side can do the same. A pre-freeze review found this on the 2023 rehearsal, through the
  real replay. Weekly statistics cannot tell an episode of several days that passes the data-quality checks from two
  weeks of model failure. The rules stay as written, and the pseudo-replay measures the price
  (`monitor/sensitivity.py`), which the report states:
  - **false alarms from short episodes:** demand multiplied by 0.7, 0.85, 1.15 or 1.3 for 1, 3, 5 or 7 days, from
    every start day between the first judged week of 2023 and two weeks before its end, each through the real
    replay. An alarm is *clean* when none of the weeks it rests on was already beyond any of its performance
    thresholds in 2023 as it was, and *assisted* otherwise;
  - **level shifts in demand:** demand multiplied by 0.9 to 1.1 from the first full week of each season (weeks until
    *retrain*). The persistence forecast follows demand through its lags, so a shift in demand leaves a much smaller
    gap between forecast and demand; the power table measures the gap, not the shift;
  - **what the echo exclusion costs:** a forecast given a random error per day (standard deviation 6 %, 10 % or
    15 %), with the rules run with and without the exclusion (runs where it suppresses or delays *retrain*).
- **Evidently.** Each week with a drift alarm is saved as an Evidently report, and the replay refuses to log a week
  where Evidently's value differs from its own.
- **The monitor never acts.** The frozen model's coefficient hash and the interval half-widths are checked before and
  after the replay; the replay fails if either changed.

## 6. Breaks: every gate and monitor is seen to fire

Each guard lands with a break that must make it fire and a clean case that must stay quiet, on the day the guard is
built:

- *Lookahead:* a feature reading demand after its origin is refused; a training row inside the embargo or after the
  fold start is refused; design code given a 2024 row is refused; thresholds derived from replay data are refused.
- *Clock:* the replay reading past its clock raises; records corrupted after a cut change nothing judged before it;
  a record that arrives a week late is not used before it arrives.
- *Monitors:* injected temperature drift fires the drift monitor (the seasonal reference stays quiet on clean data);
  an injected sensor gap fires data quality, and so do a missing and a duplicated demand record; a shifted demand
  level fires *retrain*; deflated intervals fire *recalibrate*; two rules at once yield one recommendation with both
  pieces of evidence; a one-day shock at the end of a week (the worst place) does not yield *retrain*, while the
  two-week rule alone would; neither does a low week and its echo; with two days altered in any way, the trimmed
  statistic never passes the untouched days (a property checked on 2,000 draws); a drift reference holding replayed
  days, and thresholds built on the replayed year, are refused; partial weeks have no drift statistic; bias beyond
  the same side three weeks running fires *retrain*, unless an echo or an unjudged week breaks the run.
- *Freeze:* the 2024 replay is refused with no tag, with a rule file edited or added after it, or under a moved tag;
  a frozen model or validation run from other data, another year or other rules is refused; so is a model file or
  registered model whose hash no longer matches.
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
