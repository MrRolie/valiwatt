"""valiwatt command line: `valiwatt all` fetches the data and regenerates every figure."""

from __future__ import annotations

import argparse
import os
import sys

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")


def cmd_fetch(args) -> int:
    from valiwatt.config import stations
    from valiwatt.sources import eccc, hydroquebec

    if args.only in (None, "hq"):
        m = hydroquebec.fetch(force=args.force)
        print(f"Hydro-Québec: {m['rows']} rows, sha256 {m['sha256'][:12]}")
    if args.only in (None, "eccc"):
        for st in stations():
            m = eccc.fetch_station(st, force=args.force)
            print(f"ECCC {st.name} ({st.climate_id}): {len(m['months'])} months")
    return 0


def cmd_dq(args) -> int:
    from valiwatt.quality.run import run_dq

    return run_dq()


def cmd_validate(args) -> int:
    from valiwatt.forecast.validate import run_validate

    return run_validate()


def cmd_freeze(args) -> int:
    from valiwatt.forecast.freeze import run_freeze

    return run_freeze(args.year)


def cmd_replay(args) -> int:
    from valiwatt.monitor.run import run_replay

    return run_replay(args.year, departure=args.departure)


def cmd_breaks(args) -> int:
    from valiwatt.breaks import run_breaks

    return run_breaks()


def cmd_report(args) -> int:
    from valiwatt.report.render import render
    from valiwatt.report.validation import render_validation

    if args.dq_only:
        render(run_id=args.run_id)
    else:  # both reports from one chain: the data-quality report is the chain's own run
        render_validation(dq_run_id=args.run_id)
    return 0


def cmd_safety(args) -> int:
    from valiwatt.safety import main as safety_main

    return safety_main(allow_no_private_patterns=args.no_private_patterns)


def cmd_all(args) -> int:
    """The whole chain, in order: every figure in docs/report/ is regenerated from fresh runs."""
    from valiwatt.forecast.freeze import run_freeze
    from valiwatt.monitor.run import run_replay

    args.only, args.run_id, args.dq_only = None, None, False
    steps = (cmd_fetch, cmd_dq, cmd_validate, lambda a: run_freeze(2023), lambda a: run_replay(2023),
             lambda a: run_freeze(2024), lambda a: run_replay(2024, departure=a.departure), cmd_breaks, cmd_report)
    for step in steps:
        code = step(args)
        if code:
            return code
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="valiwatt")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("fetch", help="fetch both public sources into data/raw (skips files already verified)")
    p.add_argument("--force", action="store_true")
    p.add_argument("--only", choices=["hq", "eccc"])
    p.set_defaults(func=cmd_fetch)
    p = sub.add_parser("dq", help="data-quality and representativeness checks, tracked as an MLflow run")
    p.set_defaults(func=cmd_dq)
    p = sub.add_parser("validate", help="walk-forward validation, conformal intervals and monitoring thresholds (2019-2023)")
    p.set_defaults(func=cmd_validate)
    p = sub.add_parser("freeze", help="fit, calibrate and register the frozen model for a replay year")
    p.add_argument("--year", type=int, required=True, choices=[2023, 2024])
    p.set_defaults(func=cmd_freeze)
    p = sub.add_parser("replay", help="replay a year hour by hour with its frozen model and the monitors "
                                      "(2023: pseudo-replay on design years; 2024: needs the rules-frozen tag)")
    p.add_argument("--year", type=int, required=True, choices=[2023, 2024])
    p.add_argument("--departure", action="store_true",
                   help="allow a 2024 replay under a moved freeze tag (record the departure in AGENTS.md first)")
    p.set_defaults(func=cmd_replay)
    p = sub.add_parser("breaks", help="run every break (each gate and monitor made to fire) as a tracked run")
    p.set_defaults(func=cmd_breaks)
    p = sub.add_parser("report", help="render the data-quality and validation reports into docs/report/")
    p.add_argument("--run-id", help="the data-quality run to render: with --dq-only, default the latest that passed "
                   "(the stamp is not rewritten); otherwise it must be the chain's own, which is the default")
    p.add_argument("--dq-only", action="store_true", help="render the data-quality report only")
    p.set_defaults(func=cmd_report)
    p = sub.add_parser("safety", help="scan staged files and every object in the history for anything private (run before each commit)")
    p.add_argument("--no-private-patterns", action="store_true",
                   help="scan without the gitignored private name list (CI); private names are then not checked")
    p.set_defaults(func=cmd_safety)
    p = sub.add_parser("all", help="fetch, check, validate, freeze, replay, break and render: every figure")
    p.add_argument("--force", action="store_true")
    p.add_argument("--departure", action="store_true",
                   help="run the 2024 replay with --departure (after a departure recorded in AGENTS.md; the earlier "
                        "replays must be in mlruns/)")
    p.set_defaults(func=cmd_all)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
