# valiwatt: rules for every session and build agent

valiwatt validates and monitors a forecast of Québec's hourly electricity demand, the way a model-risk team would.

`BRIEF.md` is the dated founding record and is never edited (one ruled exception, below). This file is the operational home of its rules. Each
departure from the brief is listed below with its ruling. A disagreement that is not listed means the brief wins, and
the gap goes to the owner as a question.

## Rules (from the brief)

- **No lookahead.**
  - Splits are walk-forward with an embargo. Never random folds on a time series.
  - A feature unavailable at forecast time is never used. Observed weather standing in for a weather forecast is
    labelled on every result that depends on it.
  - A weather forecast is used from its assumed availability only (`docs/plan-v2.md`, section 3.2), and every result
    that depends on it says the availability is assumed.
- **A check that cannot fail is not a check.** Each monitor and gate has a recorded break that must make it fire.
  CI runs every break.
- **Numbers come from runs.** Report and README figures are generated from tracked MLflow runs, never typed.
- **Anyone can re-run it.** Data and run outputs stay out of git, so one command fetches the data and regenerates
  every figure in the report and the README. Stored figures are never the source of truth.
- **Claims are scoped to the checks.** No public line claims more than a passing check shows.
- **The monitor recommends; it never acts.** A tripped threshold produces a written recommendation with its evidence.
  Nothing retrains on its own.
- **The model stays simple.** A seasonal baseline, one explainable production model, and v1's model kept as the
  comparison; a new model family needs the owner's ruling.
- **Data never enters git.** Scripts fetch it into the gitignored `data/`. Hydro-Québec's series is CC BY-NC 4.0 and
  "informational, not official": attribute it and say so on every public surface. Nothing here is used commercially.
  NOAA's NBM forecast via the IEM is fetched by script, never committed, and every public surface carries its
  attribution and the widened no-endorsement line (the ruling below).
- **Authorship.** Built with Claude Code under the owner's direction. Never imply he wrote or reviewed code.

## This repository goes public with its history

- Before each commit, check it holds nothing private: credentials, paths outside the repo, agent run records or data.
  `uv run valiwatt safety` scans what git stages and every object in the history; it does not know the private names in `ESTATE.local.md`,
  so grep the staged diff for those by hand.
- Commits use this repository's configured author identity, a GitHub noreply address. Never change `user.email`.
- Commit messages end with a `Co-Authored-By:` line only, never an agent session URL (the owner's ruling,
  2026-10-14).
- The history was rewritten once, on 2026-10-14 at the owner's ruling and before any public push: agent session URLs
  were stripped from every commit message and the two `BRIEF.md` lines above removed. Every commit's hash changed;
  the `rules-frozen` tag was recreated on the rewritten commit, its rule files byte-identical. The 2024 replay then
  ran again under the recreated tag, in a fresh run store, and gave the same MAPE, coverage and recommendations as
  before; that rerun is the ledger's first entry. Four 2024 replays had run before the rewrite, all under the
  same rules and with the same MAPE, coverage and recommendations; they ran under a commit the rewrite replaced, so
  the ledger, whose entries name their freeze commit, does not hold them.
- Push only to the private remote; it turns public only at the owner's go. The tags `v1`, `plan-v2`, `v2-first-run`
  and `rules-frozen-v2` go to the private remote with the branch (`git push --follow-tags`): the history tests read
  them.
- If `ESTATE.local.md` exists, read it: it holds private context for sessions on the owner's machine. Never commit it,
  quote it or reference its contents in a tracked file.

## The owner's rulings on the brief's open decisions

- **Code licence: MIT** (2026-10-13). `LICENSE` names the repository's GitHub owner.
- **ECCC data: the Open Government Licence – Canada** (2026-10-13), the most permissive of the texts that bear on it.
  Attribution and quotations: `docs/data-licences.md`.
- **Hydro-Québec data: this project's use is NonCommercial under CC BY-NC 4.0** (2026-10-13).
- **The English report ships in v1** (2026-10-13), derived from the French one, which stays the reference.
- **Public home: the GitHub repository under the owner's account.** It exists as a PRIVATE remote from 2026-10-14 so
  CI runs on every branch push; it turns public only at his go, after he reviews v1 (rulings of 2026-10-13 and 2026-10-14).
  Push only what the public-safety scan passes: the history goes public as it is.
- **The rendered report is committed** under `docs/report/` (2026-10-14), regenerated by `valiwatt all` and stamped
  with its run IDs and code hash; a test fails when the stamp does not match the code. The runs stay the source of
  truth.
- **Replay ledger published (2026-10-14; amended 2026-10-16).** `docs/replay-ledger.json` holds one entry per
  finished 2024 or 2025 replay since the history rewrite, v1's 2024 replays and v2's 2024 and 2025 replays: run ID,
  freeze tag and commit, rules and code hashes, when the run was logged (v1's runs open after the replay has computed;
  v2's open before it reads any input), status, MAPE, coverage, recommendations, and the replays it departs from.
  v2's entries add the evaluation label (`held_out` on the clean 2025 test of record at most; `post_hoc` on a post-hoc
  one; `seen_by_developers` on every other entry, as `docs/plan-v2.md` section 10.5 gives it), the production model's
  hash, the two verdicts of `docs/plan-v2.md` section 8, one hash of the frozen artifacts the replay was judged by,
  and the standing (clean or post hoc for a 2025 entry, empty for 2024); v1's entries are never touched. It is
  append-only: an entry is never rewritten, and a run in the store that disagrees
  with its entry is refused. It is path-checked: every field has a fixed shape, and the content must pass the
  public-safety scan before it is written. It is written only by `valiwatt ledger` and by `valiwatt report` (before
  it renders), never mid-chain, and committed in the same commit as the report render, whose stamp holds its hash. A
  replay from before a departure that the store lacks is shown from its entry, labelled as recorded in the ledger and
  not re-run.
- **v2's model family: an additive model** (2026-10-15, the ruling the model rule above asks for). A spline
  temperature response for each hour of the day, plus v1's calendar cells and its 24-hour and 168-hour demand lags,
  fitted by ridge regression. He chose it on a scratch walk-forward spike over 2020–2023, outside the tracked runs,
  so v2's 2020–2023 results are not out of sample for that choice. It is built exactly as the spike measured it:
  scikit-learn's `SplineTransformer` with 10 quantile knots fitted on each fit's own training rows, degree 3, linear
  extrapolation and no bias column, on the forecast temperature, one curve per local hour, then `StandardScaler` and
  `Ridge(alpha=1.0)` (`docs/plan-v2.md`, section 4). v1's least squares stays as a comparison, on yesterday's
  temperature and on the forecast, and never recommends or sets a threshold.
- **v2's weather input: NOAA's National Blend of Models forecast for Montréal-Trudeau, from the Iowa Environmental
  Mesonet archive** (2026-10-15). A run is used from two hours after its run time, an assumption since the archive
  records no arrival time; each day takes the freshest such run, among the twelve freshest, whose own points bracket
  every hour of the day (`docs/plan-v2.md`, section 3.2). IEM declares it public domain; NOAA asks for attribution,
  forbids implying its endorsement or an affiliation, and requires modified values to be labelled as derived. The
  owner accepted these terms (2026-10-15). The quoted terms go in `docs/data-licences.md` with v2.
- **v2's test** (2026-10-15). v2 is frozen on 2019–2023. Its 2024 replay is labelled seen by the developers, never a
  held-out result. The clean held-out replay is 2025, once Hydro-Québec publishes it. The latent v1 items fixed
  inside the v2 freeze: the eve of the replayed year never calibrates a half-width, in either freeze, and design code
  reads temperatures through the replay's bounds filter (`docs/plan-v2.md`, section 6.3).
- **v2 is a second freeze, not a departure from v1's** (2026-10-16). `rules-frozen` stays on v1's freeze commit for
  good, and v1's departures section is closed by one sentence. New annotated tags `v1`, `plan-v2`, `v2-first-run` and
  `rules-frozen-v2`, each pinned to its commit by a test. v2 has its own departures section, below. This amends the
  departure rule (each freeze has its own section) and the ledger ruling above.
- **When 2025 is clean** (2026-10-16, five cards, ruled before any 2025 data exist; the third and fourth replace the
  conditions of the first two, and the fifth rules a start that stops). The 2025 start is clean only when the code
  proves every condition of `docs/plan-v2.md` section 11.3.4, and otherwise refused with its evidence; no code change
  keeps 2025 clean on its own, one needed only to read Hydro-Québec's 2025 publication included; only his dated
  ruling, an entry in the v2 departures section below naming the commit it applies to and its outcome (clean or post
  hoc), lifts a refusal. If the first 2025 start opens and then stops before it finishes, the stopped start is listed
  first, with where it stopped, and every later 2025 start refuses until his dated ruling, which names the stopped
  start: clean makes the next start the test of record, labelled held out; post hoc lets the next start run, labelled
  post hoc; a crash spends nothing by mistake. Section 11.3.5 of the plan is the rule's one statement. Superseded by
  the fifth card: the developers' design under which a start that opened and stopped spent 2025 for good, a ruling on
  it allowing only reruns. Superseded the same day by the third and fourth cards, and kept here as the record: the
  first card's data-access exception (a change confined to the Hydro-Québec fetch or the demand repairs kept 2025
  clean when the 2024 replay run again reproduced v2's 2024 entry); the second card's condition (A) on that exception
  (the rerun to issue the same forecasts and ranges on every 2024 hour whose inputs revised records did not touch); the
  developers' reading that a revised record touches the hours within one hour of it and of its two lag images, which
  was never put to the owner; and the first card's clause that he would rule whether 2025 counts as clean if revised
  records changed the frozen model's hash, made vacuous by the developers' decision that 2025 is judged by exactly the
  artifacts frozen for v2's first 2024 replay, nothing refitted.
- **The NOAA and IEM attribution** (2026-10-16; this exact wording approved verbatim by the owner on his second card
  that day), French the reference: « Prévisions météorologiques : guidance NBS du National Blend of Models de la NOAA
  pour la station CYUL (Montréal-Trudeau), téléchargée des archives de l'Iowa Environmental Mesonet de l'Université
  d'État de l'Iowa (https://mesonet.agron.iastate.edu/). Ce projet a interpolé à l'heure ces prévisions trihoraires
  et les a converties en degrés Celsius : les valeurs qu'il utilise sont modifiées et ne sont pas des données
  originales de la NOAA. » In English: "Weather forecasts: NOAA National Blend of Models NBS guidance for station CYUL
  (Montréal-Trudeau), downloaded from the Iowa Environmental Mesonet archive of Iowa State University
  (https://mesonet.agron.iastate.edu/). This project interpolated these three-hourly forecasts to hourly values and
  converted them to degrees Celsius: the values it uses are modified and are not original NOAA data." The
  no-endorsement line becomes « Ni Hydro-Québec, ni Environnement et Changement climatique Canada, ni la NOAA, ni
  l'Iowa Environmental Mesonet n'ont produit, revu ou approuvé ce travail, et ce projet n'est affilié ni à la NOAA ni
  à l'Iowa Environmental Mesonet. » / "Neither Hydro-Québec, Environment and Climate Change Canada, NOAA nor the Iowa
  Environmental Mesonet produced, reviewed or endorsed this work, and the project is affiliated with neither NOAA nor
  the Iowa Environmental Mesonet." `src/valiwatt/attribution.py` holds the same lines, and a test holds them to this
  bullet.
- **If 2025 does not confirm v2** (2026-10-16): the report and a recommendation, as `docs/plan-v2.md` section 8.5
  says. The status changes, the claims it bears on are withdrawn, the report and the story open with it, and the
  report writes a recommendation in the monitor's form. No revert is pre-committed; the model afterwards is the
  owner's ruling.

## Departures from the brief

- **2026-10-14, the owner's ruling: two lines of `BRIEF.md` were rewritten out of the history** before any public
  push. Both were private references; nothing else in `BRIEF.md` changed, and it stays never edited from here on.
- **2026-10-15, the owner's ruling: v2's production model is the GAM, and v1's least squares is kept as a comparison
  that never recommends.** The brief: "a seasonal baseline and one explainable model". The ruling: *v2's model
  family*, above.
- **2026-10-15, the owner's ruling: v2's temperature input is NOAA's NBM forecast via the IEM; ECCC's observations
  are kept for data quality, v1's model and the observed-weather bound.** The brief names ECCC's hourly station
  observations as the weather. The ruling: *v2's weather input*, above.
- **2026-10-15, the owner's ruling: the data window extends to 2025 for the held-out replay, fetched only after the
  v2 freeze.** The brief: "every hour of 2019 to 2024". The ruling: *v2's test*, above.
- **2026-10-15, the owner's ruling: 2024 is labelled seen by the developers, and 2025 is the held-out replay.** The
  brief: "2024 replayed hour by hour as if it were production". The ruling: *v2's test*, above.

## Departures from the frozen monitoring rules

These are v1's records: v1 ended at the `v1` tag, and v2 is a second freeze with its own section below, not a
departure from this one (the owner's ruling, 2026-10-16).

The monitoring rules are frozen by the `rules-frozen` tag before the 2024 replay (`docs/plan.md`, section 4). Any
change to a rule file after that tag is listed here with its date, its reason and the commit, the report labels it
post hoc and shows the earlier replay beside it, and the 2024 replay runs again only with `--departure`. A change to
`pyproject.toml` or `uv.lock` after the tag counts as a departure too: the dependencies can change the replay's
numbers, and the freeze gate does not hash them, so this rule is kept by hand: record the change here and move the
tag to the commit that makes it, so the 2024 replay runs again with `--departure` and the report labels it post hoc.

Write each commit here by at least its first 12 characters, and write no other hexadecimal run of 7 characters or
more: the report reads this section when it renders. If the section names a commit, the 2024 replay must be a
departure, and a departure replay's freeze commit must be named here; otherwise the report refuses to render. The
freeze gate finds the earlier 2024 replays in `mlruns/` and in the committed ledger `docs/replay-ledger.json`, so a
fresh clone sees a departure too, and `valiwatt all --departure` reproduces the report; an earlier replay the store
lacks is shown from its ledger entry, labelled as recorded in the ledger and not re-run.

- **2026-10-14, the owner's ruling; the tag moved to `affe089f6ed0`.** After the 2024 results, an independent
  review of v1 found that the design code could see a repaired value before the replay could, that the thresholds
  were calibrated on weeks the replay would not judge, and gaps in two gates. The owner ruled one departure carrying
  exactly these changes, made in commits `bea83455ab50`, `05d359b412a2`, `9c2975ea87fc`, `64f14c2d0084` and
  `39670300a5de`:
  - A3: a misplaced record moves only to an unambiguous destination. The next-best destination must cost at least
    the minimum ratio times the chosen one; otherwise the hour stays an unresolved duplicate and its gate fails.
  - A4: duplicate values outside the demand bounds are never repaired into place, and no path returns a usable hour
    outside the bounds.
  - B1: the validation folds, the threshold sets and the frozen models see each decision time as the replay sees its
    clock. They are built from the raw records cut at that time, so a repair whose source record arrives later cannot
    reach them, and an interval's half-width calibrates only on days scored by the fit clock that fixes it.
  - B2: the origin guard's 168-hour term, implied by its 24-hour term, could never fire alone and is removed.
  - B3: the thresholds calibrate on the weeks the replay judges. One shared predicate leaves out a week holding a day
    that fails the replay's data-quality verdict, and a week with fewer than 7 days in range.
  - B4: the freeze gate runs inside `replay()` for the held-out year, so no entry point skips it.
  - B5: the moved-tag check reads the earlier replays' freeze commits from the committed ledger as well as the store.
  - B6: the false-alarm and blind-spot measures run on the 2024 replay too; the report labels them out of sample and
    added after the results.

  Rule 4 (a temperature drift with performance inside the bands sends the monitor to "investigate the data") is
  unchanged and disclosed as a limitation. The 2024 replay ran again with `--departure`; the report labels it post
  hoc and shows the earlier replay beside it.

## Departures from the v2 frozen rules

The rules are frozen by the `rules-frozen-v2` tag before v2's 2024 replay (`docs/plan-v2.md`, section 10). After it,
no 2024 result changes the model, the forecast input or the decision rule. Any change after it to a rule file,
`pyproject.toml` or `uv.lock` is listed here with its date, its reason and the commit. Write each commit here by at
least its first 12 characters, and write no other hexadecimal run of 7 characters or more: the report and the gate
read this section, and a 7-digit station number would be read as a commit, so write none here.

Each entry is one of two kinds. A departure: its bold head ends "a departure", and it names the commit the tag moved
from, the one it moved to and every commit between them that touches a rule file or a dependency. Or the owner's
ruling on a refused 2025 start: its bold head is "<date>, the owner's ruling on 2025: clean" or "<date>, the owner's
ruling on 2025: post hoc", and it names the commit it applies to and, when it rules on a start that stopped, that
start by its start time as the refusal prints it (`YYYY-MM-DDTHH:MM:SS.mmmZ`), never by its run ID, whose 32
hexadecimal characters would be read as a commit (the ruling of 2026-10-16 on when 2025 is clean). The 2025 gate
reads a ruling only in those words. What each costs 2025 is stated once, in `docs/plan-v2.md` section 11.3.5.

None yet.
