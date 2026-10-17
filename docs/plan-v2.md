# Architecture and plan, v2

What v2 changes, and how it is built and judged. `BRIEF.md` says what valiwatt is; `AGENTS.md` holds the rules and
the owner's rulings; `docs/plan.md` fixed v1's design before any v1 result and stays v1's record, unchanged. This page
fixes v2's design before any v2 run. Unlike v1's plan it is written after results were seen: section 0 says which, and
what this page still fixes in advance. It is committed and tagged `plan-v2` before any v2 code; every later change to
it is listed in section 18, and a test checks that list against the history.

## 0. What had been seen when this page was written

### 0.1 Timeline

| When | Event | What it showed the developers |
|---|---|---|
| 2026-10-13 | Brief; v1's plan | The data-quality report on 2019–2023 (its gates also read 2024) |
| 2026-10-14 | v1's 2024 replay; the departure from v1's frozen rules; the 2024 replay run again | v1's hourly errors, weekly statistics, coverage and recommendations on 2024 |
| 2026-10-15 | Research outside the tracked runs: archives of weather forecasts; model families with observed weather | Which forecast archives cover which days of 2019–2024 (coverage only); the delivery delays of NOAA's NBM text products on its cloud bucket, 2020–2024; families' errors on the 2020–2023 folds |
| 2026-10-15 | A scratch spike: six model families × three temperature inputs on v1's 48 monthly folds, 2020–2023, scored on one common set of hours, then reproduced by a second, independent script | For each family and input: error overall and in winter, bias, the error at the five busiest hours of 2019–2023 and at hours colder than the fold's training data |
| 2026-10-15 | The owner's three rulings (sections 1, 3, 4, 11) | — |
| 2026-10-16 | The owner's rulings on the questions this page left to him, on five cards: v2 as a second freeze, and the ledger (section 10); the NOAA and IEM attribution and its exact wording, approved (section 14); what follows if 2025 does not confirm v2 (section 8.5); and when 2025 is clean, whose third and fourth cards replace the first two's conditions: the 2025 gate starts clean or refuses, no code change keeps 2025 clean on its own, and only his ruling lifts a refusal; and whose fifth rules a first 2025 start that stops before it finishes (section 11.3.5) | — |
| tag `plan-v2` | This page | — |

What nobody had computed when this page was tagged, and what it therefore still fixes in advance:

- any v2 model on any hour of 2024;
- any Hydro-Québec demand record of 2025: none is published yet, and section 11.3.1 bounds every download of the
  export. (v1 fetched ECCC's observations of January 2025, since ECCC exports a whole UTC month at a time; they are
  cut at load, where the window ends at 2025-01-01 05:00 UTC, and they are weather, not the target);
- v2's intervals, thresholds, drift alarm levels, frozen models and 2023 rehearsal;
- the decision rule of section 8, on any year.

### 0.2 The selection, said plainly

- The families compared on the same folds: v1's least squares; a cubic polynomial of temperature per hour; the
  spline model of section 4; gradient-boosted trees; v1's form plus boosted trees on its residuals; and a
  piecewise-linear form whose break points were chosen after looking at the 2019–2023 demand–temperature curve. Each
  with three temperature inputs: yesterday's observation (v1's production input), the NBM forecast of section 3, and
  the observation at the target hour.
- The spline model did not have the lowest overall error on the forecast input: v1's form plus boosted trees did.
  It was chosen for its smaller shortfall at the busiest and coldest hours and because it stays explainable, one
  temperature curve per hour of the day.
- The spike is not a tracked run: its scripts and outputs are not in this repository, so nobody can re-run its figures
  from here, and no report quotes them. They are given once, here, only to say what the choice was made on: on 34,935
  common hours of 2020–2023, mean absolute percentage error 4.41 % for v1 as shipped, 3.48 % for v1's form on the NBM
  forecast, 2.83 % for the spline model on it and 2.81 % for v1's form plus boosted residuals on it; at the five
  busiest hours of 2019–2023 (3 and 4 February 2023), a mean shortfall of 27.0 %, 12.8 %, 5.7 % and 9.5 %
  respectively.
- What ties them to this repository: before any v2 result, the developers check on the fetched data that the
  repository's code reproduces the spike (section 17, step 2): the GAM's out-of-sample predictions equal the spike's to
  1e-6 MW on every hour the spike scored, and the forecast input equals the spike's on every day but 2023-12-31. The
  outcome is entered in section 18. The validation report then prints the same measures, from tracked runs anyone can
  repeat, for the three models v2 carries (v1 as shipped, v1's form on the forecast, the GAM), on v2's common hours,
  which hold the 24 hours of 2023-12-31 the spike left out (section 3.3). v1's form plus boosted residuals is not one
  of them, so its two figures are not reproduced.
- So v2's 2020–2023 advantage over v1 is measured on the years that chose it, among eighteen family-and-input pairs
  (twelve of them usable at issue), and is biased upward by that choice. It is shown, labelled, and never used to claim
  that v2 is better.

### 0.3 What is in sample for which decision

| Result | Out of sample for the fit | For the choice of family and input | Seen before v2 was ruled |
|---|---|---|---|
| Validation, 2020–2023 | yes (walk-forward) | **no** | the spike's version of it |
| 2023 rehearsal | yes | **no** (2023 is a validation year) | no |
| 2024 replay | yes | not used in the spike, but seen through v1 before the choice: **no** | **yes**, through v1 and its departure |
| 2025 replay | yes | yes | no |

Only 2025 can be clean, when section 11.3.5 says so, its test of record being the first result computed on 2025 demand.

## 1. What changes from v1, and why

| Item | v1 | v2 | Why | Authority |
|---|---|---|---|---|
| Production model | Least squares: hour × day-type cells, heating and cooling degree-hours per block of hours, lags 24 h and 168 h | A spline model: one temperature curve per local hour, plus v1's cells and lags (section 4) | Demand's response to cold bends and differs by hour; straight lines per block under-forecast the coldest hours | Owner's ruling 1, 2026-10-15 |
| Comparison models | — | v1's least squares on yesterday's temperature (v1 as shipped) and on the NBM forecast; neither recommends or sets a threshold | To show what v2 changes, and how much comes from the family and how much from the input | Ruling 1 |
| Temperature input | ECCC observation at the same hour of D−1 | NOAA NBM station guidance for Montréal-Trudeau, issued before the origin (section 3) | A real forecast, archived with its issue times, available at issue | Ruling 2 |
| A run covering a day | — | Beyond bracketing every hour of the day, its nine points are finite and inside the data-quality bounds (section 3.2) | A blank or impossible point would leave hours without a forecast, or with an impossible one; the next run is used instead | **Not part of ruling 2**, which asks only that the run's own points bracket every hour: a data-quality refinement, expected to change no day of 2019–2023, which the reproduction of section 17 checks |
| ECCC observations | Production input | Day-level data quality, v1's comparison model, the observed-weather bound, the forecast's error diagnostic | The same sensor stays watched | — |
| Held-out test | 2024 | 2024 is **seen by the developers**; 2025 is held out, once Hydro-Québec publishes it | 2024 was seen through v1 and its departure | Ruling 3 |
| Eve of the replayed year | The 2024 half-width calibrated on 2023-12-31; the 2023 one left 2022-12-31 out | Left out for every freeze (section 6.3) | One rule for both freezes | Ruling 3 |
| Design temperature | No bounds filter, unlike the replay | One filter for design and replay (section 6.3) | Two paths for one input | Ruling 3 |
| Dependencies after the freeze | Kept by hand | Refused by the freeze gate (section 10.2) | A check kept by hand is not a gate | — |
| Freeze and records | One freeze, `rules-frozen`, with its departures section; a ledger of 2024 replays | A second freeze, `rules-frozen-v2`, with its own departures section; the one ledger also takes v2's 2024 and 2025 entries (section 10) | v2 is another model on another input, not a correction of v1's rules | The owner's ruling, 2026-10-16 |

## 2. The forecast

- **Target, issue time, horizon:** unchanged. Hydro-Québec's hourly demand, each value the mean over the hour ending
  at its UTC label; the forecast for local-standard-time day D is issued at its origin, D 05:00 UTC, the end of D−1,
  and covers the 24 hours of D.
- **Inputs, and when each is known:**

| Input | Known at the origin? | Read by |
|---|---|---|
| Demand 24 h and 168 h before the target hour | yes | every model |
| Local hour; day type (weekday, Saturday, Sunday or Québec statutory holiday) | yes | every model |
| NBM forecast temperature at the target hour, from a run available at or before the origin (*forecast weather*) | yes, under the availability rule of section 3.2, which is assumed | v2; v1's form on the forecast |
| ECCC temperature at the same hour of D−1 (*persistence weather*) | yes | v1's form as shipped |
| ECCC temperature at the target hour (*observed weather*) | **no**: stands in for a perfect forecast | the observed-weather bounds only, always labelled |

- "Known at the origin" is judged on timestamps, with the values as fetched: no revision of any source is modelled,
  and a forecast run's arrival is inferred from its run time, not observed. Every result that depends on the
  forecast says so.
- Design code keeps v1's rule: it sees each decision time as the replay sees its clock. Demand is rebuilt from the raw
  records stamped at or before that time; a forecast run is in view only from its availability time.

## 3. The weather input: NBM station guidance for CYUL

### 3.1 Source and files

- NOAA's National Blend of Models (NBM), its NBS station guidance (three-hourly points) for CYUL, Montréal-Trudeau,
  the airport of v1's primary station (ECCC climate ID 7025251), as archived by the Iowa Environmental Mesonet (IEM)
  of Iowa State University: `https://mesonet.agron.iastate.edu/cgi-bin/request/mos.py`, with `station=CYUL`,
  `model=NBS`, one file per year of run time, in CSV.
- `valiwatt fetch --only nbm` downloads them into `data/raw/nbm/` (gitignored, never committed), with a manifest of
  each file's URL, retrieval time, SHA-256, row count and run-time range. It refuses a response whose header lacks
  `runtime`, `ftime`, `model`, `tmp` or `station`, whose station is not CYUL or model not NBS, whose temperature is
  neither a number nor blank, or that holds no row. The data-quality run refuses files that no longer match their
  manifest, and every later step refuses raw files other than those the data-quality run checked (v1's rule,
  extended).
- The yearly files overlap at their boundary. Identical duplicate points (same run, same valid time, same value) are
  dropped and counted; two different values for one run and valid time refuse the load.
- Only the 2 m temperature `tmp` is used. It comes in whole degrees Fahrenheit and is converted as (F − 32) × 5 / 9.
- The pairing is checked: the forecast is read only while the primary station of `config/stations.toml` is
  7025251.

### 3.2 The availability rule, and which run feeds a day

- A run is **available** two hours after its run time. This is an assumption: the archive records no arrival time.
  It rests on the times NOAA's cloud bucket shows for a related product (the hourly NBH bulletins of the 03:00 UTC
  run, May 2020 to December 2024; in a scratch measurement outside the tracked runs, 13 of 1,689 arrived more than two
  hours after their run time). That is evidence about NOAA's publication, not about any archived run, and those
  delivery times include 2024.
- For day D, a run is **compliant** when it is available at or before D's origin, and **covering** when its own
  points include all nine valid times from D 06:00 to D+1 06:00 UTC, so that every hour-ending label of D (D 06:00 to
  D+1 05:00 UTC) lies between two points of that one run, and each of those nine points holds a finite value inside
  the data-quality layer's temperature bounds. That last clause is not part of the owner's ruling, which asks only for
  the bracketing: it is a data-quality refinement (sections 1 and 16). On 2019–2023 it is expected to change no day;
  the reproduction of section 17, step 2, checks that the feed equals the spike's, which had no such clause, on every
  day but 2023-12-31.
- D takes the freshest compliant covering run among the twelve freshest compliant runs, freshest first, the twelve
  chosen by run identity whatever their age, in validation and in the replay alike. The run's time, availability
  time, rank (0 for the freshest) and lead to the origin are recorded for every day, or why no run fed it.
- The label t takes the forecast valid at the instant t, as v1's observed scenario takes the observation stamped t:
  linear interpolation in time between the two points of the chosen run that bracket t, then the conversion to
  Celsius. Points of two runs are never mixed.
- **Same information set in training and forecasting.** Every training day carries the forecast issued for it by the
  same rule, never an observation. A model is fitted on the input it is used with.
- One pure function computes all of this; validation, the frozen fits, the data-quality run, the drift references and
  the replay at each origin all call it.

### 3.3 Information time, not valid time, decides what design code may read

- A forecast's information time is its availability. A run available by the end of the design window is design
  information even where its points are valid after it.
- Hence the edge of the window: 2023-12-31's last two hours need the 2024-01-01 06:00 UTC point of a run issued on
  2023-12-31 and available before that day's origin. Under this rule 2023-12-31 is forecast like every other day, in
  validation and in the 2024 freeze; it carries no observation of 2024, and no label after the window is ever read
  from it. The scratch spike cut on valid time and left that day out, so v2's 2020–2023 hours hold 24 more than the
  spike's.
- Design code opens the run files of 2019 to 2023 only (an explicit list, never a pattern) and keeps the runs
  available at or before the end of the design window. A per-row guard (section 12) refuses any row whose run became
  available after its origin.

### 3.4 Data-quality gates and findings for the forecast

- Gates (the data-quality run fails and nothing downstream runs): any point outside v1's temperature bounds; fewer
  than 98 % of the window's days fed by a run. The 98 % was set after the archive's day counts for 2019–2024 had
  been seen.
- Findings, reported and never gating: identical duplicate points; the hour of the feeding run by year (it moves from
  00:00 to 01:00 UTC during 2020, so the lead to the origin moves from 5 to 4 hours); the rank and lead counts; every
  day without a run, with why; the longest lead of any run's points, by year, and the days fed by a run that also fed
  the day before (section 9.2); the forecast's error against the ECCC observation at the target hour, by season and
  year, on the design years.
- NBM's own model changes over 2019–2025 are not tracked: the forecast series is not homogeneous, and the report says
  so. The drift monitor watches it (section 9).

### 3.5 Sensitivity to the availability margin

Validation also fits the production model with runs available six hours after their run time, which takes away the
day's own early run (00:00 or 01:00 UTC) and feeds each day from the previous evening's run. A three-hour margin
would change no day, since those early runs would still arrive by 05:00 UTC. The six-hour run is labelled, never
chooses anything, and bounds what the assumption is worth.

## 4. The models, fixed before any v2 run

### 4.1 Production: the spline model ("the GAM"), exactly as measured in the spike

- **Temperature basis:** scikit-learn's `SplineTransformer(n_knots=10, degree=3, knots="quantile",
  extrapolation="linear", include_bias=False)` on the NBM temperature, fitted on each fit's own usable training rows
  (each validation fold, each frozen model), never on a test row: 11 basis columns. The fit and the freeze each check
  that the stored knots are the quantile rule applied to exactly those rows, recomputed on its own, and refuse
  otherwise (section 12).
- **Columns:** v1's 72 hour × day-type cells; the 11 basis columns multiplied by each of the 24 local-hour
  indicators, so each hour of the day has its own temperature curve (264 columns); demand 24 h and 168 h before
  (338 columns in all).
- **Fit:** `StandardScaler()` on every column, then `Ridge(alpha=1.0)` with scikit-learn 1.9.1's other defaults, an
  intercept included; the target is demand in MW. scikit-learn becomes a direct dependency, pinned to the version the
  lock file already resolves.
- **Usable rows:** v1's rule. A row with a missing target or input is dropped from training and not predicted;
  nothing is imputed.
- **Named for what it is:** an additive model of the GAM family fitted by penalized least squares. Its smoothness is
  not chosen by cross-validation; the knots follow each fit's training data by a fixed rule and every other setting
  is fixed here. Past the coldest and warmest training temperatures each curve continues as a straight line.

### 4.2 Comparison models (they never recommend and never set a threshold)

- **v1 as shipped:** v1's least squares on yesterday's temperature, unchanged, run in v2's harness.
- **v1's form on the forecast:** the same least squares on the NBM temperature (degree-hours from the forecast). v2
  against it isolates the family; it against v1 as shipped isolates the input.

### 4.3 Bounds and sensitivities (validation only, labelled, never choose)

- The observed-weather bounds: the GAM and v1's form on the target hour's ECCC observation.
- The GAM on the forecast with the six-hour availability margin (section 3.5).
- The seasonal baseline (the same hour one week earlier) and v1's six-station sensitivity, unchanged.

### 4.4 Frozen models

- A frozen model is a JSON file of numbers, no pickle: for the GAM, its family, input, the 10 base knots, the
  spline's degree, extrapolation and bias setting, the column names, the scaler's means and scales, the ridge
  coefficients and intercept; for least squares, its columns and coefficients; for both, the interval half-width, the
  training end and the calibration folds. Its SHA-256 covers everything prediction depends on, the three spline
  settings included; a file that no longer matches its hash is refused, and so is a GAM whose spline settings are not
  the ruled ones (degree 3, linear extrapolation, no bias column). Loading rebuilds the spline basis from the stored
  knots and settings, and validation and the frozen model predict through the same function.
- One frozen run per replayed year holds the production model, registered with the alias `replay-<year>`, and the two
  comparison models as hashed files. The replay checks all three hashes before and after; it fails if any changed.
- **Echo premise.** Rule 2's echo exclusion (section 9.2) assumes both lag coefficients are positive. The freeze
  refuses a production model whose lag coefficients, in MW per MW, are not both positive.

### 4.5 What the curves mean

The lags enter linearly and the temperature through one curve per hour, so each curve is a partial effect: what the
forecast temperature adds at that hour *given* demand one day and one week earlier. The report draws, for the model
frozen for 2024, each hour's curve relative to its value at 18 °C over the training range (the extrapolated part
marked), beside the same curves from a GAM fitted without the lags, for interpretation only, as v1 showed its
coefficients beside a fit without the lags. It prints the two lag coefficients. No standard error is given (the
errors are autocorrelated and the fit is penalized). These curves replace v1's coefficient table for v2.

### 4.6 Accepted risks

- **Extrapolation.** Below the training range the curves are straight lines. The cold-tail criterion of section 8 and
  the report's count of forecast hours outside each fold's training range measure what that costs.
- **Level drift.** As in v1: the lags carry part of it, the bias rule watches it, no trend term.
- **One point for a province.** One forecast point stands for the province's weather, as one station did in v1.

## 5. Gaps: nothing is imputed or substituted

- **Training rows** (folds and frozen fits): dropped when the target or any input of that model is missing.
- **Validation:** each model predicts the hours its inputs allow, and its own figures are on those hours. Every
  scored comparison between models is on one **common set of hours**: those with a target and a forecast from every
  model compared. The hours each model lacks are listed by reason (no run fed the day, a missing lag, an unusable
  target). The five busiest hours of a year are chosen over every hour with a target, not over the common set, and
  show a missing forecast with its reason (section 8.3).
- **A day no run fed:** its 24 hours have no forecast temperature. The production model and v1's form on the
  forecast do not predict them; v1 as shipped does, and the common set leaves them out.
- **In the replay:** at an origin with no compliant covering run in view, the production model issues nothing for D,
  each hour records why, and the day fails its data-quality verdict (`forecast_missing`, section 9.3), so rule 1 fires
  for its week and the week's performance is not judged. No other model's forecast and no other temperature stands in
  for it: that would be another model, and it is not the one frozen.
- **Inside a day:** a run covers the whole day or is not used, so a day never holds part of a forecast.
- **Calibration and thresholds** read the production model's own scored hours; weeks with a failed day are set aside
  by the same predicate in validation and in the replay, as in v1.

## 6. Validation on 2019–2023

### 6.1 Same folds, same rules

Monthly folds from January 2020 to December 2023, each refitted on the hours before it minus 7 days, every fit and
score seen as the replay would see it. Every model of section 4 is fitted in every fold. Metrics, groupings, the
week-block bootstrap (1,000 resamples, seed 0), the "A beats B only with an interval that excludes zero" rule and the
two test cases (the 2020 shift, the February 2023 peak) are v1's, applied to the production model, with v1's column
beside it. The thresholds for the 2024 replay come from the 2021–2023 weeks; those for the 2023 rehearsal from
2021–2022.

### 6.2 Added

- The comparisons of section 8 on 2020–2023, labelled **in sample for the choice of the family and the input**.
- The forecast hours outside each fold's training temperature range, with their errors.
- The six-hour availability sensitivity.
- `cold_c`: the 5th percentile of the NBM forecast temperatures of every 2019–2023 design hour that has one (inputs
  only, never the outcome), logged by the validation run; section 8 uses it.

### 6.3 The two v1 items fixed inside the v2 freeze

- **The eve of the replayed year.** In v1 a design day at the end of the data is scored at the end of the data, which
  is the 2024 freeze's fit clock; so the 2024 half-width calibrated on 2023-12-31, while the 2023 freeze, whose data
  run past its fit clock, left 2022-12-31 out. In v2 a half-width calibrates only on days that are scored, as the
  replay scores them, one hour after their last hour, at or before the fit clock; the end of the data never counts as
  a day's scoring for calibration. Both freezes leave their eve out. Each design day's scoring time is unchanged, so
  design rows still equal the replay's path, the last day included.
- **The temperature bounds.** The replay blanks ECCC temperatures outside the bounds; design code read them unfiltered.
  On data that passes the data-quality gates no value lies outside the bounds, so no v1 number changes; the two paths
  become one filter, used for the station and for the forecast.

## 7. Intervals: v1's conformal layer, recalibrated

Unchanged method: nominal 90 %; the relative score |actual − forecast| / forecast; each month calibrated on the
out-of-sample scores of the previous 12 folds already scored at its fit clock (with the eve fix); the frozen
half-width the same rule applied to one more month; the absolute and per-season forms as sensitivities; coverage
measured out of sample overall, by season, hour group and forecast-load decile; the shape of split conformal without
its finite-sample guarantee. Each model's intervals come from its own scores; only the production model's feed the
monitor.

## 8. What counts as v2 being better than v1: the decision rule

### 8.1 Contrasts, on the common set of hours

Each is the paired difference in hourly absolute percentage error, with a 95 % week-block bootstrap interval (1,000
resamples, seed 0), on the hours where the target and all three forecasts exist:

- **shipped:** v2 minus v1 as shipped. The whole change.
- **family:** v2 minus v1's form on the forecast. The same input, another family.
- **input:** v1's form on the forecast minus v1 as shipped. Reported, never judged.

On one set of hours the three are additive: shipped = family + input.

### 8.2 The cold tail

The cold hours are the common-set hours whose NBM forecast temperature lies below `cold_c` (section 6.2), chosen on the
input known at issue, never on the outcome. The shipped and family contrasts are computed again on them. With fewer
than 120 cold hours, or cold hours in fewer than 4 Monday-to-Sunday weeks, the cold tail is **not testable** on that
year, never passed: its interval resamples weeks, so cold hours from one or two weeks would give an interval of one
weather episode, near zero width.

### 8.3 States and verdicts

- A contrast is **better** when its interval's upper bound is below zero, **worse** when its lower bound is above
  zero, **not separated** otherwise. The bounds are judged as printed, to two decimals of a percentage point, so no
  printed number contradicts its state.
- Each of the shipped and family comparisons has one verdict:
  - **better**: the contrast is better and its cold-tail contrast is not worse;
  - **better on average, the cold tail not testable**: the contrast is better and the cold tail is not testable;
  - **not shown better**: the contrast is not separated, or it is better but its cold-tail contrast is worse;
  - **worse**: the contrast is worse.
- "v2 is better than v1" is said only from the **shipped** verdict; "the GAM earns its place over v1's form on the
  same forecast" only from the **family** verdict. Neither borrows the other's words.
- Reported beside them and never judged: the five busiest hours of the year (by actual demand, among every hour with
  a usable target, whatever forecasts exist; one event), each model's signed error, or why it has no forecast for the
  hour; the production model's interval coverage with its bootstrap interval, and whether that interval holds 90 %.
  The common set of hours restricts the paired contrasts only.
- One function computes the contrasts, states and verdicts, in a rule file, so the rule is frozen with the rest.

### 8.4 Where it decides

| Year | Status | What the verdicts may support |
|---|---|---|
| 2020–2023 | In sample for the choice | Nothing: shown, labelled "in sample for the choice of the family and the input" |
| 2023 rehearsal | Design year | Nothing: shown, labelled |
| 2024 | Seen by the developers | Nothing positive. A verdict of not shown better or worse is reported as a finding against v2: a pass on a seen year is weak evidence, a failure is not. Whatever it shows, v2 goes to 2025 unchanged (section 10.6) |
| 2025 | Held out | The claims of section 8.3, from the test of record, and only when 2025 is clean (section 11.3.5) |

### 8.5 The model card's status, and what v2 does if it is not better

- The status is computed from the runs, never typed, and each wording names what it is about: hourly error against
  the comparison models, nothing more.
  - *Candidate* (chosen on 2020–2023, shown on 2024 seen by the developers, held-out test pending) until a 2025 replay
    starts.
  - When 2025 is clean (section 11.3.5), from the test of record's verdicts, never a later rerun's: *lower hourly
    error than v1 as shipped and than v1's form on the same forecast, on held-out data (2025)* when both are better;
    *lower hourly error than v1 as shipped on held-out data (2025); the GAM not shown better than v1's form on the
    same forecast* when only the shipped verdict is; *not shown to have lower hourly error than v1 as shipped on
    held-out data (2025)* otherwise. "Better on average, the cold tail not testable" counts as better, and the status
    sentence says so. The input contrast, never judged, is in none of them.
  - When the owner's ruling made the test of record post hoc (section 11.3.5): *2025 replayed under the owner's
    ruling that it is post hoc: not a held-out test*. While the test of record has not finished (section 11.3.5 says
    which start it is, and when one that stopped gives way to the next): *the 2025 test of record did not finish: no
    claim on held-out data*. Neither supports any claim of section 8.3.
  - Interval coverage and the five busiest hours are reported beside the status and are part of none.
- **If a clean 2025 does not confirm v2** (the owner's ruling, 2026-10-16), its status being *not shown to have lower
  hourly error* or the one where only the shipped verdict is better, from the test of record and never from a later
  rerun: the status changes as above; the report's « En bref » and the story open with it, and the README, which quotes
  no result, points to it; every claim it bears on is withdrawn; and the report writes a recommendation to the owner in
  the monitor's form, with its evidence: if the shipped verdict is not better, *return to v1 as shipped or rule another
  model*; if only the family verdict is not better, *keep the forecast input and review the family*. The render refuses
  a report that lacks any of these (section 12).
- No revert is pre-committed. Nothing switches on its own: the monitor never acts, and the model in use afterwards is
  the owner's ruling.

## 9. Monitoring: what carries over, what is re-derived

### 9.1 Carried over unchanged

The weekly statistics, the per-season quantiles (MAPE above its 95th percentile; bias outside its 2.5th–97.5th;
coverage below its 5th), the 120 scored hours a week and 18 a day, the shared predicate of judged weeks, the quiet
partial weeks, the ordered decision table (rules 1 to 4), the trimming of two days, the echo exclusion, the three-week
bias arm, a failed or short week breaking a streak, the Evidently check, the never-acts check (extended to the
comparison models), the detection-power, false-alarm, level-shift and echo-cost measures with their grids, and the
2023 rehearsal of the freeze.

### 9.2 The lag argument, checked again for the GAM

- The bounds on what a bad day can do rest on the demand-history inputs. In v2 they are still only demand 24 h and
  168 h before the target hour, entered linearly; the other inputs are the calendar and the weather forecast, which
  reads no demand. So one bad day of demand spoils at most itself, the next day and the same day a week later, never
  more than two days of a Monday-to-Sunday week, and v1's arguments for trimming two days and for the three-week arm
  hold as written.
- A bad week's echo has the opposite sign only when both lag coefficients are positive; the freeze refuses a
  production model where they are not (section 4.4).
- A bad forecast run spoils at most two consecutive days. No lag of the forecast enters, but under the fallback of
  section 3.2 one run can feed the first day whose origin follows its availability and, when every fresher run misses a
  point of the next day, that next day too. It cannot reach a third: covering a third day takes a point at least 75
  hours after the run time (a run available by the first day's origin, 05:00 UTC, has a run time of 03:00 UTC that day
  at the latest, and the third day ends at 06:00 UTC three days later), while the archive's runs reach at most 72 hours
  (71 from 2021; 2019–2024, reported each year by the data-quality run: a property of the archive, not a rule). Two
  consecutive days put two spoiled days into one Monday-to-Sunday week, or one into each of two adjacent weeks, so v1's
  argument for trimming two days and for the three-week arm holds as written. Each replay measures it: one feeding run
  whose own points are shown to cover both days, corrupted by an offset that keeps every point inside the bounds, is
  made to feed two consecutive days, inside one week and across a week boundary (a season with no such run for a
  placement records it as not constructible); the measure reports each affected week's trimmed statistics against the
  mean of its untouched days (the argument says they never pass it, and any that does is reported as a finding against
  it), the weeks whose decision changes and whether rule 4 fired, which is its job. It is labelled and never chooses.
  v1's yesterday's temperature spoiled the next day too.
- The 72 hours are measured on 2019–2024 only. If the data-quality run finds, in any year it reads (2025 above all,
  read from the run the 2025 test of record read: section 13), a run whose points reach 75 hours or more after its run
  time, the report states it as a finding against the two-day argument, and that finding is a trigger for an entry of
  section 18: a measure of a run feeding three days, designed then and ruled by the owner. None is built now: no run
  of the archive has reached 75 hours.

### 9.3 Re-derived because the model and its input changed

- Every threshold and alarm level, by the unchanged rules, from the production model's validation weeks; the
  half-width.
- **Temperature drift reads the model's own input:** the issued NBM forecast of each week against the issued forecasts
  of the same calendar weeks (± 2) of earlier years, rebuilt from the runs in view. v1 watched the station observation.
  So a change in NOAA's model shows here. The forecast's weekly error against the ECCC observation is attached to the
  replay's record as evidence, without a threshold.
- Forecast drift reads the GAM's forecasts, against validation's out-of-sample production forecasts of earlier years.
- The day-level data-quality check keeps every v1 criterion, the station's included (the station feeds v1's model
  and the observed bound, and a failing source is still a source to investigate), and adds one: **`forecast_missing`**,
  no compliant covering run in view at the day's origin. A point outside the bounds is no criterion of its own: such a
  run does not cover (section 3.2), and the data-quality gate refuses any such point in the window.

## 10. The v2 freeze, the tags and the records

### 10.1 A new freeze, not a departure

v2 changes the model, the input and the dependencies, so it is a second freeze, as the owner ruled on 2026-10-16. The
`rules-frozen` tag stays on v1's freeze commit for good, and v1's departures section stays as it is, closed by one
sentence. A new annotated tag, `rules-frozen-v2`, freezes v2's rule files before v2's 2024 replay, and that replay
refuses to run without it. The ruling amends two records of `AGENTS.md`: the departure rule, which now applies to
each freeze in its own section, and the ledger ruling, which now takes v2's 2024 and 2025 entries (section 10.5).

### 10.2 Rule files, dependencies and the gate

- The rule files keep v1's definition: every file under `src/valiwatt` and `config` except those that only render,
  scan or dispatch; a file added later counts too. The rules hash keeps v1's definition, so v1's recorded hashes stay
  comparable.
- The v2 freeze gate also refuses when `pyproject.toml` or `uv.lock` differ from the tag's, byte for byte. What v1
  kept by hand becomes a gate.
- The freeze gate runs inside the replay function for every replay that reads past the design window (2024 and 2025),
  as in v1. For 2025 the whole gate of section 11.3.4 runs once, in the command, before the run opens, and the replay
  function then reads 2025 only for the started run of section 11.3.5.

### 10.3 Each check scoped to its freeze

- Every replay run records its freeze tag; a stored replay without one is v1's (`rules-frozen`). The moved-tag check
  is scoped by freeze, not by year: a replay is compared with every earlier replay under the same tag, of every year
  that freeze replays (2024 for v1; 2024 and 2025 for v2), in the store and in the ledger. So a 2025 replay run on its
  own still sees a tag moved since v2's 2024 replay. The ledger also refuses a v2 entry that is not a departure and
  whose freeze commit is not that of the first v2 2024 entry.
- v2's departures go in their own `AGENTS.md` section, "Departures from the v2 frozen rules", which names none yet.
  Each entry there is either a departure (a rule-file or dependency change after the tag, which moves it) or the
  owner's ruling on a refused 2025 start (section 11.3.5). The report reads the section of its replay's freeze.
- The history test checks each freeze on its own range: v1 from `rules-frozen` to `v1`, with v1's checks unchanged;
  v2, once its tag exists, by a check of its own, commit by commit, so a change since reverted is seen. The tag's
  successive commits are read from its pins (section 10.4). Every commit from the first of them to the head that
  touches a rule file or a dependency must be named by the departure entry of the move it belongs to (a dependency
  change is a departure like a rule change), unless it follows the tag's current commit, which fails; each departure
  entry names the commit the tag moved from and the one it moved to; a named commit that is neither such a change nor
  one of those freeze commits fails. A tag named like a freeze that the configuration does not list is refused.

### 10.4 Tags

| Tag | On | Why |
|---|---|---|
| `rules-frozen` | v1's freeze commit (unchanged) | v1's freeze |
| `v1` | the commit of v1's last render | v1 as published; the end of v1's checked range |
| `plan-v2` | the commit that adds this page | the pre-registration |
| `v2-first-run` | the commit from which v2 first runs on real data | the start of the range in which every rule change is disclosed |
| `rules-frozen-v2` | v2's freeze commit, before any v2 replay of 2024 | v2's freeze |

Each is annotated and pinned to its commit by a test in the commit after it is made, so none can move silently; when
`rules-frozen-v2` is moved (a departure), its new pin is committed at once, a commit that
touches only that test, and the suite is run again, so the tree is clean before any replay under the moved tag. Each
is an ancestor of the next. Every v2 command that computes on real data (data quality, validation, freeze, replay;
fetching only downloads) refuses unless `v2-first-run` exists and is an ancestor of the head, so that tag truly marks
the first real-data run.

### 10.5 The replay ledger

- One file, `docs/replay-ledger.json`, append-only, written as in v1. v1's entries are never touched: a test checks
  that the `rules-frozen` entries equal those of the file at tag `v1`, and they keep their fourteen fields and their
  year, 2024.
- v2's entries, of 2024 and 2025 replays, hold v1's fields plus five, in a fixed shape: the evaluation label, the
  production model's hash, the two verdicts of section 8, one hash of every artifact the replay was judged by (the
  production and comparison models, the thresholds, `cold_c` and the forecast-drift reference), and the standing.
  The label is `seen_by_developers` for every 2024 entry; a 2025 entry is `held_out` only when it is the test of
  record (section 11.3.5) and its standing is clean, `post_hoc` when it is the test of record and its standing is
  post hoc, and `seen_by_developers` otherwise. The replay sets the label when its run opens; the ledger checks every
  v2 entry's label against the starts before it and the ruling and standing its gate applied, and refuses any other.
  So the public, machine-readable record never holds a verdict on data the developers had seen without "seen by the
  developers", nor a post-hoc year without "post hoc", and no entry needs a label added later.
- The standing (section 11.3.5) is empty for 2024; for 2025, clean or post hoc, as the gate logged it at the start.
  On the test of record it agrees with the label, and the ledger refuses an entry where they differ, so the ledger
  alone never shows a post-hoc year as a clean test. The stamp, `docs/report/stamp.json`, also carries the test of
  record's standing once a 2025 replay has started, finished or not.
- A stored run that differs from its ledger entry in any field of its version's shape, v1's fourteen or v2's
  nineteen, is refused, as in v1: the ledger is append-only.
- A v2 replay opens its tracked run before it reads any input, so a v2 entry's start time is when the replay began
  (v1's runs opened after computing, and their entries keep that meaning). The test of record for 2025 is defined in
  section 11.3.5.

### 10.6 Before the tag

- Before `v2-first-run`, the code meets real data only to reproduce the scratch spike from its own cached tables: the
  forecast input on 2019–2023 (plus the 24 forecast temperatures of 2023-12-31, a weather input only) and the spike's
  GAM predictions. That computes no error, interval or threshold the spike had not computed.
- From `v2-first-run` on, validation, the freezes and the 2023 rehearsal run on real data to test the code, as in v1.
- Between `v2-first-run` and `rules-frozen-v2`, while no v2 model has scored any hour of 2024, no setting of the
  model, the forecast input or the decision rule changes except to fix a defect. The monitoring rules may be tuned on
  the 2023 rehearsal, as v1's were. Every rule-file or dependency change in that range is listed in section 18 with
  the run that prompted it; a test checks the list against the history.
- **After `rules-frozen-v2`, no 2024 result changes the model, the forecast input or the decision rule:** v2 goes to
  2025 as frozen, whatever 2024 shows. Any change to a rule file or the dependencies after the tag is recorded in v2's
  section of `AGENTS.md`, and what it costs 2025 is stated once, in section 11.3.5: the 2025 gate refuses until the
  owner rules, clean or post hoc.
- A review of the rule-file diff since `v1` by another AI model, in a fresh context, precedes the tag (not
  independent validation in SR 11-7's sense).

## 11. The replays

### 11.1 The 2023 rehearsal

As in v1: frozen at the end of 2022 with the embargo, 2023 replayed hour by hour with thresholds from 2021–2022,
compared with the monthly refits. It is where v2's whole chain first runs on real data. It also checks that the
forecast temperatures it issued equal validation's for every hour of 2023, and that each day of 2023 was fed by the
same run, or left unfed for the same reason, and refuses to log otherwise: design and replay must read the same
input.

### 11.2 2024, seen by the developers

- Run once v2 is frozen: the clock, the feeds and the gates of v1, plus the forecast feed, its runs stamped at their
  availability. The production model's monitor decides; the comparison models are scored afterwards on the inputs
  recorded at each issue, and issue nothing. Nothing it shows changes v2 before 2025 (section 10.6).
- Every v2 number on 2024 carries the label "seen by the developers", in every table, sentence and caption, the model
  card and the story.
- v1's 2024 replays are shown beside it from their ledger entries, recorded and not re-run. v1's model in v2's harness
  is reconciled with them: its 2024 MAPE against that of v1's last ledger entry, and its coverage, which the eve fix
  moves; the report states both differences.

### 11.3 2025, held out

1. **No Hydro-Québec record of 2025 reaches the machine before v2 is frozen.** The portal itself bounds every request
   for the Hydro-Québec export: the request asks only for the records labelled at or before the end of the year it
   fetches (the export API's filter on the record's date: 2025-01-01 05:00 UTC for 2024), and its row count is checked
   against the portal's count of the records under the same filter. A response for 2024 therefore cannot hold a record
   of 2025, and one that did would be refused, nothing written. The one request that reaches past 2024 is `valiwatt
   fetch --through 2025` (bounded at 2026-01-01 05:00 UTC), which refuses unless the rule files and the dependencies
   equal `rules-frozen-v2` and the tree is clean. A plain fetch never asks the portal again for a file it has
   verified. With `--through 2025`, `--force` is refused for ECCC and NBM: it would download again the 2019–2024
   weather files v2's first 2024 replay read (the Hydro-Québec vintage goes to a file of its own, item 2). Once
   Hydro-Québec has published 2025, a fresh clone fetches the bounded 2019–2024 export: a later vintage than the one
   v2's committed runs read, so its numbers may differ from the committed ones, and the reports print each export's
   retrieval and modification dates. Its data-quality run ends with 2024, so it runs and renders the 2024 chain, and
   shows the replays its store lacks, 2025's among them, from the ledger (section 13); v2's report as published at
   its tag stays the reference.
2. **Only a whole year.** The 2025 fetch refuses an export that does not reach 2026-01-01 05:00 UTC. It writes each
   source's new files beside the old ones. The data-quality run lists every 2019–2024 demand record that differs from
   the earlier export (added, removed or changed), compared record by record as each export publishes them, before any
   repair, as a file of its run. That list is a data-quality finding: the report carries it (section 13), and nothing
   decides on it.
3. **The artifacts frozen for 2024 judge 2025, exactly.** The 2025 replay reads the runs v2's first 2024 replay read,
   by the run IDs that replay recorded, never the latest ones: its frozen run (the production model and the two
   comparison models frozen for 2024, fitted on 2019–2023, with their half-widths) and its validation run (the 2024
   thresholds from the 2021–2023 weeks, `cold_c` and the forecast-drift reference of the design years). They are
   checked against that replay's ledger entry, committed and append-only: the production model's hash against its
   model hash, and the hashes of all of them together against its one hash of them; the run names which one differs.
   The temperature-drift reference is not among them: it is an input, rebuilt by the frozen rule from the issued
   forecasts in view at the replay's start (section 9.3), 2024's included, as for every replayed year. Only the
   forecast-drift reference is frozen and hashed. Nothing is refitted and nothing is validated or frozen again for
   2025: v2 is frozen once, on 2019–2023, so a revised 2019–2023 record never changes the model 2025 is judged by. A
   refit on 2019–2024 would be another freeze, not this test. The raw records the replay reads are the 2025
   vintage's, checked by the data-quality run of item 4.
4. **By its own command, from a clean tree, behind one gate with two outcomes.** The 2025 replay is never part of
   `valiwatt all`, which is the 2024 chain and ends after the 2024 replay, the breaks and the report; it runs only by
   `valiwatt replay --year 2025`. Its gate runs inside the replay command before the tracked run opens, holds every
   check made before a 2025 start (a check kept by hand is not a gate) and reads no demand record. It **starts clean**
   only when the code proves every condition below; otherwise it **refuses**, naming the condition that failed and its
   evidence (the commits and the files they touched, the hashes that differ, the run or file that disagrees), and
   nothing starts:
   - (a) *no code change since the freeze:* no commit after the freeze commit of v2's earliest 2024 start, finished
     or not, up to the commit the start runs, touches a rule file or a dependency, read commit by commit, so a change
     since reverted counts, and so does a fix made after a 2024 start that stopped; a merge commit, a shallow clone,
     or a commit the start runs that does not descend from that freeze commit, refuses;
   - (b) *the frozen artifacts:* those of item 3, their hashes equal to those of v2's first 2024 ledger entry (v2's
     first 2024 replay is the earliest that finished; the walk of (a) starts at its earliest 2024 start);
   - (c) *a clean start:* the rule files and the dependencies equal `rules-frozen-v2`, a moved tag is recorded (section
     10.3), the tree holds no uncommitted change, the committed ledger holds v2's first 2024 entry and the run store
     its run, no other 2025 replay is running or being started (one exclusive lock on the run store is held from the
     gate to the opening of the start's run, so two callers at once cannot both start), and every 2025 diagnostic run
     names a finished, complete 2025 replay of its own commits (item 5);
   - (d) *the data:* the latest data-quality run reaches 2026-01-01 05:00 UTC, passed its gates and ran under the
     current rules;
   - (e) *the whole chain, checked before the run opens:* every run and file the replay will read is found and
     checked from its records, without reading a demand record (the runs of item 3, the comparison models, the
     reference, read as its day and forecast columns only, and the raw files, whose hashes must equal those the
     data-quality run checked); the replay then reads exactly those runs and files;
   - (f) *no unfinished test of record:* if a 2025 replay has started, one has finished (item 5).
   Only the owner's ruling lifts a refusal (item 5). The command's one flag, `--departure`, says only what it says for
   2024, that the tag moved and the move is recorded (condition c); no flag lifts a refusal. The 2024 replay never
   reads a record of 2025. And until a 2025 replay has started, `valiwatt report` refuses to render any data-quality
   run that reaches 2025, the latest or one named by its ID, and refuses while the latest one does: nothing of the
   2025 vintage is rendered before the first start.
5. **The test of record, the owner's ruling, and when 2025 is clean.** This is the one statement in this page of the
   owner's rulings of 2026-10-16 on 2025, given before any 2025 data exist (his third, fourth and fifth cards; the
   third and fourth replace the conditions of his first two; `AGENTS.md` records all five).
   - *2025 is clean* when its test of record started clean (item 4). No code change keeps 2025 clean on its own: a
     change needed only to read Hydro-Québec's 2025 publication refuses the start like any other.
   - *Every start leaves a trace.* Once its gate has passed, and before it reads any input, every 2025 replay opens its
     tracked run, marked as started, with its freeze commit, its code commit and hash, its evaluation label and the
     standing its gate found, and logs the gate's record; the run then records each step it reaches, and the class of
     any error that stops it. It logs no metric or result file until every check that can stop it has passed, so a
     start a check stops holds no 2025 result, and the refusal of the next start says whether the stopped start's run
     holds any. The replay function reads 2025 only for a 2025 run of this code that the run store holds as started.
     A gate's refusal reads nothing and is no start.
   - *The test of record* is the earliest 2025 replay started under `rules-frozen-v2`, unless it stops before it
     finishes. A start that stops is listed first, with where it stopped (the last step its run recorded, and the
     class of the error, if one stopped it), and every later 2025 start refuses (condition f) until the owner's dated
     ruling naming the stopped start: *clean* makes the next start the test of record, labelled held out; *post hoc*
     lets the next start run as the test of record, labelled post hoc wherever it appears, its ledger label and its
     standing included (section 10.5). If that start stops too, the same holds for it. A crash spends nothing by
     mistake. Until the test of record finishes there is no 2025 claim: the status says so and the report opens with
     that fact. Once it has finished, its ledger entry is the only one labelled held out, or post hoc when its
     standing is; section 8 decides it, and the status, the withdrawn claims and the recommendation of section 8.5
     come from it alone. Every later finished 2025 replay is a rerun, labelled seen by the developers and shown
     beside the test of record with its differences; it changes no status and no claim.
   - *The owner's ruling* lifts a refusal of condition (a) or (f), and nothing else: no ruling supplies a changed
     artifact, a clean start, passing data or a runnable chain. It is his dated entry in v2's departures section of
     `AGENTS.md`, naming the commit it applies to, its outcome, clean or post hoc, and, on condition (f), the stopped
     start by its start time; it is written once he has seen the evidence (below). The gate reads it. An entry applies
     to the start when no commit after the one it names touches a rule file or a dependency (it rules on the code the
     start runs), the commit it names descends from the code of every earlier 2025 start (it lifts the next start
     only), and, on condition (f), the start it names is the latest 2025 start. An entry cannot name the commit that
     holds it, so it names the one before. The last entry that applies gives the start's standing: clean, or post hoc,
     labelled so wherever it appears and supporting no claim of section 8.3.
   - *The evidence.* One command, `valiwatt evidence`, run from a clean tree, produces what the owner rules on, and
     decides nothing: v2's 2024 replay run again on the newest export, bounded to 2024 (its records, cut at load, and
     its clock end with 2024), with exactly the artifacts of item 3 and the code the start would run; compared hour by
     hour with v2's first 2024 replay on every input and every output, each hour that differs listed; and the 2019–2024
     records the newest export revised (item 2). It is a tracked run of its own kind: not a replay, never in the ledger.
     No gate reads what it holds, but the gate applies a ruling only when a finished evidence run, made from a clean
     tree, exists for the commit the ruling names, or a descendant of it up to the commit the start runs, on the gate's
     data-quality run; otherwise the start stays refused, naming `valiwatt evidence`.
   - *Where the original lives.* v2's first 2024 replay's run, with its hourly forecasts and ranges, is in the run
     store, which is never committed (Hydro-Québec's licence keeps its demand values out of the repository); its ledger
     entry names it. Without it (a fresh clone, a store set aside), the gate refuses (condition c) and the evidence
     cannot be produced; the report then shows that replay and the 2025 entries from the ledger (section 13).
   - *The diagnostics come after.* As for every v2 replay, the sensitivity measures of section 9 run once the replay
     has finished and been logged, as a diagnostic run of their own, so a diagnostic that fails cannot leave the start
     unfinished. Each of their replays of 2025 reads 2025 only for that diagnostic run, which the run store holds as
     started and which each worker process looks up for itself, and only when the replay it names finished, complete,
     on the same code and freeze commits; a 2025 diagnostic run without such a replay refuses every 2025 start
     (condition c).
   - The report prints the standing the gate logged at the start, and the ruling entry it read; it never computes the
     standing again. The ledger's departure flag says only that the tag moved.
6. **Not enforceable, stated:** nobody looks at 2025 demand on the portal before that replay, nor in diagnosing a
   refused 2025 fetch, a failed data-quality run or a stopped start, which are diagnosed on the bounded 2019–2024
   export, the gate's counts and the step a run reached, never on 2025 values; nobody computes on 2025 records outside
   the replay command (in an interpreter, say), which leaves no started run; a ruling entry is committed only from the
   owner's card, since the gate cannot tell who wrote it; and the unfinished starts are listed from the run store,
   which is not committed, so a store discarded before the render would hide them (the 2025 gate refuses without the
   store, by condition c).

### 11.4 Data windows

The design window stays 2019–2023 for everything that fits or calibrates. The data window ends with the last
complete year fetched: 2024 by default, 2025 only under section 11.3. The 2024 replay reads records up to the end of
2024 whatever the window.

## 12. Breaks: every new gate is seen to fire

Each lands with a break that must make it fire and, where it can be quiet, a clean case that must stay quiet:

- *Forecast feed:* a run available one hour after the origin is never used, even with absurd values, and the
  next-freshest is; a run lacking one bracket point, or holding a blank point or one outside the bounds, gives way to
  the next; no covering run among twelve leaves the day without a forecast and says why; two runs are never
  interpolated together; values are linear in time inside one run and in Celsius; 2023-12-31 is forecast from its own
  day's run (control); on an archive with a gap of days, the replay feeds each day the same run, temperatures and
  reason as validation (an age window put back must fail it).
- *Forecast fetch and data quality:* a response for another station or model, an impossible temperature, an empty
  response, and a file that no longer matches its manifest are refused; two different values for one point refuse the
  load; identical duplicates pass and are counted (control); a point outside the bounds and a coverage below 98 % each
  fail their gate; a primary station other than 7025251 refuses the pairing; every Hydro-Québec request, through
  2024 or 2025, carries the portal's filter at the end of its year and checks its count under that filter (no file on
  disk, `--force`, any path that fetches again), and a response holding a record past that end is refused and writes
  nothing; with `--through 2025`, `--force` on ECCC or NBM is refused before any request.
- *Lookahead:* a row whose forecast became available after its origin is refused; a design feed holding a run
  available after the design window is refused; a fit whose spline knots are not the quantile rule on its own usable
  training rows (knots that saw a test row) is refused by the fit, and a frozen model's by the freeze; the eve of the
  replayed year never calibrates a half-width, in either freeze (the v1 clamp put back must fail it); a design
  temperature outside the bounds is blank as in the replay.
- *Frozen models:* an altered knot, scale or coefficient fails the hash; a frozen GAM with another degree,
  extrapolation or bias setting than the ruled ones is refused, and changing one changes the hash; a production model
  with a lag coefficient that is not positive is refused; a comparison model changed during the replay fails it; the
  frozen GAM predicts what the ruled scikit-learn pipeline predicts.
- *Monitors:* a day no run fed fails `forecast_missing` and yields *investigate the data*; drift of the forecast input
  with performance inside the bands yields *investigate the data*; the comparison models crossing their thresholds
  yield nothing; a rehearsal whose issued forecast temperatures differ from validation's by one value, or whose
  feeding run or reason differs on one day, is refused.
- *Decision rule:* each state and each verdict, both sides of the printed-bound rule, the cold tail not testable below
  120 hours or below 4 weeks, a contrast scored on hours another model lacks refused, the additivity of the three
  contrasts; the five busiest hours taken over the year by target, a missing forecast kept with its reason; each
  status, the held-out ones only on a clean 2025.
- *The 2025 gate (sections 11.3.4 and 11.3.5):* with no commit since the freeze commit of v2's earliest 2024 start and
  every condition met, the start is clean (control), on a 2025 vintage that revises 2019–2024 records too, the revised
  records listed and the frozen artifacts unchanged (control); an empty commit in that range passes (control); a
  rule-file change, a dependency change, and a change since reverted with the tag never moved each refuse, the commits
  and files named; a first 2024 start that stopped, followed by a fix, a moved tag and a finished departure replay of
  2024, refuses the 2025 start and names the fix; a merge commit, found by its parent count, a shallow clone, and a
  start whose commit does not descend from that freeze commit each refuse; with the owner's ruling entry naming the
  commit before it, and its evidence run, the start runs clean, labelled held out, when it says clean and labelled post
  hoc when it says post hoc (controls); a ruling entry followed by a rule change, one naming a commit an earlier 2025
  start's code descends past, one whose commit cannot be resolved, and one without a finished evidence run made from a
  clean tree for its commit, or a descendant, on the gate's data-quality run each leave the start refused, the last
  naming `valiwatt evidence`; a production model, a comparison model, the thresholds, `cold_c` or the reference that
  differ from v2's first 2024 ledger entry each refuse, a store edit that changes an artifact and the hashes its run
  logged together included; v2's first 2024 run absent from the run store, as in a fresh clone, refuses; a latest
  data-quality run that does not reach 2026-01-01 05:00 UTC, failed its gates or ran under other rules refuses; a raw
  file whose hash differs from the data-quality run's refuses, and the gate reads no demand record, the reference being
  read as its day and forecast columns only (control); a dirty tree, another 2025 replay running, a second caller
  starting at the same moment (one start opens, the other is refused), a 2025 diagnostic run without a finished,
  complete 2025 replay of its commits, and no v2 2024 entry in the committed ledger each refuse; after a start that
  stopped, a later start refuses, saying whether the stopped run holds any result, until a ruling entry naming the
  stopped start applies, and then runs as the test of record with the standing the ruling gives, labelled held out when
  it is clean and post hoc when it is post hoc (controls), while a ruling naming another start leaves it refused; once a
  test of record has finished, a later start is a rerun and condition (f) stays quiet (control); a gate's refusal opens
  no run.
- *2025 runs and diagnostics:* the replay function reads 2025 only for a 2025 run of this code the store holds as
  started (no run, another run, a run of other code, a run not started, a diagnostic run whose replay did not finish,
  complete, on its commits: refused); a 2025 replay stopped midway leaves its started run with the gate's record, the
  step it reached and the class of its error, and one stopped by a check, the Evidently cross-check or the never-acts
  check, holds no 2025 metric or result file; its diagnostic run starts only after the replay has finished, and its
  replays of 2025 run from two worker processes that each look up the diagnostic run in the store (control), a run ID
  the store does not hold refusing in a worker; a diagnostic that fails leaves the 2025 replay finished; the evidence
  run compares every input and output on every hour and lists each differing hour, and no gate reads what it holds
  (control); the evidence refuses a dirty tree, and its replay receives no record of 2025.
- *Freeze and records:* a v2 replay without `rules-frozen-v2`, with a rule file or a dependency changed after it, or
  under a moved v2 tag not recorded; a 2025 replay run on its own after the tag moved since v2's 2024 replay (the check
  is scoped by freeze, not by year); v1's replays never count as a moved v2 tag (control); a replay stored without a
  freeze tag counts as v1's; an unlisted freeze tag; a tag that moved from its pin; a real-data run without
  `v2-first-run`; a rule change after `v2-first-run` not listed in section 18; an edit of this page after `plan-v2` not
  listed; a v1 ledger entry edited; a ledger entry of another year or tag; a v2 entry without its evaluation label, or
  whose label is not the one its starts give (a 2024 entry labelled held out; a 2025 entry held out after an earlier
  finished 2025 start, or after a stopped one without the owner's ruling naming it in its gate's record; a post-hoc test
  of record labelled held out); a 2024 entry with a standing, or a 2025 entry whose standing is not its run's; a stored
  run that differs from its v2 entry in any one of the five v2 fields; a v2 entry that is not a departure under another
  freeze commit than the first v2 2024 entry's; a v2 section entry that is neither a departure nor a ruling with its
  outcome; a rule or dependency commit after the v2 tag not named by the departure entry of its move, or a commit a v2
  departure entry names that is neither such a change nor a commit the tag was on; a v2 departure entry that names the
  freeze commit it moved from, and one that records a dependency change, pass (controls); a 2025 fetch before the
  freeze, from a dirty tree or of a partial year; a 2025 replay run by `valiwatt all`; a 2024 replay reading a record of
  2025.
- *Render:* a v2 number on 2024 without its label; a "better" sentence without "held out", or from a 2025 that is not
  clean; a 2025 verdict without a 2025 replay; a test of record other than section 11.3.5's, a start that stopped not
  listed first with where it stopped, or an unfinished start not listed; a stopped start followed by its ruled test of
  record renders, the stopped start first and the status from the record (control); a status, withdrawn claim or
  recommendation from a 2025 replay other than the test of record (a passing rerun after a test of record that does not
  confirm v2 leaves it not confirmed); a clean 2025 that does not confirm v2 (*not confirmed*, or the shipped verdict
  only), rendered without its recommendation, or without « En bref » and the story opening with it; an unfinished test
  of record rendered with any 2025 claim; the revised records not listed, whatever the standing; a 2025 lead of 75 hours
  or more in the data-quality run the test of record read, not stated; a standing other than the one the 2025 gate
  logged at the start, or a 2025 record without that log; a 2025 replay whose frozen artifacts' hashes differ from those
  v2's first 2024 replay recorded; a clean 2025 with nothing changed renders beside v2's first 2024 replay, the 2024
  replay of record, with a later 2024 rerun beside it (control); a fresh clone whose ledger holds a 2025 entry renders
  the records its store lacks from the ledger, labelled recorded in the ledger and not re-run here (control), and
  refuses to render one without that label; a report rendered, before any 2025 replay has started, from a data-quality
  run that reaches 2025, named by its ID or not, or while the latest one does; v1's 2024 numbers from anything but its
  ledger entries; a missing NOAA or IEM attribution line, or one that differs from the owner's approved wording.

CI runs every break on synthetic data; `valiwatt all` and `valiwatt breaks` run the same suite as a tracked `breaks`
run, and the report refuses to render if any break did not fire.

## 13. Reports

- **The validation report** (French the reference, English derived) opens with the model card's status. It shows v1
  and v2 side by side on 2020–2023 (in sample for the choice), 2024 (seen) and 2025 (pending, then held out): v1 as
  published (from its ledger), v1's form in v2's harness, v1's form on the forecast, v2; the contrasts with their
  intervals, states and labels; the verdicts. The GAM's curves replace the coefficient table for v2. The forecast input,
  its availability assumption and its error against the observation are explained in plain words. The six-hour
  sensitivity, the 2023-12-31 difference and section 0's selection are stated, with the tracked values of the measures
  section 0.2 quotes from the spike. Before any 2025 start, the report follows its latest 2024 chain, as v1's did.
  Once a 2025 replay has started, the 2024 replay of record is v2's first 2024 replay, whose artifacts judge 2025
  (section 11.3.3), and any later 2024 rerun is shown beside it as a rerun; the two records, v2's first 2024 replay and
  the 2025 test of record, are read from their own runs and state the code they ran on, while the runs the report
  computes from (the breaks, and the latest chain) share the code that renders, as in v1. Where the run store lacks
  one of those records or a rerun (a fresh clone, a store set aside), it is shown from its ledger entry, labelled
  recorded in the ledger and not re-run here, as v1's earlier replays are, and the status comes from the test of
  record's entry; what only the run store holds (the gate's record and its ruling, the revised records, 2025's
  data-quality findings, the diagnostics, the hourly figures, any start that stopped) is said to be there, not
  rendered. Then: every start that
  stopped before the test of record, first, with where it stopped and the ruling that let the next one run; the test
  of record, or the fact that it did not finish, with every other unfinished start and every rerun beside it; its
  standing as its gate logged it, and the ruling entry the gate read, if any (also written to the stamp for machine
  readers); from the data-quality run the test of record read, never from the chain's through-2024 run, the 2019–2024
  records Hydro-Québec revised, whatever the standing, and 2025's forecast findings: its coverage, the days no run
  fed, its leads and any lead of 75 hours or more (section 9.2); each diagnostic run, or the fact that it did not
  finish; and, if a clean 2025 does not confirm v2, the recommendation of section 8.5.
- **The data-quality report** gains the forecast: coverage, the feeding run's hour by year, ranks, leads (a lead of
  75 hours or more stated as a finding against section 9.2's argument), the days no run fed, its error against the
  observation. 2025's are the validation report's, from the run its test of record read.
- **The story**, read first, keeps its seven points and tells v2 from the same runs: the question; a forecast now made
  with a weather forecast and a curve per hour; the cold; how sure it is; 2024 replayed, seen; what the monitor's
  silence is worth; what changed since v1, why 2024 is not a test, and the 2025 criteria that could still prove v2
  wrong, pending until the data exist. Each point links to its section of the full report.
- **The SR 11-7 and NIST AI RMF table** gains a row for the third-party input: covered are its data-quality gates, its
  availability rule, its error against observations, its drift and the availability sensitivity; not covered are the
  soundness and change management of NOAA's model itself. Outcomes analysis names 2025 as the held-out back-test,
  pending until it runs.
- **v1's report as published** stays in the history at tag `v1`; the README points there.

## 14. Licences and attribution

- `docs/data-licences.md` gains a section, its quotations verbatim under `Source:` lines and checked live by the
  licence test:
  - NOAA's terms for NBM data disseminated through its Open Data Dissemination program,
    <https://registry.opendata.aws/noaa-nbm/>: "NOAA data disseminated through NODD are open to the public and can be
    used as desired." and "NOAA requests attribution for the use or dissemination of unaltered NOAA data. However, it
    is not permissible to state or imply endorsement by or affiliation with NOAA. If you modify NOAA data, you may not
    state or imply that it is original, unaltered NOAA data."
  - The IEM's terms, <https://mesonet.agron.iastate.edu/disclaimer.php>: "The materials found on this website are in
    the public domain and may be used freely by anyone for any lawful purpose. Attributing the Iowa Environmental
    Mesonet of Iowa State University would be appreciated." and its warranty paragraph.
  - What the project does with them: fetched by script into `data/`, never committed or redistributed; the values
    used are interpolated to hours and converted to Celsius, so they are modified NOAA data and never presented as
    original; the reports quote no forecast value beyond a few as evidence.
  - The owner's acceptance of these terms (2026-10-15) and his ruling on the attribution, whose exact wording he
    approved on 2026-10-16, beside his rulings of 2026-10-13.
- Every public surface adds the NOAA and IEM attribution line, which says the values are interpolated to hours,
  converted to Celsius, modified and not original NOAA data, and widens the no-endorsement line to NOAA and the IEM,
  adding that the project is affiliated with neither. The wording, French the reference with its English, is the one
  the owner approved verbatim on 2026-10-16; `AGENTS.md` records it, and a test holds the code's lines to that record.
- Hydro-Québec's data stays CC BY-NC 4.0, informational, never committed, never used commercially; v2 changes
  nothing in how it is used.

## 15. What v2 does not change, and why it still holds

- **The demand and station data layer** (gates, the fall-back night, the misplaced-record repair, bounds): the target
  and the observations are the same series. Only the window's end becomes a parameter.
- **`AsOf`, origins, scoring times, the 7-day embargo, the monthly folds, the design window and its guards:** the GAM
  fits in the same folds at the same decision times; the forecast input is fixed at each day's origin, so it adds no
  clock of its own.
- **The clock and its raw feeds:** reused for the forecast by stamping each run at its availability; the refusal past
  the clock is the same code.
- **Metrics, the week-block bootstrap, paired differences, load deciles on the forecast:** model-agnostic.
- **The conformal method:** applied to the new errors; only the eve fix touches it.
- **The decision table and everything it rests on:** the lag argument of section 9.2 holds for the GAM.
- **The measures of power, false alarms, level shifts and the echo exclusion's cost:** they read the replay's hourly
  frame and decisions, which keep their shape.
- **The stamp, the code and rules hashes, the run lineage, `git_dirty`, the public-safety scan:** unchanged in kind.
- **The panel sensitivity:** still v1's model on the six-station mean; the forecast exists for one station only.
- **The reports' architecture:** French reference, English derived, story read first, numbers and figures from runs,
  refusal at any broken link.

## 16. Design decisions, and the alternatives set aside

- **A second freeze, not a second v1 departure** (the owner's ruling, 2026-10-16). A departure corrects frozen rules
  beside the replay it corrects; v2 is another model on another input. Recording it as v1's second departure would
  label every v2 number post hoc against v1, leave no clean frame for 2025, and need the refusal of a replay departing
  from two freeze commits to be weakened.
- **One ledger, not one per freeze.** The ledger already names each entry's freeze tag; a second file would be a second
  home for the same record and a second hash in the stamp. v1's entries are pinned to tag `v1` instead.
- **Tag names in the configuration, not a registry file.** They are frozen with the rules, read by the gates and the
  tests; a separate registry would add a file without adding a check.
- **The rules hash keeps v1's definition.** Folding the dependencies into it would change the hash of v1's freeze
  commit and every v1 record that carries it; the freeze gate compares the dependency files byte for byte instead.
- **The eve fix in the calibration rule, not in the scoring time.** A design day's scoring time must equal the
  replay's, the last day included (a test holds design code to it); the calibration rule is where the end of the data
  stopped counting as a scoring.
- **One freeze judges 2025.** The ruling freezes v2 on 2019–2023; refitting on 2019–2024 for 2025 would be a second
  freeze of the model, fitted on a year the developers had seen through v1. The cost: the 2025 model is two years old,
  so the accepted level drift will show as bias. The paired contrasts stay fair, every model being equally old.
- **The forecast cut on availability, not on valid time.** The code's no-lookahead model is about when information
  exists; a point valid in 2024 of a run issued in 2023 is information of 2023.
- **Rule 4 on the model's input.** The drift monitor exists to watch the inputs; v2's input is the forecast. Watching
  the station instead would leave NOAA's model changes unwatched.
- **Comparison models scored after the replay on the inputs recorded at issue.** They never touch the monitor, and the
  inputs they read are those of the issue time.
- **No pre-registered switch of model** (the owner's ruling, 2026-10-16). The verdict is fixed here; the action after
  it is the owner's ruling, with a recommendation written in the monitor's form. Pre-committing a return to v1 would
  have strengthened the pre-registration at the cost of binding a future ruling now.
- **The covering rule asks for finite points inside the bounds,** a refinement beyond the owner's ruling, which asks
  only that one run's points bracket every hour. Without it a blank point would leave hours without a forecast and an
  impossible one would feed the model; the next covering run is used instead. Its bounds part repeats the
  data-quality gate on the design window, and it is expected to change no day of 2019–2023; the reproduction of
  section 17 checks that.
- **The earliest started 2025 replay is the test of record, and one that stops gives way only by the owner's ruling
  naming it** (his ruling, 2026-10-16). A replay that stopped before logging would otherwise leave no trace, and "ran
  until one finished" could not be told from "ran once"; every stopped start is listed first, with where it stopped,
  and the next starts only on his dated ruling, so a crash spends nothing by mistake and no replay takes the place of
  one that finished, a result chosen after a first look.
- **The evaluation label inside each v2 ledger entry, with the standing and the hash of the frozen artifacts.** The
  ledger is public, machine-readable and append-only with a fixed shape: a field added after the first v2 entry would
  leave entries of two shapes. With the standing there, a post-hoc year never reads as a clean test from the ledger
  alone; with the hash, the artifacts that judge 2025 are checked against the committed record, not only against the
  run store, which a single edit could change on both sides.
- **`held_out` on a clean test of record only, not on every 2025 entry.** A rerun, made after the developers saw the
  test of record, carries a verdict on data they had seen; labelling it by its year would tell a machine reader the
  opposite. A post-hoc test of record is labelled `post_hoc`, as the owner's ruling says: `held_out` beside a post-hoc
  standing would tell a reader of the label alone that the year was held out.
- **The 2025 replay outside `valiwatt all`, behind one gate.** Inside `all` it would start straight after the 2024
  replay, and checks made by hand before it would be skipped by anyone following the chain; with a 2025 window `all`
  would also render a report on the 2025 vintage's data quality before any start. One explicit command whose gate
  holds every check makes the one irreversible step a deliberate one.
- **Two outcomes for the 2025 gate, and the owner's ruling the only way past a refusal** (his ruling, 2026-10-16). A
  gate that could start a post-hoc replay on a flag would spend 2025 on a wording or a forgotten flag; a refusal
  spends nothing, and the owner decides with the evidence in front of him. No code change keeps 2025 clean on its own:
  any exception the code judges is a rule that can misjudge, after the data exist, the case it was written for.
- **The 2025 replay reads the runs of v2's first 2024 replay, checked by their hashes, and is not validated or frozen
  again.** A refit on the 2025 vintage would change the model with revised records and make a second judge; reading
  the latest runs would let anything frozen since stand in for them. Revised records stay a data-quality finding.
- **The report prints the standing the gate logged, never computes it again.** Computing it twice would need a second
  reading of the commits, the starts and the ruling entries after the start; the logged standing is the one the start
  was made on.
- **Changes read commit by commit, not as the difference between two commits.** A change and its revert leave the
  endpoints equal; the models, thresholds or design choices made meanwhile do not revert with them.
- **The standing of 2025 computed by frozen code.** The conditions under which 2025 is clean are fixed before the data
  exist; computing them in render code would let them change after the data, unseen by the freeze.
- **The diagnostics in a run of their own, after the replay.** A sensitivity measure that fails inside the 2025 replay
  would leave the test of record unfinished, and so cost the claim, for a measure that never decides anything.
- **Coverage and the five busiest hours reported, not judged.** Neither compares v2 with v1 as a test could: coverage
  is a property of v2's intervals, and five hours are one event. As in v1, test cases carry no criterion that could
  fail. The busiest hours are the year's, not the common set's: a peak day no run fed would otherwise drop out and
  quieter hours take its place.
- **The cold tail needs four weeks as well as 120 hours.** Its interval resamples weeks; 120 hours of one cold spell
  would give an interval of one episode and a decisive state from it. Four was set here, before any v2 run.
- **One availability sensitivity.** Only a margin past 05:00 UTC changes which run feeds a day.
- **Hydro-Québec's export bounded by the portal, not cut after the download.** A response for 2024 cannot then hold a
  record of 2025, so 2025 demand reaches the machine only by the gated `--through 2025` fetch, and records past the
  window are still set aside at load, so the window gate stays as v1 wrote it. The cost: once 2025 is published, a
  fresh clone's 2024 export is a later vintage than v2's committed runs read, and its numbers may differ.
- **v1's report stays at its tag, not copied.** A copy would be a second home for it.
- **No `forecast_out_of_bounds` day criterion.** Such a point cannot feed a day, and the data-quality gate refuses it.

## 17. Order of work

| Step | Done when |
|---|---|
| 0 | v1 closed: the commit of its last render, already in the history, tagged `v1`; this page and the records in `AGENTS.md`, the owner's rulings of 2026-10-16 among them, committed and tagged `plan-v2` |
| 1 | Built on synthetic data only: the forecast feed and data windows; the GAM, the frozen models and the decision rule; validation; the replay, monitors and freeze gates; the reports, ledger and licences; the breaks and commands |
| 2 | `v2-first-run` tagged; on real data: the forecast fetched for 2019–2024, data quality, validation, the 2023 freeze and rehearsal, the 2024 freeze. Reproduction checks: v1's model in v2's harness gives v1's validation numbers; the forecast feed gives the spike's on every day but 2023-12-31 (so the covering refinement changed no day); the GAM gives the spike's predictions to 1e-6 MW. Their outcome entered in section 18, with any rule change |
| 3 | The review of section 10.6; fixes named in section 18 |
| 4 | The freeze commit, tagged `rules-frozen-v2` |
| 5 | The whole chain on the frozen code: the 2024 replay (seen), breaks, ledger and report, committed together |
| later | Hydro-Québec publishes 2025: section 11.3 |

## 18. Changes since this page was first committed

### Before the first v2 run

None yet.

### After the first v2 run

- 2026-10-17, the reproduction checks of section 17, step 2, on v2's first chain on real data (the forecast fetched for
  2019–2024, each year's file byte for byte the spike's): all three passed.
  - v1's model in v2's harness: the mean absolute percentage error of v1 as shipped (4.42 %) and of its six-station
    panel, their biases and the coverage of v1's interval equal those of v1's last validation run exactly
    (difference 0), on the same 34,997, 35,013 and 26,221 hours.
  - The forecast feed: on the 1,825 days of 2019–2023 other than 2023-12-31, the same feeding run and the same rank
    as the spike's on every day, so the covering refinement changed no day; on the 35,040 hours of 2020–2023 other
    than those of 2023-12-31, the same hourly forecast temperature (largest difference 7e-15 °C). 2023-12-31 is fed,
    by the freshest run (its 24 hours are the ones the spike left out).
  - The GAM: on the 35,021 hours the spike scored, its out-of-sample predictions equal the spike's to 8.3e-9 MW
    (largest difference), none missing; no fold trains on 2023-12-31.
  - Also checked: the frozen 2024 `persistence` comparator's 84 coefficients equal v1's frozen 2024 coefficients (70
    of them bit for bit, the others to two units in the last place), and its half-width equals exactly the conformal
    quantile of the 8,739 relative scores v1's 2024 freeze calibrated on less the 24 of 2023-12-31 (9.23 %, where
    v1's was 9.26 %); the 2023 rehearsal's weather identity holds on its 8,760 hours (largest difference 0 °C, no
    day fed otherwise).
- 2026-10-17, commit eeec2ce6e80ca840c3eadd01140738f75304d716, from the review of section 10.6 (another AI model, then
  a second review, of the rule-file change since `v1`), after the first chain's runs above. Four rule changes, none of
  which changes a number of that chain:
  - Data quality refuses a station whose fetch manifest does not list every month of the window, before any of its
    files is checked: a fetch stopped between writing a month and recording it left a month that data quality read
    and the 2025 gate never verified. A data-quality run through 2025 is refused until the 2025 vintage is fetched.
  - Every tracked run is created with its tags in one step, so a replay start never exists without its kind, year and
    freeze tags: a stop in between would have left a 2025 start no later gate could see (section 11.3.5).
  - The frozen least-squares comparators store and hash their hour blocks and base temperature and forecast from their
    file alone (section 4.4), computing their degree-hours from the temperature; a day type their cells do not cover is
    refused, and for both families a row without a known day type or hour is not forecast. The comparators frozen by
    the first chain no longer match their hashes: the 2023 and 2024 freezes run again before the 2024 replay.
  - A 2025 start that stops while its gate's record is logged keeps the class of the error that stopped it.
- 2026-10-17, the chain run again from data quality on the code with those fixes: the reproduction checks above gave
  the same outcome, number for number, and the frozen GAMs of 2023 and 2024 have the same hashes as the first chain's.
