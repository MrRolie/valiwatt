# valiwatt

valiwatt validates and monitors a forecast of Québec's hourly electricity demand the way a model-risk team would.
The forecast is deliberately simple. The project is about the checks around it: how the model is tested before it is
trusted, how it is watched once it runs, and what it says when it should no longer be trusted.

**Status: work in progress.** The data layer is in place: fetch scripts for both sources, data-quality gates, and the
data-quality and representativeness report. The forecast, its validation and its monitoring come next.

## Run it

```sh
uv sync
uv run valiwatt all      # fetch the data, run every check, render the report
uv run pytest            # the checks' own tests (add -m network for the live-endpoint tests)
```

`valiwatt all` downloads the public data into `data/`, runs the data-quality checks as one MLflow run (stored in
`mlruns/`), and renders `reports/dq.fr.md` (French, the reference) and `reports/dq.en.md` from that run. None of these
directories is in git: anyone can regenerate every figure with the one command, and a stored figure is never the
source of truth.

Steps can also run one at a time: `valiwatt fetch`, `valiwatt dq`, `valiwatt report`. `valiwatt safety` scans what git
tracks for anything that must not be published.

## The rails

The rules every part of the project follows. The forecast-side ones bind from the moment the forecast lands.

- **No lookahead.** Design choices use 2019 to 2023 only; 2024 is held back to be replayed as production, and the code
  refuses design analysis that reaches into it. Forecast splits will be walk-forward with an embargo. A feature
  unavailable at forecast time is never used, and any result that leans on observed weather in place of a weather
  forecast says so.
- **A check that cannot fail is not a check.** Every gate has a test that injects the fault it must catch: a duplicated
  hour, a misplaced record, an impossible value, a sensor outage, a network-wide gap, the weather service's empty
  response when no time zone is given.
- **Numbers come from runs.** The report is rendered from a tracked MLflow run; no number in it is typed by hand.
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
every push. CI's scan covers paths, credentials, forbidden files and every commit message, but not private names: those
live in a gitignored list, so only the local `valiwatt safety` checks them.
