# Architecture and plan, v3

What v3 changes, and how it is built and judged. `BRIEF.md` says what valiwatt is; `AGENTS.md` holds the rules and
the owner's rulings; `docs/plan.md` and `docs/plan-v2.md` stay v1's and v2's records, unchanged. This page fixes v3's
design before any v3 run. Like v2's plan it is written after results were seen: section 0 says which. It is committed
on the `v3` branch and tagged `plan-v3` before any v3 code; every later change to it is listed in section 18, and a
test checks that list against the history. Where this page says "as in v2", the section of `docs/plan-v2.md` it names
holds unchanged, read with v3's origin, tags, store and ledger.

## 0. What had been seen when this page was written

### 0.1 Timeline

| When | Event | What it showed the developers |
|---|---|---|
| 2026-10-13 to 2026-10-21 | v1 and v2, through v2's 2024 replay, the report's conceptual-soundness section and the busiest hour broken into its pieces | Everything v2's report shows on 2019–2024: errors, intervals, verdicts, the curves, the lags, the five busiest hours |
| 2026-10-25 | The owner's ruling: v3, a forecast issued at noon the day before, with added terms and intervals by temperature (section 1) | — |
| 2026-10-25 | A count on the NBM archive of 2019–2024, outside the tracked runs | Each year's run hours (00, 07, 12 and 19 UTC until February 2020, then 01, 07, 13 and 19 UTC), the longest lead (71 hours from 2021), and that the wind speed `wsp` is never blank |
| 2026-10-25 | The owner's rulings on the order of the 2025 tests and on what a ruling may give when that order fails (section 11.3) | — |
| 2026-10-25 | Reviews of the draft of this page, outside the tracked runs, on the 2019–2024 NBM and ECCC files only | An approximate 5th percentile of the noon run's temperatures (−11.1 °C over 2019–2024, −11.9 °C over 2019–2023); counts of cold hours per twelve-month calibration window (the fewest about 134, over 6 weeks); the one day of 2019–2024 whose station has fewer than 20 usable hours (2022-04-22); the largest wind speed of the archive (29 knots) |
| tag `plan-v3` | This page | — |

None of the 2026-10-25 rows read a demand record. The findings of v2 that shaped v3, all in v2's report:

- The two lag coefficients sum to 0.454, and with the lags the cold slope keeps 70 to 74 % of its strength without
  them. At the start of a cold spell the lags carry a milder day's demand, and the curve that would make up for it is
  flatter.
- At the five busiest hours of 2020–2023, v2's mean signed error is +5.7 % (the forecast too low); at those of
  2019–2023, demand fell above v2's 90 % interval 3 times out of 5.
- v2 issues at the end of the day before. Under Hydro-Québec's Rate Flex D, the notice of a peak event is sent before
  15:00 the day before (section 2), so a forecast issued at midnight comes after it.

What nobody had computed when this page was tagged, and what it therefore still fixes in advance:

- any forecast issued at noon, by any model, on any hour;
- any v3 coefficient, interval, threshold or contrast, beyond the input-only counts of the review row above;
- any Hydro-Québec demand record of 2025: none is on the machine (v2's plan, section 11.3.1, bounds every request).

### 0.2 The choices, said plainly

Three choices were made after reading v2's results on 2019–2024, the 2023-02 peak above all: the added terms (section
4.1), the intervals by temperature (section 7), and the event windows as the subset that decides (section 8). No
alternative set of terms, bins or subsets was fitted or scored: each was written here first. Still, the years v3 is
validated on are the years that suggested them. Its validation on 2020–2024 is therefore in sample for those choices,
and only 2025 can test them.

### 0.3 What is in sample for which decision

| Result | Out of sample for the fit | For the choice of the terms, the bins and the deciding subset | Seen before v3 was ruled |
|---|---|---|---|
| Validation, 2020–2024 | yes (walk-forward) | **no** | v2's version of it, at midnight |
| 2024 rehearsal | yes | **no** (2024 is a validation year) | v2's 2024 replay, at midnight |
| 2025 replay | yes | yes | no, if section 11.3 holds |

## 1. What changes from v2, and why

| Item | v2 | v3 | Why | Authority |
|---|---|---|---|---|
| Issue time | D 05:00 UTC, the end of D−1 | D−1 17:00 UTC, noon EST of D−1 | A day-before peak-event notice is sent before 15:00; a forecast at midnight comes after it | The owner's ruling, 2026-10-25 |
| Demand history | 24 h and 168 h before the target | 48 h and 168 h before | At noon the same hour the day before is not yet observed for half of D | Follows from the issue time |
| Added terms | — | The day's mean cold, the lag day's mean cold, wind with cold (section 4.1) | The lags pass a mild day's demand into a cold one, and the busiest hours were under-forecast | The owner's ruling, 2026-10-25 ("temperature change, cold persistence and wind"); their form, this page |
| Weather inputs | NBM temperature | NBM temperature and wind, from the run in view at noon; ECCC's observed temperature of the lag day | The terms read them | NBM wind: the ruling; ECCC for the lag day: this page (section 3.3), implementing the ruling's temperature-change term |
| Intervals | One half-width | One for the cold hours, one for the rest (section 7) | 3 of the 5 busiest hours fell above v2's interval | The owner's ruling, 2026-10-25 |
| Decision rule | v2 against v1 | The terms judged against v2's form at noon, the family as v2's form against v1's form at noon; the event windows decide the terms (section 8) | The terms are judged where they are meant to help | The owner's ruling, 2026-10-25 |
| Design window | 2019–2023 | 2019–2024 | 2024 is seen; 2025 is the only held-out year | — |
| Freeze and records | `rules-frozen-v2` | A third freeze, `v3-rules-frozen`, on its own branch, with its own store, ledger and stamp (section 10) | v2's 2025 test must run on v2's frozen code, untouched | The owner's ruling, 2026-10-25 |

The model family is unchanged: the additive model of v2, fitted the same way, with three more columns.

## 2. The forecast

- **Target:** unchanged. Hydro-Québec's hourly demand, each value the mean over the hour ending at its UTC label.
- **Three clocks.** The forecast for local-standard-time day D is issued at its **origin, D−1 17:00 UTC** (12:00 EST on
  D−1, 13:00 EDT). D's hours do not move: the hour-ending labels D 06:00 to D+1 05:00 UTC, each 13 to 36 hours after the
  origin. D's scoring time does not move: D+1 06:00 UTC, one hour after its last hour (v2's section 6.3), or the end of
  the data when the data end before then, the day then judged at the end of the data, as in v2; such a day never counts
  as scored for calibration (section 7). Neither is derived from D+1's origin. A fit's clock is the origin of its first
  test day.
- **Why noon.** Hydro-Québec's Rate Flex D, a public residential rate (2026 Electricity Rates, edition effective
  2026-04-01, Section 9), defines peak hours as 06:00 to 10:00 and 16:00 to 20:00 from December 1 to March 31, less
  December 24, 25, 26 and 31, January 1 and 2, Good Friday and Easter Monday (art. 2.67); its events last 4 hours, at
  most 2 a day and 120 hours a winter (art. 2.70), so at most 30 events; and the notice of an event is sent before 15:00
  the day before (art. 2.71). A forecast compared with a notice sent before 15:00 the day before must be issued before
  then; the noon origin comes 3 hours before it, 2 under daylight time. The rate's current terms are applied to every
  winter from 2020 to 2025 as a scoring convention, not as the terms in force at the time. valiwatt never says how a
  call is made, only how well a noon forecast does in those hours and how well it ranks those windows.
- **Inputs, and when each is known:**

| Input | Known at the origin? | Read by |
|---|---|---|
| Demand 48 h and 168 h before the target hour | yes: 48 h is more than 36 h | every model at noon |
| Local hour; day type (weekday, Saturday, Sunday or Québec statutory holiday) | yes | every model |
| NBM temperature at the target hour, from the run that feeds D (section 3.2) | yes, under the availability rule, which is assumed | every model at noon |
| NBM wind speed at the target hour, from the same run | yes, same rule | v3 |
| The mean NBM temperature over D's 24 hours, from the same run | yes, same rule | v3 |
| ECCC's mean observed temperature over D−2's 24 hours, at the primary station | yes, assumed: D−2's last hour is D−1 05:00 UTC, 12 hours before the origin | v3 |
| ECCC temperature at the target hour (*observed weather*) | **no**: stands in for a perfect forecast | the observed-weather bound only, always labelled |

- "Known at the origin" is judged on timestamps, with the values as fetched: no revision of any source is modelled. A
  forecast run's arrival is inferred from its run time (section 3.2). An ECCC hour is assumed in view at its own label,
  with its value as fetched from the climate archive: its publication delay and later quality control are not modelled.
  Every result that reads the forecast or the lag day's mean says so.
- One rule for every hour of D: the same two lags at every hour, though the demand 24 h before is known at noon for
  the first twelve hours of D. Two information sets inside one day would give one coefficient two meanings, and the
  monitor's two-day argument (section 9.2) would no longer hold.
- Design code keeps v2's rule: it sees each decision time as the replay sees its clock. Demand is rebuilt from the raw
  records stamped at or before the origin; a forecast run is in view only from its availability time; an ECCC hour
  only from its own label. The origin guard is per model: a lag of 48 h at or before the noon origin for the noon
  models, a lag of 24 h at or before D 05:00 UTC for v2's form at midnight (section 4.2).

## 3. The weather input at noon

### 3.1 Source and files

- v3 reads byte copies (never links) of the raw files v2's first 2024 replay read: the Hydro-Québec export, the ECCC
  files and the NBS files, with their manifests. Before any v3 run on real data they are checked file by file against
  the hashes that replay's data-quality run recorded (the NBM manifest is one file for every year and v2's 2025 fetch
  rewrites it, so its 2019–2024 entries are checked, not the whole file), and the run refuses otherwise. Nothing of
  2019–2024 is fetched again. v3's `fetch` refuses any window past 2024, before any request: the 2025 files come from
  v2's 2025 fetch, copied after v2's 2025 test of record (section 11.3).
- **A fresh clone** has neither those files nor v2's store. There, `valiwatt fetch` downloads the bounded 2019–2024
  files, a later vintage, and v3's chain runs on them labelled as a rerun on that vintage, with each export's
  retrieval and modification dates, as v2's section 11.3.1 says for v2. It writes no freeze record and no ledger
  entry, the byte-copy check and the reproduction check are reported as not run there, and the recorded runs are
  shown from `docs/freeze-v3.json` and `docs/replay-ledger-v3.json` (section 13). Only the runs of record (sections
  11.2 and 11.3) need the byte copies.
- Two NBS fields are read: the temperature `tmp`, as in v2, and the wind speed `wsp`, in whole knots, converted to km/h
  as knots × 1.852. A file whose header lacks `wsp`, or whose `wsp` is neither a whole number nor blank, is refused.
- The data-quality layer gains wind bounds: from 0 to 100 knots inclusive. A point outside them fails the gate, as a
  temperature outside its bounds does.
- The design window ends at 2025-01-01 05:00 UTC, the last label of 2024. Design code opens the NBS files of 2019 to
  2024 by an explicit list and keeps the runs available at or before that hour (v2's section 3.3). So 2024-12-31 is
  fed by the 2024-12-30 midday run, as any day is.

### 3.2 The availability rule, and which run feeds a day

- Unchanged from v2 (its section 3.2), with the noon origin: a run is **available** two hours after its run time (the
  assumption v2 states, measured on another run hour; it is assumed here for the midday run); it is **compliant** for
  day D when available at or before D−1 17:00 UTC; it is **covering** when its own points include all nine valid
  times from D 06:00 to D+1 06:00 UTC, each holding a finite temperature inside the temperature bounds **and a finite
  wind speed inside the wind bounds**. D takes the freshest compliant covering run among the twelve freshest compliant
  runs. This one feed serves every noon model.
- Expected on the archive: the midday run feeds each day, 12:00 UTC until February 2020 and 13:00 UTC after,
  available at 14:00 or 15:00 UTC, 3 or 2 hours before the origin; its last point of D is 42 or 41 hours after its
  run time. The data-quality run reports the feeding run's hour by year, as in v2, and every day whose run changes
  because of the wind clause.
- Each hour reads its temperature and wind by linear interpolation in time between the two points of the one run that
  bracket it, as v2 reads the temperature. **The day's mean** is the mean of the 24 interpolated temperatures of D's
  labels from that run.

### 3.3 The lag day's observed temperature

- The ECCC primary station of `config/stations.toml`, its hourly observations through the data-quality layer's bounds
  filter, as v1 read them, each taken at its own label (v2's section 3.2).
- **The lag day's mean** is the mean of the usable observations stamped at D−2's 24 hour-ending labels, D−2 06:00 to
  D−1 05:00 UTC. It exists only when at least 20 of them are usable; otherwise it is missing, and nothing is imputed
  (section 5).
- Why the observation and not the forecast issued for D−2: the lag reads the demand of D−2, which answered D−2's
  weather as it was. And a forecast run already feeds up to two days (section 9.2); if it also fed the lag day's mean,
  one bad run would reach four.

### 3.4 Sensitivity to the availability margin

Validation also fits v3 with runs available six hours after their run time. That takes away the midday run (available
at 18:00 or 19:00 UTC, after the origin) and feeds each day from the 07:00 UTC run of D−1, whose last point of D is 47
hours after its run time. Labelled, never chooses.

## 4. The models, fixed before any v3 run

### 4.1 Production: v3

v2's model (its section 4.1: the spline basis with its knots from each fit's own usable rows, the 72 cells, the 264
hour-by-temperature columns, `StandardScaler`, `Ridge(alpha=1.0)`, the usable-row rule), with these changes only:

- **lag 48 h in place of lag 24 h**, and lag 168 h as before;
- three columns more, all linear, each with base 18 °C (the temperature at which v2's report draws each curve as zero):
  - **the day's mean cold:** max(0, 18 − the day's mean of section 3.2). It reads how cold the whole day of D is
    forecast to be, its later hours included, beside the hour's own temperature that the curves read;
  - **the lag day's mean cold:** max(0, 18 − the lag day's mean of section 3.3). It lets the model take out of the
    48 h lag the share that was D−2's own weather, so that a mild D−2 pulls a cold D down less. With the day's mean
    cold beside it, the two are the ruling's temperature change and persistence, written so that the lag and the
    change are read together;
  - **wind with cold:** the NBM wind speed at the target hour, in km/h, times max(0, 18 − the NBM temperature at the
    target hour). Wind raises heat loss in proportion to the gap between inside and out, so the column is zero in the
    warm.
- 341 columns in all. No interaction, knot, base or penalty is chosen on the data.
- **How the terms are read.** Each term's coefficient is printed in MW per unit, unscaled, beside its variance
  inflation factor against the rest of the design. A sign is descriptive and is evidence for no mechanism: the day's
  mean cold is close to the hours' own temperatures, and the lag day's mean cold nets the correction to the lag
  against cold persisting from D−2. Each term's evidence is its leave-one-out contrast (section 4.3).

### 4.2 Comparison models (they never recommend and never set a threshold)

- **v2's form at noon:** v2's model exactly, with lag 48 h in place of lag 24 h, on the noon feed's temperature.
- **v1's form at noon:** v1's least squares on the noon feed's temperature (degree-hours from the forecast), with lag
  48 h in place of lag 24 h.
- Both noon forms are fitted and predicted on v3's usable rows (a row v3 drops for a missing lag day's mean is dropped
  for them too, and the knots are fitted on the same rows), so that v3 against v2's form at noon differs in the three
  terms and nothing else.
- **v2's form at midnight:** v2's model at v2's origin, with all of v2's clocks and inputs: its own feature table with
  origin D 05:00 UTC and lag 24 h, v2's covering rule without the wind clause, runs compliant at D 05:00 UTC, fits at
  v2's fit clocks with v2's embargo, on its own usable rows. Refitted in each fold at the fold's first test day's 05:00
  UTC, and frozen with v3's other models on the same training years: at 2024-01-01 05:00 UTC for the 2024 rehearsal and
  2025-01-01 05:00 UTC for 2025, each with v2's embargo. Its forecasts meet the noon models' on the hour label only. Its
  contrast with v2's form at noon is the cost of issuing twelve hours earlier, the training years held equal (section
  8.1). v2's own frozen models and its 2025 forecasts are never read by v3.

### 4.3 Bounds and sensitivities (validation only, labelled, never choose)

Each is scored as a paired contrast against v3, on the common set intersected with the hours the variant forecasts,
and on each subset of section 8.2 so intersected, the dropped hours listed by reason, with the same week-block
bootstrap, labelled, never judged:

- each of the three terms left out in turn, so each term's share of the change is shown;
- the six-hour availability margin (section 3.4);
- the observed-weather bound: v3 with ECCC's observed temperature at the target hour in place of the NBM temperature
  wherever it enters (the curves and the cold factor of wind with cold), the day's mean cold read from ECCC's mean over
  D's 24 labels under the 20-hour rule of section 3.3 (missing otherwise, the rows dropped with their reason), and the
  NBM wind kept;
- the seasonal baseline (the same hour one week earlier), unchanged.

### 4.4 Frozen models

As in v2 (its section 4.4): a JSON file of numbers, its SHA-256 over everything prediction depends on, now also the
three terms' definitions (base 18 °C, D−2's label set and the 20-hour rule, the knot conversion), and the interval
layer of section 7: the bin edge, the two half-widths, the cold bin's calibration count and weeks, and whether its
half-width fell back to the pooled one. The freeze refuses a production model whose two lag coefficients are not both
positive (the echo premise, section 9.2), whose terms are not the ruled ones, or whose bin edge or knots are not the
rule applied to its own training rows.

## 5. Gaps: nothing is imputed or substituted

As in v2 (its section 5), with one reason more: a row whose lag day's mean is missing is dropped from training and not
predicted by any noon model, and the hours are listed with the reason `lag_day_temperature_missing`. In the replay a
day whose lag day's mean is missing has no noon forecast and fails its data-quality verdict, as a day without a
covering run does.

## 6. Validation on 2019–2024

- Monthly folds from January 2020 to December 2024, each refitted on the hours before it minus 7 days, every fit and
  score seen as the replay would see it (v2's section 6.1, with its eve rule, section 6.3). A fold's fit clock is its
  first test day's origin, 17:00 UTC on the eve, for the noon models; v2's form at midnight keeps v2's clocks. Every
  model of section 4 is fitted in every fold. Metrics, groupings and the week-block bootstrap (1,000 resamples, seed 0)
  are v2's.
- The thresholds for the 2025 replay come from the 2022–2024 weeks; those for the 2024 rehearsal from 2021–2023.
- `cold_c`: the 5th percentile of the noon feed's temperatures over every 2019–2024 design hour that has one (inputs
  only, never the outcome), logged by the validation run. It labels the cold tail of section 8.2 and nothing else; the
  intervals have their own edge (section 7).
- Added: the forecast hours outside each fold's training range of each new term, with their errors.

## 7. Intervals: by temperature

- v2's conformal layer (its section 7: nominal 90 %, the relative score, each month calibrated on the out-of-sample
  scores of the previous 12 folds already scored at its fit clock), computed separately in two bins by the target
  hour's NBM temperature: **cold** (below the bin edge) and **the rest**.
- **The bin edge** is computed at each fit's clock, as the knots are: the 5th percentile of the noon feed's
  temperatures over that fit's own usable training rows. Validation reads each fold's edge, the frozen model stores
  its own, and no edge reads a temperature issued after its fit's clock.
- Every calibration score, and every hour the fit forecasts, is binned by its own noon-feed temperature against the
  edge of the fit being calibrated, strictly below being cold; a score keeps no bin from the fold that scored it.
- If the cold bin of a calibration holds fewer than 120 scores, or scores from fewer than 4 Monday-to-Sunday weeks, its
  hours take the pooled half-width of all the scores, and the model records the fallback; every interval issued from
  it says so. The numbers are those of v2's cold tail (its section 8.2), for the same reason: fewer would calibrate on
  one weather episode.
- At a noon fit clock the calibration set is v2's: the eve is scored after the clock and stays out, the day before it
  is in, and 2024-12-31 stays out of the freeze for 2025.
- Coverage is measured out of sample in each bin and overall; v2's single half-width is computed beside as a
  sensitivity. The monitor's coverage statistic reads the binned intervals; its thresholds are derived from validation
  weeks scored with them.

## 8. The decision rule

### 8.1 Contrasts, on the common set of hours

Each is the paired difference in hourly absolute percentage error, with a 95 % week-block bootstrap interval (1,000
resamples, seed 0), on the hours where the target and the three noon forecasts exist:

- **terms:** v3 minus v2's form at noon. The three terms, nothing else (section 4.2).
- **family:** v2's form at noon minus v1's form at noon. The same input, another family, as v2's family contrast,
  at noon.
- On one set of hours the two add up: terms + family = v3 minus v1's form at noon, the whole change at noon, reported
  and never judged.
- **horizon:** v2's form at noon minus v2's form at midnight, on the hours where the target and both exist, and on each
  subset of section 8.2 intersected with those hours. Reported, never judged: it says what issuing at noon costs, the
  model and the training years held equal.

### 8.2 The subsets

- **All common hours.**
- **The cold tail:** the common hours whose noon NBM temperature lies below `cold_c`. Not testable with fewer than 120
  hours or fewer than 4 Monday-to-Sunday weeks, as in v2.
- **The event windows:** the common hours inside the rate's peak hours (section 2): from December 1 to March 31, less
  the excluded days that fall inside the winter (section 2), the hours ending at 07:00, 08:00, 09:00, 10:00, 17:00,
  18:00, 19:00 and 20:00 local clock time, daylight time included; in the code's `hour` column, which holds the local
  hour an hour starts in, the values 6 to 9 and 16 to 19. Not testable under the same rule as the cold tail.

### 8.3 States and verdicts

- A contrast is **better**, **worse** or **not separated** as in v2 (its section 8.3), the bounds judged as printed.
- **The terms verdict:**
  - **better:** the event-window contrast is better, the all-hours contrast is not worse, and the cold tail is
    testable and its contrast not worse;
  - **better in the event windows, the cold tail not testable:** the event-window contrast is better, the all-hours
    contrast is not worse, and the cold tail is not testable;
  - **not shown better:** the event-window contrast is not separated, or it is better but the all-hours or the
    cold-tail contrast is worse;
  - **worse:** the event-window contrast is worse;
  - **not testable:** the event windows are not testable; it supports no claim.
  The event windows decide because the terms were added for them; the two guards stop a gain there being paid for
  elsewhere.
- **The family verdict:** v2's rule (its section 8.3) on the family contrast: all common hours decide, the cold tail
  guards; its states are v2's.
- "The terms improve the noon forecast in the peak-event windows" is said only from the terms verdict; "the additive
  model earns its place over v1's form at noon" only from the family verdict; neither borrows the other's words, and
  both only on held-out data.
- **Reported beside, never judged:**
  - the whole change at noon and the horizon contrast, on each subset;
  - the five busiest hours of the year, as in v2, each model's signed error or why it has none;
  - **the window ranking**, below;
  - the interval coverage, in each bin and overall, with its bootstrap interval.
- **The window ranking.** Within a scored period (each validation year, the 2024 rehearsal, the 2025 replay), each
  winter's part inside the period is ranked on its own, never joined to days of another period: 2025 holds the end of
  the winter 2024–25 (its January to March) and the start of the winter 2025–26 (its December). A part's windows are the
  two peak-hour windows of each of its days (section 8.2); a window is eligible when its four hours have a target and a
  forecast from each of the three noon models, and the others are listed with their reason. The part ranks its w
  eligible windows by actual mean demand, and by each noon model's mean forecast, and takes k = floor(30 × w / W + 0.5),
  where W = 2 × (the number of days from December 1 to March 31 of that winter, a leap day included, that are not
  excluded days falling inside it); k is at most w. It reports, for each noon model, how many of its k highest are among
  the k highest by actual demand, with w, W and k. Ties are broken by the earlier date, then the morning window before
  the evening one. v2's form at midnight is not ranked: it is issued after the notice. It measures how well a noon
  forecast ranks the windows, not any utility's call.
- One function computes the contrasts, states, verdicts and the window ranking, in a rule file, so the rule is frozen
  with the rest.

### 8.4 Where it decides

| Year | Status | What the verdicts may support |
|---|---|---|
| 2020–2024 | In sample for the choices | Nothing: shown, labelled "in sample for the choice of the terms, the interval bins and the deciding subset" |
| 2024 rehearsal | Design year | Nothing: shown, labelled |
| 2025 | Held out, when clean (section 11.3) | The claims of section 8.3, from v3's 2025 test of record |

### 8.5 The model card's status, and what v3 does if it is not better

- Computed from the runs, never typed, each sentence naming what it is about (hourly error at noon), never more:
  - *candidate*: chosen on 2020–2024, shown on 2024 seen by the developers, held-out test pending; until v3's 2025
    test of record starts;
  - from that test alone, when 2025 is clean: *the terms lower the noon forecast's hourly error in the peak-event
    windows, and the additive model lowers it against v1's form, on held-out data (2025)* when both verdicts are
    better; *the terms lower …; the additive model not shown better than v1's form* when only the terms verdict
    is; *the terms not shown to lower …; the additive model lowers …* when only the family verdict is; *neither the
    terms nor the additive model shown to lower the noon forecast's hourly error on held-out data (2025)* when neither
    is.
    "Better in the event windows, the cold tail not testable" and the family's "better on average, the cold tail not
    testable" count as better, and the sentence says so; the terms' "not testable" counts as not better;
  - *post hoc* and *unfinished*: as v2's section 8.5, on v3's test of record.
- If a clean 2025 does not show the terms better, the report opens with that, every claim it bears on is withdrawn,
  and the report writes a recommendation in the monitor's form: *keep v2's form at noon* (the noon forecast without the
  terms). If the family verdict is not better: *review the family*. If both: both, the family first. Nothing switches
  on its own; the model in use afterwards is the owner's ruling.
- v2's status and its 2025 test are v2's: nothing in v3 changes them.

## 9. Monitoring

### 9.1 Carried over unchanged

Everything of v2's section 9.1, with v3's thresholds.

### 9.2 The two-day arguments, checked again

- **Demand.** The demand-history inputs are demand 48 h and 168 h before the target hour, entered linearly. One bad day
  d of demand spoils at most itself, the day d+2 and the day d+7. A Monday-to-Sunday week holds at most two of them (d
  and d+2, or d+2 and d+7), so trimming two days and the three-week arm hold as in v1. A term reading the last observed
  hour would have added d+1, and three days in one week: it is not used (section 16).
- **The lag day's observation.** One bad day of the station spoils one day of v3, two days later.
- **A forecast run.** A run compliant at a noon origin has a run time of D−1 15:00 UTC at the latest. Covering a third
  day takes a point at D+3 06:00 UTC, 87 hours after it; the archive's runs reach at most 72 hours. So one run feeds at
  most two consecutive days, as in v2. The data-quality run reports the longest lead by year; a run reaching 87 hours
  or more is a finding against the argument and a trigger for an entry of section 18.
- The echo premise holds as in v2: the freeze refuses a model whose two lag coefficients are not both positive. The
  three terms read no demand.
- Each replay measures the run argument as v2's does (its section 9.2), with the noon origin.

### 9.3 Re-derived

As in v2 (its section 9.3), on v3's input and model: every threshold and alarm level; temperature drift on the
noon-issued forecast; forecast drift on v3's forecasts. The day-level data-quality check adds
`lag_day_temperature_missing` and the wind bounds. Wind is not watched for drift: it enters one term, and the report
says so. The replay's spike scale keeps v2's cut at the last label of the year before (YYYY-01-01 05:00 UTC), as a fixed
timestamp, never derived from the noon origin.

## 10. The v3 freeze, the branch and the records

### 10.1 A third freeze, on its own branch

v2's 2025 gate refuses if any rule file or dependency changes after `rules-frozen-v2` on the code its 2025 start runs
(v2's plan, section 11.3.4 (a)), and a new file under `src/valiwatt` is a rule file. So v3 lives on the `v3` branch,
forked from the commit of v2's last render (3a1cd4a8c763), and never enters the `v2` branch, from which v2's 2025 test
runs. On the `v3` branch v3 may change v2's files. On it, v2's record is closed at the fork, as v1's is closed at `v1`:
v2's checked range ends at the tag `v3-fork`; v2's tags are checked against their pins as of the fork
(`rules-frozen-v2` at 825b7bd134a8), so a later move of it on the `v2` branch fails no v3 test and is read only
through condition (g) of section 11.3; v2's report, stamp and ledger entries stand as rendered there, and v2's stamp
test checks `docs/report/stamp.json` against the code at `v3-fork`.

### 10.2 Tags

| Tag | On | Why |
|---|---|---|
| `v3-fork` | 3a1cd4a8c763, v2's last render | the end of v2's checked range on the `v3` branch |
| `plan-v3` | the commit that adds this page | the pre-registration |
| `v3-first-run` | the commit from which v3 first runs on real data | the start of the range in which every rule change is disclosed |
| `v3-rules-frozen` | v3's freeze commit, before any 2025 record reaches the machine | v3's freeze |

- **No v3 tag matches `rules-frozen*`.** v2's frozen code refuses any tag of that pattern its configuration does not
  list, and v2's 2025 fetch, its 2025 gate and its report all check it; a v3 tag so named would stop v2's test. On the
  `v3` branch, the configuration lists `v3-rules-frozen` as v3's freeze.
- Each tag is annotated and pinned by a test, each an ancestor of the next, as in v2 (its section 10.4). Every v3
  command that computes on real data (data quality, validation, freezes, the rehearsal, the replay, and the read of
  v2's store) refuses unless `v3-first-run` exists and is an ancestor of the head.

### 10.3 Records

- v3's departures go in their own `AGENTS.md` section, "Departures from the v3 frozen rules", in the forms of v2's.
- v3's replays are recorded in their own append-only file, `docs/replay-ledger-v3.json`, written, scanned, checked and
  committed as `AGENTS.md` and v2's section 10.5 prescribe for `docs/replay-ledger.json`. Its entries have their own
  shape: v2's nineteen fields, the verdicts being terms and family; the one hash of the artifacts that judged the replay
  covers v3, the three comparison models, the bin edges and half-widths, the thresholds, `cold_c` and the forecast-drift
  reference. The 2024 rehearsals of step 5 enter it labelled seen by the developers; v3's freeze replays 2024 and 2025.
  Runs made before `v3-rules-frozen` exists (step 2) carry the freeze tag `v3-pre-freeze`: never a v1 replay, never a
  ledger entry.
- **The freeze record.** Before any 2025 record reaches the machine, one committed file, `docs/freeze-v3.json`, names
  the run IDs of the data-quality run (with the raw-file hashes), the validation run, the frozen run for 2025 and the
  step-5 2024 rehearsal; for each, its code commit, that its tree was clean and its rules hash, which must equal the
  rules hash at the commit `v3-rules-frozen` was first pinned to; the production model's hash; and the one hash of
  every artifact that will judge 2025. It is written once: one commit adds it, a test refuses any later commit that
  touches it, and the gate reads it as that commit holds it.
- **The stores.** v3 runs only from its own checkout of the `v3` branch, never from the checkout v2's test runs from;
  its store is that checkout's `mlruns/`. v2's store is opened once, at step 2 of section 17, read-only (the database
  opened read-only, no experiment created, nothing written). From it v3 reads only v2's first 2024 replay run's tags,
  the data-quality run they name (its three raw-file hashes, for section 3.1) and the validation run they name (its
  out-of-sample forecasts, for the reproduction check), each checked against that replay's ledger entry, and logs
  what it read. v3 keeps v2's MLflow version until then. Nothing later, the 2025 gate included, reads v2's store.
- **The stamp and the README.** v3's report has its own stamp, `docs/report/v3/stamp.json`, over `docs/report/v3/` and
  `docs/replay-ledger-v3.json`. The README's generated box stays v2's on the `v3` branch.
- The history test, the rule-file definition and the freeze gate (rule files and dependencies equal to the tag's) hold
  as in v2, scoped to v3's tags, from `v3-first-run`.

### 10.4 Before the tag

As in v2 (its section 10.6): between `v3-first-run` and `v3-rules-frozen`, no setting of the model, the inputs or the
decision rule changes except to fix a defect, each change listed in section 18 with the run that prompted it; the
monitoring rules may be tuned on the 2024 rehearsal. A review of the rule-file diff since `v3-fork` by another AI
model, in a fresh context, precedes the tag.

## 11. The replays

### 11.1 The 2024 rehearsal

Fitted on 2019–2023 with the embargo, its fit clock 2023-12-31 17:00 UTC (v2's form at midnight at its own, section
4.2); 2024 replayed hour by hour at noon, thresholds from 2021–2023, compared with the monthly refits. It reads design
data and passes no held-out gate. It checks that each day of 2024 was fed by the same run as in validation, or left
unfed for the same reason (no covering run, a wind point outside its bounds, the lag day's mean missing), that the noon
inputs it issued (temperature, wind, the day's mean, the lag day's mean) equal validation's for every hour, and that
each hour's bin and half-width equal those of the January 2024 fold, whose clock is the same; it refuses to log
otherwise. Every number is labelled seen by the developers: 2024 is a validation year (section 0.3).

### 11.2 The models frozen for 2025

At step 5 of section 17, on the code tagged `v3-rules-frozen`, from the 2019–2024 files of section 3.1, before any 2025
fetch: data quality, validation and the freeze for 2025 run once, every model of section 4 fitted on 2019–2024 with the
embargo, its fit clock 2024-12-31 17:00 UTC (v2's form at midnight at its own, section 4.2), thresholds from 2022–2024;
then `docs/freeze-v3.json` is committed. Nothing is refitted for 2025, and a revised 2019–2024 record in the 2025
vintage never changes these models.

### 11.3 2025, after v2's test

The owner's rulings of 2026-10-25:

1. **The order.** Hydro-Québec's 2025 records are fetched only once `v3-rules-frozen` exists and `docs/freeze-v3.json`
   is committed, by v2's own command on the `v2` branch (`valiwatt fetch --through 2025`, v2's plan section 11.3.1).
   v2's 2025 test of record runs first, from the `v2` branch, untouched by v3. v3's 2025 test runs after it, from the
   `v3` branch, on byte copies of the 2025 files and their manifests.
2. **Why v3's test stays held out though v2's ran first.** v3 is frozen, and its 2025 artifacts committed, before any
   2025 record reaches the machine; and after that, no ruling can make a changed v3 clean (condition (i)). So what
   v2's test shows the developers cannot change v3 and keep it held out.
3. **v3's gate.** v2's section 11.3.4, conditions (a) to (f), and its section 11.3.5 (the test of record, every start's
   trace, the owner's ruling, the evidence, the diagnostics) hold for v3 on v3's tag, store, ledger, freeze record and
   departures section, with these readings and three conditions more:
   - (a) walks commit by commit from the commit `v3-rules-frozen` was first pinned to, through the commit that adds
     `docs/freeze-v3.json`, to the commit the start runs; (b) checks the runs the freeze record names against its
     hashes, and refuses one whose tree was not clean or whose rules hash is not the rules hash at the commit
     `v3-rules-frozen` was first pinned to; (a) also refuses when the freeze record's commit is not an ancestor of the
     commit the start runs; (c) requires the record committed and its runs in v3's store;
   - *the evidence* (v2's section 11.3.5) is the step-5 2024 rehearsal named in the freeze record, run again on the
     newest export bounded to 2024 with that rehearsal's frozen artifacts, compared hour by hour with it, with the
     2019–2024 records the newest export revised;
   - (g) *v2 first:* `docs/replay-ledger.json` at `refs/heads/v2` (or, where that ref is absent,
     `refs/remotes/origin/v2`, fetched before the gate) holds an entry of year 2025 under `rules-frozen-v2` labelled
     held out or post hoc, v2's test of record (v2's section 10.5); the gate logs that commit and the entry's run;
   - (h) *fetched after the freeze:* the committer date of the commit that adds `docs/freeze-v3.json`, and the end time
     of every run it names, precede **the 2025 time**: the earlier of the retrieval time of the Hydro-Québec export that
     reaches 2025, as the copied manifest records it, and the start time of the earliest 2025 entry of v2's ledger at
     the ref (g) reads. A later forced fetch rewrites the manifest's time, but never v2's ledger. Weather files are
     checked by their hashes only: v1 fetched ECCC's January 2025 before any of this;
   - (i) *no change after 2025:* no commit whose committer date follows the 2025 time touches a rule file or a
     dependency, from the freeze record's commit to the commit the start runs.
4. **What a refusal costs.** A refusal of (a) or (f) is lifted as in v2: only by the owner's dated ruling, clean or post
   hoc, under v2's section 11.3.5; (b) to (e) are lifted by no ruling. A refusal of (h) or (i) is lifted only by his
   dated ruling too, and **that ruling can only be post hoc** (his ruling of 2026-10-25): by then v2's 2025 results have
   been seen, so nothing seen in 2025 can buy a held-out label. The gate refuses an entry that says clean on (h) or (i);
   only the "(h)" or "(i)" form below lifts them, never v2's form; and whenever (h) or (i) is among a start's causes,
   its standing is post hoc, whatever other entries apply (v2's rule that the last applying entry gives the standing
   yields to this). (h) and (i) are read again at every start, so a start after a stopped one, whatever its rulings on
   (a) or (f), stays post hoc too. Such an entry takes v2's form, its head naming the condition: "<date>, the owner's
   ruling on 2025 (h): post hoc" or "(i)". A refusal of (g) is lifted by "<date>, the owner's ruling on v2 first",
   naming by its full hash the `v2`-branch commit it rules on, for the case where v2's test does not run; it gives no
   standing of its own, and it is read only by (g), never by the walk of (a) or (f) or the check that a named commit is
   in v3's history.
5. **Not enforceable, stated.** The order of item 1 is kept by hand on the `v2` branch, whose sessions read it in
   `AGENTS.md` there; (h) and (i) record it after the fact from the machine's own clocks (committer dates and
   retrieval times), and do not prove it, beyond what v2's committed ledger fixes.
6. **A departure after the freeze record** moves the tag and changes the code the replay runs, never the artifacts: they
   stay those the record names, checked against the rules hash at the tag's first pin. Its cost is (a)'s, ruled clean or
   post hoc, before the 2025 time; after it, (i)'s: post hoc. Nobody looks at 2025 demand before v2's test of record, as
   v2's plan already says.

## 12. Breaks: every new gate is seen to fire

Each lands with a break that must make it fire and, where it can be quiet, a clean case that must stay quiet:

- *The spike scale:* its cut is YYYY-01-01 05:00 UTC whatever the origin.
- *The clocks:* a noon-issued day carries exactly the 24 labels D 06:00 to D+1 05:00 UTC and is scored at D+1 06:00
  UTC, no target blank because of the clock; a day's data-quality verdict reads only the raw records stamped in its own
  hours.
- *The noon origin:* a run available at D−1 17:01 UTC is never used for D, even with absurd values, and the
  next-freshest is; a demand record stamped after the origin never reaches a lag; no lag of 24 h enters a noon model;
  the observation at D−2 05:00 UTC is not in D−2's mean, and the one at D−1 05:00 UTC is; an ECCC hour after D−1
  05:00 UTC never reaches it.
- *Wind:* a file without `wsp`, a wind that is not a whole number, and a point above 100 knots are refused; a run with
  one blank wind point gives way to the next, though its temperatures are whole.
- *The lag day's mean:* 19 usable hours leave it missing and the rows dropped with their reason, for every noon model;
  20 compute it.
- *Intervals:* a cold bin of 119 scores, or of 120 in 3 weeks, takes the pooled half-width, the model records it and
  the intervals say so; 120 in 4 weeks keeps its own. Changing a temperature issued after a fold's clock leaves that
  fold's bin edge unchanged. A frozen model whose bin edge is not the rule on its own rows is refused.
- *The verdicts:* each line of section 8.3 has a case, "not testable" included; a gain in the event windows paid for
  by a worse all-hours or cold-tail contrast is not shown better.
- *The event windows:* the hours ending 06:00, 11:00, 16:00 and 21:00 local are outside, those ending 07:00, 10:00,
  17:00 and 20:00 inside; on a March day under daylight time the windows are the hours ending 11:00 to 14:00 and 21:00
  to 00:00 UTC; December 25 holds none.
- *The window ranking:* k follows w and W as written; equal means are ordered by date, then morning first; a window
  missing one forecast hour is not eligible and is listed.
- *The freeze:* a model with a lag coefficient at or below zero, or a term not the ruled one, is refused.
- *Tags:* on a scratch clone of v2's code (3a1cd4a8c763), creating `v3-fork`, `plan-v3`, `v3-first-run` and
  `v3-rules-frozen` leaves v2's freeze check passing.
- *Data:* a raw file whose hash differs from v2's first 2024 replay's data-quality run refuses; v3's `fetch` past 2024
  refuses before any request.
- *v2's store:* the step-2 read leaves its database and artifacts byte-identical; no other v3 command opens it.
- *The 2025 gate:* it refuses while v2's test of record has not finished; an artifact differing from
  `docs/freeze-v3.json`, or a named run from a dirty tree or other rules, is refused; a second commit touching the
  freeze record fails its test; an export retrieved before the freeze record's commit or one of its runs' end refuses
  (h), and a rule change committed after that retrieval refuses (i); a "clean" ruling on (h) or (i) is refused and a
  "post hoc" one lifts it; a ruling on v2 first lifts (g) only.
- *Calibration bins:* two folds whose edges differ bin the same calibration score by the edge of the fit being
  calibrated.
- *The window ranking's W:* a winter whose Easter falls in April counts no excluded day for it; k rounds half up.
- *The rehearsal:* a day fed by another run, an input that differs, or a bin or half-width that differs from the
  January 2024 fold refuses to log.
- *The freeze record:* the gate reads it as the commit that added it holds it, so an edited working copy changes
  nothing; a record whose commit is not an ancestor of the start refuses (a).
- *First run:* a real-data command on a head that does not descend from `v3-first-run` refuses.
- *The 2025 time:* an export re-fetched after the freeze, its manifest time later, with a v2 2025 entry started before
  the freeze record's commit, refuses (h); (a) and (i) both firing, an (i) post-hoc entry followed by a clean (a)
  entry, gives a post-hoc standing, and so does the reverse order; a start after a stopped one, with a clean (f) entry,
  stays post hoc when (h) or (i) fires.
- *Reproduction:* v2's form at midnight, in v3's harness, on a different set of 2020–2023 hours than v2's recorded
  validation, or more than 1e-6 MW from it on one hour, and the run refuses.

## 13. Reports

v3 has its own validation report, French the reference and English derived, under `docs/report/v3/`, rendered by
`valiwatt report` on the `v3` branch. It opens with v3's model card status and says, before any number, that the
forecast is issued at noon the day before and why. It shows the contrasts, the subsets, the verdicts, the window
ranking, the intervals by bin, each term's coefficient with its variance inflation factor (section 4.1), and section
4.3's sensitivities, every number labelled as section 8.4 gives it. v2's report is unchanged.

## 14. Licences and attribution

Unchanged. The NBM wind speed comes from the same NOAA product through the same IEM archive, under the attribution v2
carries. The rate text is cited, not reproduced.

## 15. What v3 does not change

The target; the model family and its fit; v2's frozen models, its 2025 test and its status; the data sources and their
licences; v1's and v2's records; the rule that a monitor recommends and never acts.

## 16. Design decisions, and the alternatives set aside

- **A term that switches on in extreme cold.** Set aside: the design years hold one cold peak, 2023-02, so such a term
  would be fitted on one episode and could not be tested. The three terms are continuous and act on every cold day.
- **Lag 24 h where it is known.** Set aside: two information sets in one day (section 2).
- **The last observed hour as a level.** Set aside: one bad day would spoil three days of a week (section 9.2).
- **The lag day's mean from the forecast issued for it.** Set aside: one bad run would reach four days (section 3.3).
- **D−1's mean temperature.** Set aside: at noon it is half observed and half forecast, two sources in one column.
- **A week-lag mean cold, or the lag day's cold by block of hours.** Set aside: one more column each, fitted on the same
  in-sample years.
- **Wind chill as an index.** Set aside: wind times the gap to 18 °C is one explainable column; an index's formula is
  built for skin, not buildings.
- **More temperature bins for the intervals.** Set aside: a colder bin would rarely reach 120 scores.
- **An earlier origin, for markets that close in the morning.** Set aside: the owner ruled noon.
- **Another family, or quantile regression.** Out of scope: a new family needs the owner's ruling (`AGENTS.md`).

## 17. Order of work

| Step | Done when |
|---|---|
| 0 | `v3-fork` tagged; this page and the three rulings in `AGENTS.md` committed on the `v3` branch and tagged `plan-v3`; the ruling on the order of the 2025 tests also committed on the `v2` branch, as one `AGENTS.md` bullet that touches no rule file, dependency, test or report |
| 1 | Built on synthetic data only: the clocks, the noon feed, wind and the lag day's mean; v3 and the comparison models; the binned intervals; the contrasts, verdicts and window ranking; validation, freezes, the 2024 rehearsal; the freeze record; the gate; the breaks; the report |
| 2 | `v3-first-run` tagged; on real data: the byte copies checked, data quality, validation, the freeze for the 2024 rehearsal and the rehearsal. Reproduction check: v2's form at midnight, in v3's harness, against the production forecasts of the validation run named by v2's first 2024 replay, read from v2's store: the same set of 2020–2023 hours with a forecast, and every value within 1e-6 MW. Its outcome entered in section 18, by commit, with no run ID |
| 3 | The review of section 10.4; fixes named in section 18 |
| 4 | The freeze commit, tagged `v3-rules-frozen` |
| 5 | On the frozen code: data quality, validation, the freeze for the 2024 rehearsal and the rehearsal again, the freeze for 2025, the breaks and the report, committed together with `docs/freeze-v3.json` |
| later | Hydro-Québec publishes 2025: section 11.3, v2's test first |

## 18. Changes since this page was first committed

### Before the first v3 run

- 2026-10-25, from mapping the code for step 1, before any v3 code: two sentences of this page could not be built as
  written.
  - Section 12, "a winter whose Easter falls in April counts no excluded day for it": Good Friday and Easter Monday
    each count when their own date falls inside the winter, so a winter can count one and not the other (Good Friday
    2024-03-29 counts, Easter Monday 2024-04-01 does not). The rule is date membership; the break uses 2025 (both in
    April) and 2024 (one each side).
  - Section 11.1 lists "a wind point outside its bounds" among the reasons a day is left unfed. Section 3.1's gate
    refuses any such point in the window, so on data that passed it the reason never arises; a run with such a point
    does not cover, and the day takes the next run or is unfed for want of a covering run, the one reason recorded.
- 2026-10-26, from building step 1, before any v3 run: two sentences read as written would contradict others.
  - Section 11.2, "every model of section 4 fitted on 2019–2024": section 4.3 calls its models "validation only", and
    section 10.3 lists what judges 2025 as v3 and the three comparison models. The freezes fit and store the models of
    sections 4.1 and 4.2, and refuse the others.
  - Section 10.3, "From it v3 reads only v2's first 2024 replay run's tags, the data-quality run they name … and the
    validation run they name": to check each run against that replay's ledger entry, the read also takes the replay
    run's `results/replay.json` and the tags and parameters of the two runs it names (that each is the run named,
    passed or complete, and from the same NBM manifest). It reads nothing else, and it is refused a second time.
- 2026-10-26, from the review of the 2025 gate, before any v3 run: section 11.3 (h) reads "the retrieval time of the
  Hydro-Québec export that reaches 2025, as the copied manifest records it", and a forced fetch rewrites that time.
  The gate takes the earliest retrieval time the machine has recorded for that export: the copied manifest's, the one
  each earlier 2025 start's gate logged, and the one each data-quality run through 2025 logged, whatever its status.
  So a start after a stopped one cannot move the 2025 time later and turn post hoc into clean. No other clock enters
  it.
- 2026-10-26, same review: v2's section 11.3.4 (d) asks that "the latest data-quality run" passed, and v2's section 18
  reads it as the latest finished one. v3's gate reads it as written: the latest v3 data-quality run through 2025,
  whatever its status, deleted runs included, must be finished, passed and under the current rules, so a later failed
  run is never passed over. And the rule files are listed with git's NUL-separated output, so a file whose name is not
  plain ASCII is a rule file like any other (none exists at `v3-fork`, so no rules hash recorded before changes).
- 2026-10-27, after reviews of the 2025 gate by a model of another family: what the gate can prove. It looks for the
  freeze's first pin and the freeze record's adding commit in every commit the repository still records (every ref
  and every reflog), so neither can be replaced by moving a branch; the walks of (a) and (i) read, as section 11.3
  defines them, the commits from the first pin and from the freeze record's commit to the commit the start runs. Git's
  replacement objects are turned off, and a replacement or a graft refuses. It reads every run v3's store holds,
  deleted ones included, and checks each 2025 run against the record it logged when it opened. So a mistake, or a
  history moved with ordinary git commands, is seen and refused or ruled on. What it cannot prove is a deliberate
  falsification of what it reads: commits pruned from the repository, a run purged from the store, or a store edited
  by hand to agree with itself. Section 11.3 (5) already says (h) and (i) record the order after the fact and do not
  prove it; the same holds here, and the committed, public history (the freeze record's commit, ledger-v3 and v2's
  ledger) is what fixes it beyond the machine.
- 2026-10-28: commit 0637b2ec7563 reworded the entry above, first committed in 94d6312ed27f, so that it says exactly what
  the gate reads (the pin and the record's addition in every ref and reflog; the walks of (a) and (i) over the commits
  section 11.3 names; a purged run among what it cannot prove).

### After the first v3 run

- 2026-10-28, step 2 of section 17, every run from commit cb5c52ce1fca (which descends from `v3-first-run`), on the byte
  copies of section 3.1, checked file by file against v2's first 2024 replay and found identical. v2's store was read
  once, read-only, and was byte-identical after the read. Data quality passed every gate. The reproduction check
  passed: v2's form at midnight, in v3's harness, forecast the same 35,045 hours of 2020–2023 as v2's recorded
  validation, and no value differs from it (largest difference 0.0 MW, against the 1e-6 MW allowed). The freeze for
  the 2024 rehearsal and the rehearsal ran; the rehearsal's identity check against validation's January 2024 fold
  passed and it logged. No rule or dependency changed.
- 2026-10-28, step 3 (section 10.4), from the review of the rule-file diff since `v3-fork` by a model of another
  family, commit 43c76c9486da changes rule files to fix five defects. The horizon contrast's cold tail and event windows
  were built from the horizon's own hours; they are now section 8.2's subsets of the common hours intersected with the
  horizon's, as section 8.1 says. v2's coverage counted an undefined interval bound as uncovered at `v3-fork` and v3's
  change had made it missing; v2's path is restored and the check applies to the binned intervals only. `read-v2`
  opened v2's store before it recorded its own run, so a crash between the two left no trace of the read; it now
  records the attempt first and refuses any earlier attempt, whatever its status. A window past 2024 now refuses any
  of its files reached through a link, not only the 2019–2024 files. The NBM wind is parsed on v3's path only; v2's
  loader reads its files as at `v3-fork`. None of this changes a step-2 result recorded above: the reproduction check
  reads v2's form at midnight, whose inputs these changes leave as they were, and step 5 runs every model again on
  the frozen code.
- 2026-10-28, same review: section 3.1 checks "the raw files v2's first 2024 replay read … with their manifests" against
  the hashes that replay's data-quality run recorded. That run recorded the Hydro-Québec export's hash, not its
  manifest's, so the export is checked by its hash and its manifest only as a byte copy (no link); the manifest's
  retrieval time, which condition (h) reads for the export reaching 2025, is checked by (h) itself.
- 2026-10-28, step 3, the same review's third part: commits cbd5ea38c4b3 and 3d248ed3140e change rule files to fix four
  defects and close two gaps they left. A 2025 run that had lost its opening gate record was taken as finished or
  skipped; it is now refused unless it stopped while opening, before any input was read, which a marker set once the
  record is logged tells apart. A replay was tagged complete before its run was finished, so an interruption between
  the two read as a store inconsistency no ruling lifts; it now reads as a stopped start, under (f). The check that
  v3's store is its own ran after the 2025 start lock and the gate's first reads; it now runs before both, and on the
  commands that only read v3's store. The ledger checks the gate runs sat in `src/valiwatt/report/`, outside the rule
  files; they moved to a rule module, and v2's ledger code the gate calls is held byte-identical to `v3-fork` by a
  test, so the rules hash recorded at `v3-fork` is unchanged. The fresh-clone label is refused once a read of v2's
  store was attempted on the machine. No step-2 result changes.
- 2026-10-28, step 5 of section 17, on the code tagged `v3-rules-frozen`: data quality, validation, the freeze for
  the 2024 rehearsal and the rehearsal, and the freeze for 2025 ran from commit fa4683323cb5, the byte copies checked again
  and the reproduction check passed again; the freeze record was written from those runs and committed alone in
  cc93edeba8fa; the breaks (every one fired) and the report followed in 19368dfb89a2. Section 17 asks that the breaks and the report
  be "committed together with `docs/freeze-v3.json`": they are not in the same commit, because the report names the
  freeze record's commit and the record's commit must touch nothing else (section 10.3); they follow it directly. No
  rule or dependency changed after the tag; the commits after it touch tests, the records and this section only. No
  2025 record has reached the machine.
