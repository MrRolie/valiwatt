"""valiwatt command line: `valiwatt all` fetches the data and regenerates every figure of the 2024 chain."""

from __future__ import annotations

import argparse
import os
import sys

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")


def cmd_fetch(args) -> int:
    """Each source's files of the data window through `args.through`. Past 2024 every source goes through its own gate
    (sources/http.py `require_fetchable`), which this command never bypasses. Past 2024 `--force` may reach the
    Hydro-Québec source only, whose new vintage goes to files of its own: with any other source the command refuses
    before any source runs, so a refused fetch never leaves half its sources fetched (docs/plan-v2.md, section
    11.3.1)."""
    from valiwatt.config import THROUGH_DEFAULT, stations
    from valiwatt.sources import eccc, hydroquebec, nbm
    from valiwatt.sources.http import FetchRefused

    through, only = int(args.through), args.only
    if args.force and through > THROUGH_DEFAULT and only != "hq":
        refused = "ECCC and NBM" if only is None else {"eccc": "ECCC", "nbm": "NBM"}[only]
        raise FetchRefused(f"--force through {through} is refused for {refused}: it would download again the "
                           "2019-2024 weather files v2's first 2024 replay read; only `--only hq` may be forced, and "
                           "no source has run")
    if only in (None, "hq"):
        m = hydroquebec.fetch(force=args.force, through=through)
        print(f"Hydro-Québec: {m['rows']} rows, sha256 {m['sha256'][:12]}")
    if only in (None, "eccc"):
        for st in stations():
            m = eccc.fetch_station(st, force=args.force, through=through)
            print(f"ECCC {st.name} ({st.climate_id}): {len(m['months'])} months")
    if only in (None, "nbm"):
        m = nbm.fetch(through=through, force=args.force)
        print(f"NBM {nbm.STATION}: {len(m['years'])} years of runs")
    return 0


def cmd_fetch_v3(args) -> int:
    """v3's fetch (docs/plan-v3.md, section 3.1): a window past 2024 is refused before any source runs and before any
    request (sources/http.py `require_fetchable_v3`); through 2024 it is `cmd_fetch`, each source behind its own gate,
    never forced: it takes no --force, so a file already verified is never fetched again. On the machine of record
    nothing of 2019-2024 is fetched again: the byte-copy check refuses a run on files that are not v2's."""
    from valiwatt.sources.http import require_fetchable_v3

    require_fetchable_v3(args.through)
    args.force = False
    return cmd_fetch(args)


def cmd_dq_v3(args) -> int:
    from valiwatt.quality.run_v3 import run_dq_v3

    return run_dq_v3(through=args.through, fresh_clone=args.fresh_clone)


def cmd_validate_v3(args) -> int:
    from valiwatt.forecast.validate_v3 import run_validate_v3

    return run_validate_v3()


def cmd_freeze_v3(args) -> int:
    from valiwatt.forecast.freeze_v3 import run_freeze_v3

    return run_freeze_v3(args.year)


def cmd_read_v2(args) -> int:
    """The one command that opens v2's store, at the path it is given (docs/plan-v3.md, section 10.3)."""
    from valiwatt.v2store import run_read_v2

    return run_read_v2(args.path)


def cmd_dq(args) -> int:
    from valiwatt.quality.run import run_dq

    return run_dq(through=args.through)


def cmd_validate(args) -> int:
    from valiwatt.forecast.validate import run_validate

    return run_validate()


def cmd_freeze(args) -> int:
    from valiwatt.forecast.freeze import run_freeze

    return run_freeze(args.year)


def cmd_replay(args) -> int:
    """The replay of one year. Its one flag, --departure, says only that the freeze tag moved and that AGENTS.md
    records the move; nothing else reaches the replay, whatever the year."""
    from valiwatt.monitor.run import run_replay

    return run_replay(args.year, departure=args.departure)


def cmd_evidence(args) -> int:
    from valiwatt.monitor.run import run_evidence

    return run_evidence(departure=args.departure)


def cmd_soundness(args) -> int:
    from valiwatt.report.soundness import run_soundness

    return run_soundness()


def cmd_breaks(args) -> int:
    from valiwatt.breaks import run_breaks

    return run_breaks()


def cmd_ledger(args) -> int:
    """Append the store's finished 2024 and 2025 replays to docs/replay-ledger.json. Never run inside the replay:
    every run records whether the tree was dirty, and a write in the middle of the chain would mark the later runs
    dirty."""
    from valiwatt.paths import rel
    from valiwatt.report.ledger import LEDGER, LedgerError, sync

    try:
        entries = sync()
    except LedgerError as e:
        print(f"replay ledger: REFUSED: {e}")
        return 1
    print(f"replay ledger: {len(entries)} finished replays of 2024 and 2025 in {rel(LEDGER)}")
    return 0


def cmd_report(args) -> int:
    from valiwatt.report.render import render
    from valiwatt.report.validation import render_validation

    code = cmd_ledger(args)  # the ledger first: the report reads the replays it holds
    if code:
        return code
    if args.dq_only:
        render(run_id=args.run_id)
    else:  # both reports from one chain: the data-quality report is the chain's own run
        render_validation(dq_run_id=args.run_id)
    return 0


def cmd_safety(args) -> int:
    from valiwatt.safety import main as safety_main

    return safety_main(allow_no_private_patterns=args.no_private_patterns)


def cmd_all(args) -> int:
    """The 2024 chain, in order, and nothing after it: every figure in docs/report/ is regenerated from fresh runs.
    The soundness run (added after the freeze, deciding nothing) reads the two frozen models after the 2024 replay, so
    it can never hold up the replay the plan pre-registers.
    It never replays 2025 and never runs the evidence: the 2025 replay runs only by `valiwatt replay --year 2025`
    (docs/plan-v2.md, section 11.3.4). --departure reaches the 2024 replay, never the rehearsal."""
    from valiwatt.config import THROUGH_DEFAULT
    from valiwatt.forecast.freeze import run_freeze
    from valiwatt.monitor.run import run_replay

    args.only, args.run_id, args.dq_only, args.through = None, None, False, THROUGH_DEFAULT
    steps = (cmd_fetch, cmd_dq, cmd_validate, lambda a: run_freeze(2023), lambda a: run_replay(2023),
             lambda a: run_freeze(2024), lambda a: run_replay(2024, departure=a.departure), lambda a: cmd_soundness(a),
             cmd_breaks, cmd_report)
    for step in steps:
        code = step(args)
        if code:
            return code
    return 0


REPLAY_HELP = (
    "replay a year hour by hour with its frozen models and the monitors. 2023: the rehearsal, on design years. 2024: "
    "seen by the developers; needs the rules-frozen-v2 tag. 2025, the held-out year: needs the rules-frozen-v2 tag "
    "and runs only by this command, never by `all`. Its gate starts it clean or refuses with its evidence; no flag "
    "lifts a refusal, and only the owner's dated ruling entry in AGENTS.md does (for a code change since the freeze, "
    "or a start that stopped). Every start is recorded. The earliest start is the test of record, unless it stops: "
    "then every later start refuses until the owner's ruling naming it (docs/plan-v2.md, sections 11.3.4 and 11.3.5)")


def build_parser() -> argparse.ArgumentParser:
    from valiwatt.config import THROUGH_DEFAULT, THROUGH_YEARS

    parser = argparse.ArgumentParser(prog="valiwatt")
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name: str, text: str) -> argparse.ArgumentParser:  # the help also heads `valiwatt <name> -h`
        return sub.add_parser(name, help=text, description=text)

    p = command("fetch", "fetch the public sources (Hydro-Québec, ECCC, NOAA's NBM via the IEM) into "
                         "data/raw (skips files already verified)")
    p.add_argument("--through", type=int, choices=THROUGH_YEARS, default=THROUGH_DEFAULT,
                   help="the data window's last year (default %(default)s). 2025 runs only behind the v2 freeze, "
                        "from a clean tree: each source's own gate refuses otherwise, before any request")
    p.add_argument("--only", choices=["hq", "eccc", "nbm"], help="fetch one source only")
    p.add_argument("--force", action="store_true",
                   help="fetch again files already verified. Through 2025 only `--only hq` may be forced (its 2025 "
                        "vintage goes to files of its own); with any other source the command refuses before any "
                        "source runs: it would download again the 2019-2024 weather files v2's first 2024 replay read")
    p.set_defaults(func=cmd_fetch)
    p = command("dq", "data-quality and representativeness checks, tracked as an MLflow run")
    p.add_argument("--through", type=int, choices=THROUGH_YEARS, default=THROUGH_DEFAULT,
                   help="the data window's last year (default %(default)s); 2025 reads the files the 2025 fetch wrote")
    p.set_defaults(func=cmd_dq)
    p = command("fetch-v3", "v3's fetch: the public sources through 2024, as `fetch` (skips files already verified); "
                            "a window past 2024 is refused before any request, its files being byte copies of v2's "
                            "own fetch")
    p.add_argument("--through", type=int, choices=THROUGH_YEARS, default=THROUGH_DEFAULT,
                   help="the data window's last year (default %(default)s); 2025 is refused before any request")
    p.add_argument("--only", choices=["hq", "eccc", "nbm"], help="fetch one source only")
    p.set_defaults(func=cmd_fetch_v3)
    p = command("dq-v3", "v3's data-quality checks (the noon feed, its wind, the lag day's mean), after the byte-copy "
                         "check of the raw files against v2's first 2024 replay, tracked as an MLflow run")
    p.add_argument("--through", type=int, choices=THROUGH_YEARS, default=THROUGH_DEFAULT,
                   help="the data window's last year (default %(default)s)")
    p.add_argument("--fresh-clone", action="store_true",
                   help="on a fresh clone, holding neither v2's files nor its store: the byte-copy check is not run, "
                        "and the run is labelled so; without it, a missing read of v2's store refuses")
    p.set_defaults(func=cmd_dq_v3)
    p = command("read-v2", "read v2's store once, read-only: v2's first 2024 replay, its data-quality run's raw-file "
                           "hashes and its validation run's forecasts, each checked against v2's ledger entry, logged "
                           "into v3's own store")
    p.add_argument("path", help="v2's mlruns/ directory in place, in v2's own checkout (its mlflow.db and artifacts, "
                                "whose recorded locations must lie under it: a copy set aside is refused); no default")
    p.set_defaults(func=cmd_read_v2)
    p = command("validate-v3", "v3's walk-forward validation on 2020-2024 at noon, v2's form at midnight beside it, "
                               "the intervals by temperature and the thresholds, after the reproduction check against "
                               "the read of v2's store, tracked as an MLflow run")
    p.set_defaults(func=cmd_validate_v3)
    p = command("freeze-v3", "fit, calibrate and register v3's frozen models for a replay year (2024: the rehearsal; "
                             "2025: the models frozen for 2025), from v3's data-quality and validation runs")
    p.add_argument("--year", type=int, required=True, choices=[2024, 2025])
    p.set_defaults(func=cmd_freeze_v3)
    p = command("validate", "walk-forward validation on 2020-2023 (models fitted from 2019), intervals of "
                            "split-conformal shape and the monitoring thresholds")
    p.set_defaults(func=cmd_validate)
    p = command("freeze", "fit, calibrate and register the frozen models for a replay year (2023: the "
                          "rehearsal; 2024: v2's 2024 replay). There is no freeze for 2025: it is judged "
                          "by the artifacts frozen for v2's first 2024 replay")
    p.add_argument("--year", type=int, required=True, choices=[2023, 2024])
    p.set_defaults(func=cmd_freeze)
    p = command("replay", REPLAY_HELP)
    p.add_argument("--year", type=int, required=True, choices=[2023, 2024, 2025])
    p.add_argument("--departure", action="store_true",
                   help="for any year: the freeze tag moved and AGENTS.md records the move (the earlier replays are "
                        "read from mlruns/ and from docs/replay-ledger.json); it lifts no refusal of the 2025 gate")
    p.set_defaults(func=cmd_replay)
    p = command("evidence", "produce what the owner rules on, deciding nothing: v2's 2024 replay run again "
                            "on the newest export, bounded to 2024, with the artifacts frozen for 2024, "
                            "compared hour by hour with v2's first 2024 replay. The 2025 gate applies a "
                            "ruling only once a finished evidence run, made from a clean tree, of the "
                            "commit it names or a descendant exists on the gate's data-quality run")
    p.add_argument("--departure", action="store_true",
                   help="the freeze tag moved and AGENTS.md records the move, as for `replay`")
    p.set_defaults(func=cmd_evidence)
    p = command("soundness", "interpret and check the frozen models' factors (each hour's curve, its shape and size, "
                             "their stability across folds and freezes, the lags, sensitivity in the sense of SR 11-7) "
                             "as a tracked run: added after the freeze, it decides nothing and refits no frozen model")
    p.set_defaults(func=cmd_soundness)
    p = command("breaks", "run every break (each gate and monitor made to fire) as a tracked run")
    p.set_defaults(func=cmd_breaks)
    p = command("report", "update the replay ledger, then render the data-quality and validation "
                          "reports into docs/report/")
    p.add_argument("--run-id", help="the data-quality run to render: with --dq-only, default the latest that passed "
                   "(the stamp is not rewritten); otherwise it must be the chain's own, which is the default")
    p.add_argument("--dq-only", action="store_true", help="render the data-quality report only")
    p.set_defaults(func=cmd_report)
    p = command("ledger", "append the store's finished 2024 and 2025 replays to docs/replay-ledger.json "
                          "(append-only; `report` does it too)")
    p.set_defaults(func=cmd_ledger)
    p = command("safety", "scan staged files and every object in the history for anything private (run "
                          "before each commit)")
    p.add_argument("--no-private-patterns", action="store_true",
                   help="scan without the gitignored private name list (CI); private names are then not checked")
    p.set_defaults(func=cmd_safety)
    p = command("all", "the 2024 chain: fetch, check, validate, freeze, rehearse 2023, replay 2024, check the frozen "
                       "models' soundness (after the replay, deciding nothing), break "
                       "and render every figure. It never replays 2025, which runs only by `valiwatt "
                       "replay --year 2025`, and takes no --through")
    p.add_argument("--force", action="store_true", help="fetch again the files already verified, through 2024")
    p.add_argument("--departure", action="store_true",
                   help="run the 2024 replay with --departure, after a departure recorded in AGENTS.md (the rehearsal "
                        "never takes it): the earlier 2024 replays it departs from are found in mlruns/ or in the "
                        "committed docs/replay-ledger.json, and the report shows one that mlruns/ lacks from its "
                        "ledger entry")
    p.set_defaults(func=cmd_all)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
