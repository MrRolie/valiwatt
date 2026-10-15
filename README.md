# valiwatt

valiwatt validates and monitors a forecast of Québec's hourly electricity demand the way a model-risk team would.
The forecast is deliberately simple. The project is about the checks around it: how the model is tested before it is
trusted, how it is watched once it runs, and what it says when it should no longer be trusted.

**What it found.** The validation report measures the forecast's error on 2020–2023, out of sample, against a naive
baseline; how often its prediction intervals cover the actual demand, overall and season by season; what the monitor
recommended when 2024 was replayed hour by hour under rules frozen before 2024 was seen, and again after the one
change made to those rules since; and how often the rules raise a false alarm or miss a real change, measured on a
2023 rehearsal and on 2024. The numbers are in the « En bref » block at the top of each report ("In brief" in
English), and every one of them comes from a tracked run:

- validation and monitoring, with the model card and a mapping to SR 11-7 and the NIST AI RMF:
  [French](docs/report/validation.fr.md), the reference; [English](docs/report/validation.en.md);
- data quality: [French](docs/report/dq.fr.md); [English](docs/report/dq.en.md).

**Why the accuracy is limited.** The forecast is issued at the end of the day before, and it uses no weather
forecast: yesterday's temperature, the only one known at issue time, stands in for one. Its error therefore includes
the weather it could not know. The report also gives the bound with the observed weather of the target hour, which
stands in for a perfect weather forecast; that bound is optimistic, and it issues no recommendation.

**Status: v1.** The monitoring rules were frozen by the `rules-frozen` tag before 2024 was replayed, then changed
once after the 2024 results had been seen: on 2026-10-14, by the owner's ruling, recorded in `AGENTS.md`. The 2024
replay ran again as a departure; the report labels it post hoc and shows the earlier replay beside it. What is built:

- the data layer and its gates;
- a day-ahead forecast (ordinary least squares on named features) with walk-forward validation, conformal prediction
  intervals and two test cases;
- a frozen model registered in MLflow;
- a monitor that replays a year hour by hour and recommends *investigate the data*, *recalibrate* or *retrain*, its
  rules frozen by the `rules-frozen` tag before 2024 was replayed and changed once since, as above;
- the rules' false alarms and blind spots, measured on the 2023 rehearsal and, since that change, on 2024 out of
  sample.

Every gate and monitor has a break that makes it fire.

## Run it

```sh
uv sync
uv run valiwatt all --departure  # fetch the data, run every check, render the report (see below)
uv run pytest -m "not network"   # the offline tests, every injected break included
uv run pytest -m network         # the live tests only: the public endpoints and the quoted licence and standard texts
```

A plain `uv run pytest` runs both sets.

`valiwatt all --departure` downloads the public data into `data/`, then runs the whole chain as tracked MLflow runs
(stored in `mlruns/`): data quality, validation on 2019–2023, the frozen models, the 2023 rehearsal and the 2024
replay, and the breaks. It renders the reports into `docs/report/` (French, the reference, and English), stamped with
the IDs of the runs they come from and the hash of the code that made them; a test fails when the code no longer
matches the stamp. Neither `data/` nor `mlruns/` is in git: anyone can regenerate every figure with the one command,
and the runs, not the rendered files, are the source of truth.

The replay ledger, [`docs/replay-ledger.json`](docs/replay-ledger.json), is committed with each report. It holds one
entry per finished 2024 replay since the history rewrite of 2026-10-14: its run ID, freeze commit, rules and code
hashes, when its run was logged, MAPE, coverage and recommendations. The four 2024 replays that ran before the
rewrite, under the same rules and with the same MAPE, coverage and recommendations, ran under a commit the rewrite
replaced and are not in it (`AGENTS.md`). `valiwatt report` appends the replays it lacks before it renders (`valiwatt
ledger` does only that); an entry is never rewritten, a run that disagrees with its entry is refused, and the new
content must pass the public-safety scan before it is written. The ledger is not written during the replay itself,
because every run records whether the tree was clean. Its hash is in the report's stamp, so a test fails when it
changes after the render.

Steps can also run one at a time: `valiwatt fetch`, `dq`, `validate`, `freeze --year Y`, `replay --year Y`, `breaks`,
`ledger`, `report`. The 2024 replay refuses to run until the monitoring rules are frozen by the `rules-frozen` tag.
`valiwatt safety` scans what git stages and every object in the history for anything that must not be published.

After a departure from the frozen rules (each is listed in `AGENTS.md`), the report shows the 2024 replay as post hoc
beside the earlier replays it departs from. Where `mlruns/` lacks one of those earlier replays (a fresh clone), the
report shows it from its ledger entry, labelled as recorded in the ledger and not re-run here. The render still refuses
a 2024 replay that does not carry the recorded departure. So after a departure, `uv run valiwatt all --departure` is
the command that reproduces the report: its 2024 replay finds the earlier replays it departs from in `mlruns/` or in
the committed ledger and, finding them, runs only when `AGENTS.md` names the commit the tag now points to. Without
`--departure`, the 2024 replay refuses to run once an earlier one ran under another freeze commit.

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
