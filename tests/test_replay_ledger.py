"""The replay ledger (docs/replay-ledger.json): synced from a real MLflow store in a temporary directory, append-only,
refused when a stored run disagrees with its entry or when its content fails the public-safety scan; and read by the
report, which shows a departed replay the store lacks from its entry, labelled as recorded and not re-run."""

import json
from pathlib import Path
from types import SimpleNamespace

import jinja2
import pandas as pd
import pytest
from mlflow.exceptions import MlflowException
from mlflow.protos.databricks_pb2 import RESOURCE_DOES_NOT_EXIST
from test_report_lineage import CODE, FREEZE, RECORDED, RULES, clean_chain, fake_run, serve

from valiwatt import tracking
from valiwatt.monitor import run as monitor_run
from valiwatt.report import ledger, lineage
from valiwatt.report import validation as RV
from valiwatt.report.render import environment, filters

COMMIT_A, COMMIT_B = "a" * 40, "b" * 40


def results(mape=0.0423, coverage=0.894, recs=(), freeze_commit=COMMIT_A):
    """What a replay writes to results/replay.json, as far as the ledger reads it."""
    decisions = [{"week_start": f"2024-01-{d:02d}T00:00:00", "recommendation": None} for d in (1, 8)]
    decisions += [{"week_start": w + "T00:00:00", "recommendation": r} for w, r in recs]
    counts = {r: [x for _, x in recs].count(r) for r in sorted({x for _, x in recs})}
    return {"summary": {"mape": {"mean": mape}, "coverage": {"mean": coverage}, "recommendations": counts},
            "decisions": decisions, "freeze_tag": "rules-frozen", "freeze_commit": freeze_commit}


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A real MLflow store in a temporary directory, and a way to log a replay into it as `valiwatt replay` does."""
    import mlflow

    monkeypatch.setattr(tracking, "MLRUNS", tmp_path / "mlruns")

    def log(year=2024, kind="replay", freeze_commit=COMMIT_A, fail=False, departure_from=(), **kw):
        tags = {"kind": kind, "year": str(year), "rules_sha256": RULES, "freeze_commit": freeze_commit,
                "departure": str(bool(departure_from)).lower(), "departure_from": ",".join(departure_from)}
        try:
            with tracking.start_run(monitor_run.EXPERIMENT, kind, tags=tags) as run:
                mlflow.log_text(json.dumps(results(freeze_commit=freeze_commit, **kw)), "results/replay.json")
                if fail:
                    raise RuntimeError("the replay stopped")
                mlflow.set_tag("status", "complete")
        except RuntimeError:
            pass
        return run.info.run_id

    return log


def test_the_ledger_round_trips_every_finished_2024_replay_from_the_store(store, tmp_path):
    first = store(recs=[("2024-02-05", "investigate_data")])
    store(fail=True)  # a replay that did not finish
    store(year=2023, kind="pseudo_replay")  # the 2023 rehearsal
    second = store(mape=0.05, coverage=0.88, freeze_commit=COMMIT_B, departure_from=[first])
    path = tmp_path / "replay-ledger.json"
    entries = ledger.sync(path, root=tmp_path)
    assert [e["run_id"] for e in entries] == [first, second]
    assert ledger.read(path) == entries
    a, b = entries
    assert tuple(a) == ledger.FIELDS
    assert a["recommendations"] == [{"week_start": "2024-02-05", "recommendation": "investigate_data"}]
    assert (a["mape"], a["coverage"], a["status"], a["freeze_commit"], a["departure"]) == (
        0.0423, 0.894, "complete", COMMIT_A, False)
    assert a["code_sha256"] == tracking.code_sha256() and a["rules_sha256"] == RULES
    assert (b["departure"], b["departure_from"], b["mape"]) == (True, [first], 0.05)
    assert a["started_utc"] <= a["ended_utc"] <= b["started_utc"]
    before = path.read_bytes()
    assert ledger.sync(path, root=tmp_path) == entries and path.read_bytes() == before  # nothing new: unchanged


def test_the_ledger_is_append_only(store, tmp_path):
    """An entry the store no longer holds (a fresh clone) is kept as it was; new runs are appended after it."""
    path = tmp_path / "replay-ledger.json"
    old = {"run_id": "0" * 32, "year": 2024, "freeze_tag": "rules-frozen", "freeze_commit": "e" * 40,
           "rules_sha256": RULES, "code_sha256": CODE, "started_utc": "2020-01-01T00:00:00.000Z",
           "ended_utc": "2020-01-01T01:00:00.000Z", "status": "complete", "mape": 0.04, "coverage": 0.9,
           "recommendations": [], "departure": False, "departure_from": []}
    path.write_text(ledger.dumps([old]))
    new = store()
    entries = ledger.sync(path, root=tmp_path)
    assert entries[0] == old and [e["run_id"] for e in entries] == [old["run_id"], new]
    assert json.loads(path.read_text())["replays"][0] == old


def test_a_store_run_that_disagrees_with_its_ledger_entry_is_refused(store, tmp_path):
    path = tmp_path / "replay-ledger.json"
    store()
    ledger.sync(path, root=tmp_path)
    data = json.loads(path.read_text())
    data["replays"][0]["mape"] = 0.01  # the entry rewritten by hand
    path.write_text(json.dumps(data, indent=2) + "\n")
    before = path.read_bytes()
    store()  # a new run that would otherwise be appended
    with pytest.raises(ledger.LedgerError, match=r"disagrees with its ledger entry on \['mape'\]"):
        ledger.sync(path, root=tmp_path)
    assert path.read_bytes() == before


def test_a_ledger_whose_content_fails_the_scan_is_not_written(store, tmp_path):
    """A private name in the new content refuses the write; so does a field outside its fixed shape, such as a path
    standing in for the freeze tag."""
    path = tmp_path / "replay-ledger.json"
    store()
    (tmp_path / ".private-patterns").write_text("rules-frozen\n")
    with pytest.raises(ledger.LedgerError, match="public-safety scan"):
        ledger.sync(path, root=tmp_path)
    assert not path.exists()
    (tmp_path / ".private-patterns").unlink()
    entry = ledger.sync(path, root=tmp_path)[0]
    for field, value in (("freeze_tag", "/ho" + "me/someone/x"), ("status", "C:" + "\\Users\\x"), ("mape", "0.04")):
        with pytest.raises(ledger.LedgerError, match=field):
            ledger.check([{**entry, field: value}])
    with pytest.raises(ledger.LedgerError, match="exactly"):
        ledger.check([{**entry, "note": "/ho" + "me/someone"}])
    for name in ("/ho" + "me/someone/x", "do_nothing"):  # a recommendation outside the decision table: free text
        with pytest.raises(ledger.LedgerError, match="recommendations"):
            ledger.check([{**entry, "recommendations": [{"week_start": "2024-03-04", "recommendation": name}]}])
    with pytest.raises(ledger.LedgerError, match="departure_from"):  # a departure that departs from nothing
        ledger.check([{**entry, "departure": True, "departure_from": []}])
    with pytest.raises(ledger.LedgerError, match="departure_from"):  # earlier replays named, yet not a departure
        ledger.check([{**entry, "departure": False, "departure_from": ["1" * 32]}])
    later = {**entry, "run_id": "f" * 32, "started_utc": "2099-01-01T00:00:00.000Z"}
    ledger.check([entry, later])
    with pytest.raises(ledger.LedgerError, match="not sorted by start time"):
        ledger.check([later, entry])


def test_the_committed_ledger_is_well_formed_and_public_safe():
    text = ledger.LEDGER.read_text(encoding="utf-8")
    entries = ledger.read()
    assert text == ledger.dumps(entries)
    ledger.scan(text)


# The reader. A fake store whose get_run says "not found" for a run it lacks, as MLflow does, and serves each run's
# results/replay.json.
def results_store(monkeypatch, tmp_path, runs: dict, files: dict) -> None:
    serve(monkeypatch, runs)

    def get_run(run_id):
        if run_id not in runs:
            raise MlflowException(f"Run with id={run_id} not found", RESOURCE_DOES_NOT_EXIST)
        return runs[run_id]

    def download_artifacts(run_id, path, dst):
        out = Path(dst) / path
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix in (".parquet", ".csv"):  # a run's frames (the validation's out-of-sample hours, coefficients)
            frame = pd.DataFrame({"feature": pd.Series([], dtype=str)})
            frame.to_parquet(out) if out.suffix == ".parquet" else frame.to_csv(out, index=False)
        else:
            out.write_text(json.dumps(files[run_id]))
        return str(out)

    client = SimpleNamespace(get_run=get_run, download_artifacts=download_artifacts)
    monkeypatch.setattr(tracking, "client", lambda: client)


def timed(run, start=1_700_000_000_000):
    run.info.start_time, run.info.end_time = start, start + 3_600_000
    return run


EARLIER = "1" * 32


def departure_chain() -> dict:
    """A clean chain whose 2024 replay departs from EARLIER, a replay this store does not hold."""
    runs = clean_chain()
    runs["replay"].data.tags |= {"departure": "true", "departure_from": EARLIER}
    return runs


def earlier_entry(**kw) -> dict:
    run = timed(fake_run(EARLIER, kind="replay", year="2024", status="complete", freeze_commit="e" * 40,
                         rules_sha256=RULES, code_sha256=CODE))
    return {**ledger.entry(run, results(recs=[("2024-03-04", "retrain")], freeze_commit="e" * 40)), **kw}


class Unread(jinja2.ChainableUndefined):
    """A value the test's runs leave out. It renders as nothing and survives arithmetic, comparison and iteration, so
    the whole validation template renders around the departures, which are real."""
    __slots__ = ()

    def _same(self, *args, **kwargs):
        return self

    __add__ = __radd__ = __sub__ = __rsub__ = __mul__ = __rmul__ = __truediv__ = __rtruediv__ = _same
    __floordiv__ = __rfloordiv__ = __mod__ = __rmod__ = __pow__ = __rpow__ = __neg__ = __pos__ = __abs__ = _same
    __call__ = __round__ = _same
    __hash__ = object.__hash__

    def __lt__(self, other):
        return False

    __le__ = __gt__ = __ge__ = __lt__

    def __eq__(self, other):
        return other is self

    def __ne__(self, other):
        return other is not self

    def __format__(self, spec):
        return ""

    def __int__(self):
        return 0

    __index__ = __int__

    def __float__(self):
        return 0.0


def render_report(ctx: dict, lang: str) -> str:
    """validation.{lang}.md.j2 rendered from a context as `render_validation` renders it, in the report's own
    environment and filters, every value the context lacks left blank."""
    env = environment(lang)
    env.undefined = Unread

    def blank_on_unread(f):
        def g(*args, **kwargs):
            unread = any(isinstance(x, jinja2.Undefined) for x in (*args, *kwargs.values()))
            return Unread() if unread else f(*args, **kwargs)
        return g

    env.filters.update({name: blank_on_unread(f) for name, f in filters(lang).items()})
    return env.get_template(f"validation.{lang}.md.j2").render(**ctx, fig={}, lang=lang)


def renderable(runs: dict) -> dict:
    """The fake runs with what `validation.context` reads besides their tags: start times and parameters."""
    for run in runs.values():
        timed(run)
        run.data.params = {}
    return runs


LABEL = {"fr": "**Consigné dans le registre, non rejoué ici.**", "en": "**Recorded in the ledger, not re-run here.**"}


def test_a_departed_replay_absent_from_the_store_renders_from_the_ledger(monkeypatch, tmp_path):
    """A fresh clone: the store holds the departure replay but not the replay it departs from. The validation report,
    through `context` and its template, shows that one from its ledger entry, with its MAPE, coverage and
    recommendations, labelled as recorded and not re-run; a departed replay from the store gets no label."""
    runs = renderable(departure_chain())
    results_store(monkeypatch, tmp_path, runs, {run_id: {} for run_id in runs})
    monkeypatch.setattr(RV, "derive", lambda *payloads: {})  # the sentences computed from the payloads: not read here
    chain = lineage.follow(CODE, RULES, FREEZE, recorded=RECORDED, entries=[earlier_entry()])
    assert [type(d) for d in chain.departed] == [lineage.RecordedReplay]
    ctx = RV.context(chain, CODE, RULES)
    shown = ctx["departures"]
    assert len(shown) == 1 and shown[0]["recorded"] is True and shown[0]["id"] == EARLIER
    assert shown[0]["summary"] == {
        "mape": {"mean": 0.0423}, "coverage": {"mean": 0.894}, "recommendations": {"retrain": 1}}
    words = {"fr": ("Le rejeu `" + EARLIER + "` (règles du commit `eeeeeeeeeeee`)", "son EAMP de 4,23\u00a0%",
                    "sa couverture de 89,4\u00a0%", "ses recommandations (semaine du 2024-03-04 : réentraîner)"),
             "en": ("Replay `" + EARLIER + "` (rules of commit `eeeeeeeeeeee`)", "its MAPE of 4.23%",
                    "its coverage of 89.4%", "its recommendations (week of 2024-03-04: retrain)")}
    for lang, expected in words.items():
        text = render_report(ctx, lang)
        assert text.count(LABEL[lang]) == 1, (lang, text)
        paragraph = text[text.index(LABEL[lang]):].split("\n\n")[0]
        assert all(w in paragraph for w in expected), (lang, paragraph)
        stored = render_report({**ctx, "departures": [{**shown[0], "recorded": False}]}, lang)
        assert LABEL[lang] not in stored and EARLIER in stored  # a replay from the store: shown, with no label


def test_a_departed_replay_in_neither_the_store_nor_the_ledger_is_refused(monkeypatch, tmp_path):
    results_store(monkeypatch, tmp_path, departure_chain(), {})
    with pytest.raises(lineage.ChainBroken, match="neither in the store nor in the replay ledger"):
        lineage.follow(CODE, RULES, FREEZE, recorded=RECORDED, entries=[])
    with pytest.raises(lineage.ChainBroken, match="not complete"):
        lineage.follow(CODE, RULES, FREEZE, recorded=RECORDED, entries=[earlier_entry(status="unmarked")])


def test_a_replay_that_disagrees_with_its_ledger_entry_is_refused(monkeypatch, tmp_path):
    """The store and the ledger both hold the 2024 replay: its entry must be what the store says, field for field."""
    runs = clean_chain()
    replay = timed(runs["replay"])
    replay.info.run_id = "2" * 32  # a run ID of MLflow's shape, as the ledger requires
    replay.data.tags |= {"year": "2024", "status": "complete"}
    files = {replay.info.run_id: results(freeze_commit=FREEZE["freeze_commit"])}
    results_store(monkeypatch, tmp_path, runs, files)
    good = ledger.entry(replay, files[replay.info.run_id])
    assert lineage.follow(CODE, RULES, FREEZE, recorded="None yet.", entries=[good]).replay is runs["replay"]
    with pytest.raises(lineage.ChainBroken, match=r"disagrees with its ledger entry on \['coverage'\]"):
        lineage.follow(CODE, RULES, FREEZE, recorded="None yet.", entries=[{**good, "coverage": 0.95}])


def test_a_departed_replay_that_disagrees_with_its_ledger_entry_is_refused(monkeypatch, tmp_path):
    """The store and the ledger both hold the replay the 2024 replay departs from: its entry must be what the store
    says, field for field, or the chain is refused."""
    runs = departure_chain()
    runs[EARLIER] = timed(fake_run(EARLIER, kind="replay", year="2024", status="complete", freeze_commit="e" * 40,
                                   rules_sha256=RULES, code_sha256=CODE))
    files = {EARLIER: results(recs=[("2024-03-04", "retrain")], freeze_commit="e" * 40)}
    results_store(monkeypatch, tmp_path, runs, files)
    good = earlier_entry()
    assert lineage.follow(CODE, RULES, FREEZE, recorded=RECORDED, entries=[good]).departed == [runs[EARLIER]]
    with pytest.raises(lineage.ChainBroken, match=r"replay 1{32} disagrees with its ledger entry on \['mape'\]"):
        lineage.follow(CODE, RULES, FREEZE, recorded=RECORDED, entries=[{**good, "mape": 0.01}])


def test_ledger_and_report_commands_sync_before_rendering(monkeypatch):
    """`valiwatt report` syncs the ledger first and renders nothing when the sync is refused."""
    from valiwatt import cli
    from valiwatt.report import validation

    calls = []
    monkeypatch.setattr(ledger, "sync", lambda: calls.append("sync") or [])
    monkeypatch.setattr(validation, "render_validation", lambda dq_run_id=None: calls.append("render"))
    assert cli.main(["report"]) == 0 and calls == ["sync", "render"]
    calls.clear()

    def refused():
        raise ledger.LedgerError("disagrees")

    monkeypatch.setattr(ledger, "sync", refused)
    assert cli.main(["report"]) == 1 and calls == []
    assert cli.main(["ledger"]) == 1
