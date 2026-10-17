# valiwatt

valiwatt validates and monitors a forecast of Québec's hourly electricity demand the way a model-risk team would.
The project is about the checks around the forecast: how the model is tested before it is trusted, how it is watched
once it runs, and what it says when it should no longer be trusted.

**What it measures.** The validation report measures the forecast's error on 2020–2023, out of sample, against the
model v1 shipped and against v1's form on the same weather forecast, under a decision rule fixed before any v2 replay;
how often its prediction intervals cover the actual demand, overall and group by group; what the monitor recommended
when 2023 was rehearsed and 2024 replayed hour by hour under rules frozen before either replay; and how often the rules
raise a false alarm or miss a real change. Every number comes from a tracked run, and every result carries its
evaluation label: 2020–2023 served to choose the model, and 2024 had been seen by the developers through v1 before v2
was chosen, so neither proves v2 better. Only 2025, held out, can.

**Status: v2.** The model card's status is in the validation report, never here: it reads *candidate* until a 2025
replay starts; then that the 2025 test of record did not finish, while it has not; then what that test showed, under
the decision rule of [`docs/plan-v2.md`](docs/plan-v2.md). v1, the first version, stays in the history at the tag
`v1`, its report as published included. What is built:

- the data layer and its gates, the weather forecast included;
- a day-ahead forecast: an additive model of the GAM family on NOAA's National Blend of Models temperature forecast,
  one temperature curve per hour, with the day before's and the week before's demand; walk-forward validation beside
  two comparison models (v1 as shipped, and v1's form on the same forecast); prediction intervals of split-conformal
  shape (without that method's finite-sample coverage guarantee: their coverage is measured out of sample);
- frozen models registered in MLflow;
- a monitor that replays a year hour by hour and recommends *investigate the data*, *recalibrate* or *retrain*, its
  rules frozen by the `rules-frozen-v2` tag before any v2 replay;
- the rules' false alarms and blind spots, measured after each replay in a diagnostic run of their own;
- a gate in front of the 2025 replay that finds it clean or refuses it; only a dated ruling of the owner, recorded in
  `AGENTS.md`, lifts a refusal.

Every gate and monitor has a break that makes it fire.

Read the short version first: the findings told in seven plain points (the question, the forecast, the cold, how
sure it is, 2024 replayed but already seen, what the monitor's silence is worth, and what changed since v1 with the
2025 criteria), each linking to the section of the full report that carries the rest:
[French](docs/report/story.fr.md), the reference; [English](docs/report/story.en.md). The full reports, rendered from
the same runs, are the reference behind it; each opens with an « En bref » block ("In brief" in English):

- validation and monitoring, with the model card and a mapping to SR 11-7 and the NIST AI RMF:
  [French](docs/report/validation.fr.md), the reference; [English](docs/report/validation.en.md);
- data quality: [French](docs/report/dq.fr.md); [English](docs/report/dq.en.md).

**The weather forecast.** The forecast is issued at the end of the day before from a temperature forecast already
issued then: NOAA's National Blend of Models NBS guidance for Montréal-Trudeau (CYUL), from the Iowa Environmental
Mesonet's archive, interpolated to the hour and converted to degrees Celsius. The archive records no arrival time, so
a forecast is assumed available two hours after its issue time; the report measures what a later margin would cost.
The report also gives the bound with the observed weather of the target hour, which stands in for a perfect weather
forecast; that bound is optimistic, and it issues no recommendation.

## Run it

```sh
uv sync
uv run valiwatt all              # fetch the data, run every check, render the report (see below)
uv run pytest -m "not network"   # the offline tests, every injected break included
uv run pytest -m network         # the live tests only: the public endpoints and the quoted licence and standard texts
```

A plain `uv run pytest` runs both sets.

`valiwatt all` downloads the public data into `data/`, then runs the 2024 chain as tracked MLflow runs (stored in
`mlruns/`): data quality, walk-forward validation on 2020–2023 with models fitted from 2019, the frozen models, the
2023 rehearsal and the 2024 replay, and the breaks. It renders the reports into `docs/report/` (French, the reference,
and English), stamped with the IDs of the runs they come from and the hash of the code that made them; a test fails
when the code no longer matches the stamp. It never replays 2025. Neither `data/` nor `mlruns/` is in git: anyone can
regenerate every figure, and the runs, not the rendered files, are the source of truth.

Steps can also run one at a time: `valiwatt fetch`, `dq`, `validate`, `freeze --year Y`, `replay --year Y`, `breaks`,
`ledger`, `report`. A replay refuses to run until the monitoring rules are frozen by the `rules-frozen-v2` tag.
`valiwatt safety` scans what git stages and every object in the history for anything that must not be published.

**2025, once Hydro-Québec publishes it.** No 2025 demand is read before then. Afterwards: `valiwatt fetch --through
2025` and `valiwatt dq --through 2025`, then `valiwatt replay --year 2025`, which runs only by that command and only
behind its gate. The gate finds the start clean or refuses it with its evidence; no flag lifts a refusal, only the
owner's ruling entry in `AGENTS.md` does, and `valiwatt evidence` produces what he rules on, deciding nothing. Every
start is recorded, and the earliest is the test of record, unless it stopped before it finished and a ruling of the
owner names the start that takes its place. Nothing of the 2025 vintage is rendered before the first 2025 start.

**A later vintage.** A clone that fetches after Hydro-Québec has published 2025 reads a later vintage of 2019–2024,
whose records Hydro-Québec may have revised: its numbers may differ from the committed reports, and the 2025
data-quality run lists every revised record. Such a clone's store lacks the replays of record: the report shows them
from their entries in the committed replay ledger, labelled as recorded in the ledger and not re-run here, and says
what only the original store holds.

The replay ledger, [`docs/replay-ledger.json`](docs/replay-ledger.json), is committed with each report. It holds one
entry per finished replay of 2024 and 2025: its run ID, freeze tag and commit, rules and code hashes, start and end
times, MAPE, coverage and recommendations, and for v2 its evaluation label, its model's hash, its verdicts, the hash of
the frozen artifacts that judged it and, for 2025, its standing. v1's entries stay as they were. `valiwatt report`
appends the replays it lacks before it renders (`valiwatt ledger` does only that); an entry is never rewritten, a run
that disagrees with its entry is refused, and the new content must pass the public-safety scan before it is written.
Its hash is in the report's stamp, so a test fails when it changes after the render.

How it is built, and why each choice was made before any v2 result was seen: [`docs/plan-v2.md`](docs/plan-v2.md)
(v1's plan: [`docs/plan.md`](docs/plan.md)). The standards the report maps to, quoted and checked against their PDFs:
[`docs/standards.md`](docs/standards.md).

## The rails

The rules every part of the project follows.

- **No lookahead.** Design choices use 2019 to 2023 only; 2024 and 2025 are replayed as production, and the code
  refuses design analysis that reaches into them. Validation is walk-forward with an embargo. A forecast is used only
  from the time it is assumed available, and any result that leans on observed weather in place of a weather forecast
  says so. The replay reads raw records, forecasts included, through a clock that refuses anything later.
- **A check that cannot fail is not a check.** Every gate and monitor has a test that injects the fault it must catch:
  a duplicated hour, a misplaced record, an impossible value, a sensor outage, a day no forecast fed, a reading past
  the clock, a shifted demand level, intervals made too narrow, a moved freeze tag. `valiwatt breaks` runs them all as
  a tracked run, and the report refuses to render if one did not fire.
- **Numbers come from runs.** The reports are rendered from tracked MLflow runs; no number in them is typed by hand.
- **Claims stay within the checks.** Each report ends with what it does not show.
- **The monitor recommends; it never acts.** When a threshold trips, it writes a recommendation with its evidence;
  nothing retrains on its own.

## Data

- **Demand:** Hydro-Québec open data, "Historique de la demande d'électricité au Québec", hourly, from 2019.
- **Weather forecast:** NOAA's National Blend of Models NBS guidance for station CYUL (Montréal-Trudeau), from the
  Iowa Environmental Mesonet's archive.
- **Observed weather:** Environment and Climate Change Canada hourly station observations from climate.weather.gc.ca,
  for one station in each of six population centres (`config/stations.toml`), for data quality, v1's model and the
  optimistic bound.

Data is fetched by script and never committed. The licence terms of every source, quoted with their URLs, are in
[`docs/data-licences.md`](docs/data-licences.md). Each report also gives the reference to ECCC's site in the form its
FAQ asks for, with the date the data was retrieved.

> Demand data: Hydro-Québec, "Historique de la demande d'électricité au Québec" (https://donnees.hydroquebec.com/explore/dataset/historique-demande-electricite-quebec/), licensed CC BY-NC 4.0 (https://creativecommons.org/licenses/by-nc/4.0/). Hydro-Québec offers this data for information only, without warranty of quality; it is not official data. This project checked it, masked or corrected a few hours (each case documented) and aggregated it; it does not redistribute the dataset, and the reports quote only a few hourly values from it, as evidence.
>
> Weather data: Environment and Climate Change Canada, used under the Open Government Licence – Canada (https://open.canada.ca/en/open-government-licence-canada).
>
> Weather forecasts: NOAA National Blend of Models NBS guidance for station CYUL (Montréal-Trudeau), downloaded from the Iowa Environmental Mesonet archive of Iowa State University (https://mesonet.agron.iastate.edu/). This project interpolated these three-hourly forecasts to hourly values and converted them to degrees Celsius: the values it uses are modified and are not original NOAA data.
>
> Neither Hydro-Québec, Environment and Climate Change Canada, NOAA nor the Iowa Environmental Mesonet produced, reviewed or endorsed this work, and the project is affiliated with neither NOAA nor the Iowa Environmental Mesonet.
>
> Built with Claude Code under the repository owner's direction.

## Licence

The code is under the MIT licence ([`LICENSE`](LICENSE)). The data is not part of this repository and keeps its own
licences ([`docs/data-licences.md`](docs/data-licences.md)).

## Continuous integration

`.github/workflows/checks.yml` runs the offline tests (every injected break included) and the public-safety scan on
every branch push and pull request. CI's scan covers paths, credentials, forbidden files, every file in the history and every commit and tag
message, but not private names: those live in a gitignored list, so only the local `valiwatt safety` checks them.
