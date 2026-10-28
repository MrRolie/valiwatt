# Validation and monitoring of a forecast of Québec's electricity demand, v3: the noon forecast

Report generated from v3's MLflow runs (the runs of record the freeze record `docs/freeze-v3.json` names, added in commit `cc93edeba8fa`), code hash `2d4e6e33ee98`, rules hash `526e7ee3484c`. Every measured value comes from these runs and carries its evaluation label (plan section 8.4): "in sample for the choice of the terms, the interval bins and the deciding subset" for 2020–2024, "seen by the developers" for the 2024 rehearsal, and for 2025 "held-out data" only when its test of record is clean; a sensitivity is never judged. "The developers" are the AI agents that built this project and the owner who directs them. The design constants this report writes in numerals are read from the code that applies them. v2's report (`docs/report/`) stands as rendered; this report does not change it. This English version is derived from the French one.

> Demand data: Hydro-Québec, "Historique de la demande d'électricité au Québec" (https://donnees.hydroquebec.com/explore/dataset/historique-demande-electricite-quebec/), licensed CC BY-NC 4.0 (https://creativecommons.org/licenses/by-nc/4.0/). Hydro-Québec offers this data for information only, without warranty of quality; it is not official data. This project checked it, masked or corrected a few hours (each case documented) and aggregated it; it does not redistribute the dataset, and the reports quote only a few hourly values from it, as evidence.
>
> Weather data: Environment and Climate Change Canada, used under the Open Government Licence – Canada (https://open.canada.ca/en/open-government-licence-canada).
>
> Extracted from the Environment and Climate Change Canada Historical Climate Data web site ( https://climate.weather.gc.ca/index_e.html ) on 2026-10-14.
>
> Weather forecasts: NOAA National Blend of Models NBS guidance for station CYUL (Montréal-Trudeau), downloaded from the Iowa Environmental Mesonet archive of Iowa State University (https://mesonet.agron.iastate.edu/). This project interpolated these three-hourly forecasts to hourly values and converted them to degrees Celsius: the values it uses are modified and are not original NOAA data.
>
> Neither Hydro-Québec, Environment and Climate Change Canada, NOAA nor the Iowa Environmental Mesonet produced, reviewed or endorsed this work, and the project is affiliated with neither NOAA nor the Iowa Environmental Mesonet.
>
> Built with Claude Code under the repository owner's direction.

## In brief

- **Status: candidate.** Chosen on 2020–2024 (in sample for the choice), shown on 2024 seen by the developers; the held-out test, 2025, is pending.
- **The forecast for day D is issued at noon the day before** (12:00 EST on D−1, 13:00 EDT under daylight time; that is 17:00 UTC), no longer at the end of the day before as v2's is. Why noon: under Hydro-Québec's Rate Flex D, a public residential rate (2026 Electricity Rates, edition effective 2026-04-01, Section 9), the notice of a peak event is sent before 15:00 the day before (art. 2.71); a forecast compared with that notice must be issued before it, and the noon forecast is issued 3 hours before it, 2 under daylight time. The rate's text is cited, not reproduced. This report never says how a peak event is called, only how well the noon forecast does in the rate's peak hours and how well it ranks its windows.
- Validation on 2020–2024 (_in sample for the choice of the terms, the interval bins and the deciding subset_): the mean absolute percentage error (MAPE) is 3.25% for v3, 3.28% for v2's form at noon, 4.11% for v1's form at noon and 2.77% for v2's form at midnight, each on its own hours. Terms verdict: "better"; family verdict: "better". These years suggested the terms, the interval bins and the deciding subset: these verdicts support no claim (section 2).
- The 2024 rehearsal (_seen by the developers_): MAPE 2.95%, coverage 92.1%; terms verdict "better", family verdict "better". A year already seen supports no claim (section 3).
- 2025: pending; the test of record will be the first 2025 replay started under `v3-rules-frozen`, after v2's test of record (section 4).

## Model card

| | |
|---|---|
| **Status** | **Status: candidate.** Chosen on 2020–2024 (in sample for the choice), shown on 2024 seen by the developers; the held-out test, 2025, is pending. |
| **What it forecasts** | Québec's hourly electricity demand as Hydro-Québec publishes it, for the 24 hours of day D, issued at noon the day before (section 1), with a forecast interval whose half-width depends on the forecast temperature. |
| **Intended use** | To demonstrate, on a real forecast, validation and monitoring of the kind a model-risk team performs. |
| **Out of scope** | Any operational dispatch, trading or pricing, any decision, and any call of a peak event. Hydro-Québec publishes this data for information only; it is not official data. |
| **Model** | v2's additive model, fitted the same way, with demand 48 h and 168 h before the target hour and three more terms: the day's mean cold, the lag day's mean cold and wind with cold (section 1). The model frozen for 2025 has 341 columns, fitted on the data up to 2024-12-24; hash `5c64cc9af273`. Compared with v2's form at noon, v1's form at noon and v2's form at midnight, which never recommend anything. |
| **Interval** (_in sample for the choice of the terms, the interval bins and the deciding subset_) | The model frozen for 2025: below −11.1 °C, forecast × (1 ± 8.12%); elsewhere, forecast × (1 ± 6.38%) (section 2.4). |
| **Validation** (_in sample for the choice of the terms, the interval bins and the deciding subset_) | v3's MAPE 3.25% [3.11%, 3.41%]; verdicts: terms "better", family "better" (section 2). |
| **The 2024 rehearsal** (_seen by the developers_) | MAPE 2.95%, coverage 92.1% (section 3). |
| **2025** | Pending (section 4). |
| **Weather caveat** | An NBM weather forecast (one run) is assumed available 2 hours after its run time, and an ECCC observed hour from its own label; every result that reads the forecast or the lag day's mean rests on these assumptions (section 1). The observed temperature at the target hour is an optimistic bound only. |
| **Limitations** | Section 6. |
| **Author** | Built with Claude Code under the repository owner's direction. |

## 1. The noon forecast, and what v3 changes

- **Three clocks.** The forecast for day D (in standard time) is issued at its origin, D−1 at 17:00 UTC, that is 12:00 EST, or 13:00 EDT under daylight time. D's hours do not move: each carries the UTC hour it ends at, from D at 06:00 UTC to the next day at 05:00 UTC. D is scored one hour after its last hour, as in v2. v2's form at midnight keeps v2's origin, D at 05:00 UTC.
- **The inputs, known at the origin.** Demand 48 h and 168 h before the target hour (the demand 24 h before is not known for half of D); the local hour and the day type; the NBM forecast temperature and wind at the target hour, from the run that feeds D; the mean of the forecast temperatures of D's 24 hours; the mean of ECCC's observed temperatures at the primary station over the 24 hours of D−2 (the lag day), which exists only with at least 20 usable hours: otherwise it is missing, and nothing is imputed.
- **The availability assumption.** An NBM weather forecast (one run) is assumed available 2 hours after its run time, and an ECCC hour from its own label, with its value as fetched: neither the publication delay nor later quality control is modelled. A validation variant makes each run available 6 hours after its run time (section 2.6).
- **The three terms**, all linear, each with base 18 °C: the day's mean cold, max(0, 18 − the forecast mean of D's hours); the lag day's mean cold, max(0, 18 − D−2's observed mean), which lets the model take out of the demand 48 hours earlier the share that was D−2's own weather; wind with cold, the forecast wind in km/h (knots × 1.852) times max(0, 18 − the hour's forecast temperature), zero in the warm. The forecast wind must lie between 0 and 100 knots, both included. No term reads demand.
- **The comparison models**, which never recommend anything: v2's form at noon (v2 exactly, on v3's rows, without the terms); v1's form at noon (v1's least squares on the same forecast); v2's form at midnight (v2 with all of its clocks and inputs, to measure what issuing twelve hours earlier costs).
- **The error.** The mean absolute percentage error (MAPE) says by how much, on average, the forecast misses actual demand, as a percentage of it. The signed bias says in which direction: bias = (demand − forecast) / demand, positive when the forecast is too low.
- **The interval, and its coverage.** Each forecast carries an interval, forecast × (1 ± q). The half-width q is the ⌈(n + 1) × 0.90⌉-th smallest of the n relative errors |demand − forecast| / forecast of the 12 months before, already scored: the form of a split conformal interval, without its guarantee, since these hourly errors are autocorrelated. Coverage is the share of hours whose demand falls inside the interval; its target is 90%. It is measured only from the first month preceded by 12 scored months, on fewer hours than the error.
- **Intervals by temperature.** That quantile is taken separately for the cold hours, below each fit's edge (the 5th percentile of the forecast temperatures of its own training rows), and for the rest. A cold bin of fewer than 120 errors, or of errors from fewer than 4 Monday-to-Sunday weeks, takes the pooled half-width of all the errors, and the model records it.
- **The variance inflation factor** of a coefficient says how much its variance grows because its column overlaps the model's other columns: the larger it is, the less the coefficient reads on its own.
- **The decision rule.** On the common hours (the target and the three noon forecasts), the paired difference in hourly absolute percentage error, with a 95% week-block interval (1,000 resamples in this validation; each replay gives its own, sections 3 and 4): **terms**, v3 minus v2's form at noon; **family**, v2's form at noon minus v1's form at noon; their sum, the whole change, reported and never judged; and the **horizon**, v2's form at noon minus its form at midnight, reported and never judged. A negative difference is a lower error: it is better when its interval's upper bound is below zero, worse when its lower bound is above it, not separated otherwise. The subsets: all common hours; the cold tail, those whose forecast temperature (noon issue) lies below the 5th percentile of the design years'; the event windows, those inside the rate's peak hours: December 1 to March 31, less December 24, 25, 26 and 31, January 1 and 2, Good Friday and Easter Monday (art. 2.67), the hours ending at 7, 8, 9 and 10 and at 17, 18, 19 and 20 o'clock, local time. A subset is not testable under 120 hours or 4 weeks. **The terms verdict**: better when the event-window contrast is better, the all-hours one not worse and the cold tail testable and not worse; "better in the event windows, the cold tail not testable" when it is not testable; not shown better when the event-window contrast is not separated, or better but paid for elsewhere; worse when it is worse; not testable when the windows are not. **The family verdict** follows v2's rule: all common hours decide and the cold tail guards; better when the all-hours contrast is better and the cold tail, testable, is not worse; "better on average, the cold tail not testable" when it is not testable; not shown better when that contrast is not separated, or better with a worse cold tail; worse when it is worse. "The terms improve the noon forecast in the peak-event windows" is said only from the terms verdict, "the additive model earns its place over v1's form at noon" only from the family verdict, and both only on held-out data.
- **The window ranking.** In each scored period, each part of a winter is ranked on its own: its w eligible windows (four hours with a target and the three noon forecasts) are ranked by actual mean demand and by each noon model's mean forecast; k = floor(30 × w / W + 0.5), where 30 is the most events a winter holds (120 hours in events of 4 hours, art. 2.70) and W the windows of the whole winter, two a day outside the excluded days; for each model, how many of its k highest are among the k highest by actual demand. v2's form at midnight is not ranked: it is issued after the notice.

## 2. Validation on 2020–2024 (_in sample for the choice of the terms, the interval bins and the deciding subset_)

Each month from 2020-01 to 2024-12 (60 folds) is forecast by models refitted on the hours before it, less the last 7 days, each noon model at its fold's noon origin. These years suggested the terms, the interval bins and the event windows as the deciding subset: every result is in sample for those choices, and only 2025 can test them.

### 2.1 The contrasts

The subsets (_in sample for the choice of the terms, the interval bins and the deciding subset_): 43,791 common hours over 262 Monday-to-Sunday weeks; the cold tail, the hours whose forecast temperature (noon issue) is below −11.1 °C, 1,834 hours over 46 weeks, testable; the event windows, 4,608 hours over 92 weeks, testable.

| Contrast (_in sample for the choice of the terms, the interval bins and the deciding subset_) | Subset | Hours | MAPE difference | 95% CI | State |
|---|---|---|---|---|---|
| terms: v3 minus v2's form at noon | all common hours | 43,791 | −0.03 pp | [−0.06 pp, 0.00 pp] | not separated |
| terms: v3 minus v2's form at noon | the cold tail | 1,834 | 0.00 pp | [−0.14 pp, +0.15 pp] | not separated |
| terms: v3 minus v2's form at noon | the event windows | 4,608 | −0.12 pp | [−0.19 pp, −0.06 pp] | better |
| family: v2's form at noon minus v1's form at noon | all common hours | 43,791 | −0.83 pp | [−0.95 pp, −0.72 pp] | better |
| family: v2's form at noon minus v1's form at noon | the cold tail | 1,834 | −0.62 pp | [−0.96 pp, −0.26 pp] | better |
| family: v2's form at noon minus v1's form at noon | the event windows | 4,608 | −0.10 pp | [−0.18 pp, −0.01 pp] | better |
| the whole change at noon: v3 minus v1's form at noon (reported, never judged) | all common hours | 43,791 | −0.86 pp | [−0.97 pp, −0.75 pp] | better |
| the whole change at noon: v3 minus v1's form at noon (reported, never judged) | the cold tail | 1,834 | −0.62 pp | [−0.97 pp, −0.29 pp] | better |
| the whole change at noon: v3 minus v1's form at noon (reported, never judged) | the event windows | 4,608 | −0.22 pp | [−0.33 pp, −0.11 pp] | better |

On each subset, the terms plus the family give the whole change, to rounding.

| Horizon: v2's form at noon minus v2's form at midnight, reported, never judged (_in sample for the choice of the terms, the interval bins and the deciding subset_) | Hours | MAPE difference | 95% CI |
|---|---|---|---|
| the horizon's own hours (the target, v2's form at noon and v2's form at midnight) | 43,779 | +0.51 pp | [+0.46 pp, +0.57 pp] |
| the cold tail | 1,834 | +0.57 pp | [+0.21 pp, +0.89 pp] |
| the event windows | 4,608 | +0.74 pp | [+0.61 pp, +0.88 pp] |

### 2.2 The verdicts

- **Terms verdict** (_in sample for the choice of the terms, the interval bins and the deciding subset_): "better".
- **Family verdict** (_in sample for the choice of the terms, the interval bins and the deciding subset_): "better".
- On years that suggested the choices, neither supports a claim; each is said only from its own contrast.

### 2.3 Each model's error

| Model (_in sample for the choice of the terms, the interval bins and the deciding subset_) | MAPE | 95% CI | Signed bias | Hours |
|---|---|---|---|---|
| v3 (production) | 3.25% | [3.11%, 3.41%] | +1.44% | 43,791 |
| v2's form at noon | 3.28% | [3.13%, 3.43%] | +1.46% | 43,791 |
| v1's form at noon | 4.11% | [3.98%, 4.24%] | +1.47% | 43,791 |
| v2's form at midnight | 2.77% | [2.66%, 2.89%] | +1.21% | 43,814 |
| the same hour one week earlier (naive baseline) | 7.84% | [7.27%, 8.45%] | −0.56% | 43,826 |

### 2.4 Intervals by temperature

| v3's bin (_in sample for the choice of the terms, the interval bins and the deciding subset_) | Coverage (nominal: 90%) | 95% CI | Hours |
|---|---|---|---|
| cold | 74.4% | [61.7%, 88.9%] | 1,189 |
| the rest | 90.6% | [88.8%, 92.0%] | 33,824 |
| all | 90.0% | [88.3%, 91.7%] | 35,013 |
| one half-width, as v2's (sensitivity) | 90.6% | [88.9%, 92.1%] | 35,013 |

Coverage is never a criterion of the decision rule. Each calibrated fold, its edge and its half-widths:

| v3's fold (_in sample for the choice of the terms, the interval bins and the deciding subset_) | Edge | Cold | The rest | Pooled | Cold errors | Cold weeks | Fallback |
|---|---|---|---|---|---|---|---|
| 2021-01 | −12.4 °C | 8.77% | 7.46% | 7.50% | 244 | 9 | no |
| 2021-02 | −12.4 °C | 8.05% | 7.29% | 7.31% | 232 | 7 | no |
| 2021-03 | −13.0 °C | 4.13% | 7.13% | 7.10% | 175 | 6 | no |
| 2021-04 | −12.8 °C | 4.13% | 7.16% | 7.14% | 209 | 9 | no |
| 2021-05 | −12.8 °C | 4.13% | 7.00% | 6.96% | 209 | 9 | no |
| 2021-06 | −12.8 °C | 4.13% | 6.97% | 6.94% | 209 | 9 | no |
| 2021-07 | −12.6 °C | 4.95% | 7.15% | 7.14% | 243 | 10 | no |
| 2021-08 | −12.4 °C | 6.40% | 7.21% | 7.19% | 260 | 10 | no |
| 2021-09 | −12.2 °C | 6.75% | 7.25% | 7.23% | 273 | 10 | no |
| 2021-10 | −12.2 °C | 6.75% | 7.24% | 7.23% | 273 | 10 | no |
| 2021-11 | −12.0 °C | 6.85% | 7.04% | 7.03% | 319 | 10 | no |
| 2021-12 | −11.9 °C | 6.93% | 6.72% | 6.72% | 327 | 10 | no |
| 2022-01 | −11.7 °C | 3.65% | 7.00% | 6.91% | 309 | 11 | no |
| 2022-02 | −12.6 °C | 8.81% | 7.27% | 7.42% | 508 | 11 | no |
| 2022-03 | −13.3 °C | 8.50% | 7.46% | 7.59% | 546 | 11 | no |
| 2022-04 | −13.5 °C | 8.62% | 7.42% | 7.55% | 555 | 9 | no |
| 2022-05 | −13.3 °C | 8.57% | 7.56% | 7.68% | 566 | 9 | no |
| 2022-06 | −13.3 °C | 8.57% | 7.59% | 7.73% | 566 | 9 | no |
| 2022-07 | −13.3 °C | 8.57% | 7.45% | 7.56% | 566 | 9 | no |
| 2022-08 | −13.0 °C | 8.62% | 7.38% | 7.54% | 608 | 9 | no |
| 2022-09 | −12.8 °C | 8.57% | 7.34% | 7.51% | 621 | 9 | no |
| 2022-10 | −12.8 °C | 8.57% | 7.38% | 7.56% | 621 | 9 | no |
| 2022-11 | −12.8 °C | 8.57% | 7.44% | 7.61% | 621 | 9 | no |
| 2022-12 | −12.6 °C | 8.57% | 7.61% | 7.76% | 644 | 9 | no |
| 2023-01 | −12.6 °C | 8.57% | 7.13% | 7.31% | 644 | 9 | no |
| 2023-02 | −12.4 °C | 7.25% | 7.21% | 7.21% | 263 | 8 | no |
| 2023-03 | −12.6 °C | 10.58% | 7.34% | 7.42% | 174 | 9 | no |
| 2023-04 | −12.6 °C | 11.36% | 7.14% | 7.21% | 147 | 8 | no |
| 2023-05 | −12.6 °C | 11.36% | 7.19% | 7.28% | 147 | 8 | no |
| 2023-06 | −12.4 °C | 10.87% | 7.16% | 7.23% | 150 | 8 | no |
| 2023-07 | −12.2 °C | 10.87% | 7.14% | 7.21% | 150 | 8 | no |
| 2023-08 | −12.2 °C | 10.87% | 7.13% | 7.20% | 150 | 8 | no |
| 2023-09 | −12.2 °C | 10.87% | 7.08% | 7.16% | 150 | 8 | no |
| 2023-10 | −12.0 °C | 10.87% | 7.04% | 7.14% | 164 | 8 | no |
| 2023-11 | −11.9 °C | 10.87% | 7.02% | 7.13% | 168 | 8 | no |
| 2023-12 | −11.9 °C | 10.87% | 6.94% | 7.04% | 168 | 8 | no |
| 2024-01 | −11.7 °C | 10.58% | 7.05% | 7.13% | 187 | 9 | no |
| 2024-02 | −11.9 °C | 10.57% | 6.88% | 6.99% | 191 | 7 | no |
| 2024-03 | −11.7 °C | 6.66% | 6.69% | 6.66% | 87 | 4 | yes |
| 2024-04 | −11.7 °C | 6.77% | 6.79% | 6.77% | 86 | 3 | yes |
| 2024-05 | −11.7 °C | 6.47% | 6.49% | 6.47% | 86 | 3 | yes |
| 2024-06 | −11.7 °C | 6.45% | 6.47% | 6.45% | 86 | 3 | yes |
| 2024-07 | −11.5 °C | 6.49% | 6.49% | 6.49% | 111 | 6 | yes |
| 2024-08 | −11.5 °C | 6.51% | 6.52% | 6.51% | 111 | 6 | yes |
| 2024-09 | −11.3 °C | 6.88% | 6.55% | 6.55% | 122 | 6 | no |
| 2024-10 | −11.1 °C | 6.88% | 6.50% | 6.50% | 134 | 6 | no |
| 2024-11 | −11.1 °C | 6.88% | 6.48% | 6.49% | 134 | 6 | no |
| 2024-12 | −11.1 °C | 6.88% | 6.28% | 6.29% | 134 | 6 | no |

- The model frozen for 2024 (_in sample for the choice of the terms, the interval bins and the deciding subset_): edge −11.7 °C; half-widths cold 10.58%, the rest 7.05%, pooled 7.13%; 187 cold errors over 9 weeks; fallback: no.
- The model frozen for 2025 (_in sample for the choice of the terms, the interval bins and the deciding subset_): edge −11.1 °C; half-widths cold 8.12%, the rest 6.38%, pooled 6.40%; 181 cold errors over 7 weeks; fallback: no.

### 2.5 The terms: coefficients and variance inflation factors

| The model frozen for 2025 (_in sample for the choice of the terms, the interval bins and the deciding subset_) | Coefficient | Unit | Variance inflation factor |
|---|---|---|---|
| the day's mean cold | 85.962 | MW per °C colder (below 18 °C) | 51.20 |
| the lag day's mean cold | −12.991 | MW per °C colder (below 18 °C) | 14.83 |
| wind with cold | 1.938 | MW per km/h per °C colder (below 18 °C) | 3.33 |
| demand 48 h before | 0.242 | MW per MW | 14.66 |
| demand 168 h before | 0.080 | MW per MW | 5.20 |

| The model frozen for 2024 (_in sample for the choice of the terms, the interval bins and the deciding subset_) | Coefficient | Unit | Variance inflation factor |
|---|---|---|---|
| the day's mean cold | 93.813 | MW per °C colder (below 18 °C) | 50.97 |
| the lag day's mean cold | −8.141 | MW per °C colder (below 18 °C) | 15.19 |
| wind with cold | 1.867 | MW per km/h per °C colder (below 18 °C) | 3.27 |
| demand 48 h before | 0.232 | MW per MW | 15.09 |
| demand 168 h before | 0.077 | MW per MW | 5.34 |

Each coefficient is printed per unit of its column, unscaled, beside its variance inflation factor against the model's other columns. A sign is descriptive and is evidence for no mechanism: the day's mean cold is close to the hours' own temperatures, and the lag day's mean cold nets the correction to the demand 48 hours earlier against cold persisting from D−2. Each term's evidence is its leave-one-out contrast (section 2.6).

| v3's input, outside its fold's training range (_in sample for the choice of the terms, the interval bins and the deciding subset_) | Hours forecast | Below the range | Above | MAPE outside |
|---|---|---|---|---|
| the forecast temperature | 43,802 | 92 | 12 | 5.00% |
| the day's mean cold | 43,802 | 0 | 120 | 4.66% |
| the lag day's mean cold | 43,802 | 0 | 120 | 6.48% |
| wind with cold | 43,802 | 0 | 31 | 4.75% |

### 2.6 Bounds and sensitivities (_in sample for the choice of the terms, the interval bins and the deciding subset_; _sensitivity, never judged_)

Each is the paired MAPE difference of the variant minus v3, on the common hours where the variant forecasts, and on each subset so reduced; never judged. Its direction is the reverse of the contrasts': a positive difference says the variant does worse than v3, so what it removes or changes helped v3; a negative one, that it does better.

| Variant (_in sample for the choice of the terms, the interval bins and the deciding subset_; _sensitivity, never judged_) | Subset | Hours | MAPE difference | 95% CI |
|---|---|---|---|---|
| v3 without the day's mean cold | all common hours | 43,791 | −0.02 pp | [−0.03 pp, 0.00 pp] |
| v3 without the day's mean cold | the cold tail | 1,834 | +0.01 pp | [−0.07 pp, +0.08 pp] |
| v3 without the day's mean cold | the event windows | 4,608 | +0.04 pp | [+0.01 pp, +0.07 pp] |
| v3 without the lag day's mean cold | all common hours | 43,791 | −0.01 pp | [−0.02 pp, 0.00 pp] |
| v3 without the lag day's mean cold | the cold tail | 1,834 | 0.00 pp | [−0.01 pp, +0.02 pp] |
| v3 without the lag day's mean cold | the event windows | 4,608 | 0.00 pp | [−0.01 pp, +0.02 pp] |
| v3 without wind with cold | all common hours | 43,791 | +0.04 pp | [+0.02 pp, +0.06 pp] |
| v3 without wind with cold | the cold tail | 1,834 | −0.02 pp | [−0.15 pp, +0.12 pp] |
| v3 without wind with cold | the event windows | 4,608 | +0.08 pp | [+0.03 pp, +0.12 pp] |
| v3, the longer availability margin | all common hours | 43,767 | +0.01 pp | [0.00 pp, +0.02 pp] |
| v3, the longer availability margin | the cold tail | 1,825 | +0.07 pp | [+0.01 pp, +0.13 pp] |
| v3, the longer availability margin | the event windows | 4,600 | +0.02 pp | [0.00 pp, +0.04 pp] |
| v3 on the observed temperature (optimistic bound) | all common hours | 43,727 | −0.17 pp | [−0.23 pp, −0.10 pp] |
| v3 on the observed temperature (optimistic bound) | the cold tail | 1,833 | −0.28 pp | [−0.56 pp, −0.02 pp] |
| v3 on the observed temperature (optimistic bound) | the event windows | 4,608 | −0.52 pp | [−0.67 pp, −0.38 pp] |
| the same hour one week earlier (naive baseline) | all common hours | 43,791 | +4.59 pp | [+4.07 pp, +5.15 pp] |
| the same hour one week earlier (naive baseline) | the cold tail | 1,834 | +7.79 pp | [+6.29 pp, +9.63 pp] |
| the same hour one week earlier (naive baseline) | the event windows | 4,608 | +6.81 pp | [+6.02 pp, +7.62 pp] |

- v3, the longer availability margin (_in sample for the choice of the terms, the interval bins and the deciding subset_; _sensitivity, never judged_): common hours without its forecast, by reason: none of the available forecasts covers the day (24 hours).
- v3 on the observed temperature (optimistic bound) (_in sample for the choice of the terms, the interval bins and the deciding subset_; _sensitivity, never judged_): common hours without its forecast, by reason: the day's observed mean is missing (5 hours); the observed temperature is missing (40 hours); the observed temperature is missing and the day's observed mean is missing (19 hours).

### 2.7 The busiest hours (reported, never judged)

| Hour ending, UTC (_in sample for the choice of the terms, the interval bins and the deciding subset_) | Demand | Forecast temperature | v3 (production) | v2's form at noon | v1's form at noon | v2's form at midnight |
|---|---|---|---|---|---|---|
| 2023-02-03 23:00 UTC | 42,473 MW | −24.8 °C | +0.3% | +4.3% | +9.6% | +4.4% |
| 2023-02-04 00:00 UTC | 42,254 MW | −25.0 °C | +2.2% | +6.0% | +9.3% | +6.5% |
| 2023-02-04 01:00 UTC | 41,799 MW | −25.2 °C | +2.3% | +6.1% | +9.0% | +8.2% |
| 2023-02-04 02:00 UTC | 41,141 MW | −25.4 °C | +2.1% | +5.9% | +8.7% | +9.3% |
| 2023-02-03 22:00 UTC | 40,585 MW | −24.6 °C | −2.9% | +1.7% | +8.0% | +0.4% |


Each model's signed error is (demand − forecast) / demand (section 1).

### 2.8 The window ranking (reported, never judged)

| 2020, winter (_in sample for the choice of the terms, the interval bins and the deciding subset_) | Days | W | w | k | v3 (production) | v2's form at noon | v1's form at noon | Windows not eligible |
|---|---|---|---|---|---|---|---|---|
| 2019-20 | 2020-01-01 to 2020-03-31 | 232 | 178 | 23 | 18 of 23 | 19 of 23 | 18 of 23 | 0 |
| 2020-21 | 2020-12-01 to 2020-12-31 | 230 | 54 | 7 | 7 of 7 | 7 of 7 | 7 of 7 | 0 |


| 2021, winter (_in sample for the choice of the terms, the interval bins and the deciding subset_) | Days | W | w | k | v3 (production) | v2's form at noon | v1's form at noon | Windows not eligible |
|---|---|---|---|---|---|---|---|---|
| 2020-21 | 2021-01-01 to 2021-03-31 | 230 | 176 | 23 | 16 of 23 | 17 of 23 | 17 of 23 | 0 |
| 2021-22 | 2021-12-01 to 2021-12-31 | 230 | 54 | 7 | 3 of 7 | 2 of 7 | 2 of 7 | 0 |


| 2022, winter (_in sample for the choice of the terms, the interval bins and the deciding subset_) | Days | W | w | k | v3 (production) | v2's form at noon | v1's form at noon | Windows not eligible |
|---|---|---|---|---|---|---|---|---|
| 2021-22 | 2022-01-01 to 2022-03-31 | 230 | 176 | 23 | 15 of 23 | 15 of 23 | 16 of 23 | 0 |
| 2022-23 | 2022-12-01 to 2022-12-31 | 230 | 54 | 7 | 5 of 7 | 5 of 7 | 5 of 7 | 0 |


| 2023, winter (_in sample for the choice of the terms, the interval bins and the deciding subset_) | Days | W | w | k | v3 (production) | v2's form at noon | v1's form at noon | Windows not eligible |
|---|---|---|---|---|---|---|---|---|
| 2022-23 | 2023-01-01 to 2023-03-31 | 230 | 176 | 23 | 16 of 23 | 16 of 23 | 16 of 23 | 0 |
| 2023-24 | 2023-12-01 to 2023-12-31 | 230 | 54 | 7 | 5 of 7 | 6 of 7 | 5 of 7 | 0 |


| 2024, winter (_in sample for the choice of the terms, the interval bins and the deciding subset_) | Days | W | w | k | v3 (production) | v2's form at noon | v1's form at noon | Windows not eligible |
|---|---|---|---|---|---|---|---|---|
| 2023-24 | 2024-01-01 to 2024-03-31 | 230 | 176 | 23 | 15 of 23 | 15 of 23 | 15 of 23 | 0 |
| 2024-25 | 2024-12-01 to 2024-12-31 | 230 | 54 | 7 | 5 of 7 | 5 of 7 | 5 of 7 | 0 |


Each model's cell says how many of its k highest windows are among the k highest by actual demand.

### 2.9 The noon input

| Design days (_in sample for the choice of the terms, the interval bins and the deciding subset_) | Count |
|---|---|
| days | 2,192 |
| days fed by a forecast | 2,191 |
| days without a covering forecast | 1 |
| days without the lag day's mean | 3 |

- 2019-01-01 (_in sample for the choice of the terms, the interval bins and the deciding subset_): no forecast, no forecast available before the origin.
- Days without the lag day's mean (_in sample for the choice of the terms, the interval bins and the deciding subset_): 2019-01-01 to 2019-01-02 and 2022-04-24; no noon model forecasts them, and their hours are left out for the reason "the lag day's mean is missing".

### 2.10 The reproduction check

v2's form at midnight, in v3's harness, reproduces v2's recorded validation forecasts on 35,045 hours, from 2020-01-01 to 2023-12-31 (_in sample for the choice of the terms, the interval bins and the deciding subset_): no difference, against a tolerance of 1 × 10⁻⁶ MW.

## 3. The 2024 rehearsal (_seen by the developers_)

The models frozen for 2024, fitted on the data up to 2023-12-24, replay 2024 hour by hour at noon (run `fe5d0e5bf99f411fa3c814f6ff51628c`). 2024 is a validation year: every number is seen by the developers and supports no claim. The rehearsal checked that it reads exactly validation's input: 8,784 hours and 366 days, 0 differences. Its week-block intervals come from 1,000 resamples.

| Model (_seen by the developers_) | MAPE | 95% CI | Coverage |
|---|---|---|---|
| v3 (production) | 2.95% | [2.67%, 3.24%] | 92.1% |
| v2's form at noon | 3.01% | [2.70%, 3.33%] | 92.2% |
| v1's form at noon | 3.84% | [3.61%, 4.10%] | 91.1% |
| v2's form at midnight | 2.58% | [2.33%, 2.85%] | no interval |


| v3's bin (_seen by the developers_) | Coverage (nominal: 90%) | 95% CI | Hours |
|---|---|---|---|
| cold | 100.0% | [100.0%, 100.0%] | 129 |
| the rest | 92.0% | [89.2%, 94.4%] | 8,649 |
| all | 92.1% | [89.4%, 94.4%] | 8,778 |


The subsets (_seen by the developers_): 8,778 common hours over 53 Monday-to-Sunday weeks; the cold tail, the hours whose forecast temperature (noon issue) is below −11.1 °C, 181 hours over 7 weeks, testable; the event windows, 920 hours over 19 weeks, testable.

| Contrast (_seen by the developers_) | Subset | Hours | MAPE difference | 95% CI | State |
|---|---|---|---|---|---|
| terms: v3 minus v2's form at noon | all common hours | 8,778 | −0.06 pp | [−0.12 pp, +0.01 pp] | not separated |
| terms: v3 minus v2's form at noon | the cold tail | 181 | −0.02 pp | [−0.54 pp, +0.81 pp] | not separated |
| terms: v3 minus v2's form at noon | the event windows | 920 | −0.15 pp | [−0.27 pp, −0.02 pp] | better |
| family: v2's form at noon minus v1's form at noon | all common hours | 8,778 | −0.84 pp | [−1.10 pp, −0.59 pp] | better |
| family: v2's form at noon minus v1's form at noon | the cold tail | 181 | −0.80 pp | [−1.35 pp, −0.54 pp] | better |
| family: v2's form at noon minus v1's form at noon | the event windows | 920 | −0.31 pp | [−0.49 pp, −0.10 pp] | better |
| the whole change at noon: v3 minus v1's form at noon (reported, never judged) | all common hours | 8,778 | −0.90 pp | [−1.14 pp, −0.66 pp] | better |
| the whole change at noon: v3 minus v1's form at noon (reported, never judged) | the cold tail | 181 | −0.82 pp | [−1.24 pp, −0.08 pp] | better |
| the whole change at noon: v3 minus v1's form at noon (reported, never judged) | the event windows | 920 | −0.46 pp | [−0.71 pp, −0.21 pp] | better |

| Horizon: v2's form at noon minus v2's form at midnight, reported, never judged (_seen by the developers_) | Hours | MAPE difference | 95% CI |
|---|---|---|---|
| the horizon's own hours (the target, v2's form at noon and v2's form at midnight) | 8,776 | +0.42 pp | [+0.33 pp, +0.53 pp] |
| the cold tail | 181 | +0.30 pp | [−0.92 pp, +0.74 pp] |
| the event windows | 920 | +0.64 pp | [+0.42 pp, +0.87 pp] |

- Verdicts (_seen by the developers_): terms "better", family "better".
- Days failing data quality: 0 of 366; the monitor's recommendations: retrain 1 times.

The busiest hours of 2024 (reported, never judged):

| Hour ending, UTC (_seen by the developers_) | Demand | Forecast temperature | v3 (production) | v2's form at noon | v1's form at noon | v2's form at midnight |
|---|---|---|---|---|---|---|
| 2024-01-19 13:00 UTC | 36,348 MW | −17.8 °C | −0.8% | −1.0% | +1.0% | −0.5% |
| 2024-01-19 14:00 UTC | 35,967 MW | −17.2 °C | +0.6% | +0.2% | +0.7% | +0.3% |
| 2024-01-19 23:00 UTC | 35,717 MW | −14.6 °C | +1.0% | +1.1% | +2.5% | +2.1% |
| 2024-12-23 14:00 UTC | 35,711 MW | −12.0 °C | +7.5% | +6.1% | +6.8% | +3.1% |
| 2024-01-20 00:00 UTC | 35,668 MW | −15.0 °C | +0.8% | +1.3% | +2.3% | +2.3% |


| 2024, winter (_seen by the developers_) | Days | W | w | k | v3 (production) | v2's form at noon | v1's form at noon | Windows not eligible |
|---|---|---|---|---|---|---|---|---|
| 2023-24 | 2024-01-01 to 2024-03-31 | 230 | 176 | 23 | 15 of 23 | 15 of 23 | 15 of 23 | 0 |
| 2024-25 | 2024-12-01 to 2024-12-31 | 230 | 54 | 7 | 5 of 7 | 5 of 7 | 5 of 7 | 0 |



## 4. 2025, the test of record

Pending. The test of record will be the first 2025 replay started under `v3-rules-frozen`, after v2's test of record and behind v3's 2025 gate (plan section 11.3); no 2025 number is shown before it.

## 5. v3's replay ledger

| Run | Year | Started | Label | MAPE | Coverage | Terms | Family | Standing |
|---|---|---|---|---|---|---|---|---|
| `fe5d0e5bf99f411fa3c814f6ff51628c` | 2024 | 2026-10-28T17:37:41.982Z | _seen by the developers_ | 2.95% | 92.1% | better | better | none |

## 6. Limitations

- The terms, the interval bins and the event windows were chosen after reading v2's results on the validation years: on those years, every result is in sample for these choices; only 2025 can test them.
- Every result that reads the forecast or the lag day's mean rests on the availability assumption (section 1).
- The rate's current terms are applied to every winter as a scoring convention, not as the terms in force at the time.
- Wind enters one term only; it is not watched for drift.

## 7. Provenance

Runs of record, named by `docs/freeze-v3.json` (commit `cc93edeba8fae4037a73e5f583de344375946fe8`, hash `4456590e4ca8`), under the tag `v3-rules-frozen` first pinned to commit `6ffedf1741ed`.
| Role | Run | Commit | Clean tree | Freeze tag | Rules |
|---|---|---|---|---|---|
| data quality | `1871de5a164e44fea60013b53fa6bf4b` | `fa4683323cb5` | yes | `v3-rules-frozen` | `526e7ee3484c` |
| validation | `df111835244c4c40a323db65a96c25a2` | `fa4683323cb5` | yes | `v3-rules-frozen` | `526e7ee3484c` |
| models frozen for 2024 | `7f2ee1c96fb64a9ba492a57d60211f0e` | `fa4683323cb5` | yes | `v3-rules-frozen` | `526e7ee3484c` |
| the 2024 rehearsal | `fe5d0e5bf99f411fa3c814f6ff51628c` | `fa4683323cb5` | yes | `v3-rules-frozen` | `526e7ee3484c` |
| models frozen for 2025 | `95beb8658ae84eee90a990890dfff3fb` | `fa4683323cb5` | yes | `v3-rules-frozen` | `526e7ee3484c` |

- The byte-copy check: passed.
- The reproduction check: passed.
- The exports the data-quality run read: Hydro-Québec retrieved on 2026-10-14, the dataset modified on 2026-06-05; ECCC retrieved from 2026-10-14 to 2026-10-14; NBM from 2026-10-17 to 2026-10-17.
