"""The report rendered from what the replay's own code writes (docs/plan-v2.md, sections 9.2, 11.3.5 and 13): the
2023 rehearsal, a 2024 replay and the clean 2025 test of record, each run by `run_replay` on the synthetic chain of
tests/replay_world.py, with its diagnostic run (the run-reuse measure itself, a placement of it constructible), the
2025 gate's record and the ledger entries `report/ledger.py` writes from them; then the validation report and the
story, in both languages, through the render's own path (`validation.render_pages`: the templates under
StrictUndefined, `check_rendered`) and its figures. No replay, diagnostic or gate result is written by hand, so a key
a template reads that the replay's code does not write fails here.

What is not under test here is given as the report's other tests give it: the validation, data-quality, breaks and
frozen-model runs are tests/report_payloads.py's (their producers are tested on their own, and the chain's synthetic
validation cannot also be the rehearsal's), and the lineage is walked on tests/report_store.py's store, the produced
results re-pointed at its runs (their run IDs, freeze commit and frozen hashes: the links the seam tests hold, never a
key). The false-alarm measures are the one stand-in: on the chain's two- and three-week replays their episode grid
holds no start, so the report could not state their design; their shape is held to the measure's own output on the
same replay instead, and their episode rows to one row the measure builds from a start given to it."""

from __future__ import annotations

import functools

import pandas as pd
import pytest
import replay_world as rw
import report_payloads as RP
import report_store as S
import report_world as W

from valiwatt import tracking
from valiwatt.config import data_last
from valiwatt.monitor import run as mrun
from valiwatt.monitor import sensitivity as SENS
from valiwatt.report import ledger
from valiwatt.report import validation as RV
from valiwatt.report.render import filters

REAL_REUSE, REAL_MEASURE = SENS.forecast_run_reuse, SENS.measure
RECORD_2025 = "b" * 32  # the 2025 test of record in the report's store
LONGEST = 72.0  # every year's longest lead in the chain's data-quality runs (replay_world._log_dq)


def same_shape(stand_in, real) -> None:
    """The stand-in's keys are the measure's own, wherever the measure's output has an element to compare."""
    if isinstance(real, dict):
        assert set(stand_in) == set(real), (sorted(stand_in), sorted(real))
        for k in real:
            same_shape(stand_in[k], real[k])
    elif isinstance(real, list) and real:
        same_shape(stand_in[0], real[0])


def measure_stand_in(checked: list, *args, **kwargs):
    """The false-alarm measures, in tests/report_payloads.py's shape for the replayed year; on the 2025 replay the
    measure itself runs once beside it, and the two shapes are compared."""
    r = args[5]
    year = pd.Timestamp(r.days[0]["day"]).year
    out = RP.sensitivity(RP.Distinct(), year, 500 + year % 10)
    if year == RP.H_YEAR:
        checked.append((out, REAL_MEASURE(*args, **{**kwargs, "workers": 1})))
        # The grid holds no start on the chain, so the measure's rows are empty and same_shape compares none: one
        # row is built by the producer's own path (the episode replayed, its alarms counted), from the first judged
        # day, and its keys are held to the stand-in's.
        first = min(w["week_start"] for w in r.weeks if w["evaluated"])
        one = SENS.episodes(*args, **{**kwargs, "workers": 1}, days=(1,), factors=(0.7,), starts=[first])
        checked.append(("episode rows", out["episodes"]["rows"], one["rows"]))
    return out


def produce(tmp_path, monkeypatch) -> dict:
    """The rehearsal, a 2024 replay and the 2025 test of record, each by `run_replay` on the chain, its diagnostics
    run after it with the run-reuse measure itself; their results, diagnostics and the 2025 gate's record as the run
    store holds them."""
    c = rw.chain_copy(tmp_path, monkeypatch)
    checked: list = []
    monkeypatch.setattr(SENS, "forecast_run_reuse", functools.partial(REAL_REUSE, workers=1))
    monkeypatch.setattr(SENS, "measure", functools.partial(measure_stand_in, checked))
    # v1's two ledger entries, so the 2024 replay reconciles v1's model with them (plan section 11.2).
    rw.write_ledger(c.repo, [RP.V1_PLAIN, RP.V1_DEPARTED, c.entry])
    assert mrun.run_replay(2025, n_boot=20) == 0
    client = tracking.client()
    client.set_tag(c.dq25, "gates", "failed")  # the 2024 replay reads the through-2024 run again
    assert mrun.run_replay(2024, n_boot=20) == 0
    with pytest.MonkeyPatch.context() as mp:
        rw.install_rehearsal(mp, c)
        assert mrun.run_replay(2023, n_boot=20) == 0
    assert checked, "the measure itself never ran beside its stand-in"
    for stand_in, real in (x for x in checked if len(x) == 2):
        same_shape(stand_in, real)
    ((_, stand_in, one),) = [x for x in checked if len(x) == 3]
    assert len(one) == 1 and one[0]["starts"] == 1, one  # the producer built its one row
    assert stand_in and one  # rows to compare: all() over none would pass vacuously
    assert all(set(row) == set(one[0]) for row in stand_in), (sorted(stand_in[0]), sorted(one[0]))

    def read(run, path="results/replay.json"):
        return mrun._load_json_artifact(client, run.info.run_id, path)

    (pseudo,) = rw.replay_runs(2023, kind="pseudo_replay")
    replay = rw.replay_runs(2024)[-1]
    (held,) = rw.replay_runs(2025)
    out = {"p": read(pseudo), "r": read(replay), "h": read(held), "gate": read(held, mrun.GATE_RECORD),
           "h_tags": dict(held.data.tags)}
    for key, run in (("diag_p", pseudo), ("diag_r", replay), ("diag_h", held)):
        d = RV.diagnostics(client, run)
        assert d is not None, f"the diagnostics of {key[5:]} did not finish"
        out[key] = {k: v for k, v in d.items() if k != "run_id"}
    return out


def report_world(produced: dict) -> S.World:
    """The report's store (tests/report_store.py) holding the produced results in place of its replay payloads: the
    rehearsal, the 2024 replay and the 2025 test of record, each with its diagnostics, the 2025 gate's record and the
    ledger entries `ledger.entry` writes from them. Re-pointed at the store's runs: the freeze commit, the frozen
    hashes and the run IDs the gate's record names."""
    w = S.World()
    dq = w.files[("dq", "results/dq_results.json")]
    dq["checks"]["nbm_runs"]["metrics"]["longest_lead_h"] = {str(y): LONGEST for y in range(2019, 2025)}
    w.files[("pseudo", "results/replay.json")] = produced["p"] | {"frozen_sha256": S.FROZEN23}
    w.files[(S.REPLAY, "results/replay.json")] = produced["r"] | {"freeze_commit": W.F0, "frozen_sha256": W.FROZEN}
    w.files[("diag-pseudo", "results/diagnostics.json")] = produced["diag_p"]
    w.files[(f"diag-{S.REPLAY}", "results/diagnostics.json")] = produced["diag_r"]

    w.entries.append(w.entry(S.REPLAY))
    results, rows = RP.dq_2025()
    results["checks"]["nbm_runs"]["metrics"]["longest_lead_h"] = {str(y): LONGEST for y in range(2019, 2026)}
    w.add(S.fake_run("dq25", start=w.tick(), params={"through": "2025", "window_last_utc": data_last(2025).isoformat()},
                     gates="passed", code_sha256=S.CODE, kind="dq", status="complete"),
          {"results/dq_results.json": {**w.payloads["dq"], **results, "run_id": "dq25"},
           "revisions/hydroquebec.csv": rows})
    tags = produced["h_tags"]
    run = w.add_replay(RECORD_2025, produced["h"], year=2025, evaluation=tags["evaluation"],
                       standing=tags["standing"], dq_run_id="dq25")
    gate = produced["gate"] | {"frozen_sha256": W.FROZEN, "freeze_commit": W.F0, "first_2024_run_id": S.REPLAY,
                               "dq_run_id": "dq25"}
    w.files[(RECORD_2025, mrun.GATE_RECORD)] = gate
    w.add_diagnostics(RECORD_2025, produced["diag_h"])
    w.entries.append(w.entry(RECORD_2025))
    w.starts.append(W.start(RECORD_2025, ledger._utc(run.info.start_time), True, gate, standing=tags["standing"],
                            stage="logged"))
    return w


@pytest.fixture(scope="module")
def produced(tmp_path_factory):
    with pytest.MonkeyPatch.context() as mp:
        return produce(tmp_path_factory.mktemp("produced"), mp)


def test_the_report_renders_what_the_replay_writes(produced, tmp_path, monkeypatch):
    """Both pages, in both languages, render from the results the replay's code wrote and pass the render's checks;
    the sentences that read them print their values: the forecast's error by season (the replay's `by_season`), the
    run-reuse measure placement by placement and offset by offset (its constructible placement's days), and the 2025
    test of record's coverage with its interval."""
    w = report_world(produced).install(monkeypatch)
    ctx = w.context()
    assert ctx["held"]["status"] in RV.HELD_OUT_RESULTS and ctx["held"]["record"]["evaluation"] == "held_out"
    reuse = produced["diag_h"]["reuse_sensitivity"]
    made = [p for p in reuse["placements"] if p["constructible"]]
    assert made, "no placement of the run-reuse measure was constructible on the chain's 2025"
    pages = RV.render_pages(ctx, {lang: RV.figures(ctx, lang, tmp_path / lang) for lang in RP.LANGS})
    assert set(pages) == {(doc, lang) for doc in ("validation", "story") for lang in RP.LANGS}
    for lang in RP.LANGS:
        f = filters(lang)
        text = pages[("validation", lang)]
        eight, nine = RV.numbered_section(text, 8), RV.numbered_section(text, 9)
        for s, e in produced["r"]["forecast_error"]["by_season"].items():
            assert f"{f['num'](e['mae_c'], 2)} °C" in eight, (lang, s)
        for p in made:
            assert all(f["day"](d) in nine for d in p["days"]), (lang, p["placement"])
        cov = produced["h"]["summary"]["coverage"]
        assert all(f["pct"](x, 1) in nine for x in (cov["mean"], cov["ci95_low"], cov["ci95_high"])), lang
