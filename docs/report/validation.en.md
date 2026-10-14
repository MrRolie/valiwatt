# Validating and monitoring a forecast of Québec's electricity demand

_Report generated from a chain of MLflow runs followed back from the 2024 replay (section 11), code `c2bf3b164863`, code hash `c5805d8516d3`, monitoring rules frozen at commit `8ad0fd012354` (tag `rules-frozen`). Every measured value comes from those runs; none is typed by hand. This English version is derived from the French one, which is the reference._

> Demand data: Hydro-Québec, "Historique de la demande d'électricité au Québec" (https://donnees.hydroquebec.com/explore/dataset/historique-demande-electricite-quebec/), licensed CC BY-NC 4.0 (https://creativecommons.org/licenses/by-nc/4.0/). Hydro-Québec offers this data for information only, without warranty of quality; it is not official data. This project checked it, masked or corrected a few hours (each case documented) and aggregated it; it does not redistribute the dataset, and the reports quote only a few hourly values from it, as evidence.
>
> Weather data: Environment and Climate Change Canada, used under the Open Government Licence – Canada (https://open.canada.ca/en/open-government-licence-canada).
>
> Extracted from the Environment and Climate Change Canada Historical Climate Data web site ( https://climate.weather.gc.ca/index_e.html ) on 2026-10-14.
>
> Neither Hydro-Québec nor Environment and Climate Change Canada produced, reviewed or endorsed this work.
>
> Built with Claude Code under the repository owner's direction.

## Model card

| | |
|---|---|
| **What it forecasts** | Québec's hourly electricity demand as published by Hydro-Québec, for the 24 hours of day D, issued at the end of day D−1 (Eastern Standard Time), with a 90% prediction interval. |
| **Intended use** | To show, on a real forecast, validation and monitoring of the kind a model-risk team does. |
| **Out of scope** | Any operational dispatch, any trading or pricing, any decision. Hydro-Québec publishes this data for information; it is not official data. |
| **Data** | Hydro-Québec hourly demand; hourly temperature at the Montréal station (ECCC). Designed on 2019–2023; 2024 replayed as production. |
| **Model** | Ordinary least squares on named features: one constant per hour and day type, heating and cooling (degree-hours, base 18 °C) per time-of-day block, and demand one day and one week earlier. Frozen on data up to 2023-12-24; version 9 of the registered model `valiwatt-demand-persistence` (alias `replay-2024`). |
| **Interval** | Forecast × (1 ± 9.26%): the conformal quantile of the relative out-of-sample errors of the previous 12 months. |
| **Validation error** | MAPE 4.42% [4.14%, 4.68%] over 2020–2023, against 7.81% for the naive baseline. |
| **2024 replay** | MAPE 4.23%, coverage 89.4%; no recommendation (section 7). |
| **Test cases** | Spring 2020: bias −1.86% against −0.60% in other springs, a gap not confirmed by the criterion set in advance. Cold peak of 2023-02: 0 of the 5 busiest hours inside the interval. |
| **Weather caveat** | Production uses the previous day's temperature, the only one known at issue time. The observed weather of the target hour, which stands in for a perfect weather forecast, is only an optimistic bound; it issues no recommendation. |
| **Limitations** | Section 8. |
| **Author** | Built with Claude Code under the repository owner's direction. |

## 1. The methods, in plain words

- **Walk-forward validation.** Each month from 2020 to 2023 is forecast by a model refitted on the hours before it only, minus the last 7 days (the embargo). Nothing from the tested month helps fit it.
- **The error.** Mean absolute percentage error (MAPE): on average, how far the forecast is from actual demand. The signed bias says in which direction.
- **The uncertainty of a mean.** Neighbouring hours share the weather, so whole weeks are resampled, 1,000 times, to get a 95% interval around each mean. "A beats B" is written only when the interval of the difference excludes zero.
- **The prediction interval (conformal).** Over the previous 12 months, look at each hour's relative error; the quantile that covers 90% of them becomes the half-width of next month's interval. Nothing is assumed about the shape of the errors; only that future errors resemble past ones, and whether they do is measured.
- **The frozen model.** In production the model is not refitted: it is fitted once, frozen with the hash of its coefficients, then replayed hour by hour.
- **The thresholds.** For each season, the weekly thresholds are quantiles of the validation weeks: MAPE above the 95th percentile, bias outside the 2.5th–97.5th percentiles, coverage below the 5th percentile.
- **Drift.** Each week, the distribution of temperatures and of forecasts is compared with the same weeks (± 2) of earlier years, using Evidently's normed Wasserstein distance. The alarm level is the 95th percentile of that distance over the validation weeks.
- **The decision table.** Each week, at most one recommendation, with its evidence: (1) a data-quality failure → investigate the data; (2) the same failure two weeks running (MAPE above its threshold, or bias beyond the same side of its band), broad across each week, or the bias beyond the same side of its band three weeks running → retrain; (3) coverage below its threshold two weeks running, broad, with bias inside its band → recalibrate the intervals; (4) the same input drifting two weeks running, with performance inside its thresholds → investigate the data. "Broad": the week stays beyond the threshold when its two most extreme days are set aside. One bad day never touches more than two days of a week (itself and, through the lags, the next day or the same day a week later), so it cannot trigger a recommendation unless the weeks already held failing days of their own. A bad Monday-to-Sunday week comes back the next week, through the week-old lag, with the opposite sign: not the same failure twice, so with its neighbouring weeks inside their bands it triggers nothing either. An episode of three days or more can trigger "retrain" on its own; section 6 measures how often. The monitor recommends; it never acts.

## 2. Validation on 2020–2023

48 monthly folds, 2020-01 to 2023-12, 7-day embargo.

| Forecast | MAPE | 95% CI | Signed bias | Hours |
|---|---|---|---|---|
| previous day's weather (production) | 4.42% | [4.14%, 4.68%] | +0.22% | 35,001 |
| observed weather (optimistic bound) | 3.40% | [3.29%, 3.51%] | +1.22% | 35,001 |
| same hour one week earlier | 7.81% | [7.14%, 8.44%] | −0.60% | 35,048 |
| previous day's weather, six-station mean | 4.46% | [4.17%, 4.73%] | −0.01% | 35,017 |

MAPE differences are in percentage points (pt).

- Previous day's weather against the naive baseline: −3.40 pt of MAPE [−3.93 pt, −2.91 pt], a confirmed gap.
- Observed weather against the previous day's: −1.01 pt [−1.27 pt, −0.73 pt]; this is the most a perfect weather forecast would add.
- Six-station mean against the primary station (a sensitivity run, never used to choose): +0.04 pt [+0.01 pt, +0.07 pt].

![Monthly MAPE](figures/validation/validation-monthly-mape.en.png)

**By season and by year** (previous day's weather):

| Group | MAPE | 95% CI | Bias | 95% CI of the bias |
|---|---|---|---|---|
| fall | 3.83% | [3.40%, 4.27%] | +0.38% | [−0.09%, +0.90%] |
| spring | 5.15% | [4.69%, 5.62%] | −1.42% | [−1.97%, −0.86%] |
| summer | 2.61% | [2.42%, 2.79%] | +0.83% | [+0.50%, +1.10%] |
| winter | 6.11% | [5.61%, 6.61%] | +1.12% | [+0.41%, +1.72%] |
| 2020 | 4.72% | [4.14%, 5.29%] | −0.31% | [−0.99%, +0.38%] |
| 2021 | 4.31% | [3.81%, 4.91%] | +0.45% | [−0.05%, +0.95%] |
| 2022 | 4.45% | [3.92%, 5.02%] | +0.49% | [−0.06%, +1.04%] |
| 2023 | 4.19% | [3.68%, 4.75%] | +0.27% | [−0.31%, +0.84%] |

**By decile of forecast load** (deciles are taken on the forecast, never on actual demand):

| Decile | MAPE | Bias |
|---|---|---|
| 1 | 2.59% | +1.09% |
| 2 | 3.29% | 0.00% |
| 3 | 3.12% | +0.53% |
| 4 | 3.16% | +0.33% |
| 5 | 3.55% | −0.23% |
| 6 | 4.91% | −0.64% |
| 7 | 5.92% | +0.77% |
| 8 | 5.90% | +0.60% |
| 9 | 5.75% | +0.40% |
| 10 | 5.98% | −0.60% |

**Coefficients of the model frozen for 2024.** Each coefficient says what a feature adds *given* demand one day and one week earlier; the fit without the lags, shown beside it for interpretation only, gives the total effect of the weather. The 72 constants (one per hour and day type) are not shown. No least-squares standard error is given: the errors are autocorrelated.

| Feature | Frozen model | Without lags |
|---|---|---|
| heating, night (0–5 h) (MW per degree-hour)  | 109.9 | 489.3 |
| cooling, night (0–5 h) (MW per degree-hour)  | 109.6 | 517.2 |
| heating, morning (6–9 h) (MW per degree-hour)  | 117.1 | 516.3 |
| cooling, morning (6–9 h) (MW per degree-hour)  | 64.5 | 315.4 |
| heating, day (10–15 h) (MW per degree-hour)  | 103.5 | 439.4 |
| cooling, day (10–15 h) (MW per degree-hour)  | 49.0 | 192.6 |
| heating, evening (16–20 h) (MW per degree-hour)  | 114.5 | 494.1 |
| cooling, evening (16–20 h) (MW per degree-hour)  | 52.6 | 210.0 |
| heating, late evening (21–23 h) (MW per degree-hour)  | 106.3 | 462.1 |
| cooling, late evening (21–23 h) (MW per degree-hour)  | 91.6 | 404.9 |
| demand one day earlier (share)  | 0.599 | — |
| demand one week earlier (share)  | 0.169 | — |

## 3. Test cases

**Spring 2020.** Months 3, 4, 5, 6: bias −1.86% [−3.06%, −0.71%] in 2020, against −0.60% [−1.12%, −0.11%] in the other years. The criterion set in advance (disjoint intervals) does not confirm the gap. Prediction intervals do not cover 2020: they need 12 months of calibration.

| Month | MAPE | Bias |
|---|---|---|
| 2020-03 | 5.41% | −1.50% |
| 2020-04 | 5.52% | −3.03% |
| 2020-05 | 4.72% | −2.48% |
| 2020-06 | 3.04% | −0.43% |
| 2021-03 | 7.76% | −0.96% |
| 2021-04 | 6.85% | −1.54% |
| 2021-05 | 3.48% | −0.73% |
| 2021-06 | 3.06% | +1.55% |
| 2022-03 | 5.40% | −0.75% |
| 2022-04 | 4.86% | −1.41% |
| 2022-05 | 3.01% | −0.50% |
| 2022-06 | 2.22% | +1.24% |
| 2023-03 | 4.31% | −0.94% |
| 2023-04 | 6.42% | −2.20% |
| 2023-05 | 4.10% | −1.07% |
| 2023-06 | 2.28% | +0.15% |

**Cold peak of 2023-02.** The 5 busiest hours of 2019–2023 fall in 2023-02. 0 of 5 are inside the 90% interval. This is a known conditional failure: a model linear in degree-hours, fed the previous day's temperature, under-forecasts a deepening cold snap.

![Cold peak](figures/validation/validation-peak-hours.en.png)

| Hour (UTC) | Demand | Temperature | Previous day's weather | Interval | Observed weather |
|---|---|---|---|---|---|
| 2023-02-03 23:00 UTC | 42,473 MW | −26.1 °C | 31,016 MW | 28,051–33,981 | 37,062 MW |
| 2023-02-04 00:00 UTC | 42,254 MW | −26.2 °C | 30,964 MW | 28,004–33,924 | 37,033 MW |
| 2023-02-04 01:00 UTC | 41,799 MW | −26.3 °C | 30,435 MW | 27,526–33,344 | 36,591 MW |
| 2023-02-04 02:00 UTC | 41,141 MW | −26.4 °C | 29,650 MW | 26,816–32,484 | 35,940 MW |
| 2023-02-03 22:00 UTC | 40,585 MW | −26.1 °C | 29,910 MW | 27,051–32,769 | 36,190 MW |

## 4. Prediction intervals

Nominal 90%, from 2021-01.

| Form | Coverage | 95% CI | Mean half-width |
|---|---|---|---|
| relative, one quantile (production) | 90.6% | [88.7%, 92.5%] | 9.99% |
| relative, one quantile per season (sensitivity) | 90.0% | [88.5%, 91.6%] | 9.63% |
| absolute (sensitivity) | 90.1% | [87.9%, 92.4%] | 11.93% |
| relative, observed weather | 90.2% | [88.9%, 91.4%] | 7.60% |

Overall coverage is close to nominal; conditional coverage is not. Winter and the high-load deciles are under-covered, summer is over-covered. One quantile per season fixes most of it (a sensitivity run). The production form stays the one fixed before validation; changing it after seeing the results would make them depend on the choice.

![Coverage by season](figures/validation/validation-coverage-by-season.en.png)

| Season | One quantile | One quantile per season |
|---|---|---|
| winter | 80.0% | 89.0% |
| spring | 87.9% | 88.9% |
| summer | 99.4% | 90.8% |
| fall | 94.8% | 91.4% |

| Decile of forecast load | Coverage (one quantile) |
|---|---|
| 1 | 98.5% |
| 2 | 96.7% |
| 3 | 96.7% |
| 4 | 97.3% |
| 5 | 96.0% |
| 6 | 90.9% |
| 7 | 82.1% |
| 8 | 81.4% |
| 9 | 84.2% |
| 10 | 82.5% |

## 5. Monitoring thresholds

Drawn from the 2021–2023 validation weeks (the 2023 replay uses 2021–2022 only). Weeks run Monday to Sunday, standard time; a week with fewer than 120 scored hours is not judged.

| Season | MAPE above | Bias outside | Coverage below | Weeks |
|---|---|---|---|---|
| winter | 8.31% | −3.64% to +5.45% | 61.7% | 38 |
| spring | 8.32% | −5.19% to +2.09% | 65.1% | 39 |
| summer | 3.75% | −0.48% to +2.60% | 96.4% | 40 |
| fall | 6.00% | −1.45% to +4.10% | 82.0% | 39 |

Drift: alarm above 1.250 for temperature and 1.034 for the forecast.

## 6. The frozen model, rehearsed on 2023

Before replaying 2024, the same procedure is rehearsed on the design years: a model frozen on data up to 2022-12-24, thresholds from 2021–2022, and 2023 replayed hour by hour.

| | Frozen model | Folds refitted each month |
|---|---|---|
| MAPE | 4.18% | 4.19% |
| Bias | +0.34% | +0.27% |
| Coverage | 92.2% | 90.4% |
| Weeks with MAPE above threshold | 1 | 1 |
| Weeks with bias above its band | 1 | 1 |
| Weeks with bias below its band | 3 | 3 |
| Weeks with low coverage | 0 | 0 |
| Recommendations | 0 | 0 |

MAPE difference (frozen minus refitted), on the same 8,739 hours: −0.01 pt [−0.02 pt, 0.00 pt]: no confirmed gap; freezing the model for a year costs no measurable accuracy on 2023.

**Why "the same failure, broad".** Under the first wording of rule 2 (MAPE or bias out of band two weeks running, in any mix), the monitor would have recommended retraining in the week of 2023-02-06 (bias +4.92% then −5.47%), then in the week of 2023-02-27 (bias +5.93% then −3.93%). Each time the bias changes sign from one week to the next: the profile of a passing gap that the 24-hour and 168-hour lags carry into the following days; a degraded model would rather keep an error of the same sign. The rule kept needs the same failure two weeks running, broad in each week: one day is never enough unless the weeks already held failing days; an episode of several days can be, and how often is measured below.

**The rules' power on 2023.** From the first full week of each season, a sustained change is imposed and the weekly rules are run again on the 2023 hours: a gap between demand and forecast (−8% to +8%), counted in weeks until "retrain", or intervals narrowed (half-width × 0.9 to × 0.5), counted in weeks until "recalibrate" (—: nothing within 8 weeks). A measurement, never a recommendation.

| Starts | −8% | −6% | −4% | −2% | +2% | +4% | +6% | +8% | 
|---|---|---|---|---|---|---|---|---|
| 2023-01-02 (winter) | 3 | — | — | — | — | — | 2 | 2 | 
| 2023-04-03 (spring) | 2 | 2 | 5 | — | — | — | 5 | 4 | 
| 2023-07-03 (summer) | 2 | 2 | 3 | 3 | — | 3 | 2 | 2 | 
| 2023-10-02 (fall) | 2 | 2 | 2 | — | — | 6 | 2 | 2 | 

| Starts | × 0.9 | × 0.8 | × 0.7 | × 0.6 | × 0.5 | 
|---|---|---|---|---|---|
| 2023-01-02 (winter) | — | — | — | — | — | 
| 2023-04-03 (spring) | — | — | — | — | 4 | 
| 2023-07-03 (summer) | — | — | — | — | — | 
| 2023-10-02 (fall) | — | — | — | — | 6 | 

**False alarms from short episodes.** An episode is not a model failure. Demand is multiplied by a factor for 1, 3, 5 or 7 days, from every day of 2023 (from 2023-01-02 on, as long as the episode ends at least two weeks before the end of the year, so that its echo is judged), and each case is replayed hour by hour by the real monitor. Any "retrain" or "recalibrate" that follows is a false alarm; it is *clean* when none of the weeks it rests on was already beyond any of its performance thresholds in 2023 as it was. The last column counts the starts the echo exclusion keeps from "retrain".

| Length | Factor | Starts | "Retrain" | of which clean | "Recalibrate" | Kept by the echo exclusion |
|---|---|---|---|---|---|---|
| 1 d | × 0.70 | 350 | 0 (0.0%) | 0 | 0 | 0 |
| 1 d | × 0.85 | 350 | 0 (0.0%) | 0 | 0 | 0 |
| 1 d | × 1.15 | 350 | 0 (0.0%) | 0 | 0 | 0 |
| 1 d | × 1.30 | 350 | 0 (0.0%) | 0 | 0 | 0 |
| 3 d | × 0.70 | 348 | 1 (0.3%) | 1 | 0 | 2 |
| 3 d | × 0.85 | 348 | 0 (0.0%) | 0 | 0 | 0 |
| 3 d | × 1.15 | 348 | 0 (0.0%) | 0 | 0 | 0 |
| 3 d | × 1.30 | 348 | 1 (0.3%) | 0 | 0 | 0 |
| 5 d | × 0.70 | 346 | 32 (9.2%) | 19 | 0 | 45 |
| 5 d | × 0.85 | 346 | 0 (0.0%) | 0 | 0 | 0 |
| 5 d | × 1.15 | 346 | 0 (0.0%) | 0 | 0 | 0 |
| 5 d | × 1.30 | 346 | 4 (1.2%) | 3 | 0 | 35 |
| 7 d | × 0.70 | 344 | 112 (32.6%) | 85 | 0 | 58 |
| 7 d | × 0.85 | 344 | 7 (2.0%) | 5 | 0 | 2 |
| 7 d | × 1.15 | 344 | 1 (0.3%) | 1 | 0 | 5 |
| 7 d | × 1.30 | 344 | 19 (5.5%) | 16 | 0 | 75 |

No one-day shock gave an alarm: one bad day spoils at most two days of a week, which the trim sets aside, so it can only complete failing days the weeks already held. An episode of several days does more: through the day-old lag it spoils one day more than it lasts, and through the week-old lag as many days the following week, so from three days on both weeks can be broad on the same failure with no failing days of their own. When one of the two weeks' bias stays inside its band (a band wide on that side, or a week that also holds days of the episode, as when it crosses a Monday), the echo exclusion does not engage. Weekly statistics cannot tell such an episode from two weeks of model failure; the rules stay as written, and this table gives the price.

**A shift in the level of demand.** Demand is multiplied by a factor from the first full week of each season, for 10 weeks (weeks until "retrain"; —: nothing within 10 weeks). The persistence forecast follows demand through its lags, so a shift in level leaves a gap between forecast and demand much smaller than the shift itself. The power table measures that gap; this one measures the shift in level.

| Starts | −10% | −5% | +5% | +10% | 
|---|---|---|---|---|
| 2023-01-02 (winter) | — | — | — | — | 
| 2023-04-03 (spring) | 2 | — | — | — | 
| 2023-07-03 (summer) | 2 | 4 | — | — | 
| 2023-10-02 (fall) | 2 | — | — | — | 

**What the echo exclusion costs.** From the first full week of each season, for 8 weeks, the forecast takes a random error drawn each day from a normal law: a degraded model whose weekly bias can change sides. The rules are run with and without the exclusion.

| Daily standard deviation | Runs | "Retrain" with the exclusion | without it | Suppressed by the exclusion | Delayed |
|---|---|---|---|---|---|
| 6% | 80 | 19 | 21 | 2 | 0 |
| 10% | 80 | 44 | 44 | 0 | 8 |
| 15% | 80 | 74 | 76 | 2 | 12 |

![2023 replay](figures/validation/pseudo-replay-2023-weekly.en.png)

## 7. 2024 replayed as production

The clock advances hour by hour from 2024-01-01 to 2024-12-31; every read goes through an accessor that refuses any record later than the clock. The model and its intervals are checked by hash before and after: `c8e1725aad77`, unchanged.

| | |
|---|---|
| MAPE | 4.23% [3.70%, 4.84%] |
| Bias | +0.31% [−0.28%, +0.91%] |
| Coverage (nominal 90%) | 89.4% [85.1%, 92.9%] |
| Hours forecast / scored | 8,760 / 8,758 |
| Days failing data quality | 0 of 366 |
| Weeks judged | 52 of 53 |

**Recommendations.**

No week of 2024 triggered a recommendation.

Under the first wording of rule 2: "retrain" in the week of 2024-11-04.

**The rules' power on 2024**, measured the same way (weeks until "retrain", then until "recalibrate"; —: nothing within 8 weeks):

| Starts | −8% | −6% | −4% | −2% | +2% | +4% | +6% | +8% | 
|---|---|---|---|---|---|---|---|---|
| 2024-01-01 (winter) | 6 | 6 | — | — | — | — | 3 | 2 | 
| 2024-04-01 (spring) | 2 | 2 | 6 | — | — | 8 | 3 | 2 | 
| 2024-07-01 (summer) | 2 | 2 | 2 | — | — | 2 | 2 | 2 | 
| 2024-10-07 (fall) | 2 | 2 | 2 | 3 | — | — | 3 | 2 | 

| Starts | × 0.9 | × 0.8 | × 0.7 | × 0.6 | × 0.5 | 
|---|---|---|---|---|---|
| 2024-01-01 (winter) | — | — | — | — | 3 | 
| 2024-04-01 (spring) | — | — | — | — | 2 | 
| 2024-07-01 (summer) | — | — | — | — | — | 
| 2024-10-07 (fall) | 5 | 5 | 5 | 3 | 3 | 

![2024 replay](figures/validation/replay-2024-weekly.en.png)

![2024 drift](figures/validation/replay-2024-drift.en.png)

For each week with a drift alarm, the Evidently report is kept in the run, and Evidently's value is compared with the monitor's: 4 comparisons, all equal.

## 8. Limitations

- **Conditional coverage.** The production interval under-covers winter and high loads; the winter coverage thresholds inherit that under-coverage, so the monitor does not flag it as decay.
- **Extreme cold.** The 2023-02 peak is not covered; the model under-forecasts deepening cold snaps.
- **Level drift.** Weather-adjusted demand has risen since 2021; the lags carry part of it, the rest shows up as bias, which the monitor watches.
- **Episodes of several days.** An episode of several days can yield "retrain" with nothing wrong in the model: on 2023, up to 32.6% of starts for 7 days at × 0.70, and at most 2.0% for an episode of ±15% (section 6).
- **Shift in level.** A sustained 10% rise in demand did not yield "retrain" within 10 weeks at 4 of 4 season starts: persistence absorbs most of it through its lags; a 10% fall did at 3 of 4. A fall leaves a larger relative gap than a rise of the same size, since the bias is measured against the lower demand; where 2023's weekly bias sat within each season's band does the rest. The power tables measure a gap between forecast and demand, not a shift in level.
- **Echo exclusion.** On a degraded model whose bias changes sides, it suppresses "retrain" in 4 of 240 runs and delays it in 20 (section 6); that is the price of avoiding the false alarms of an episode followed by its echo.
- **Real-time data.** Values are as downloaded in 2026; no publication delay and no revision are modelled.
- **Independence.** The model, the checks and this report come from one AI agent session under one owner's direction, with no independent human review of the code. The reviews were done by fresh-context agents, which is not independent validation in the SR 11-7 sense.

## 9. Breaks: every check is seen to fire

Each gate and monitor has a test that injects the fault it exists to catch. The `breaks` run executes them all; the report refuses to render if any of them did not fire.

| Check | Break | Result |
|---|---|---|
| Data-quality gates | A duplicate of no known shape fails the gate | fired |
| Data-quality gates | An ambiguous misplaced record is not moved and fails the gate | fired |
| Data-quality gates | A record outside the window fails the gate | fired |
| Data-quality gates | A demand value out of bounds fails the gate | fired |
| Data-quality gates | Coverage below its threshold fails the gate | fired |
| Data-quality gates | Duplicate weather rows fail the index gate | fired |
| Data-quality gates | An impossible temperature fails the bounds gate | fired |
| Data-quality gates | A sensor outage fails the coverage gate | fired |
| Data-quality gates | An outage shared by several stations fails the panel gate | fired |
| Data-quality gates | A failing gate stops the chain: no canonical data is written | fired |
| Data-quality gates | Raw files that no longer match their manifest are refused | fired |
| Data-quality gates | A record off the hourly grid fails the window gate, with its timestamp | fired |
| Data-quality gates | An out-of-bounds value inside a fall-back pair still fails the bounds gate | fired |
| Data-quality gates | An out-of-bounds value is set aside before a repair reads it | fired |
| Data-quality gates | An out-of-bounds value makes no spike next to it | fired |
| No lookahead | An input read after the forecast's origin is refused | fired |
| No lookahead | A training row inside the embargo is refused | fired |
| No lookahead | A 2024 row is refused by design code | fired |
| No lookahead | Validation refuses a 2024 row | fired |
| No lookahead | A drift reference holding replayed days is refused | fired |
| No lookahead | Thresholds built on the replayed year are refused | fired |
| No lookahead | A replay's drift thresholds ignore the replayed year | fired |
| No lookahead | The frozen model refuses training rows inside its embargo | fired |
| Replay clock | Records corrupted after a cut change nothing judged before it | fired |
| Replay clock | A record that arrives a week late is not used before it arrives | fired |
| Replay clock | Reading past the clock, or turning it back, is refused | fired |
| Replay clock | A forecast issued after its origin is refused | fired |
| Monitors and decision table | Control: clean data yield no recommendation | fired |
| Monitors and decision table | The seasonal reference stays quiet on clean data and fires on a shift | fired |
| Monitors and decision table | Temperature drift with performance inside its thresholds: investigate the data | fired |
| Monitors and decision table | A sensor gap fails the day's data quality and suspends the performance rules | fired |
| Monitors and decision table | A missing and a duplicated demand record fail their day | fired |
| Monitors and decision table | A low week (−30%) and its echo through the one-week lag do not yield retrain | fired |
| Monitors and decision table | A high week (+30%) and its echo, MAPE high in both weeks, do not yield retrain | fired |
| Monitors and decision table | MAPE high two weeks with bias on opposite sides is an echo; without the flip it fires | fired |
| Monitors and decision table | Bias beyond the same side three weeks running yields retrain; an echo or an unjudged week breaks it | fired |
| Monitors and decision table | The power measure finds a sustained 8% gap or intervals cut to 20%, and nothing without a change | fired |
| Monitors and decision table | The level-shift measure finds a 12% rise in demand, and nothing without a change | fired |
| Monitors and decision table | The episode measure: a one-day shock brings no false alarm, and every start is counted | fired |
| Monitors and decision table | The episode measure finds the false alarm of five days across a Monday, and the echo the exclusion keeps out | fired |
| Monitors and decision table | A false alarm resting on a week already out of band is called assisted; an alarm the year already had is not counted | fired |
| Monitors and decision table | The echo exclusion only ever removes or delays retrain | fired |
| Monitors and decision table | Two altered days never lift the trimmed statistic past the untouched days (2,000 draws) | fired |
| Monitors and decision table | Partial weeks have no drift statistic | fired |
| Monitors and decision table | The last day is judged at the end of the data, and says so | fired |
| Monitors and decision table | Duplicated temperatures and an off-grid demand record fail their day | fired |
| Monitors and decision table | A shift in the demand level: retrain | fired |
| Monitors and decision table | Intervals made too narrow: recalibrate | fired |
| Monitors and decision table | Two rules at once: one recommendation with both pieces of evidence | fired |
| Monitors and decision table | A one-day shock (Saturday) does not yield retrain | fired |
| Monitors and decision table | A one-day shock (Sunday) does not yield retrain | fired |
| The monitor never acts | A replay that changes the model or its intervals fails | fired |
| Frozen rules | Without the freeze tag, the 2024 replay is refused | fired |
| Frozen rules | A rule file edited after the freeze is refused and named | fired |
| Frozen rules | A rule file added after the freeze is refused | fired |
| Frozen rules | A moved freeze tag is refused unless recorded as a departure | fired |
| Frozen rules | Earlier replays are found even when deleted | fired |
| Frozen rules | Raw files other than the ones checked are refused | fired |
| Frozen rules | A registered model that changed during the replay fails it | fired |
| Frozen rules | A frozen model from another validation run, year or rule set is refused | fired |
| Frozen rules | A validation or data-quality run made on other data or other rules is refused | fired |
| Frozen rules | A frozen model file that no longer matches its hash is refused | fired |
| Frozen rules | The registered model is refused when its run records another hash | fired |
| Report rendering | A report whose code changed since its run is refused | fired |
| Report rendering | The public-safety scan fires on private content | fired |
| Report rendering | A figure whose metadata carries a private path, or with bytes hidden after its end, is refused | fired |

## 10. Mapping to SR 11-7 and the NIST AI RMF

The quotations, with their sections and pages, are in `docs/standards.md`. The table says what the checks cover, nothing more.

| Expectation | What valiwatt does | Where it is checked |
|---|---|---|
| SR 11-7, conceptual soundness, including sensitivity analysis | Configuration fixed in the plan before validation; named features, coefficients shown beside a fit without the lags; sensitivity runs: six stations, absolute interval, one quantile per season, the first wording of rule 2. | Plan, section 2; validation and frozen-model runs |
| SR 11-7, data quality and relevance | 23 data-quality gates over the full window, each repair documented; the station's representativeness; in production, a daily data-quality monitor on the raw records. | Data-quality report; replays |
| SR 11-7, ongoing monitoring: process verification | Lookahead guards; a clock that refuses later records; the model's hash checked before and after; the 2024 replay refuses to run if the rules differ from the `rules-frozen` tag. Each guard has its break. | Section 9 |
| SR 11-7, ongoing monitoring: benchmarking | Naive baseline; observed weather as an optimistic bound; frozen model against refitted folds on 2023 (a parallel outcomes analysis). | Sections 2 and 6 |
| SR 11-7, outcomes analysis and back-testing, with intervals | Monthly folds with embargo; coverage measured overall, by season, by hour block and by decile; weekly statistics judged against thresholds fixed in advance. | Sections 2, 4, 6, 7 |
| SR 11-7, thresholds of acceptability, then recalibration or redevelopment | Per-season thresholds from earlier weeks; an ordered decision table (investigate, recalibrate, retrain), with evidence; recommends without acting. | Sections 5 and 7 |
| SR 11-7, documentation and validation reports | This report and its model card, rendered from the runs and stamped with their IDs and the code hash. | This report |
| NIST MAP 2.2, 2.3 | Intended use, out of scope, weather caveat, limitations; validation design and data-quality findings documented. | Model card; plan |
| NIST MEASURE 1.1, 2.1, 2.3, 2.4, 2.5, 2.13 | MLflow runs (test sets, metrics, parameters, versions); replays under production conditions; breaks; the "not covered" list below. | Runs; section 9 |
| NIST MANAGE 2.2, 2.4 | Weekly recommendations with evidence; who acts on them, and how, is out of scope. | Section 7 |

**Not covered.** Independent validation and effective challenge (SR 11-7, V; NIST MEASURE 1.3); governance, policies and controls, internal audit, model inventory (SR 11-7, VI; most of NIST GOVERN); periodic review; override analysis; use in decisions and materiality; independent review of a recalibration; NIST MANAGE 4.1 and 1.4 beyond the model card's limitations; real-time data.

## 11. Provenance

| Run | ID | Date |
|---|---|---|
| data quality | `acabb0f7c15446ddbd44f264889b9b83` | 2026-10-14 10:44 UTC |
| validation | `eaa2b2a5d89045288dd9663a2dd34e3e` | 2026-10-14 10:45 UTC |
| model frozen for 2023 | `e102fa42655e404fb27921570f84f56a` | 2026-10-14 10:45 UTC |
| model frozen for 2024 | `7e8bfcae5e494227a5e36c35ec2a2ff2` | 2026-10-14 11:09 UTC |
| 2023 replay | `55047fb2d73b46d7bbd815ec0f0ae2af` | 2026-10-14 11:09 UTC |
| 2024 replay | `9ef349267e31437f9d81481a474d8d90` | 2026-10-14 11:09 UTC |
| breaks | `1c6d4d6a828b480c967ca99816d8411c` | 2026-10-14 11:13 UTC |

Code hash: `c5805d8516d3c0e191a53f5d74d10fa770ccf3c1d26df40978ebff210578d6c9`. Rules hash: `addf19afe5cd7959702740426462862989d5f377967077cdaf7099568fcd40dc`. `tests/test_report_stamp.py` fails when the repository's code no longer matches this hash.
