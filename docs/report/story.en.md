# Can a forecast of Québec's electricity demand be trusted?

_The short version of the validation report, in seven points: read this first._

> Demand data: Hydro-Québec, "Historique de la demande d'électricité au Québec" (https://donnees.hydroquebec.com/explore/dataset/historique-demande-electricite-quebec/), licensed CC BY-NC 4.0 (https://creativecommons.org/licenses/by-nc/4.0/). Hydro-Québec offers this data for information only, without warranty of quality; it is not official data. This project checked it, masked or corrected a few hours (each case documented) and aggregated it; it does not redistribute the dataset, and the reports quote only a few hourly values from it, as evidence.
>
> Weather data: Environment and Climate Change Canada, used under the Open Government Licence – Canada (https://open.canada.ca/en/open-government-licence-canada).
>
> Extracted from the Environment and Climate Change Canada Historical Climate Data web site ( https://climate.weather.gc.ca/index_e.html ) on 2026-10-14.
>
> Neither Hydro-Québec nor Environment and Climate Change Canada produced, reviewed or endorsed this work.
>
> Built with Claude Code under the repository owner's direction.

The [full report](validation.en.md) is the reference, and each point links to its section. Every number here comes from the same MLflow runs: code `3a67ac3d2e3f`, 2024 replay `9f87c124e34344a8a65e4a7679c3c458`. This English version is derived from the French one, which is the reference.

## The question

Can a simple forecast of Québec's hourly electricity demand be trusted, and how would you know when to stop trusting it? This project does not look for the best forecast: it checks this one the way a model-risk team would. It measures its error on months it has not seen, checks what its ranges are worth, then replays a whole year as production, under a monitor that can recommend investigating the data, recalibrating the ranges or retraining the model. The monitor recommends; it never acts. ([section 1](validation.en.md#1-the-methods-in-plain-words))

## The forecast

At the end of each day, it forecasts the next day's 24 hours from the hour and the type of day, the previous day's and the previous week's demand, and the previous day's temperature in Montréal, the only one known when the forecast is issued. Each month from 2020 to 2023 is forecast by a model fitted on the hours before it only. Over 2020–2023, the forecast is off by 4.42% of hourly demand on average, against 7.81% for a naive forecast (the same hour one week earlier): it does better, and the gap is confirmed. With the target hour's actually observed temperature, as if the weather were perfectly forecast, the average gap would be 3.40%: an optimistic bound, which the forecast does not have. ([section 2](validation.en.md#2-validation-on-20202023))

## Where it breaks: the cold

Its average gap runs from 2.61% in summer to 6.11% in winter; by forecast-load decile (ten bands, from the lightest to the heaviest), it runs from 2.59% (decile 1) to 5.98% (decile 10). At the 5 busiest hours of 2019–2023, all during the cold peak of 2023-02, demand stayed inside the 90% range at 0 of 5 hours judged; it went above it at 5. Even with the observed temperature, the forecast stays 10.8% to 12.7% below demand at those hours; the cause is not tested, and only one peak is studied. ([section 2](validation.en.md#2-validation-on-20202023), [section 3](validation.en.md#3-test-cases))

![Cold peak](figures/validation/validation-peak-hours.en.png)

## How sure it is

Each forecast comes with a range that promises to hold demand 90% of the time. From 2021-01 to 2023-12, it held it 90.6% of the time: the gap to the promise stays within the uncertainty of the measurement. It promises too much where demand falls outside it more often than promised, beyond that uncertainty: winter; forecast-load deciles 7, 8, 9 and 10. It is wider than it needs to be where demand stays inside it more often than promised, beyond that uncertainty: summer and fall; hour blocks day (10–15 h) and evening (16–20 h); forecast-load deciles 1, 2, 3, 4 and 5. The coverage thresholds of winter are drawn from weeks that already had this flaw: the monitor does not flag it as decay. ([section 4](validation.en.md#4-prediction-intervals), [section 8](validation.en.md#8-limitations))

## The real test: 2024

2024 was then replayed hour by hour, as production. The model, frozen on the data up to 2023-12-24, was not refitted, and every read went through a clock that refuses any later record. The monitor's rules had been frozen before 2024 was first replayed, then changed once after those results had been seen (below); this replay ran again after that change: it is **post hoc**. The average gap was 4.23%, and the 90% range held demand 89.4% of the time. The monitor recommended nothing all year. ([section 7](validation.en.md#7-2024-replayed-as-production))

![2024 replay](figures/validation/replay-2024-weekly.en.png)

## Is silence good news?

A silent monitor reassures only if it can see a real change. The report measured that by imposing sustained changes at a few fixed starts and replaying the real rules. On 2024, a sustained gap of ±6% or more between demand and forecast yielded "retrain" within 8 weeks at each of the 4 starts; a gap of ±2% yielded it at 1 of 8 cases. The forecast leans on the previous day's demand: it follows a lasting rise in demand, and leaves between the two only a gap much smaller than the rise itself. A sustained 10% rise yielded "retrain" within 10 weeks at 0 of 4 starts on 2023 (the year the rules were designed on), and at 1 of 4 on 2024 (out of sample, a measurement added after the results). Silence is therefore no proof: sustained changes went unnoticed at some of the starts measured. ([section 6](validation.en.md#6-the-frozen-model-rehearsed-on-2023), [section 7](validation.en.md#7-2024-replayed-as-production))

## The honesty ledger

The monitor's rules were changed once, on 2026-10-14, after the 2024 results had been seen. The departure is recorded in [`AGENTS.md`](../../AGENTS.md), and the full report shows the earlier replay beside the later one. The replays before and after applied the same frozen model. The 2024 result did not move: the same average gap (4.23%), the same coverage (89.4%) and no recommendation, before and after.

Every finished 2024 replay since the history rewrite recorded in `AGENTS.md` is entered in the replay ledger, [`docs/replay-ledger.json`](../replay-ledger.json): an entry is never rewritten, and a replay that disagrees with its entry makes the report refuse to render. ([section 9](validation.en.md#9-breaks-on-synthetic-data))

What the report does not show: an independent human review of the code (everything comes from AI agents under one owner's direction), the false drift-alarm rate in 2024, cold snaps as a class, nor publication delays or revisions of the data. ([section 8](validation.en.md#8-limitations))
