"""The replay ledger (docs/replay-ledger.json): synced from a real MLflow store in a temporary directory, append-only,
refused when a stored run disagrees with its entry or when its content fails the public-safety scan; and read by the
report, which shows a departed replay the store lacks from its entry, labelled as recorded and not re-run."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import report_payloads as RP
import report_store as S
import report_world as W
from mlflow.exceptions import MlflowException
from mlflow.protos.databricks_pb2 import RESOURCE_DOES_NOT_EXIST
from test_report_lineage import CODE, FREEZE, RECORDED, RULES, clean_chain, fake_run, serve

from valiwatt import config, tracking
from valiwatt.monitor import run as monitor_run
from valiwatt.paths import REPO_ROOT
from valiwatt.report import ledger, lineage
from valiwatt.report import validation as RV
from valiwatt.report.render import ReportRefused, environment, filters

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
    W.install_lane_d(monkeypatch)

    def log(year=2024, kind="replay", freeze_commit=COMMIT_A, fail=False, departure_from=(), complete=True, **kw):
        tags = {"kind": kind, "year": str(year), "rules_sha256": RULES, "freeze_commit": freeze_commit,
                "departure": str(bool(departure_from)).lower(), "departure_from": ",".join(departure_from)}
        if not complete:  # a run that ends without marking itself complete: MLflow FINISHED, its status `started`
            tags["status"] = "started"
        try:
            with tracking.start_run(monitor_run.EXPERIMENT, kind, tags=tags) as run:
                mlflow.log_text(json.dumps(results(freeze_commit=freeze_commit, **kw)), "results/replay.json")
                if fail:
                    raise RuntimeError("the replay stopped")
                if complete:
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


def test_a_finished_replay_not_marked_complete_stays_out_of_the_ledger(store, tmp_path):
    """A finished replay is MLflow FINISHED and tagged complete: a run MLflow calls FINISHED whose status tag still
    says `started` (it never reached its end) enters neither the store's entries nor the ledger."""
    done = store()
    started = store(complete=False)
    assert tracking.client().get_run(started).info.status == "FINISHED"
    assert [e["run_id"] for e in ledger.from_store()] == [done]
    assert [e["run_id"] for e in ledger.sync(tmp_path / "replay-ledger.json", root=tmp_path)] == [done]


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


LABEL = {"fr": "**Consigné dans le registre, non rejoué ici.**", "en": "**Recorded in the ledger, not re-run here.**"}
REC = {"fr": {"retrain": "réentraîner", "recalibrate": "recalibrer les intervalles",
              "investigate_data": "examiner les données"},
       "en": {"retrain": "retrain", "recalibrate": "recalibrate the intervals", "investigate_data": "investigate the data"}}


def test_a_departed_replay_absent_from_the_store_renders_from_the_ledger(monkeypatch):
    """A fresh clone: the store holds v2's departure replay of 2024 but not the replay it departs from, v2's first,
    which the ledger holds. The validation report, through `context`, its templates and its own checks, shows that one
    from its ledger entry, with its MAPE, coverage and recommendations, labelled as recorded and not re-run; a departed
    replay from the store gets no label."""
    w = S.World().install(monkeypatch)
    first = w.entry(S.REPLAY)
    w.entries.append(first)
    for run_id in (S.REPLAY, f"diag-{S.REPLAY}"):  # this copy's store lacks v2's first 2024 replay
        del w.runs[run_id]
    departure = "c" * 32
    w.add_replay(departure, w.payloads["r"], freeze_commit=W.F1, departure="true", departure_from=S.REPLAY)
    w.add_diagnostics(departure, w.payloads["diag"]["replay"])
    w.section = S.NONE_RECORDED.replace("None yet.\n", S.departure_entry("2026-11-20", [W.F0, W.F1]))
    chain = lineage.follow(S.CODE, S.RULES, {"freeze_tag": S.TAG, "freeze_commit": W.F1}, recorded=w.section,
                           entries=list(w.entries), starts=[])
    assert chain.replay.info.run_id == departure and [type(d) for d in chain.departed] == [lineage.RecordedReplay]
    ctx = RV.context(chain, S.CODE, S.RULES, list(w.entries), [])
    shown = ctx["departures"]
    assert len(shown) == 1 and shown[0]["recorded"] is True and shown[0]["id"] == S.REPLAY
    assert shown[0]["summary"] == {"mape": {"mean": first["mape"]}, "coverage": {"mean": first["coverage"]},
                                   "recommendations": ledger.recommendation_counts(first["recommendations"])}
    assert first["recommendations"] and ctx["dep_entries"][0]["to"] == W.F1[:12]
    for lang in ("fr", "en"):
        f = filters(lang)
        recs = [(f"semaine du {x['week_start']} : " if lang == "fr" else f"week of {x['week_start']}: ")
                + REC[lang][x["recommendation"]] for x in first["recommendations"]]
        mape, coverage = f["pct"](first["mape"], 2), f["pct"](first["coverage"], 1)
        expected = ((f"Le rejeu `{S.REPLAY}` (règles du commit `{W.F0[:12]}`)", f"son EAMP de {mape}",
                     f"sa couverture de {coverage}") if lang == "fr" else
                    (f"Replay `{S.REPLAY}` (rules of commit `{W.F0[:12]}`)", f"its MAPE of {mape}",
                     f"its coverage of {coverage}"))
        text = RP.render(ctx, lang)
        assert text.count(LABEL[lang]) == 1, lang
        paragraph = text[text.index(LABEL[lang]):].split("\n\n")[0]
        assert all(x in paragraph for x in (*expected, *recs)), (lang, paragraph)
        stored = RP.render({**ctx, "departures": [{**shown[0], "recorded": False}]}, lang)
        assert LABEL[lang] not in stored and S.REPLAY in stored  # a replay from the store: shown, with no label


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


# --- v2's entries (docs/plan-v2.md, section 10.5) ------------------------------------------------------------------

V1_TAG, V2_TAG = "rules-frozen", "rules-frozen-v2"


def v1_entries_at(tag: str = "v1") -> list[dict]:
    """The v1 entries of the ledger as committed at `tag`."""
    text = subprocess.run(["git", "show", f"{tag}:docs/replay-ledger.json"], cwd=REPO_ROOT, capture_output=True,
                          text=True, check=True).stdout
    return [e for e in json.loads(text)["replays"] if e["freeze_tag"] == V1_TAG]


def v1_changes(entries: list[dict], at_v1: list[dict]) -> list[str]:
    """How the v1 entries of `entries` differ from those at tag v1: each entry added, removed or changed."""
    now = {e["run_id"]: e for e in entries if e["freeze_tag"] == V1_TAG}
    then = {e["run_id"]: e for e in at_v1}
    return ([f"added {k}" for k in now if k not in then] + [f"removed {k}" for k in then if k not in now]
            + [f"changed {k}: {ledger.disagreement(now[k], then[k])}" for k in now if k in then and now[k] != then[k]])


def test_the_v1_entries_equal_those_of_the_ledger_at_tag_v1():
    """v1's entries are never touched (plan section 10.5): the ledger's `rules-frozen` entries equal those of the file
    at tag `v1`, each with v1's fourteen fields and its year 2024. Break: one v1 entry edited."""
    at_v1 = v1_entries_at()
    assert at_v1 and v1_changes(ledger.read(), at_v1) == []
    assert all(tuple(e) == ledger.FIELDS and e["year"] == 2024 for e in at_v1)
    edited = [dict(e) for e in ledger.read()]
    edited[0]["coverage"] = round(edited[0]["coverage"] + 0.01, 6)
    assert v1_changes(edited, at_v1) == [f"changed {edited[0]['run_id']}: ['coverage']"]
    assert v1_changes(edited[1:], at_v1) == [f"removed {edited[0]['run_id']}"]


def test_every_freeze_has_one_shape_and_its_years_come_from_the_configuration():
    """Each freeze the configuration lists has one shape, in exactly one ledger: v1's and v2's here, v3's in its own
    (report/ledger_v3.py, docs/plan-v3.md section 10.3), so v2's ledger never takes v3's shape by default."""
    from valiwatt.report import ledger_v3

    assert set(ledger.SHAPES) | set(ledger_v3.SHAPES) == set(config.FREEZES)
    assert not set(ledger.SHAPES) & set(ledger_v3.SHAPES)
    assert ledger.YEARS == {t: tuple(f["years"]) for t, f in config.FREEZES.items()}
    assert ledger.V2_FIELDS[:len(ledger.FIELDS)] == ledger.FIELDS and len(ledger.V2_FIELDS) == 19
    assert ledger.fields_for(V1_TAG) == ledger.FIELDS and ledger.fields_for(V2_TAG) == ledger.V2_FIELDS


def v2_run(run_id="3" * 32, year=2024, start=1_800_000_000_000, **tags):
    base = {"kind": "replay", "year": str(year), "freeze_tag": V2_TAG, "freeze_commit": W.F0, "rules_sha256": RULES,
            "code_sha256": CODE, "status": "complete", "departure": "false", "departure_from": "",
            "evaluation": "seen_by_developers" if year == 2024 else "held_out", "coefficient_sha256": W.MODEL}
    if year == 2025:
        base["standing"] = "clean"
    return timed(fake_run(run_id, **(base | tags)), start)


def v2_results(verdict=("better_cold_untested", "not_shown_better"), freeze_commit=W.F0, **kw):
    return {**results(freeze_commit=freeze_commit, **kw), "freeze_tag": V2_TAG, "frozen_sha256": W.FROZEN,
            "verdict": {"shipped": verdict[0], "family": verdict[1], "label": "seen_by_developers"}}


def test_a_v2_entry_holds_v2_fields_in_order(monkeypatch):
    """v2's nineteen fields, in order: v1's fourteen, then the label copied from the run tag `evaluation`, the
    production model's hash from `coefficient_sha256`, the two verdicts, one digest of the frozen artifacts' hashes,
    and the standing from the run tag ("" for 2024)."""
    W.install_lane_d(monkeypatch)
    e = ledger.entry(v2_run(), v2_results(recs=[("2024-02-05", "retrain")]))
    assert tuple(e) == ledger.V2_FIELDS
    assert (e["evaluation"], e["model_sha256"], e["verdict"], e["frozen_sha256"], e["standing"]) == (
        "seen_by_developers", W.MODEL, {"shipped": "better_cold_untested", "family": "not_shown_better"},
        monitor_run.frozen_digest(W.FROZEN), "")
    assert e["frozen_sha256"] == W.DIGEST
    held = ledger.entry(v2_run("4" * 32, 2025, start=1_800_000_100_000), v2_results())
    assert (held["evaluation"], held["standing"]) == ("held_out", "clean")
    with pytest.raises(ledger.LedgerError, match="different freeze tags"):
        ledger.entry(v2_run(freeze_tag=V1_TAG), v2_results())
    with pytest.raises(ledger.LedgerError, match="frozen_sha256"):
        ledger.entry(v2_run(), {**v2_results(), "frozen_sha256": None})


def test_a_v2_entry_of_another_year_is_refused():
    """Each freeze's entries are of the years it replays (config.FREEZES): v2's 2024 and 2025, v1's 2024 only."""
    for year in (2024, 2025):
        ledger.check_entry(W.v2_entry("5" * 32, year))
    for year in (2023, 2026):
        with pytest.raises(ledger.LedgerError, match="year"):
            ledger.check_entry({**W.v2_entry("5" * 32), "year": year})
    v1 = W.v2_entry("5" * 32)
    v1 = {k: v1[k] for k in ledger.FIELDS} | {"freeze_tag": V1_TAG}
    ledger.check_entry(v1)
    with pytest.raises(ledger.LedgerError, match="year"):
        ledger.check_entry({**v1, "year": 2025})


def test_an_entry_of_an_unknown_freeze_tag_is_refused():
    for tag in ("rules-frozen-v3", "", None):
        with pytest.raises(ledger.LedgerError, match="freeze tag"):
            ledger.fields_for(tag)
        with pytest.raises(ledger.LedgerError, match="freeze tag"):
            ledger.check_entry({**W.v2_entry("5" * 32), "freeze_tag": tag})
    with pytest.raises(ledger.LedgerError, match="object"):
        ledger.check_entry(["not", "an", "entry"])


def test_a_v1_entry_with_v2_fields_is_refused():
    with pytest.raises(ledger.LedgerError, match="exactly"):
        ledger.check_entry({**W.v2_entry("5" * 32), "freeze_tag": V1_TAG})
    v2 = W.v2_entry("5" * 32)
    with pytest.raises(ledger.LedgerError, match="exactly"):  # and a v2 entry with v1's fields only
        ledger.check_entry({k: v2[k] for k in ledger.FIELDS})


def test_a_v2_entry_without_its_evaluation_label_is_refused():
    e = W.v2_entry("5" * 32)
    with pytest.raises(ledger.LedgerError, match="exactly"):
        ledger.check_entry({k: v for k, v in e.items() if k != "evaluation"})
    for label in ("", None, "in_sample_for_choice", "rehearsal"):
        with pytest.raises(ledger.LedgerError, match="evaluation"):
            ledger.check_entry({**e, "evaluation": label})


def test_a_v2_entry_holds_only_hashes_and_verdict_words():
    e = W.v2_entry("5" * 32)
    for field, value in (("model_sha256", "x" * 64), ("frozen_sha256", "abc"), ("verdict", {"shipped": "better"}),
                         ("verdict", {"shipped": "better", "family": "fine"}),
                         ("verdict", {"family": "better", "shipped": "better"})):
        with pytest.raises(ledger.LedgerError, match=field):
            ledger.check_entry({**e, field: value})


def first_2024(**kw):
    return W.v2_entry("a" * 32, 2024, W.utc(0), **kw)


def test_a_v2_label_other_than_its_starts_give_is_refused():
    """`check` with the starts, one case each: a 2024 entry labelled held_out; a second 2025 entry labelled held_out
    after a finished one; a held_out entry after a stopped earlier start whose gate's record holds no ruling naming
    it."""
    held = {**first_2024(), "evaluation": "held_out"}
    with pytest.raises(ledger.LedgerError, match="evaluation"):
        ledger.check([held], [])
    one = W.v2_entry("b" * 32, 2025, W.utc(10))
    two = W.v2_entry("c" * 32, 2025, W.utc(11))  # labelled held_out, after a finished start
    starts = [W.start(one["run_id"], one["started_utc"], True, W.gate()),
              W.start(two["run_id"], two["started_utc"], True, W.gate())]
    ledger.check_entry(two)  # well formed on its own: only its starts refuse it
    for s in (starts, None):
        with pytest.raises(ledger.LedgerError, match="give 'seen_by_developers'"):
            ledger.check([first_2024(), one, two], s)
    stopped = W.start("d" * 32, W.utc(9), False, W.gate(), stage="inputs_read", error="KeyError")
    record = W.start(one["run_id"], one["started_utc"], True, W.gate())  # no ruling naming the stopped start
    with pytest.raises(ledger.LedgerError, match="needs the owner's ruling naming it"):
        ledger.check([first_2024(), one], [stopped, record])
    ledger.check([first_2024(), one], None)  # the ledger alone cannot see the stopped start: only the store can


def test_a_post_hoc_test_of_record_labelled_held_out_is_refused():
    """By `check_entry`, and by `check` with the starts and with None; a clean test of record labelled `post_hoc`
    too. Control: a first start ruled post hoc on (a), labelled `post_hoc`, passes."""
    lie = W.v2_entry("b" * 32, 2025, W.utc(10), evaluation="held_out", standing="post_hoc")
    with pytest.raises(ledger.LedgerError, match="evaluation"):
        ledger.check_entry(lie)
    post = W.gate("post_hoc", ["a"], W.ruling("post_hoc"))
    for s in ([W.start(lie["run_id"], lie["started_utc"], True, post)], None):
        with pytest.raises(ledger.LedgerError, match="evaluation"):
            ledger.check([first_2024(), lie], s)
    with pytest.raises(ledger.LedgerError, match="evaluation"):
        ledger.check_entry(W.v2_entry("b" * 32, 2025, W.utc(10), evaluation="post_hoc", standing="clean"))
    ok = W.v2_entry("b" * 32, 2025, W.utc(10), standing="post_hoc")
    assert ok["evaluation"] == "post_hoc"
    ledger.check_entry(ok)
    for s in ([W.start(ok["run_id"], ok["started_utc"], True, post)], None):
        assert ledger.check([first_2024(), ok], s) == [first_2024(), ok]
    with pytest.raises(ledger.LedgerError, match="standing"):  # a gate that logged another standing than the entry's
        ledger.check([first_2024(), ok], [W.start(ok["run_id"], ok["started_utc"], True, W.gate("clean"))])


@pytest.mark.parametrize("outcome, label", [("clean", "held_out"), ("post_hoc", "post_hoc")])
def test_a_ruled_test_of_record_after_a_stopped_start_passes(outcome, label):
    """Control: the stopped start, then a start whose gate's record holds the owner's ruling naming it: the ruled start
    is the test of record, `held_out` under a clean ruling and `post_hoc` under a post-hoc one, the standing in the
    entry."""
    stopped = W.start("d" * 32, W.utc(9), False, W.gate(), stage="replayed", error="ValueError")
    g = W.gate(outcome, ["f"], W.ruling(outcome, start=stopped["started_utc"]))
    e = W.v2_entry("b" * 32, 2025, W.utc(10), standing=outcome)
    assert (e["evaluation"], e["standing"]) == (label, outcome)
    assert ledger.check([first_2024(), e], [stopped, W.start(e["run_id"], e["started_utc"], True, g)])
    wrong = W.gate(outcome, ["f"], W.ruling(outcome, start=W.utc(8)))  # a ruling naming another start
    with pytest.raises(ledger.LedgerError, match="ruling naming it"):
        ledger.check([first_2024(), e], [stopped, W.start(e["run_id"], e["started_utc"], True, wrong)])


def test_a_later_2025_entry_labelled_seen_by_developers_passes():
    one = W.v2_entry("b" * 32, 2025, W.utc(10))
    rerun = W.v2_entry("c" * 32, 2025, W.utc(11), evaluation="seen_by_developers", standing="clean")
    starts = [W.start(one["run_id"], one["started_utc"], True, W.gate()),
              W.start(rerun["run_id"], rerun["started_utc"], True, W.gate())]
    for s in (starts, None):
        assert ledger.check([first_2024(), one, rerun], s)
    stopped_rerun = W.start("e" * 32, W.utc(12), False, W.gate())  # a later start that stopped changes nothing
    assert ledger.check([first_2024(), one, rerun], starts + [stopped_rerun])


def test_a_2024_entry_with_a_standing_or_a_2025_entry_without_one_is_refused():
    for standing in ("clean", "post_hoc"):
        with pytest.raises(ledger.LedgerError, match="standing"):
            ledger.check_entry({**first_2024(), "standing": standing})
    for standing in ("", "unknown", None):
        with pytest.raises(ledger.LedgerError, match="standing"):
            ledger.check_entry({**W.v2_entry("b" * 32, 2025), "standing": standing})


def test_a_non_departure_v2_entry_under_another_freeze_commit_is_refused():
    later = W.v2_entry("b" * 32, 2024, W.utc(1), freeze_commit=W.F1)
    with pytest.raises(ledger.LedgerError, match="not a departure, yet its freeze commit"):
        ledger.check([first_2024(), later])
    held = W.v2_entry("c" * 32, 2025, W.utc(10), freeze_commit=W.F1)
    with pytest.raises(ledger.LedgerError, match="not a departure, yet its freeze commit"):
        ledger.check([first_2024(), held])
    departed = W.v2_entry("b" * 32, 2024, W.utc(1), freeze_commit=W.F1, departure_from=["a" * 32])
    assert ledger.check([first_2024(), departed])  # control: a departure moves the freeze commit
    v1 = {k: v for k, v in first_2024().items() if k in ledger.FIELDS} | {
        "freeze_tag": V1_TAG, "run_id": "9" * 32, "started_utc": W.utc(5), "ended_utc": W.end(W.utc(5)),
        "freeze_commit": W.F2}
    assert ledger.check([first_2024(), v1])  # v1's entries never count for v2's commit


def test_a_v2_2025_entry_before_any_v2_2024_entry_is_refused():
    held = W.v2_entry("b" * 32, 2025, W.utc(10))
    with pytest.raises(ledger.LedgerError, match="starts before any rules-frozen-v2 replay of 2024"):
        ledger.check([held])
    early = W.v2_entry("b" * 32, 2025, W.utc(0, month=10))
    with pytest.raises(ledger.LedgerError, match="starts before any rules-frozen-v2 replay of 2024"):
        ledger.check([early, first_2024()])
    assert ledger.check([first_2024(), held])


@pytest.mark.parametrize("field, value", [("evaluation", "seen_by_developers"), ("model_sha256", "f" * 64),
                                          ("verdict", {"shipped": "worse", "family": "better"}),
                                          ("frozen_sha256", "0" * 64), ("standing", "post_hoc")])
def test_a_store_run_differing_from_its_v2_entry_in_one_v2_field_is_refused(field, value):
    recorded = W.v2_entry("b" * 32, 2025, W.utc(10))
    stored = {**recorded, field: value}
    assert ledger.disagreement(stored, recorded) == [field]
    with pytest.raises(ledger.LedgerError, match=rf"disagrees with its ledger entry on \['{field}'\]"):
        ledger.merge([first_2024(), recorded], [stored])
    assert ledger.merge([first_2024(), recorded], [recorded]) == [first_2024(), recorded]  # control


def test_the_earliest_started_2025_replay_is_the_test_of_record():
    """Break: after a finished first start, a later finished start offered as the record. The walk keeps the first;
    the later one is a rerun, and a ledger labelling it held out is refused."""
    one = W.v2_entry("b" * 32, 2025, W.utc(10))
    two = W.v2_entry("c" * 32, 2025, W.utc(11), evaluation="seen_by_developers")
    starts = [W.start(two["run_id"], two["started_utc"], True, W.gate()),  # out of order: the walk sorts them
              W.start(one["run_id"], one["started_utc"], True, W.gate())]
    entries = [first_2024(), one, two]
    rec = ledger.of_record(entries, starts)
    assert rec["record"]["run_id"] == one["run_id"] and rec["entry"] == one
    assert rec["reruns"] == [two] and rec["stopped"] == [] and rec["unfinished"] == []
    with pytest.raises(ledger.LedgerError, match="give 'seen_by_developers'"):
        ledger.check([first_2024(), one, {**two, "evaluation": "held_out"}], starts)
    assert ledger.of_record(entries, []) is None
    with pytest.raises(ReportRefused, match="no entry"):  # a finished start the ledger lacks
        ledger.of_record([first_2024(), two], starts)


def test_a_stopped_first_start_gives_way_only_to_the_start_its_ruling_names():
    stopped = W.start("d" * 32, W.utc(9), False, W.gate(), stage="replayed", error="KeyError")
    g = W.gate("clean", ["f"], W.ruling("clean", start=stopped["started_utc"]))
    e = W.v2_entry("b" * 32, 2025, W.utc(10))
    second = W.start(e["run_id"], e["started_utc"], True, g)
    rec = ledger.of_record([first_2024(), e], [stopped, second])
    assert rec["record"] == second and rec["entry"] == e
    assert rec["stopped"] == [{**stopped, "ruling": g["ruling"]}]
    assert [s["run_id"] for s in rec["unfinished"]] == [stopped["run_id"]]
    assert (rec["stopped"][0]["stage"], rec["stopped"][0]["error"]) == ("replayed", "KeyError")
    running = {**second, "finished": False, "stage": "inputs_read"}
    rec = ledger.of_record([first_2024()], [stopped, running])
    assert rec["record"] == running and rec["entry"] is None and rec["stopped"][0]["run_id"] == stopped["run_id"]
    assert [s["run_id"] for s in rec["unfinished"]] == [stopped["run_id"], running["run_id"]]
    rec = ledger.of_record([first_2024()], [stopped])  # nothing after it yet: the stopped start is the record
    assert rec["record"] == stopped and rec["entry"] is None and rec["stopped"] == []
    for bad in (W.gate("clean"), W.gate("clean", ["f"], W.ruling("clean", start=W.utc(8))),
                W.gate("clean", ["a"], W.ruling("clean", start=stopped["started_utc"]))):
        with pytest.raises(ReportRefused, match="no owner's ruling naming that start"):
            ledger.of_record([first_2024(), e], [stopped, {**second, "gate": bad}])


def test_a_finished_v2_replay_in_the_store_round_trips_with_its_starts(store, tmp_path, monkeypatch):
    """A real store: v2's first 2024 replay and the 2025 test of record, its gate's record logged when it opened. The
    sync writes both v2 entries, the 2025 label checked against the store's starts; in another store, a start that
    stopped before the record, which then ran with no ruling naming it, refuses the write."""
    import mlflow

    def log_v2(year, standing=None, gate_record=None, finish=True):
        tags = {"kind": "replay", "year": str(year), "rules_sha256": RULES, "freeze_tag": V2_TAG,
                "freeze_commit": W.F0, "departure": "false", "departure_from": "", "coefficient_sha256": W.MODEL,
                "evaluation": "seen_by_developers" if year == 2024 else "held_out", "status": "started"}
        if standing:
            tags["standing"] = standing
        try:
            with tracking.start_run(monitor_run.EXPERIMENT, "replay", tags=tags) as run:
                if gate_record is not None:
                    mlflow.log_text(json.dumps(gate_record), "results/held_out_gate.json")
                if not finish:
                    raise RuntimeError("stopped")
                mlflow.log_text(json.dumps(v2_results()), "results/replay.json")
                mlflow.set_tag("status", "complete")
        except RuntimeError:
            pass
        return run.info.run_id

    path = tmp_path / "replay-ledger.json"
    first = log_v2(2024)
    held = log_v2(2025, "clean", W.gate())
    entries = ledger.sync(path, root=tmp_path)
    assert [(e["run_id"], e["evaluation"], e["standing"]) for e in entries] == [
        (first, "seen_by_developers", ""), (held, "held_out", "clean")]
    assert ledger.read(path) == entries and all(tuple(e) == ledger.V2_FIELDS for e in entries)
    assert ledger.of_record(entries, ledger.starts(ledger={"replays": entries}))["entry"]["run_id"] == held
    path.unlink()
    monkeypatch.setattr(tracking, "MLRUNS", tmp_path / "other" / "mlruns")
    log_v2(2024)
    log_v2(2025, "clean", W.gate(), finish=False)
    log_v2(2025, "clean", W.gate())
    with pytest.raises(ledger.LedgerError, match="ruling naming it"):
        ledger.sync(path, root=tmp_path)
    assert not path.exists()


# v2's first 2024 replay, named from the committed ledger before any 2025 start (validation.first_2024).
def test_v2s_first_2024_replay_is_named_and_a_rerun_held_to_its_printed_results_and_hashes():
    first = W.v2_entry("1" * 32, 2024, W.utc(0))
    same = W.v2_entry("2" * 32, 2024, W.utc(1))
    view = RV.first_2024([first, same], W.FREEZE_TAG, same)
    assert view["run_id"] == first["run_id"] and not view["same"] and view["differences"] == []
    other = W.v2_entry("2" * 32, 2024, W.utc(1), mape=0.0399, model_sha256="9" * 64, frozen_sha256="8" * 64)
    view = RV.first_2024([other, first], W.FREEZE_TAG, other)  # the earliest start, whatever the order
    assert view["run_id"] == first["run_id"]
    assert view["differences"] == ["mape", "model_sha256", "frozen_sha256"]
    view = RV.first_2024([first], W.FREEZE_TAG, first)
    assert view["same"] and view["differences"] == []


def test_every_later_v2_2024_entry_is_named_with_what_it_changes_from_the_first():
    first = W.v2_entry("1" * 32, 2024, W.utc(0))
    same = W.v2_entry("2" * 32, 2024, W.utc(1))
    other = W.v2_entry("3" * 32, 2024, W.utc(2), mape=0.0399)
    chain = W.v2_entry("4" * 32, 2024, W.utc(3))
    view = RV.first_2024([chain, other, first, same], W.FREEZE_TAG, chain)
    assert [x["run_id"] for x in view["others"]] == [same["run_id"], other["run_id"]]  # earliest first, chain's apart
    assert [x["differences"] for x in view["others"]] == [[], ["mape"]]
    assert RV.first_2024([first, chain], W.FREEZE_TAG, chain)["others"] == []
    assert [x["run_id"] for x in RV.first_2024([first, same], W.FREEZE_TAG, first)["others"]] == [same["run_id"]]


@pytest.mark.parametrize("lang", ("fr", "en"))
def test_section_8_names_every_v2_2024_rerun_of_the_ledger_and_keeps_its_paragraphs_apart(lang):
    ctx = RP.state("none")
    chain = dict(ctx["first24"]["chain"])
    mid = W.v2_entry("5" * 32, 2024, W.utc(1), mape=0.0399)
    late = {**chain, "started_utc": W.utc(2)}
    ctx["first24"] = RV.first_2024([RP.FIRST_2024, mid, late], W.FREEZE_TAG, late)
    eight = RV.numbered_section(RP.render(ctx, lang), 8)
    assert eight.count(f"`{mid['run_id']}`") == 2  # in the paragraph and in the table
    assert ("differs from the first on MAPE" if lang == "en" else "diffère du premier sur l'EAMP") in eight
    side = "**2024 side by side.**" if lang == "en" else "**2024 côte à côte.**"
    assert f".\n\n{side}" in eight  # the first replay's paragraph ends before the table's heading


@pytest.mark.parametrize("lang", ("fr", "en"))
def test_once_2025_has_started_section_8_still_names_every_v2_2024_rerun_of_the_ledger(lang):
    rerun = RP.rerun24_view()
    chain = {k: rerun[k] for k in ledger.V2_FIELDS}
    mid = W.v2_entry("5" * 32, 2024, W.utc(2), mape=0.0399)
    ctx = RP.context(held_view=RP.clean_record(), rec24=RP.rec24_view(), rerun24=rerun,
                     first24=RV.first_2024([RP.FIRST_2024, chain, mid], W.FREEZE_TAG, chain))
    text = RP.render(ctx, lang)
    eight = RV.numbered_section(text, 8)
    assert eight.count(f"`{mid['run_id']}`") == 2  # in the paragraph and in the table
    assert eight.count(f"`{RP.FIRST_2024['run_id']}`") == 2 and eight.count(f"`{rerun['run_id']}`") == 2  # once each
    assert ("It differs from the record on MAPE" if lang == "en" else "Il diffère du rejeu de référence sur l'EAMP") \
        in eight
    side = "**2024 side by side.**" if lang == "en" else "**2024 côte à côte.**"
    assert f".\n\n{side}" in eight
    with pytest.raises(ReportRefused, match="not named in section 8"):
        RV.check_rendered(text.replace(mid["run_id"], "0" * 32), lang, ctx, "validation",
                          RV.status_sentence(environment(lang), ctx, lang))


def test_a_v2_ledger_with_v2_entries_but_no_v2_2024_entry_is_refused():
    chain = W.v2_entry("2" * 32, 2024, W.utc(1))
    with pytest.raises(ReportRefused, match="first replayed year"):
        RV.first_2024([RP.V1_PLAIN, W.v2_entry("3" * 32, 2025)], W.FREEZE_TAG, chain)
    assert RV.first_2024([RP.V1_PLAIN], W.FREEZE_TAG, chain) is None  # no v2 entry yet: `valiwatt report` syncs first
    assert RV.first_2024([RP.V1_PLAIN], ledger.V1_TAG, RP.V1_PLAIN) is None  # v1 names no first v2 replay


@pytest.mark.parametrize("lang", ("fr", "en"))
def test_the_report_names_v2s_first_2024_replay_in_sections_8_and_9(lang):
    ctx = RP.state("none")
    text = RP.render(ctx, lang)
    first = ctx["first24"]["run_id"]
    assert first in RV.numbered_section(text, 8) and first in RV.numbered_section(text, 9)
    assert ctx["first24"]["chain"]["run_id"] in RV.numbered_section(text, 8)
    with pytest.raises(ReportRefused, match="first 2024 replay"):
        RV.check_rendered(text.replace(first, "0" * 32), lang, ctx, "validation",
                          RV.status_sentence(environment(lang), ctx, lang))
    same = RP.context(first24="same")
    text = RP.render(same, lang)
    assert same["first24"]["run_id"] in RV.numbered_section(text, 8)
    assert ("rerun" if lang == "en" else "refait") not in RV.numbered_section(text, 8).split("|")[0]


def test_a_replayed_years_weekly_coverage_counts_its_judged_weeks_below_the_nominal_by_season():
    def week(start, covered, evaluated=True):
        return {"week_start": start, "evaluated": evaluated, "stats": {"covered": covered}}

    weeks = [week("2024-01-01", 0.70), week("2024-01-08", 0.95), week("2024-07-01", 0.89),
             week("2024-07-08", 0.50, evaluated=False), week("2024-02-26", 0.80)]  # Thursday 29 February: winter
    got = RV.weekly_coverage(weeks, 0.9)
    assert got == {"judged": 4, "below": 3, "seasons": {"winter": {"judged": 3, "below": 2},
                                                        "summer": {"judged": 1, "below": 1}}}
