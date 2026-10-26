"""The command line reaches each step with what it is given and nothing else (docs/plan-v2.md, sections 11.3.1 and
11.3.4): `all` is the 2024 chain and never replays 2025; only --departure reaches the 2025 replay; `fetch` goes through
each source's own gate, and past 2024 forces Hydro-Québec alone; `report` refuses the 2025 vintage before any 2025
start. The steps are stand-ins unless a test says otherwise: no data and no real store are touched."""

from __future__ import annotations

import argparse
from functools import partial

import pytest

from valiwatt import cli
from valiwatt.config import stations
from valiwatt.forecast import freeze
from valiwatt.monitor import run
from valiwatt.sources import eccc, hydroquebec, nbm
from valiwatt.sources.http import FetchRefused


def subparsers() -> dict[str, argparse.ArgumentParser]:
    parser = cli.build_parser()
    return next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction)).choices


def options(p: argparse.ArgumentParser) -> set[str]:
    return {s for a in p._actions for s in a.option_strings}


def rejected(argv: list[str]) -> bool:
    """Whether the parser refuses `argv` (argparse exits with status 2), before any command runs."""
    try:
        cli.build_parser().parse_args(argv)
    except SystemExit as e:
        return e.code == 2
    return False


@pytest.fixture
def chain(monkeypatch):
    """Every step of `all` a stand-in that records what reaches it."""
    seen = {"fetch": [], "dq": [], "freeze": [], "replay": [], "evidence": []}
    monkeypatch.setattr(cli, "cmd_fetch", lambda a: seen["fetch"].append((a.through, a.only, a.force)) or 0)
    monkeypatch.setattr(cli, "cmd_dq", lambda a: seen["dq"].append(a.through) or 0)
    for name in ("cmd_validate", "cmd_breaks", "cmd_report"):
        monkeypatch.setattr(cli, name, lambda a: 0)
    seen["soundness"] = []
    monkeypatch.setattr(cli, "cmd_soundness", lambda a: seen["soundness"].append(list(seen["freeze"])) or 0)
    monkeypatch.setattr(freeze, "run_freeze", lambda year: seen["freeze"].append(year) or 0)
    monkeypatch.setattr(run, "run_replay", lambda year, departure=False: seen["replay"].append((year, departure)) or 0)
    monkeypatch.setattr(run, "run_evidence", lambda departure=False: seen["evidence"].append(departure) or 0)
    return seen


def test_all_passes_departure_to_the_2024_replay_only(chain):
    assert cli.main(["all", "--departure"]) == 0
    assert chain["replay"] == [(2023, False), (2024, True)]
    chain["replay"].clear()
    assert cli.main(["all"]) == 0
    assert chain["replay"] == [(2023, False), (2024, False)]


def test_all_runs_the_soundness_run_once_after_the_2024_replay(chain, monkeypatch):
    """The soundness run (report/soundness.py, added after the freeze, deciding nothing) reads both frozen models: `all`
    runs it once, after the 2024 replay, so it can never hold up that pre-registered replay, and before the breaks and
    the report."""
    order = []
    monkeypatch.setattr(run, "run_replay", lambda year, departure=False: order.append(("replay", year)) or 0)
    monkeypatch.setattr(cli, "cmd_soundness", lambda a: order.append(("soundness", list(chain["freeze"]))) or 0)
    monkeypatch.setattr(cli, "cmd_breaks", lambda a: order.append(("breaks",)) or 0)
    assert cli.main(["all"]) == 0
    assert order == [("replay", 2023), ("replay", 2024), ("soundness", [2023, 2024]), ("breaks",)]
    assert "decides nothing" in " ".join(subparsers()["soundness"].format_help().split())


def test_all_never_runs_the_2025_replay(chain):
    """With every flag `all` accepts, it runs the 2024 chain on the 2024 window and nothing after it: no replay of 2025
    and no evidence run. It takes no --through, which on 2025 would render the 2025 vintage before any 2025 start."""
    assert options(subparsers()["all"]) == {"-h", "--help", "--force", "--departure"}
    for flags in ([], ["--departure"], ["--force"], ["--departure", "--force"]):
        for seen in chain.values():
            seen.clear()
        assert cli.main(["all", *flags]) == 0, flags
        assert [y for y, _ in chain["replay"]] == [2023, 2024] and chain["freeze"] == [2023, 2024], flags
        assert chain["evidence"] == [], flags
        assert chain["fetch"] == [(2024, None, "--force" in flags)] and chain["dq"] == [2024], flags
    for argv in (["all", "--through", "2025"], ["all", "--through", "2024"], ["all", "--year", "2025"]):
        assert rejected(argv), argv


def test_no_flag_but_departure_reaches_the_2025_replay(chain):
    """`replay` has one flag, --departure; every other option of the command line, or one it does not know, is refused
    by the parser on `replay --year 2025`, and nothing runs."""
    commands = subparsers()
    own = {"-h", "--help", "--year", "--departure"}
    assert options(commands["replay"]) == own
    foreign = set().union(*(options(p) for p in commands.values())) - own
    foreign |= {"--clean", "--held-out", "--ruling", "--allow-dirty", "--n-boot", "--standing"}
    for opt in sorted(foreign):
        assert rejected(["replay", "--year", "2025", opt]) and rejected(["replay", "--year", "2025", opt, "2025"]), opt
    assert chain["replay"] == []
    assert cli.main(["replay", "--year", "2025"]) == 0
    assert cli.main(["replay", "--year", "2025", "--departure"]) == 0
    assert chain["replay"] == [(2025, False), (2025, True)]


def test_replay_accepts_2025_and_freeze_does_not(chain):
    """2025 is replayed with the artifacts frozen for v2's first 2024 replay: there is no freeze for it."""
    assert next(a for a in subparsers()["replay"]._actions if "--year" in a.option_strings).choices == \
        list(run.REPLAY_YEARS)
    assert rejected(["freeze", "--year", "2025"]) and not rejected(["freeze", "--year", "2024"])
    assert rejected(["replay", "--year", "2022"])
    assert cli.main(["freeze", "--year", "2024"]) == 0 and cli.main(["replay", "--year", "2025"]) == 0
    assert chain["freeze"] == [2024] and chain["replay"] == [(2025, False)]


def test_the_help_names_the_tag_and_what_the_2025_gate_does():
    commands = subparsers()
    replay = " ".join(commands["replay"].format_help().split())
    assert all(w in replay for w in ("rules-frozen-v2", "never by `all`", "no flag lifts a refusal",
                                     "owner's dated ruling entry in AGENTS.md", "Every start is recorded",
                                     "test of record", "11.3.4 and 11.3.5")), replay
    evidence = " ".join(commands["evidence"].format_help().split())
    assert "deciding nothing" in evidence and "finished evidence run" in evidence
    assert "`--only hq` may be forced" in " ".join(commands["fetch"].format_help().split())
    assert "2024 and 2025 replays" in commands["ledger"].format_help()


def test_evidence_calls_run_evidence_with_departure(chain):
    assert cli.main(["evidence"]) == 0 and cli.main(["evidence", "--departure"]) == 0
    assert chain["evidence"] == [False, True] and chain["replay"] == []


# --- v3 (docs/plan-v3.md) --------------------------------------------------------------------------------------------


def test_validate_v3_calls_run_validate_v3(monkeypatch):
    from valiwatt.forecast import validate_v3

    seen = []
    monkeypatch.setattr(validate_v3, "run_validate_v3", lambda: seen.append("validate-v3") or 0)
    assert cli.main(["validate-v3"]) == 0 and seen == ["validate-v3"]


def test_freeze_v3_calls_run_freeze_v3_with_its_year(monkeypatch):
    """v3 freezes for the 2024 rehearsal and for 2025 (docs/plan-v3.md, sections 11.1 and 11.2), and no other year."""
    from valiwatt.forecast import freeze_v3

    seen = []
    monkeypatch.setattr(freeze_v3, "run_freeze_v3", lambda year: seen.append(year) or 0)
    assert cli.main(["freeze-v3", "--year", "2024"]) == 0 and cli.main(["freeze-v3", "--year", "2025"]) == 0
    assert seen == [2024, 2025]
    assert rejected(["freeze-v3"]) and rejected(["freeze-v3", "--year", "2023"])


def test_replay_v3_calls_run_replay_v3_with_its_year(monkeypatch):
    """v3 replays 2024 (the rehearsal) and 2025 (docs/plan-v3.md, sections 11.1 and 11.3), with --departure and no
    other flag, and no path or store argument."""
    from valiwatt.monitor import run_v3

    seen = []
    monkeypatch.setattr(run_v3, "run_replay_v3", lambda year, departure=False: seen.append((year, departure)) or 0)
    assert cli.main(["replay-v3", "--year", "2024"]) == 0
    assert cli.main(["replay-v3", "--year", "2025", "--departure"]) == 0
    assert seen == [(2024, False), (2025, True)]
    assert rejected(["replay-v3"]) and rejected(["replay-v3", "--year", "2023"])
    assert rejected(["replay-v3", "--year", "2025", "mlruns"])


def test_evidence_v3_calls_run_evidence_v3(monkeypatch):
    from valiwatt.monitor import run_v3

    seen = []
    monkeypatch.setattr(run_v3, "run_evidence_v3", lambda departure=False: seen.append(departure) or 0)
    assert cli.main(["evidence-v3"]) == 0 and cli.main(["evidence-v3", "--departure"]) == 0
    assert seen == [False, True] and rejected(["evidence-v3", "--year", "2024"])


def test_freeze_record_v3_calls_its_writer_with_the_rehearsal_run_id(monkeypatch):
    """The freeze record is written from the rehearsal it is given by run ID (docs/plan-v3.md, section 10.3); no run
    ID, or a path beside it, is refused before anything runs."""
    from valiwatt import freeze_record

    seen = []
    monkeypatch.setattr(freeze_record, "run_write_freeze_record", lambda run_id: seen.append(run_id) or 0)
    assert cli.main(["freeze-record-v3", "--rehearsal", "a" * 32]) == 0 and seen == ["a" * 32]
    assert rejected(["freeze-record-v3"]) and rejected(["freeze-record-v3", "--rehearsal", "a" * 32, "mlruns"])


def test_ledger_v3_syncs_v3_s_ledger_and_says_when_it_is_refused(monkeypatch, capsys):
    from valiwatt.report import ledger, ledger_v3
    from valiwatt.report.ledger import LedgerError

    seen = []
    monkeypatch.setattr(ledger, "sync", lambda *a, **k: seen.append("v2's ledger") or [])  # never v2's file
    monkeypatch.setattr(ledger_v3, "run_sync", lambda: seen.append("ledger-v3") or 0)
    assert cli.main(["ledger-v3"]) == 0 and seen == ["ledger-v3"] and rejected(["ledger-v3", "mlruns"])

    def refused():
        raise LedgerError("a stored run disagrees")

    monkeypatch.setattr(ledger_v3, "run_sync", refused)
    assert cli.main(["ledger-v3"]) == 1 and "REFUSED: a stored run disagrees" in capsys.readouterr().out


def test_dq_v3_passes_its_window_and_whether_it_runs_on_a_fresh_clone(monkeypatch):
    from valiwatt.quality import run_v3

    seen = []
    monkeypatch.setattr(run_v3, "run_dq_v3", lambda through, fresh_clone: seen.append((through, fresh_clone)) or 0)
    assert cli.main(["dq-v3"]) == 0 and cli.main(["dq-v3", "--fresh-clone"]) == 0
    assert seen == [(2024, False), (2024, True)]


# --- fetch -----------------------------------------------------------------------------------------------------------

def stand_in_sources(monkeypatch) -> list[tuple]:
    """The three sources as stand-ins recording what reaches them; like the real ones, ECCC and NBM refuse --force past
    2024."""
    calls: list[tuple] = []

    def weather(name, through, force):
        if force and through > 2024:
            raise FetchRefused(f"{name}: --force is refused through {through}")
        calls.append((name, through, force))

    monkeypatch.setattr(hydroquebec, "fetch", lambda force=False, through=2024: calls.append(("hq", through, force))
                        or {"rows": 0, "sha256": "0" * 64})
    monkeypatch.setattr(eccc, "fetch_station", lambda st, force=False, log=print, through=2024:
                        weather(f"eccc {st.climate_id}", through, force) or {"months": {}})
    monkeypatch.setattr(nbm, "fetch", lambda through=2024, force=False: weather("nbm", through, force) or {"years": {}})
    return calls


class Stop(Exception):
    """The stand-in HTTP layer's answer to a request: the request is recorded, and the fetch goes no further."""


def test_fetch_through_2025_goes_through_the_sources_gate(tmp_path, monkeypatch):
    """Through 2025 each real source runs its own gate before any request, and a gate that refuses leaves no request
    made. --force past 2024 reaches Hydro-Québec alone: with any other source it is refused before any source is
    called, so no half-run. The control: through 2024, --force reaches every source."""
    events: list[tuple] = []

    def gate(through):
        events.append(("gate", int(through)))

    def get(url, params=None, **kw):
        events.append(("get", url))
        raise Stop(url)

    for module in (hydroquebec, eccc, nbm):
        monkeypatch.setattr(module, "require_fetchable", gate)
        monkeypatch.setattr(module, "get", get)
    for name in ("CSV_PATH", "METADATA_PATH", "MANIFEST_PATH"):
        monkeypatch.setattr(hydroquebec, name, tmp_path / "hq" / getattr(hydroquebec, name).name)
    monkeypatch.setattr(eccc, "RAW_ECCC", tmp_path / "eccc")
    monkeypatch.setattr(nbm, "RAW_NBM", tmp_path / "nbm")
    monkeypatch.setattr(nbm, "MANIFEST_PATH", tmp_path / "nbm" / "manifest.json")
    for only in ("hq", "eccc", "nbm"):
        events.clear()
        with pytest.raises(Stop):
            cli.main(["fetch", "--through", "2025", "--only", only])
        assert events[0] == ("gate", 2025) and [e[0] for e in events[1:]] == ["get"], (only, events)

    def refusing(through):
        events.append(("gate", int(through)))
        raise FetchRefused("a fetch through 2025 runs only behind the frozen rules")

    for module in (hydroquebec, eccc, nbm):
        monkeypatch.setattr(module, "require_fetchable", refusing)
    events.clear()
    with pytest.raises(FetchRefused, match="behind the frozen rules"):
        cli.main(["fetch", "--through", "2025"])
    assert events == [("gate", 2025)]

    calls = stand_in_sources(monkeypatch)
    for only in ([], ["--only", "eccc"], ["--only", "nbm"]):
        with pytest.raises(FetchRefused, match="only `--only hq` may be forced, and no source has run"):
            cli.main(["fetch", "--through", "2025", "--force", *only])
        assert calls == [], only
    assert cli.main(["fetch", "--through", "2025", "--force", "--only", "hq"]) == 0
    assert calls == [("hq", 2025, True)]
    weather = [f"eccc {st.climate_id}" for st in stations()] + ["nbm"]
    for argv, through, force in ((["--through", "2025"], 2025, False), (["--through", "2024", "--force"], 2024, True),
                                 (["--force"], 2024, True), ([], 2024, False)):
        calls.clear()
        assert cli.main(["fetch", *argv]) == 0, argv
        assert calls == [("hq", through, force)] + [(w, through, force) for w in weather], argv


# --- report ----------------------------------------------------------------------------------------------------------

def test_report_refuses_a_2025_data_quality_run_named_by_id_before_any_2025_start(tmp_path, monkeypatch):
    """`valiwatt dq --through 2025`, `valiwatt dq --through 2024`, then `valiwatt report --dq-only --run-id <the first
    run>`, with no 2025 replay started: the render refuses, naming that run and the pre-start guard of the 2025 vintage,
    and writes nothing into the report folder. The data-quality runs are the real `run_dq` on synthetic sources in a
    scratch store (so they carry its tags and params); the ledger is a scratch file. The control: `report --dq-only`
    with no ID renders the through-2024 run, the latest."""
    from test_pipeline_refuses import isolate

    from valiwatt import tracking
    from valiwatt.config import DEMAND_LAST, data_last
    from valiwatt.quality import run as dq
    from valiwatt.report import ledger
    from valiwatt.report.render import ReportRefused

    raw = isolate(tmp_path, monkeypatch, 2025)  # scratch store and report folder, synthetic sources through 2025
    vintages = {str(dq.hydroquebec.csv_path(2024)): raw[raw["ts_utc"] <= DEMAND_LAST].reset_index(drop=True),
                str(dq.hydroquebec.csv_path(2025)): raw}
    monkeypatch.setattr(dq.hydroquebec, "load_raw", lambda path=None: vintages[str(path)])
    scratch = tmp_path / "replay-ledger.json"
    monkeypatch.setattr(ledger, "sync", partial(ledger.sync, scratch))
    monkeypatch.setattr(run, "LEDGER", str(scratch))  # the starts the guard reads: the scratch store and ledger
    asked, real = [], dq.run_dq
    monkeypatch.setattr(dq, "run_dq", lambda through=2024: asked.append(through) or real(through))
    reports = tmp_path / "reports"

    ids = {}
    for through in (2025, 2024):
        assert cli.main(["dq", "--through", str(through)]) == 0
        r = tracking.latest_run(dq.EXPERIMENT)
        assert (r.data.tags["kind"], r.data.tags["gates"], r.info.status) == ("dq", "passed", "FINISHED")
        assert int(r.data.params["through"]) == through
        assert r.data.params["window_last_utc"] == data_last(through).isoformat()
        ids[through] = r.info.run_id
    assert asked == [2025, 2024]

    with pytest.raises(ReportRefused) as refused:
        cli.main(["report", "--dq-only", "--run-id", ids[2025]])
    assert f"data-quality run {ids[2025]} reaches 2025, and no 2025 replay has started" in str(refused.value)
    assert not reports.exists() or not any(reports.iterdir())

    assert cli.main(["report", "--dq-only"]) == 0  # control
    assert sorted(p.name for p in reports.glob("dq.*.md")) == ["dq.en.md", "dq.fr.md"]
    assert ids[2024] in (reports / "dq.fr.md").read_text(encoding="utf-8")
