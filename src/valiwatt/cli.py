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


def cmd_report(args) -> int:
    from valiwatt.report.render import render

    render(run_id=args.run_id)
    return 0


def cmd_safety(args) -> int:
    from valiwatt.safety import main as safety_main

    return safety_main()


def cmd_all(args) -> int:
    args.only, args.run_id = None, None
    for step in (cmd_fetch, cmd_dq, cmd_report):
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
    p = sub.add_parser("report", help="render the data-quality report from a tracked run")
    p.add_argument("--run-id")
    p.set_defaults(func=cmd_report)
    p = sub.add_parser("safety", help="scan tracked and staged files for anything private (run before each commit)")
    p.set_defaults(func=cmd_safety)
    p = sub.add_parser("all", help="fetch, check and render: regenerates every figure")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_all)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
