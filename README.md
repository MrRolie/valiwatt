# valiwatt

valiwatt validates and monitors a forecast of Québec's hourly electricity demand the way a model-risk team would.
The forecast is deliberately simple. The project is about the checks around it: how the model is tested before it is
trusted, how it is watched once it runs, and what it says when it should no longer be trusted.

**Status: v1, rendered on the frozen rules.** The data layer and its gates; a day-ahead forecast (ordinary least
squares on named features) with walk-forward validation, conformal prediction intervals and two test cases; a frozen
model registered in MLflow; a monitor that replays a year hour by hour and recommends *investigate the data*,
*recalibrate* or *retrain*, its rules frozen by the `rules-frozen` tag before 2024 was replayed; and the rules' false
alarms and blind spots, measured on the 2023 rehearsal. Every gate and monitor has a break that makes it fire.

The reports, with the model card and a mapping to SR 11-7 and the NIST AI RMF: validation and monitoring
([French](docs/report/validation.fr.md), the reference; [English](docs/report/validation.en.md)) and data quality
([French](docs/report/dq.fr.md); [English](docs/report/dq.en.md)).

## Run it

```sh
uv sync
uv run valiwatt all      # fetch the data, run every check, render the report
uv run pytest            # the checks' own tests (add -m network for the live-endpoint tests)
```

`valiwatt all` downloads the public data into `data/`, then runs the whole chain as tracked MLflow runs (stored in
`mlruns/`): data quality, validation on 2019–2023, the frozen models, the 2023 rehearsal and the 2024 replay, and the
breaks. It renders the reports into `docs/report/` (French, the reference, and English), stamped with the IDs of the
runs they come from and the hash of the code that made them; a test fails when the code no longer matches the stamp.
Neither `data/` nor `mlruns/` is in git: anyone can regenerate every figure with the one command, and the runs, not
the rendered files, are the source of truth.

Steps can also run one at a time: `valiwatt fetch`, `dq`, `validate`, `freeze --year Y`, `replay --year Y`, `breaks`,
`report`. The 2024 replay refuses to run until the monitoring rules are frozen by the `rules-frozen` tag.
`valiwatt safety` scans what git stages and every object in the history for anything that must not be published.

How it is built, and why each choice was made before any result was seen: [`docs/plan.md`](docs/plan.md). The
standards the report maps to, quoted and checked against their PDFs: [`docs/standards.md`](docs/standards.md).

## The rails

The rules every part of the project follows. The forecast-side ones bind from the moment the forecast lands.

- **No lookahead.** Design choices use 2019 to 2023 only; 2024 is held back to be replayed as production, and the code
  refuses design analysis that reaches into it. Validation is walk-forward with an embargo. A feature unavailable at
  forecast time is never used, and any result that leans on observed weather in place of a weather forecast says so.
  The replay reads raw records through a clock that refuses anything later.
- **A check that cannot fail is not a check.** Every gate and monitor has a test that injects the fault it must catch:
  a duplicated hour, a misplaced record, an impossible value, a sensor outage, a reading past the clock, a shifted
  demand level, intervals made too narrow, a moved freeze tag. `valiwatt breaks` runs them all as a tracked run, and
  the report refuses to render if one did not fire.
- **Numbers come from runs.** The reports are rendered from tracked MLflow runs; no number in them is typed by hand.
- **Claims stay within the checks.** Each report ends with what it does not show.
- **The monitor recommends; it never acts.** When a threshold trips, it writes a recommendation with its evidence;
  nothing retrains on its own.

## Data

- **Demand:** Hydro-Québec open data, "Historique de la demande d'électricité au Québec", hourly, 2019 to 2024.
- **Weather:** Environment and Climate Change Canada hourly station observations from climate.weather.gc.ca, for one
  station in each of six population centres (`config/stations.toml`).

Data is fetched by script and never committed. The licence terms of both sources, quoted with their URLs, are in
[`docs/data-licences.md`](docs/data-licences.md). Each report also gives the reference to ECCC's site in the form its
FAQ asks for, with the date the data was retrieved.

> Demand data: Hydro-Québec, "Historique de la demande d'électricité au Québec" (https://donnees.hydroquebec.com/explore/dataset/historique-demande-electricite-quebec/), licensed CC BY-NC 4.0 (https://creativecommons.org/licenses/by-nc/4.0/). Hydro-Québec offers this data for information only, without warranty of quality; it is not official data. This project checked it, masked or corrected a few hours (each case documented) and aggregated it; it does not redistribute the dataset, and the reports quote only a few hourly values from it, as evidence.
>
> Weather data: Environment and Climate Change Canada, used under the Open Government Licence – Canada (https://open.canada.ca/en/open-government-licence-canada).
>
> Neither Hydro-Québec nor Environment and Climate Change Canada produced, reviewed or endorsed this work.
>
> Built with Claude Code under the repository owner's direction.

## Licence

The code is under the MIT licence ([`LICENSE`](LICENSE)). The data is not part of this repository and keeps its own
licences ([`docs/data-licences.md`](docs/data-licences.md)).

## Continuous integration

`.github/workflows/checks.yml` runs the offline tests (every injected break included) and the public-safety scan on
every push. CI's scan covers paths, credentials, forbidden files, every file in the history and every commit and tag
message, but not private names: those live in a gitignored list, so only the local `valiwatt safety` checks them.
