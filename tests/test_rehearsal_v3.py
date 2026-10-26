"""The 2024 rehearsal (docs/plan-v3.md, section 11.1; monitor/run_v3.py `rehearsal_identity`): the models frozen for
2024 replay 2024 at noon on the runs validation read, and the replay refuses to log anything unless each day was fed by
the run validation fed it (or left unfed for the same reason), every noon input it issued is validation's bit for
bit, and each hour's bin and half-width are those of validation's January 2024 fold, the interval it issued included.

On the rehearsal base of tests/v3_world.py: a scratch repository holding `v3-rules-frozen` (pinned) and `v3-first-run`,
a v3 data-quality run through 2024, v3's validation run of the world (its frames holding, of 2024, the two weeks the
rehearsal replays), and the models frozen for 2024 and 2025. Each fault is injected into what the replay issued (a
day's run, rank or reason, an hour's input, bin or interval) or into validation's interval layer, and the run must stop
at the identity check with nothing logged."""

import numpy as np
import pandas as pd
import pytest

import records_v3_world as rw
import v3_world as vw
from valiwatt import tracking
from valiwatt.config import V3_FREEZE_TAG
from valiwatt.monitor import run as mrun
from valiwatt.monitor import run_v3
from valiwatt.monitor.run_v3 import RehearsalIdentityError

DAY = pd.Timestamp("2024-01-09")  # a fed day of the rehearsal
HOUR = pd.Timestamp("2024-01-09T20:00Z")  # one of its hours


@pytest.fixture
def chain(tmp_path, monkeypatch):
    return vw.chain_copy(tmp_path, monkeypatch)


def issued(monkeypatch, change) -> None:
    """The replay's result changed by `change(r)` before the checks read it: what it issued differs."""
    replay = run_v3._replay_v3

    def changed(*args, **kwargs):
        r = replay(*args, **kwargs)
        change(r)
        return r

    monkeypatch.setattr(run_v3, "_replay_v3", changed)


def nudge(x: float, toward: float = np.inf) -> float:
    """The next float from x toward `toward`: different bit for bit, and in nothing else."""
    return float(np.nextafter(x, toward))


def layer(chain, **change) -> None:
    """validation's January 2024 fold with another interval layer (bins.parquet), logged as a new validation run on the
    same data-quality run, and the models frozen for 2024 on it (the rehearsal reads the latest of each)."""
    frames = vw.validation_frames()
    frames["bins"] = frames["bins"].assign(**change)
    val = vw.log_validation(chain.dq24, frames)
    vw.log_frozen(2024, val, chain.dq24)


def day_changed(col, change):
    """The rehearsal's record of DAY with `col` changed."""
    def apply(r):
        rec = r.forecast_record
        rec[col] = rec[col].where(rec.index != DAY, change(rec.loc[DAY, col]))
    return apply


def hour_nudged(col):
    """The rehearsal's `col` at HOUR one float away."""
    def apply(r):
        r.hourly.loc[HOUR, col] = nudge(r.hourly.loc[HOUR, col])
    return apply


def bin_flipped(r):
    r.hourly.loc[HOUR, "bin"] = "rest" if r.hourly.loc[HOUR, "bin"] == "cold" else "cold"


ISSUED = {"run": day_changed("runtime", lambda t: t - pd.Timedelta(hours=1)),
          "rank": day_changed("rank", lambda k: k + 1),
          "reason": day_changed("reason", lambda _: "no_run_covers_the_day"),
          "temp": hour_nudged("temp_forecast"), "wind": hour_nudged("wind_forecast"),
          "daymean": hour_nudged("daymean_forecast"), "lagday": hour_nudged("lagday_observed"), "bin": bin_flipped,
          "interval": hour_nudged("upper")}
# The fold's column changed, and the direction: the edge moves down, so an hour exactly at it (a whole °F) stays in its
# bin and only the layer's own check can see the change.
LAYER = {"half_width": ("q_cold", np.inf), "edge": ("edge_c", -np.inf)}


@pytest.mark.parametrize("fault", [*ISSUED, *LAYER])
def test_a_rehearsal_that_differs_from_validation_refuses_to_log(chain, monkeypatch, fault):
    """Each fault stops the rehearsal at the identity check: RehearsalIdentityError, the run left started at stage
    replayed with the error's class, holding no metric and no results/replay.json. A day fed by another run (one hour
    older), at another rank, or left unfed for another reason; an hour's temperature, wind, day's mean or lag day's
    mean one float away; an hour in the other bin; an hour's issued upper bound one float away, its half-width no
    longer the January fold's; the January fold's cold half-width or bin edge one float away (the edge down, so no
    hour changes bin)."""
    if fault in ISSUED:
        issued(monkeypatch, ISSUED[fault])
    else:
        col, toward = LAYER[fault]
        layer(chain, **{col: nudge(float(vw.validation_frames()["bins"][col].iloc[0]), toward)})
    with pytest.raises(RehearsalIdentityError, match="the 2024 rehearsal differs from validation"):
        run_v3.run_replay_v3(2024, n_boot=20)
    (run,) = vw.replay_runs_v3(2024)
    t = run.data.tags
    assert (t["status"], t["stage"], t["error"]) == ("started", "replayed", "RehearsalIdentityError")
    assert run.data.metrics == {}
    assert "results/replay.json" not in mrun._artifact_paths(tracking.client(), run.info.run_id)


def test_a_rehearsal_identical_to_validation_logs(chain):
    """The control: the rehearsal under the tag, every check passed, logs its run complete with its results: the
    identity on every 2024 day and hour validation holds, the comparison with the monthly refits, the judgement and
    verdicts labelled seen by the developers, and the hashes of what judged it."""
    from valiwatt.freeze_record import frozen_chain_v3

    assert run_v3.run_replay_v3(2024, n_boot=20) == 0
    (run,) = vw.replay_runs_v3(2024)
    t = run.data.tags
    assert (t["status"], t["stage"], t["evaluation"], t["issue"]) == ("complete", "logged", "seen_by_developers",
                                                                       "noon")
    assert (t["freeze_tag"], t["freeze_commit"]) == (V3_FREEZE_TAG, chain.f0)
    assert (t["frozen_run_id"], t["validation_run_id"], t["dq_run_id"]) == (chain.frozen_2024, chain.validation,
                                                                            chain.dq24)
    results = mrun._load_json_artifact(tracking.client(), run.info.run_id, "results/replay.json")
    days = len(pd.date_range(*vw.DAYS[2024]))
    assert results["identity"]["differences"] == 0 and results["identity"]["days"] == days
    assert results["identity"]["hours"] == 24 * days
    chain24 = frozen_chain_v3(chain.frozen_2024, chain.validation, 2024)
    assert results["frozen_sha256"] == chain24["frozen_sha256"]
    assert t["coefficient_sha256"] == chain24["model"].sha256()
    assert results["verdict"]["label"] == "seen_by_developers" and set(results["coverage_by_bin"]) == {
        "cold", "rest", "all"}
    assert set(results["comparators"]) == {"v2_noon", "v1_noon", "v2_midnight"}
    assert results["comparators"]["v2_midnight"]["coverage"] is None
    assert results["refitted"]["hours"] > 0
    for key in ("year", "kind", "data_end", "clock_end", "summary", "days", "weeks", "decisions", "thresholds",
                "power", "departure_from", "freeze_tag", "freeze_commit", "rules_sha256", "model", "judgement",
                "forecast_error", "evaluation", "run_id", "evidently_checks"):
        assert key in results, key


def test_a_comparison_model_changed_during_the_rehearsal_stops_it(chain, monkeypatch):
    """The never-acts check: after the replay, each comparison model read again from the frozen run must hash as the one
    the replay was given; one changed in memory (as a monitor acting on it would) stops the run before it logs."""
    import dataclasses

    from valiwatt.monitor.replay import MonitorActedError

    read = run_v3._read_inputs_v3

    def acting(*args, **kwargs):
        inputs = read(*args, **kwargs)
        m = inputs["chain"]["comparators"]["v1_noon"]
        inputs["chain"]["comparators"]["v1_noon"] = dataclasses.replace(m, intercept=m.intercept + 1e-6)
        return inputs

    monkeypatch.setattr(run_v3, "_read_inputs_v3", acting)
    with pytest.raises(MonitorActedError, match="a comparison model changed"):
        run_v3.run_replay_v3(2024, n_boot=20)
    (run,) = vw.replay_runs_v3(2024)
    assert run.data.metrics == {} and run.data.tags["error"] == "MonitorActedError"


def test_a_rehearsal_on_a_frozen_run_of_another_validation_run_is_refused(chain):
    """Lineage: the models frozen for 2024 were built on an earlier validation run than the latest complete one the
    rehearsal reads: refused, naming the validation run, before any replay."""
    from valiwatt.forecast.validate import LineageError

    vw.log_validation(chain.dq24)
    with pytest.raises(LineageError, match="has another validation run"):
        run_v3.run_replay_v3(2024, n_boot=20)
    (run,) = vw.replay_runs_v3(2024)
    assert run.data.tags["stage"] == "opened" and run.data.metrics == {}


def test_a_rehearsal_on_a_frozen_run_under_other_rules_is_refused(chain):
    """Lineage: the models frozen for 2024 carry another rules hash than the freeze in force (fitted before a rule
    fix, say): refused, naming the rules, before any replay."""
    from valiwatt.forecast.validate import LineageError

    tracking.client().set_tag(chain.frozen_2024, "rules_sha256", "0" * 64)
    with pytest.raises(LineageError, match=f"frozen run {chain.frozen_2024} has another rules"):
        run_v3.run_replay_v3(2024, n_boot=20)
    (run,) = vw.replay_runs_v3(2024)
    assert run.data.tags["stage"] == "opened" and run.data.metrics == {}


def test_a_validation_run_without_the_january_fold_s_interval_layer_refuses_to_log(chain):
    """The identity reads the January 2024 fold's row of validation's frames/bins.parquet: a validation whose bins
    hold no row of that fold (its one row another fold's) gives the rehearsal nothing to check its interval layer
    against, and it refuses to log, naming the row."""
    layer(chain, fold="2024-02")
    with pytest.raises(RehearsalIdentityError, match=r"on \[\"the January fold's row \(none, or several\)\"\]"):
        run_v3.run_replay_v3(2024, n_boot=20)
    (run,) = vw.replay_runs_v3(2024)
    assert run.data.metrics == {} and run.data.tags["error"] == "RehearsalIdentityError"


def test_before_the_tag_the_rehearsal_is_pre_freeze_and_never_a_ledger_entry(tmp_path, monkeypatch):
    """Step 2 of section 17 (section 10.3): before `v3-rules-frozen` exists, the rehearsal runs on the pre-freeze
    lineage, its run tagged `v3-pre-freeze` with no freeze commit, and it is neither a ledger-v3 entry nor a start. Once
    the tag exists on that same code, it still never enters the ledger, the freeze record refuses it, and a rehearsal on
    the pre-freeze models is refused: no run is pre-freeze under the tag."""
    from valiwatt import records_v3
    from valiwatt.config import V3_FIRST_RUN_TAG, V3_PRE_FREEZE_TAG
    from valiwatt.forecast.validate import LineageError
    from valiwatt.freeze_record import FreezeRecordError, write_freeze_record
    from valiwatt.report import ledger_v3

    root = tmp_path / "pre"
    repo = root / "repo"
    f0 = rw.make_repo(repo, frozen=False)["f0"]
    rw.tag(repo, V3_FIRST_RUN_TAG, f0)
    rw.commit(repo, {"docs/replay-ledger.json": vw.v2_ledger_text(vw.v2_2024_entry())}, "v2's ledger")
    vw.install_chain(monkeypatch, root)
    assert records_v3.current_freeze()["freeze_tag"] == V3_PRE_FREEZE_TAG
    dq = rw.log_dq_v3(repo, 2024, freeze_tag=V3_PRE_FREEZE_TAG)
    val = vw.log_validation(dq)
    frozen = vw.log_frozen(2024, val, dq)
    vw.log_frozen(2025, val, dq)
    assert run_v3.run_replay_v3(2024, n_boot=20) == 0
    (run,) = vw.replay_runs_v3(2024)
    t = run.data.tags
    assert (t["status"], t["freeze_tag"], t["freeze_commit"], t["frozen_run_id"]) == (
        "complete", V3_PRE_FREEZE_TAG, "", frozen)
    results = mrun._load_json_artifact(tracking.client(), run.info.run_id, "results/replay.json")
    assert (results["freeze_tag"], results["freeze_commit"]) == (V3_PRE_FREEZE_TAG, "")
    assert ledger_v3.from_store() == [] and records_v3.replay_starts_v3(2024) == []

    rw.tag(repo, V3_FREEZE_TAG, "HEAD")
    rw.pin(repo, V3_FREEZE_TAG, "HEAD")
    assert records_v3.current_freeze()["freeze_tag"] == V3_FREEZE_TAG
    assert ledger_v3.from_store() == [] and records_v3.earlier_freeze_commits_v3() == {}
    with pytest.raises(FreezeRecordError, match=f"freeze tag '{V3_PRE_FREEZE_TAG}'"):
        write_freeze_record(run.info.run_id)
    with pytest.raises(LineageError, match="has another freeze tag"):
        run_v3.run_replay_v3(2024, n_boot=20)


def test_a_fresh_clone_data_quality_run_is_refused_by_the_rehearsal_once_a_read_of_v2_s_store_is_logged(
        chain, monkeypatch):
    """The slice-3 forward note, at the replay (quality/run_v3.py `require_design_run`): the latest data-quality run is
    labelled a fresh clone while v3's store holds a read of v2's store; the rehearsal is refused before its run opens.
    The quiet case, a fresh clone with no read logged, is test_quality_v3's."""
    from valiwatt import v2store
    from valiwatt.forecast.validate import LineageError

    dq = rw.log_dq_v3(chain.repo, 2024, fresh_clone="true", byte_copies="not_run")
    monkeypatch.setattr(v2store, "recorded", lambda: {"run_id": "f" * 32})
    with pytest.raises(LineageError, match=f"data-quality run {dq} is labelled a fresh clone"):
        run_v3.run_replay_v3(2024, n_boot=20)
    assert vw.replay_runs_v3(2024) == []
