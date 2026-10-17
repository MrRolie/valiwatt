"""v2's replay (docs/plan-v2.md, sections 3.2, 5, 9 and 11): the NBM runs fed behind the clock and chosen by the run
rule, the forecast-missing criterion, drift on the issued forecast, the comparison models scored on the inputs
recorded at issue, the reuse measure, and the 2025 path (its started run and stages, its diagnostics, the evidence
run). The 2022 tests replay design days of world_v2() (no gate); the 2024 and 2025 tests run on the synthetic chain of
tests/replay_world.py: a scratch repository tagged rules-frozen-v2 and a run store holding v2's first 2024 replay."""

import dataclasses
import fcntl
import json
import os
import tempfile

import numpy as np
import pandas as pd
import pytest

import replay_world as rw
from replay_world import LAST, world_v2
from valiwatt import tracking
from valiwatt.config import FREEZE_TAG, data_last
from valiwatt.forecast import evaluate
from valiwatt.forecast import validate as vd
from valiwatt.forecast.features import REASONS, forecast_temperature
from valiwatt.forecast.guards import LookaheadError
from valiwatt.monitor import replay as rp
from valiwatt.monitor import run as mrun
from valiwatt.monitor import sensitivity as S
from valiwatt.monitor.clock import Clock, RawFeed
from valiwatt.monitor.rules import INVESTIGATE, RECALIBRATE, RETRAIN, rules_sha256
from valiwatt.quality import run as qrun
from valiwatt.quality.checks import jsonable
from valiwatt.quality.weather import BOUNDS_C

START, END = "2022-01-03", "2022-02-06"  # five Monday-to-Sunday weeks
SHORT_END = "2022-01-23"  # three
H = pd.Timedelta(hours=1)
MEASURE = S.measure  # the measures themselves: the chain stubs sensitivity.measure for the command's diagnostics
# v1's hourly frame, column for column.
V1_COLUMNS = ["day", "hour", "day_type", "lag24", "lag168", "temp_persistence", "forecast", "lower", "upper", "origin",
              "not_predicted_because", "target", "status", "spike_z", "temp_observed", "ape", "rel_bias", "covered"]


def v2(runs=None, raw=None, obs=None, model=None, comparators=None, start=START, end=END, th=None, **kw):
    """A v2 replay of world_v2()'s design days (2022: no gate), with its runs and its two comparison models."""
    w = world_v2()
    return rp.replay(w.raw if raw is None else raw, w.obs if obs is None else obs, model or w.model,
                     th or w.thresholds, w.reference, start, end, LAST, runs=w.runs if runs is None else runs,
                     comparators=w.comparators if comparators is None else comparators, **kw)


@pytest.fixture(scope="module")
def clean():
    return v2()


@pytest.fixture(scope="module")
def short():
    return v2(end=SHORT_END)


@pytest.fixture
def chain(tmp_path, monkeypatch):
    return rw.chain_copy(tmp_path, monkeypatch)


def recs(r):
    return [(d["week_start"].date().isoformat(), d["recommendation"]) for d in r.decisions if d["recommendation"]]


def on_day(r, day):
    return r.hourly[r.hourly["day"] == pd.Timestamp(day)]


def late_run(runs, runtime, available, offset_f):
    """A run of `runtime`: the points of the run issued just before it that are valid after `runtime`, moved by
    `offset_f` °F, available at `available`."""
    rt = pd.Timestamp(runtime)
    before = runs.loc[runs["runtime"] < rt, "runtime"].max()
    pts = runs[(runs["runtime"] == before) & (runs["ftime"] > rt)].copy()
    pts["runtime"] = pd.Series(rt, index=pts.index).astype(runs["runtime"].dtype)
    pts["available_utc"] = pd.Series(pd.Timestamp(available), index=pts.index).astype(runs["available_utc"].dtype)
    pts["tmp_f"] = pts["tmp_f"] + offset_f
    return pts


def with_run(runs, extra):
    return pd.concat([runs, extra]).sort_values(["runtime", "ftime"], kind="stable").reset_index(drop=True)


def reached(what):
    def fake(*args, **kwargs):
        raise AssertionError(f"{what} reached")
    return fake


def head(chain):
    return rw.git(chain.repo, "rev-parse", "HEAD")


def lock_is_held(root) -> bool:
    fd = os.open(root / "mlruns" / mrun.LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


# ------------------------------------------------------------------------------------ the forecast feed (2022 worlds)

def test_the_replay_sees_a_run_only_once_it_is_available(clean):
    """A run whose run time plus the assumed lag falls before day D's origin, but which became available only after
    it (its own `available_utc`), is in no view at the origin: D is fed by the run that was. The run rule alone, on the
    whole frame, would take it; available before the origin, it feeds D. day_verdicts sees it no sooner: when it is
    the only run covering D, D fails forecast_missing in both."""
    w = world_v2()
    day = pd.Timestamp("2022-01-12")
    o = rp.origin(day)
    late = late_run(w.runs, o - 2 * H, available=o + H, offset_f=9.0)
    runs = with_run(w.runs, late)
    assert forecast_temperature(runs[list(rp.RUN_POINTS)], [day]).record.loc[day, "runtime"] == o - 2 * H
    r = v2(runs=runs, end="2022-01-16")
    assert r.forecast_record.loc[day, "runtime"] == clean.forecast_record.loc[day, "runtime"] == o - 4 * H
    np.testing.assert_array_equal(on_day(r, day)["temp_forecast"], on_day(clean, day)["temp_forecast"])
    soon = with_run(w.runs, late.assign(available_utc=o - H))
    assert v2(runs=soon, start="2022-01-10", end="2022-01-13").forecast_record.loc[day, "runtime"] == o - 2 * H

    # day_verdicts (validation's calibration weeks) reads the runs through the same clock: no run in view at D's origin
    # covers D, and a run that does, with a run time the lag allows but available only after the origin, leaves D
    # failing forecast_missing there as in the replay. The covering run is copied before D's point is removed.
    covering = late_run(w.runs, o - 2 * H, available=o + H, offset_f=0.0)
    unfed = w.runs[~((w.runs["runtime"] <= o - 2 * H) & (w.runs["ftime"] == o + 25 * H))]
    runs = with_run(unfed, covering)
    assert forecast_temperature(runs[list(rp.RUN_POINTS)], [day]).record.loc[day, "runtime"] == o - 2 * H
    missing = [{"kind": "forecast_missing", "reason": REASONS[1]}]
    assert rp.day_verdicts(w.raw, w.obs, day, day, runs=runs)[day] == missing
    assert v2(runs=runs, start="2022-01-10", end="2022-01-13").days[2]["problems"] == missing

    # The feed itself: the freshest runs by run identity among those with a point in view, each with only its points
    # in view; a point stamped after the clock stays out, even of a run chosen.
    t0 = pd.Timestamp("2022-01-01T00:00Z")
    records = pd.DataFrame({"runtime": [t0, t0, t0 + H, t0 + 2 * H, t0 + 2 * H, t0 - H],
                            "ts_utc": [t0 + H, t0 + 5 * H, t0 + 2 * H, t0 + 3 * H, t0 + 9 * H, t0 + 4 * H]})
    feed = RawFeed(records, Clock(t0 + 4 * H), "forecast")
    got = feed.freshest("runtime", 2)
    assert list(zip(got["runtime"], got["ts_utc"])) == [(t0 + H, t0 + 2 * H), (t0 + 2 * H, t0 + 3 * H)]
    assert len(feed.freshest("runtime", 12)) == 4


def sparse_runs():
    """world_v2()'s runs with a week-long gap (no run issued from 2022-01-10 to 01-16), the last run before it reaching
    out 168 hours: within the gap, a day whose only covering run among the twelve freshest is days old, then a day no
    run covers."""
    w = world_v2()
    runs = w.runs
    long_rt = pd.Timestamp("2022-01-09T19:00Z")
    gap = (runs["runtime"] >= pd.Timestamp("2022-01-10T00:00Z")) & (runs["runtime"] < pd.Timestamp("2022-01-17T00:00Z"))
    ftime = pd.DatetimeIndex(long_rt + pd.Timedelta(hours=2) + 3 * H * np.arange(56))
    temp_c = w.obs.set_index("ts_utc")["temp_c"].reindex(ftime).to_numpy()
    long = pd.DataFrame({"runtime": long_rt, "ftime": ftime, "tmp_f": np.round(temp_c * 9 / 5 + 32),
                         "available_utc": long_rt + rw.AVAILABILITY_LAG}).astype(runs.dtypes.to_dict())
    # And on 2022-01-20 the freshest run misses the day's first bracket point: the run before it feeds the day.
    missing = ((runs["runtime"] == pd.Timestamp("2022-01-20T01:00Z"))
               & (runs["ftime"] == pd.Timestamp("2022-01-20T06:00Z")))
    kept = runs[~gap & (runs["runtime"] != long_rt) & ~missing]
    return with_run(kept, long[list(runs.columns)]), long_rt


def test_the_replay_and_validation_feed_the_same_run_on_a_sparse_archive():
    """On an archive with a gap of a week: per day, the replay's feeding run, rank, lead, reason and hourly
    temperatures equal validate.forecast_inputs' on the same runs, including a day fed by a run more than four days
    old and a day no run covers (an age window put back would leave the first unfed in the replay alone)."""
    runs, long_rt = sparse_runs()
    first, last = "2022-01-03", SHORT_END
    r = v2(runs=runs, start=first, end=last)
    days = pd.date_range(first, last)
    val = vd.forecast_inputs(runs, days)["forecast"]
    cols = ["runtime", "rank", "lead_h", "reason"]
    pd.testing.assert_frame_equal(r.forecast_record[cols], val.record[cols])
    pd.testing.assert_series_equal(r.hourly["temp_forecast"], val.hourly.reindex(r.hourly.index), check_names=False)
    old = r.forecast_record.loc[pd.Timestamp("2022-01-14")]
    assert old["runtime"] == long_rt and old["rank"] == 0 and old["lead_h"] > 96
    assert r.forecast_record.loc[pd.Timestamp("2022-01-16"), "reason"] == REASONS[1]
    assert (r.forecast_record["reason"] != "").sum() == 1
    second = r.forecast_record.loc[pd.Timestamp("2022-01-20")]
    assert second["runtime"] == pd.Timestamp("2022-01-19T19:00Z") and second["rank"] == 1


# --------------------------------------------------------------------------------------------- the reuse measure

def reuse(r, runs, **kw):
    w = world_v2()
    return S.forecast_run_reuse(w.raw, w.obs, w.model, w.thresholds, w.reference, r, LAST, runs,
                                r.temperature_reference, workers=1, **kw)


def test_the_reuse_measure_feeds_one_corrupted_run_to_two_days(short):
    """On world_v2(): each placement of the season the replay holds is constructible, the replay's forecast record
    showing the corrupted run feeding D and D+1 for each offset, every point of that run inside the bounds and moved by
    the offset, the runs issued after it up to D+1's origin withheld; the report's keys are there, labelled, with the
    longest lead beside it. The seasons the replay does not hold are recorded not constructible, with the reason."""
    w = world_v2()
    out = reuse(short, w.runs, longest_lead_h=72.0)
    assert set(out) == {"label", "placements", "offsets_c", "constructible", "beyond_untouched", "longest_lead_h",
                        "reuse_lead_h"}
    assert (out["label"], out["offsets_c"], out["longest_lead_h"], out["reuse_lead_h"]) == (
        "sensitivity_never_chooses", [5.0, -5.0], 72.0, 75)
    assert [(p["season"], p["placement"]) for p in out["placements"]] == [
        (s, p) for s in S.SEASON_ORDER for p in S.REUSE_PLACEMENTS]
    winter = [p for p in out["placements"] if p["season"] == "winter"]
    assert all(p["constructible"] for p in winter) and out["constructible"] == 2
    assert all(p == {"season": p["season"], "placement": p["placement"], "constructible": False,
                     "reason": "no_full_week_of_the_season_replayed"}
               for p in out["placements"] if p["season"] != "winter")
    for p in winter:
        d, nxt = p["days"]
        assert nxt == d + pd.Timedelta(days=1) and S._on_side(d, p["placement"])
        assert p["runtime"] == short.forecast_record.loc[d, "runtime"]
        assert [x["offset_c"] for x in p["offsets"]] == [5.0, -5.0]
        for x in p["offsets"]:
            assert x["shown"] and x["days_fed"] == [d, nxt]
            assert {"weeks", "decisions_changed", "rule4_fired", "beyond_untouched"} <= set(x)
            assert all({"trimmed", "untouched_mean", "beyond_untouched"} <= set(wk) for wk in x["weeks"])
            spoiled = S.corrupted_runs(w.runs, p["runtime"], rp.origin(nxt), x["offset_c"])
            mine = spoiled[spoiled["runtime"] == p["runtime"]].reset_index(drop=True)
            before = w.runs[w.runs["runtime"] == p["runtime"]].reset_index(drop=True)
            assert ((mine["tmp_f"] - 32) * 5 / 9).between(*BOUNDS_C).all()
            np.testing.assert_allclose(mine["tmp_f"] - before["tmp_f"], x["offset_c"] * 9 / 5)
            assert not ((spoiled["runtime"] > p["runtime"]) & (spoiled["available_utc"] <= rp.origin(nxt))).any()
    clipped = S.corrupted_runs(w.runs, winter[0]["runtime"], rp.origin(winter[0]["days"][1]), 200.0)
    assert ((clipped["tmp_f"] - 32) * 5 / 9).max() <= BOUNDS_C[1] + 1e-9  # clipped: still inside, so it still covers


def test_the_reuse_measure_never_claims_two_days_from_a_run_that_misses_one(short, monkeypatch):
    """One point valid on D+1 only removed from the run feeding the first placement's D, every day still fed: the
    measure takes another pair whose run is proven to cover both days. With the withholding undone, the replay shows
    the run feeding D alone: the placement is recorded not constructible, never as a two-day result. With every lead
    cut at 29 hours, no run covers a next day: each placement is recorded not constructible, with the reason."""
    w = world_v2()
    start = S._season_starts(short)[0]
    d, runtime = S.reuse_pair(short, w.runs, start, "within_week")
    nxt = d + pd.Timedelta(days=1)
    point = (w.runs["runtime"] == runtime) & (w.runs["ftime"] == rp.origin(nxt) + 4 * H)
    assert point.sum() == 1
    spoiled = w.runs[~point].reset_index(drop=True)
    assert not S.covers_next_day(spoiled, runtime, nxt) and S.covers_next_day(w.runs, runtime, nxt)
    r = v2(runs=spoiled, end=SHORT_END)
    assert (r.forecast_record["reason"] == "").all() and r.forecast_record.loc[d, "runtime"] == runtime
    within = next(p for p in reuse(r, spoiled, offsets=(5.0,))["placements"]
                  if (p["season"], p["placement"]) == ("winter", "within_week"))
    assert within["constructible"] and within["days"][0] != d
    assert within["offsets"][0]["days_fed"] == within["days"]

    real = S.corrupted_runs
    monkeypatch.setattr(S, "corrupted_runs", lambda runs, rt, until, offset: real(runs, rt, rt, offset))
    out = reuse(short, w.runs, offsets=(5.0,))
    winter = [p for p in out["placements"] if p["season"] == "winter"]
    assert out["constructible"] == 0 and len(winter) == 2
    for p in winter:
        assert p["reason"] == "the_replay_did_not_show_the_run_feeding_both_days" and not p["constructible"]
        assert p["days_fed"] == {"5.0": [p["days"][0]]} and "offsets" not in p
    monkeypatch.undo()

    cut = w.runs[(w.runs["ftime"] - w.runs["runtime"]) <= 29 * H].reset_index(drop=True)
    r = v2(runs=cut, end=SHORT_END)
    assert (r.forecast_record["reason"] == "").all()
    out = reuse(r, cut)
    assert out["constructible"] == 0
    assert [p["reason"] for p in out["placements"] if p["season"] == "winter"] == [
        "no_feeding_run_proven_to_cover_the_next_day"] * 2


# ------------------------------------------------------------------------------- data quality, drift, comparators

def test_a_day_no_run_fed_is_not_predicted_and_fails_forecast_missing(clean):
    """Every run in view at day D's origin misses D's last bracket point: no run covers D. Its 24 hours are not
    predicted, each saying why; the day fails forecast_missing with the record's reason, and its week recommends
    investigating the data. day_verdicts with the runs gives validation the same verdict. The clean world: no day
    fails."""
    assert not any(d["failed"] for d in clean.days) and (clean.forecast_record["reason"] == "").all()
    w = world_v2()
    day = pd.Timestamp("2022-01-19")
    o = rp.origin(day)
    runs = w.runs[~((w.runs["runtime"] <= o - 2 * H) & (w.runs["ftime"] == o + 25 * H))].reset_index(drop=True)
    r = v2(runs=runs)
    assert r.forecast_record.loc[day, "reason"] == REASONS[1] and pd.isna(r.forecast_record.loc[day, "runtime"])
    hours = on_day(r, day)
    assert hours["forecast"].isna().all() and len(hours) == 24
    assert all(f"temp_forecast ({REASONS[1]})" in why for why in hours["not_predicted_because"])
    failed = [x for x in r.days if x["failed"]]
    assert [x["day"] for x in failed] == [day]
    assert failed[0]["problems"] == [{"kind": "forecast_missing", "reason": REASONS[1]}]
    week = next(x for x in r.decisions if x["week_start"] == pd.Timestamp("2022-01-17"))
    assert week["recommendation"] == INVESTIGATE and week["rule"] == 1
    assert rp.day_verdicts(w.raw, w.obs, day, day, runs=runs)[day] == failed[0]["problems"]
    assert rp.day_verdicts(w.raw, w.obs, day, day)[day] == []  # v1's criteria: no forecast record


def test_forecast_input_drift_with_performance_inside_yields_investigate():
    """The runs forecast 10 °C warmer from 2022-01-10 and demand answers as to a warmer week, while the station's
    observation stays as it was: the temperature drift, on the issued forecast the model reads, fires rule 4, and
    performance stays inside (no retrain)."""
    w = world_v2()
    shift = pd.Timestamp("2022-01-10T05:00Z")
    warm_runs = w.runs.assign(tmp_f=w.runs["tmp_f"] + np.where(w.runs["ftime"] > shift, 18.0, 0.0))
    warm = w.obs.assign(temp_c=w.obs["temp_c"] + np.where(w.obs["ts_utc"] > shift, 10.0, 0.0))
    r = v2(runs=warm_runs, raw=rw.respond(w.raw, w.obs, warm))
    fired = [f for d in r.decisions for f in d["fired"]]
    assert any(f["rule"] == 4 and f["recommendation"] == INVESTIGATE for f in fired)
    assert not {RETRAIN, RECALIBRATE} & {rec for _, rec in recs(r)}
    drifted = [x for x in r.weeks if x["drift_flags"]["temperature"]]
    assert drifted and all(x["week_start"] >= pd.Timestamp("2022-01-10") for x in drifted)


def test_a_temperature_drift_reference_holding_replayed_days_is_refused(clean):
    """The temperature-drift reference handed in precomputed (the sensitivity replays pass the main replay's) must
    hold only days before the replayed period, as the forecast-drift reference must: with one issued temperature of
    the first replayed day added, the replay is refused before it starts. The reference as the replay built it runs
    (control)."""
    first_hour = on_day(clean, START)["temp_forecast"].iloc[:1]
    leaky = pd.concat([clean.temperature_reference, first_hour])
    with pytest.raises(LookaheadError, match="temperature-drift reference holds days of the replayed period"):
        v2(forecast_reference=leaky, end="2022-01-04")
    assert len(v2(forecast_reference=clean.temperature_reference, end="2022-01-04").days) == 2


def test_comparators_are_scored_on_the_inputs_recorded_at_issue(clean):
    """A run that arrives after day D's issue, which the run rule would take for D once it is in view: the comparison
    models' forecasts of D are those of the inputs recorded at the issue, as if the run never came."""
    w = world_v2()
    day = pd.Timestamp("2022-01-12")
    o = rp.origin(day)
    late = late_run(w.runs, o - 3 * H, available=o + H, offset_f=18.0)
    runs = with_run(w.runs, late)
    assert forecast_temperature(runs[list(rp.RUN_POINTS)], [day]).record.loc[day, "runtime"] == o - 3 * H
    r = v2(runs=runs, end="2022-01-16")
    for col in ("temp_forecast", "pred_forecast", "lower_forecast", "pred_persistence", "forecast"):
        np.testing.assert_array_equal(on_day(r, day)[col], on_day(clean, day)[col], err_msg=col)
    assert on_day(r, day)["pred_forecast"].notna().all()


def test_the_frozen_least_squares_score_the_replay_as_the_previous_formula(clean):
    """No number of the replay moves: the comparison models' forecasts of every replayed hour, now computed from the
    recorded temperatures with their stored base and blocks, are those of the previous formula on the degree-hours
    the replay recorded at issue (test_frozen.ols_previous_formula), 0 MW apart, missing where it was missing, the
    intervals with them."""
    from test_frozen import ols_previous_formula

    w = world_v2()
    for key, m in w.comparators.items():
        assert m.family == "ols"
        expected = ols_previous_formula(m, clean.hourly)
        assert expected.notna().sum() > 500, key
        np.testing.assert_array_equal(clean.hourly[f"pred_{key}"].to_numpy(), expected.to_numpy(), err_msg=key)
        np.testing.assert_array_equal(clean.hourly[f"upper_{key}"].to_numpy(), (expected * (1 + m.q)).to_numpy(),
                                      err_msg=key)


def test_comparators_crossing_their_thresholds_yield_no_recommendation(clean):
    """Comparison models whose intervals are a fifth as wide miss far more hours than the coverage threshold allows:
    they touch no monitor, so the decisions and the production model's hours are the clean replay's."""
    w = world_v2()
    narrow = {k: dataclasses.replace(m, q=m.q / 5) for k, m in w.comparators.items()}
    r = v2(comparators=narrow)
    assert recs(r) == recs(clean) == []
    assert json.dumps(jsonable(r.decisions)) == json.dumps(jsonable(clean.decisions))
    production = [c for c in clean.hourly.columns if not c.startswith(("pred_", "lower_", "upper_"))]
    pd.testing.assert_frame_equal(r.hourly[production], clean.hourly[production])
    scored = r.hourly["target"].notna()
    for key in narrow:
        inside = (r.hourly["target"] >= r.hourly[f"lower_{key}"]) & (r.hourly["target"] <= r.hourly[f"upper_{key}"])
        assert inside[scored].mean() < w.thresholds["performance"]["winter"]["coverage_low"]


class Drifting:
    """A comparison model whose hash changes after it is first read: a monitor that changed it."""

    def __init__(self, model):
        self.model, self.reads = model, 0

    def sha256(self):
        self.reads += 1
        return self.model.sha256() + ("" if self.reads == 1 else "-changed")

    def predict(self, frame):
        return self.model.predict(frame)


def test_a_comparator_changed_during_the_replay_fails_it(tmp_path, monkeypatch):
    """In the replay, a comparison model whose hash changes; in the 2024 run, one that differs when it is loaded again
    after the replay: each fails it, the run left FAILED at stage replayed with no metric."""
    w = world_v2()
    with pytest.raises(rp.MonitorActedError, match="comparison models"):
        v2(comparators={"forecast": Drifting(w.comparators["forecast"]), "persistence": w.comparators["persistence"]},
           end="2022-01-09")

    c = rw.chain_copy(tmp_path, monkeypatch)
    tracking.client().set_tag(c.dq25, "gates", "failed")  # the 2024 replay reads the through-2024 run again
    real, loads = mrun.load_comparators, []

    def changing(run):
        out = real(run)
        loads.append(run.info.run_id)
        if len(loads) > 1:
            out = dict(out, forecast=dataclasses.replace(out["forecast"], q=out["forecast"].q * 1.5))
        return out

    monkeypatch.setattr(mrun, "load_comparators", changing)
    with pytest.raises(rp.MonitorActedError, match="comparison model"):
        mrun.run_replay(2024, n_boot=20)
    (run,) = [x for x in rw.replay_runs(2024) if x.info.run_id != c.first_2024]
    assert run.info.status == "FAILED" and not run.data.metrics
    assert (run.data.tags["stage"], run.data.tags["error"]) == ("replayed", "MonitorActedError")


def test_replay_without_runs_is_v1_s():
    """Without runs, the replay is v1's: v1's hourly columns, no forecast record, no temperature reference, no
    comparator; comparison models without runs are refused."""
    raw, obs = rw.world()
    frozen, ref = rw.frozen_and_reference()
    r = rp.replay(raw, obs, frozen, rw.thresholds(), ref, "2022-01-03", "2022-01-16", LAST)
    assert list(r.hourly.columns) == V1_COLUMNS
    assert r.forecast_record is None and r.temperature_reference is None and r.comparators_sha256 == {}
    assert all("forecast_record" not in d for d in r.days)
    with pytest.raises(ValueError, match="give the runs"):
        rp.replay(raw, obs, frozen, rw.thresholds(), ref, "2022-01-03", "2022-01-09", LAST,
                  comparators=world_v2().comparators)


def test_a_rehearsal_whose_issued_forecast_differs_from_validation_s_is_refused(clean):
    """The rehearsal's input check: identical, it passes (control); one hour's temperature 0.5 °C off, one day fed by
    another run, one day left unfed for another reason, or one hour validation lacks, each refuses."""
    oos = clean.hourly[["day", "temp_forecast"]].copy()
    record = clean.forecast_record
    out = mrun.weather_identity(clean.hourly, record, oos, record.copy(), 2022)
    assert out == {"hours": len(oos), "max_abs_diff_c": 0.0, "days": len(record), "run_or_reason_differences": 0}
    warm = oos.copy()
    warm.iloc[30, warm.columns.get_loc("temp_forecast")] += 0.5
    with pytest.raises(mrun.WeatherIdentityError, match=r"1 of \d+ hours \(largest difference 0\.(5|49)"):
        mrun.weather_identity(clean.hourly, record, warm, record, 2022)
    other_run = record.copy()
    other_run.iloc[5, other_run.columns.get_loc("runtime")] -= pd.Timedelta(hours=6)
    other_reason = record.copy()
    other_reason.iloc[5, other_reason.columns.get_loc("reason")] = REASONS[1]
    for spoiled in (other_run, other_reason):
        with pytest.raises(mrun.WeatherIdentityError, match=r"1 of \d+ days fed by another run"):
            mrun.weather_identity(clean.hourly, record, oos, spoiled, 2022)
    with pytest.raises(mrun.WeatherIdentityError, match="1 of"):
        mrun.weather_identity(clean.hourly, record, oos.drop(oos.index[10]), record, 2022)


def test_the_2023_rehearsal_runs_end_to_end_and_refuses_an_input_validation_did_not_issue(tmp_path, monkeypatch):
    """The rehearsal's v2 path end to end (plan section 11.1), on a synthetic 2023 (tests/replay_world.py's
    rehearsal_world, two weeks): run_replay(2023) opens its run with the data-quality, validation and frozen runs it
    located, reads the runs of the design years only, replays and finishes, its issued forecast equal to validation's on
    every 2023 hour and day validation holds (weather_identity), the frozen GAM compared with validation's refitted
    folds on their intervals, its diagnostics run after it. With one 2023 hour of validation's temp_forecast 0.5 °C off,
    the next rehearsal is refused before it logs anything: its run FAILED at stage replayed, error
    WeatherIdentityError, no metric and no artifact."""
    c = rw.chain_copy(tmp_path, monkeypatch)
    ids = rw.install_rehearsal(monkeypatch, c)
    assert mrun.run_replay(2023, n_boot=20) == 0
    (run,) = rw.replay_runs(2023, kind="pseudo_replay")
    t = run.data.tags
    assert (t["status"], t["stage"], t["freeze_tag"], t["evaluation"]) == ("complete", "logged", "", "rehearsal")
    assert (t["dq_run_id"], t["validation_run_id"], t["frozen_run_id"]) == (ids["dq"], ids["validation"], ids["frozen"])
    assert ids["years"] == [tuple(vd.DESIGN_YEARS)]
    res = mrun._load_json_artifact(tracking.client(), run.info.run_id, "results/replay.json")
    days = pd.date_range(*rw.CHAIN_DAYS[2023])
    assert res["weather_identity"] == {"hours": 24 * len(days), "max_abs_diff_c": 0.0, "days": len(days),
                                       "run_or_reason_differences": 0}
    assert res["refitted"]["hours"] == res["summary"]["hours_scored"] > 0
    assert res["model"]["sha256"] == rw.rehearsal_world().model.sha256() and set(res["comparators"]) == {
        "forecast", "persistence"}
    (diag,) = rw.replay_runs(2023, kind="diagnostic")
    assert (diag.data.tags["status"], diag.data.tags["replay_run_id"]) == ("complete", run.info.run_id)

    f = rw.artifact_file(ids["validation"], "frames/oos.parquet")
    oos = pd.read_parquet(f)
    at = oos.index[oos["day"] >= days[0]][30]
    oos.loc[at, "temp_forecast"] += 0.5
    oos.to_parquet(f)
    with pytest.raises(mrun.WeatherIdentityError, match=r"1 of \d+ hours"):
        mrun.run_replay(2023, n_boot=20)
    spoiled = rw.replay_runs(2023, kind="pseudo_replay")[-1]
    assert spoiled.info.run_id != run.info.run_id and spoiled.info.status == "FAILED" and not spoiled.data.metrics
    assert (spoiled.data.tags["stage"], spoiled.data.tags["error"]) == ("replayed", "WeatherIdentityError")
    assert mrun._artifact_paths(tracking.client(), spoiled.info.run_id) == []


# ---------------------------------------------------------------------------------------------- 2024 on the chain

def test_the_2024_replay_reads_no_record_of_2025(chain, monkeypatch):
    """The export and the runs holding 2025's records too: the 2024 replay is given data_end = data_last(2024), and
    none of its three feeds holds a record stamped after it."""
    tracking.client().set_tag(chain.dq25, "gates", "failed")  # the 2024 replay reads the through-2024 run again
    raw25, obs25 = rw.vintage(2025)
    assert raw25["ts_utc"].max() > data_last(2024) and rw.chain_world().runs["available_utc"].max() > data_last(2024)
    monkeypatch.setattr(mrun, "load_raw", lambda dq: rw.vintage(2025))
    monkeypatch.setattr(qrun, "load_runs", lambda dq, years=None: rw.chain_world().runs)
    seen: dict = {}
    real_init = rp.RawFeed.__init__

    def feed(self, records, clock, name):
        seen.setdefault(name, []).append(pd.Timestamp(records["ts_utc"].max()))
        real_init(self, records, clock, name)

    real_replay = mrun.replay

    def spy(*args, **kwargs):
        seen["data_end"] = pd.Timestamp(args[7])
        return real_replay(*args, **kwargs)

    monkeypatch.setattr(rp.RawFeed, "__init__", feed)
    monkeypatch.setattr(mrun, "replay", spy)
    assert mrun.run_replay(2024, n_boot=20) == 0
    assert seen["data_end"] == data_last(2024)
    assert {"demand", "temperature", "forecast"} <= set(seen)
    assert all(t <= data_last(2024) for name in ("demand", "temperature", "forecast") for t in seen[name])


def test_the_decision_rule_runs_on_the_replayed_year_with_its_label(chain):
    """v2's first 2024 replay logged the contrasts and the verdict computed on its own year's hours (its hourly frame,
    the comparators of its frozen run, cold_c from its validation run), each with the run's evaluation label; the 2025
    test of record carries its own."""
    c = tracking.client()
    res = mrun._load_json_artifact(c, chain.first_2024, "results/replay.json")
    label = c.get_run(chain.first_2024).data.tags["evaluation"]
    assert res["contrasts"]["label"] == res["verdict"]["label"] == label == "seen_by_developers"
    with tempfile.TemporaryDirectory() as tmp:
        hourly = pd.read_parquet(c.download_artifacts(chain.first_2024, "frames/hourly.parquet", tmp))
    assert hourly["day"].dt.year.unique().tolist() == [2024]
    held = mrun.frozen_for_2025(chain.entry)
    contrasts = evaluate.contrasts(mrun.contrast_frame(hourly, held["comparators"]), held["cold_c"], n_boot=20)
    assert json.loads(json.dumps(jsonable(contrasts))) == {k: v for k, v in res["contrasts"].items() if k != "label"}
    assert {k: v for k, v in res["verdict"].items() if k != "label"} == evaluate.verdict(contrasts)
    assert mrun.run_replay(2025, n_boot=20) == 0
    (start,) = rw.replay_runs(2025)
    res25 = mrun._load_json_artifact(c, start.info.run_id, "results/replay.json")
    assert res25["contrasts"]["label"] == res25["verdict"]["label"] == start.data.tags["evaluation"] == "held_out"


# ---------------------------------------------------------------------------------------------- 2025 on the chain

def test_the_2025_replay_refuses_until_the_window_reaches_its_end(chain, monkeypatch):
    """The latest finished data-quality run must reach the end of 2025, by its `through` and by its last hour: a run
    through 2024, one whose last hour says 2025's end but whose `through` is 2024, and one through 2025 whose last hour
    is earlier each refuse, opening no run. Then the replay is given data_end = data_last(2025)."""
    for through, window in ((2024, None), (2024, data_last(2025)), (2025, pd.Timestamp("2025-06-30T05:00Z"))):
        rw.log_dq(through, **({"window_last_utc": window.isoformat()} if window is not None else {}),
                  **({"hq_revised_records": 1} if through == 2025 else {}))
        with pytest.raises(mrun.FreezeError, match="is not a passing through-2025 run"):
            mrun.run_replay(2025, n_boot=20)
    assert rw.replay_runs(2025) == []
    rw.log_dq(2025, hq_revised_records=1)
    seen = {}
    real = mrun.replay

    def spy(*args, **kwargs):
        seen["data_end"], seen["demand"] = pd.Timestamp(args[7]), args[0]["ts_utc"].max()
        return real(*args, **kwargs)

    monkeypatch.setattr(mrun, "replay", spy)
    assert mrun.run_replay(2025, n_boot=20) == 0
    assert seen["data_end"] == data_last(2025) and seen["demand"] > data_last(2024)


def test_the_2025_replay_refuses_a_dirty_tree(chain):
    """An untracked file, or a change to a tracked file that is no rule file, refuses the 2025 start before any run
    opens; the clean tree passes (control)."""
    (chain.repo / "notes.txt").write_text("an untracked file\n")
    with pytest.raises(mrun.FreezeError, match="uncommitted changes"):
        mrun.run_replay(2025, n_boot=20)
    (chain.repo / "notes.txt").unlink()
    (chain.repo / "src/valiwatt/report/render.py").write_text("# renders otherwise\n")
    with pytest.raises(mrun.FreezeError, match="uncommitted changes"):
        mrun.held_out_gate(2025)
    assert rw.replay_runs(2025) == []
    rw.git(chain.repo, "checkout", "--", "src/valiwatt/report/render.py")
    assert mrun.held_out_gate(2025)[2]["standing"] == "clean"


def test_the_2025_replay_uses_the_artifacts_frozen_for_2024(chain):
    """A later validation run and a later freeze of 2024 in the store, with other hashes, the alias replay-2024 moved
    to it: the 2025 replay reads v2's first 2024 replay's runs, by their IDs, and is judged by their hashes."""
    from valiwatt.forecast.freeze import load_frozen

    w = rw.chain_world()
    th = json.loads(json.dumps(jsonable(w.thresholds)))
    th["performance"]["winter"]["mape_high"] *= 1.1
    val2 = rw.log_validation(w, chain.dq24, thresholds=th)
    frozen2 = rw.log_frozen_models(w, val2, chain.dq24, model=dataclasses.replace(w.model, q=w.model.q * 1.2))
    assert load_frozen(2024)[1].info.run_id == frozen2 != chain.frozen
    assert mrun.run_replay(2025, n_boot=20) == 0
    (start,) = rw.replay_runs(2025)
    t = start.data.tags
    assert (t["frozen_run_id"], t["validation_run_id"], t["coefficient_sha256"]) == (
        chain.frozen, chain.validation, chain.entry["model_sha256"])
    c = tracking.client()
    res = mrun._load_json_artifact(c, start.info.run_id, "results/replay.json")
    first = mrun._load_json_artifact(c, chain.first_2024, "results/replay.json")
    assert res["frozen_sha256"] == first["frozen_sha256"] and res["thresholds"] == first["thresholds"]
    assert res["model"]["sha256"] == chain.entry["model_sha256"]


def started_tags(chain) -> dict:
    """Every tag run_replay(2025) opens its run with, as a clean first start under the chain sets them."""
    return {"kind": "replay", "year": "2025", "freeze_tag": FREEZE_TAG, "freeze_commit": chain.f0,
            "rules_sha256": rules_sha256(), "coefficient_sha256": chain.entry["model_sha256"], "departure": "false",
            "departure_from": "", "evaluation": "held_out", "dq_run_id": chain.dq25, "frozen_run_id": chain.frozen,
            "validation_run_id": chain.validation, "standing": "clean", "status": "started", "git_commit": head(chain),
            "git_dirty": "false"}


def test_a_2025_replay_stopped_midway_leaves_its_started_run(tmp_path, monkeypatch):
    """An exception after the run opens leaves the run in the store, FAILED, with every tag it opened with, its stage
    the last step reached, `error` the exception's class and its gate record. Stopped reading its inputs: stage
    opened. Stopped in the Evidently cross-check or the never-acts check: stage replayed, no metric and no artifact but
    the gate record (holds_results false). Stopped writing an artifact after the metrics: stage checked, holds_results
    true. A run holding an artifact besides its gate record, and no metric: holds_results true. The next start's
    refusal says which."""
    import mlflow

    def boom(error):
        def fail(*args, **kwargs):
            raise error("stopped on purpose")
        return fail

    cases = [("load_raw", mrun, RuntimeError, "opened", False),
             ("_evidently_checks", mrun, RuntimeError, "replayed", False),
             ("check_unchanged", mrun, rp.MonitorActedError, "replayed", False),
             ("log_artifact", mlflow, OSError, "checked", True)]
    for name, module, error, stage, holds in cases:
        with pytest.MonkeyPatch.context() as mp:
            c = rw.chain_copy(tmp_path / name, mp)
            expected = started_tags(c) | {"stage": stage, "error": error.__name__}
            real = getattr(module, name)
            mp.setattr(module, name, boom(error))
            with pytest.raises(error):
                mrun.run_replay(2025, n_boot=20)
            mp.setattr(module, name, real)
            (run,) = rw.replay_runs(2025)
            assert run.info.status == "FAILED", name
            assert {k: run.data.tags.get(k) for k in expected} == expected, name
            artifacts = mrun._artifact_paths(tracking.client(), run.info.run_id)
            assert bool(run.data.metrics) is holds and artifacts == [mrun.GATE_RECORD], name
            (s,) = mrun.replay_starts(2025)
            assert (s["stage"], s["error"], s["holds_results"], s["finished"]) == (stage, error.__name__, holds, False)
            assert s["gate"]["standing"] == "clean" and s["gate"]["first_2024_run_id"] == c.first_2024
            said = (f"stopped at stage '{stage}' with error '{error.__name__}', and its run "
                    f"{'holds' if holds else 'holds no'} results")
            with pytest.raises(mrun.FreezeError, match=said):
                mrun.held_out_gate(2025)

    # A start whose run holds an artifact besides its gate record, and no metric, holds results too: what anyone
    # opening the store could read of 2025 (a run made by hand here; the command logs its metrics first).
    with pytest.MonkeyPatch.context() as mp:
        c = rw.chain_copy(tmp_path / "artifact_only", mp)
        client = tracking.client()
        run_id = rw.fake_run({"kind": "replay", "year": "2025", "freeze_tag": FREEZE_TAG, "freeze_commit": c.f0,
                              "git_commit": head(c), "status": "started", "stage": "checked"}, status="RUNNING")
        client.log_text(run_id, "{}", mrun.GATE_RECORD)
        client.log_text(run_id, "2025's hours", "frames/hourly.parquet")
        client.set_terminated(run_id, "FAILED")
        (s,) = mrun.replay_starts(2025)
        assert s["holds_results"] and not client.get_run(run_id).data.metrics
        with pytest.raises(mrun.FreezeError, match="and its run holds results"):
            mrun.held_out_gate(2025)


def test_a_2025_start_stopped_while_its_gate_record_is_logged_keeps_its_error_class(chain, monkeypatch):
    """The 2025 run opens, then its gate record is logged under the start lock: a stop there (a store or disk error)
    leaves the run with every tag it opened with, stage opened, `error` the exception's class and no gate record, and
    the next start's refusal names that class, never "none"."""
    import mlflow

    expected = started_tags(chain) | {"stage": "opened", "error": "OSError"}
    real = mlflow.log_text

    def full_disk(text, artifact_file, *args, **kwargs):
        if artifact_file == mrun.GATE_RECORD:
            raise OSError("no space left on device")
        return real(text, artifact_file, *args, **kwargs)

    monkeypatch.setattr(mlflow, "log_text", full_disk)
    with pytest.raises(OSError, match="no space left"):
        mrun.run_replay(2025, n_boot=20)
    monkeypatch.setattr(mlflow, "log_text", real)
    (run,) = rw.replay_runs(2025)
    assert run.info.status == "FAILED"
    assert {k: run.data.tags.get(k) for k in expected} == expected
    assert mrun._artifact_paths(tracking.client(), run.info.run_id) == [] and not run.data.metrics
    (s,) = mrun.replay_starts(2025)
    assert (s["stage"], s["error"], s["holds_results"], s["finished"], s["gate"]) == ("opened", "OSError", False,
                                                                                     False, None)
    with pytest.raises(mrun.FreezeError, match="stopped at stage 'opened' with error 'OSError', and its run holds no "
                                               "results"):
        mrun.held_out_gate(2025)


def test_a_2025_run_exists_only_with_its_tags(chain, monkeypatch):
    """A run is created with its tags in the one call that creates it (tracking.start_run). The tags' computation
    failing (here the git state) creates no run; a stop right after the creation call (the process killed there)
    leaves a run that already carries kind, year, the freeze tags and the git tags, so replay_starts lists it as a
    stopped start and the next start's gate refuses on it."""
    import mlflow
    from mlflow.entities import ViewType

    def every_run():
        c = tracking.client()
        exp = c.get_experiment_by_name(mrun.EXPERIMENT)
        return {r.info.run_id for r in c.search_runs([exp.experiment_id], "", run_view_type=ViewType.ALL)}

    before = every_run()

    def no_git(*args, **kwargs):
        raise RuntimeError("git is not there")

    with monkeypatch.context() as mp:
        mp.setattr(tracking, "git_state", no_git)
        with pytest.raises(RuntimeError, match="git is not there"):
            mrun.run_replay(2025, n_boot=20)
    assert every_run() == before and rw.replay_runs(2025) == []

    class Killed(BaseException):
        pass

    real = mlflow.start_run

    def killed_after_create(*args, **kwargs):
        real(*args, **kwargs)
        mlflow.end_run("KILLED")
        raise Killed

    with monkeypatch.context() as mp:
        mp.setattr(mlflow, "start_run", killed_after_create)
        with pytest.raises(Killed):
            mrun.run_replay(2025, n_boot=20)
    (run,) = rw.replay_runs(2025)
    assert every_run() == before | {run.info.run_id}
    expected = started_tags(chain) | {"stage": "opened"}
    assert {k: run.data.tags.get(k) for k in expected} == expected and "code_sha256" in run.data.tags
    (s,) = mrun.replay_starts(2025)
    assert (s["run_id"], s["stage"], s["finished"], s["running"], s["gate"]) == (run.info.run_id, "opened", False,
                                                                                 False, None)
    with pytest.raises(mrun.FreezeError, match=rf"\(run {run.info.run_id}\), stopped at stage 'opened'"):
        mrun.held_out_gate(2025)


@pytest.mark.parametrize("name, value", [
    ("MLFLOW_RUN_ID", "0" * 32), ("MLFLOW_RUN_CONTEXT", '{"mlflow.runName": "ambient-name"}'),
    ("MLFLOW_EXPERIMENT_NAME", "valiwatt-validation"), ("MLFLOW_ENABLE_SYSTEM_METRICS_LOGGING", "true")],
    ids=["run_id", "run_context", "experiment_name", "system_metrics"])
def test_a_2025_start_refuses_an_ambient_mlflow_environment_before_any_run_exists(chain, monkeypatch, name, value):
    """An MLflow environment variable other than the allowed ones, each of which can make mlflow.start_run enter
    another run, add to it or fail once it exists: the 2025 start is refused before any run exists, so it is no start
    (replay_starts lists none) and, the variable unset, the next start is the test of record."""
    from mlflow.entities import ViewType

    c = tracking.client()

    def every_run():
        return {r.info.run_id for e in c.search_experiments()
                for r in c.search_runs([e.experiment_id], "", run_view_type=ViewType.ALL)}

    before = every_run()
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=rf"MLflow environment variables \['{name}'\] are set"):
        mrun.run_replay(2025, n_boot=20)
    assert every_run() == before and mrun.replay_starts(2025) == []
    monkeypatch.delenv(name)
    assert tracking.ambient_mlflow_env() == []


def test_a_2025_start_is_a_fresh_run_whatever_run_id_the_environment_names(chain, monkeypatch):
    """Past the environment's refusal (the allowed variables widened here to let MLFLOW_RUN_ID and MLFLOW_RUN_CONTEXT
    through): MLFLOW_RUN_ID naming an older run of another experiment (mlflow.start_run would resume it, in its
    experiment and with its start time, where replay_starts never looks), the 2025 start is still a fresh run of the
    monitoring experiment, listed by replay_starts, and the older run is left as it was."""
    from mlflow.entities import ViewType

    monkeypatch.setattr(tracking, "MLFLOW_ENV_ALLOWED", (*tracking.MLFLOW_ENV_ALLOWED, "MLFLOW_RUN_ID",
                                                         "MLFLOW_RUN_CONTEXT"))
    c = tracking.client()
    other = c.get_experiment_by_name("valiwatt-validation")
    (old,) = c.search_runs([other.experiment_id], "", run_view_type=ViewType.ALL, max_results=1)
    old_tags = dict(old.data.tags)
    monkeypatch.setenv("MLFLOW_RUN_ID", old.info.run_id)
    real = mrun.load_raw

    def stop(*args, **kwargs):
        raise RuntimeError("stopped on purpose")

    monkeypatch.setattr(mrun, "load_raw", stop)
    with pytest.raises(RuntimeError, match="stopped on purpose"):
        mrun.run_replay(2025, n_boot=20)
    monkeypatch.setattr(mrun, "load_raw", real)
    (run,) = rw.replay_runs(2025)
    assert run.info.run_id != old.info.run_id
    assert run.info.experiment_id == c.get_experiment_by_name(mrun.EXPERIMENT).experiment_id
    assert [s["run_id"] for s in mrun.replay_starts(2025)] == [run.info.run_id]
    after = c.get_run(old.info.run_id)
    assert dict(after.data.tags) == old_tags and after.info.start_time == old.info.start_time
    # An ambient context naming the run otherwise neither renames it nor stops its creation; the context's other tags
    # (mlflow's user and source) are kept beside the run's own.
    monkeypatch.delenv("MLFLOW_RUN_ID", raising=False)  # end_run clears it once the start ends
    monkeypatch.setenv("MLFLOW_RUN_CONTEXT", '{"mlflow.runName": "ambient-name", "ambient.key": "kept"}')
    with tracking.start_run(mrun.EXPERIMENT, "named", tags={"kind": "probe"}) as probe:
        pass
    t = c.get_run(probe.info.run_id).data.tags
    assert (t["mlflow.runName"], t["kind"], t["ambient.key"]) == ("named", "probe", "kept") and "mlflow.user" in t


def test_a_run_that_could_not_be_entered_is_refused_before_it_exists(chain, monkeypatch):
    """A run already active, or another experiment selected with mlflow.set_experiment, would refuse entering a fresh
    run once created: tracking.start_run refuses before creating any. Should entering fail all the same, the created
    run is ended FAILED with its error's class, never left RUNNING: a 2025 start so stopped is a stopped start, which
    the next gate refuses as one, not a running one."""
    import mlflow
    from mlflow.entities import ViewType
    from mlflow.tracking import fluent

    c = tracking.client()

    def every_run():
        return {r.info.run_id for e in c.search_experiments()
                for r in c.search_runs([e.experiment_id], "", run_view_type=ViewType.ALL)}

    with tracking.start_run("valiwatt-validation", "outer"):
        before = every_run()
        with pytest.raises(RuntimeError, match="is active"):
            with tracking.start_run(mrun.EXPERIMENT, "inner"):
                pass
        assert every_run() == before
    with monkeypatch.context() as mp:
        mp.setattr(fluent, "_active_experiment_id", c.get_experiment_by_name("valiwatt-validation").experiment_id)
        before = every_run()
        with pytest.raises(RuntimeError, match="is selected"):
            mrun.run_replay(2025, n_boot=20)
        assert every_run() == before and rw.replay_runs(2025) == []

    def refused(*args, **kwargs):
        raise OSError("cannot enter")

    with monkeypatch.context() as mp:
        mp.setattr(mlflow, "start_run", refused)
        with pytest.raises(OSError, match="cannot enter"):
            mrun.run_replay(2025, n_boot=20)
    (run,) = rw.replay_runs(2025)
    assert (run.info.status, run.data.tags.get("error"), run.data.tags.get("kind")) == ("FAILED", "OSError", "replay")
    (s,) = mrun.replay_starts(2025)
    assert (s["running"], s["finished"], s["error"]) == (False, False, "OSError")
    with pytest.raises(mrun.FreezeError, match=r"stopped at stage 'opened' with error 'OSError'"):
        mrun.held_out_gate(2025)


def test_a_gate_refusal_opens_no_run(chain, monkeypatch):
    """A refused 2025 start opens no run and leaves the start lock free: the next start, once the refusal's cause is
    gone, opens one run."""
    rw.write_ledger(chain.repo, [])
    opened = []
    real = tracking.start_run
    monkeypatch.setattr(tracking, "start_run", lambda *a, **k: opened.append(a) or real(*a, **k))
    with pytest.raises(mrun.FreezeError, match="holds no 2024 entry"):
        mrun.run_replay(2025, n_boot=20)
    assert opened == [] and rw.replay_runs(2025) == [] and not lock_is_held(chain.root)
    rw.write_ledger(chain.repo, [chain.entry])
    assert mrun.run_replay(2025, n_boot=20) == 0
    assert len(rw.replay_runs(2025)) == 1 and not lock_is_held(chain.root)


def test_the_replay_function_refuses_2025_without_its_open_started_run(chain):
    """replay() reads 2025 only for the started 2025 run of this code, looked up in the store by its ID: refused with
    no run ID, a run ID the store lacks, a run of another commit, year, kind (an evidence run, a rehearsal's) or
    freeze commit, a run whose status tag is not
    started or that is not RUNNING, and a diagnostic run whose replay_run_id names a 2025 replay that did not finish
    complete, one of other commits, a run of another kind (a finished diagnostic run), or none. An active run in this
    process is no authority: only the ID given is read.
    The controls: a started replay run, and a diagnostic run naming its finished replay."""
    base = {"kind": "replay", "year": "2025", "freeze_tag": FREEZE_TAG, "freeze_commit": chain.f0,
            "git_commit": head(chain), "status": "started"}
    w = rw.chain_world()
    raw, obs = rw.vintage(2025)
    held = mrun.frozen_for_2025(chain.entry)

    def call(run_id):
        return rp.replay(raw, obs, held["model"], held["thresholds"], held["reference"], "2025-01-06", "2025-01-08",
                         data_last(2025), runs=w.runs, run_id=run_id)

    good = rw.fake_run(base, status="RUNNING")
    assert len(call(good).days) == 3
    finished = rw.fake_run(base | {"status": "complete"})
    failed = rw.fake_run(base, status="FAILED")
    elsewhere = rw.fake_run(base | {"status": "complete", "git_commit": chain.f0})
    diagnostic = base | {"kind": "diagnostic"}
    mrun.check_2025_run(rw.fake_run(diagnostic | {"replay_run_id": finished}, status="RUNNING"))
    # A finished, complete 2025 diagnostic run of these commits, naming the finished replay: no replay itself.
    finished_diagnostic = rw.fake_run(diagnostic | {"status": "complete", "replay_run_id": finished})
    cases = {None: "no run ID was given", "0" * 32: "holds no run",
             rw.fake_run(base | {"git_commit": chain.f0}, status="RUNNING"): "not HEAD",
             rw.fake_run(base | {"year": "2024"}, status="RUNNING"): "its year is '2024'",
             rw.fake_run(base | {"kind": "evidence"}, status="RUNNING"): "its kind is 'evidence'",
             rw.fake_run(base | {"kind": "pseudo_replay"}, status="RUNNING"): "its kind is 'pseudo_replay'",
             rw.fake_run(base | {"freeze_commit": "0" * 40}, status="RUNNING"): "its freeze commit is",
             rw.fake_run(base | {"status": "complete"}, status="RUNNING"): "not started",
             rw.fake_run(base): "not RUNNING",
             rw.fake_run(diagnostic | {"replay_run_id": failed}, status="RUNNING"): "names no finished",
             rw.fake_run(diagnostic | {"replay_run_id": elsewhere}, status="RUNNING"): "names no finished",
             rw.fake_run(diagnostic | {"replay_run_id": finished_diagnostic}, status="RUNNING"): "names no finished",
             rw.fake_run(diagnostic, status="RUNNING"): "replay_run_id 'none'"}
    for run_id, said in cases.items():
        with pytest.raises(mrun.FreezeError, match=said):
            call(run_id)
    with tracking.start_run(mrun.EXPERIMENT, "replay", tags=base):
        for run_id in (None, "0" * 32):
            with pytest.raises(mrun.FreezeError):
                mrun.check_2025_run(run_id)


def test_the_diagnostics_run_after_the_replay_has_finished(chain, monkeypatch):
    """A sensitivity measure that fails: the 2025 replay's run had finished complete before the measures started, and
    stays so; its diagnostic run, naming it, is FAILED with the error's class; the command exits non-zero, and the
    next gate is not refused by that diagnostic run."""
    seen = {}

    def measure(*args, **kwargs):
        (rep,) = rw.replay_runs(2025)
        seen["replay"] = (rep.info.status, rep.data.tags["status"], rep.data.tags["stage"])
        seen["kwargs"] = kwargs
        raise RuntimeError("a measure failed")

    monkeypatch.setattr(S, "measure", measure)
    assert mrun.run_replay(2025, n_boot=20) == 1
    (rep,) = rw.replay_runs(2025)
    (diag,) = rw.replay_runs(2025, kind="diagnostic")
    assert seen["replay"] == ("FINISHED", "complete", "logged")
    assert (rep.info.status, rep.data.tags["status"]) == ("FINISHED", "complete")
    assert (diag.info.status, diag.data.tags["status"], diag.data.tags["error"]) == (
        "FAILED", "started", "RuntimeError")
    assert diag.data.tags["replay_run_id"] == rep.info.run_id
    assert diag.data.tags["git_commit"] == rep.data.tags["git_commit"]
    assert seen["kwargs"]["run_id"] == diag.info.run_id and seen["kwargs"]["runs"] is not None
    assert "sensitivity" not in mrun._load_json_artifact(tracking.client(), rep.info.run_id, "results/replay.json")
    assert mrun.held_out_gate(2025)[2]["causes"] == []


def test_two_workers_replay_2025_under_the_diagnostic_run(chain, monkeypatch):
    """sensitivity.measure with two forkserver workers on the synthetic 2025, under a recorded diagnostic run naming
    the finished replay: each worker authorizes its replays from the store by the run ID it receives, and never ends
    or changes that run (on the chain's nineteen days the episode grid holds no start, so the workers' replays are the
    level shifts'). A run ID the store lacks refuses inside a worker."""
    assert mrun.run_replay(2025, n_boot=20) == 0
    (rep,) = rw.replay_runs(2025)
    diag = rw.fake_run({"kind": "diagnostic", "year": "2025", "replay_run_id": rep.info.run_id,
                        "git_commit": rep.data.tags["git_commit"], "freeze_commit": chain.f0, "status": "started"},
                       status="RUNNING")
    w = rw.chain_world()
    raw, obs = rw.vintage(2025)
    held = mrun.frozen_for_2025(chain.entry)
    first_day, last_day = rw.CHAIN_DAYS[2025]
    r = rp.replay(raw, obs, held["model"], held["thresholds"], held["reference"], first_day, last_day, data_last(2025),
                  runs=w.runs, comparators=held["comparators"], run_id=diag)
    monkeypatch.setattr(S, "_init", rw.worker_init)  # each worker installs the chain first
    real_inputs = S._inputs
    monkeypatch.setattr(S, "_inputs", lambda *a, **k: real_inputs(*a, **k) | {"_chain_root": str(chain.root)})
    args = (raw, obs, held["model"], held["thresholds"], held["reference"], r, data_last(2025))
    kw = dict(runs=w.runs, forecast_reference=r.temperature_reference, workers=2)
    out = MEASURE(*args, run_id=diag, **kw)
    assert out["episodes"]["rows"] == []
    assert sorted(x["shift"] for x in out["level_shifts"]) == sorted(round(f - 1, 4) for f in S.STEP_FACTORS)
    assert all(x["weekly_bias"] for x in out["level_shifts"])  # each worker's replay judged its weeks
    with pytest.raises(mrun.FreezeError, match="holds no run"):
        MEASURE(*args, run_id="0" * 32, **kw)
    after = tracking.client().get_run(diag)
    assert after.info.status == "RUNNING" and after.data.tags["status"] == "started" and "error" not in after.data.tags


# --------------------------------------------------------------------------------------------------- the evidence

def test_the_evidence_compares_every_column_and_decides_nothing(chain):
    """On the 2025 vintage revising one 2024 record: the evidence run lists the differing hours and columns (the
    revised hour's target, the next day's lag), counts the revised records, is of kind evidence, adds no start and no
    ledger entry, and the 2025 gate's outcome is the same after it. Its comparison counts a column or an hour only one
    frame holds as differing, and NaN equal to NaN. Refused: v2's first 2024 run absent from the store, or its
    frames/hourly.parquet."""
    hours = pd.date_range("2024-01-01T06:00Z", periods=4, freq="h", name="ts_utc")
    a = pd.DataFrame({"x": [1.0, np.nan, 3.0], "only_a": [0.0, 0.0, 0.0]}, index=hours[:3])
    b = pd.DataFrame({"x": [1.0, np.nan, 3.0, 4.0], "only_b": ["s"] * 4}, index=hours)
    summary, cells = mrun.compare_hourly(a, b)
    assert summary == {"hours": 4, "columns": 3, "hours_differing": 4, "differing": {"only_a": 3, "only_b": 4, "x": 1}}
    assert (cells.loc[cells["column"] == "only_a", "rerun"] == "absent").all()
    assert (cells.loc[cells["column"] == "only_b", "original"] == "absent").all()
    assert cells.loc[cells["column"] == "x", ["utc", "original", "rerun"]].values.tolist() == [[hours[3], "absent",
                                                                                                "4.0"]]
    assert mrun.compare_hourly(a, a.copy())[0]["hours_differing"] == 0

    c = tracking.client()
    gate = mrun.held_out_gate(2025)
    ledger = (chain.repo / mrun.LEDGER).read_text()
    starts = (mrun.replay_starts(2024), mrun.replay_starts(2025))
    assert mrun.run_evidence() == 0
    (ev,) = mrun._replay_runs(c, kind="evidence")
    res = mrun._load_json_artifact(c, ev.info.run_id, "results/evidence.json")
    with tempfile.TemporaryDirectory() as tmp:
        cells = pd.read_parquet(c.download_artifacts(ev.info.run_id, "frames/differences.parquet", tmp))
    assert res["revised_records"] == 1 and res["hours_differing"] == cells["utc"].nunique() > 0
    assert res["differing"] == cells["column"].value_counts().to_dict()
    assert "target" in set(cells.loc[cells["utc"] == str(rw.REVISED_UTC), "column"])
    assert str(rw.REVISED_UTC + 24 * H) in set(cells.loc[cells["column"] == "lag24", "utc"])
    assert ev.data.tags["kind"] == "evidence" and ev.data.tags["first_2024_run_id"] == chain.first_2024
    assert (mrun.replay_starts(2024), mrun.replay_starts(2025)) == starts
    assert (chain.repo / mrun.LEDGER).read_text() == ledger and mrun.held_out_gate(2025) == gate

    rw.write_ledger(chain.repo, [chain.entry | {"run_id": "f" * 32}])
    with pytest.raises(mrun.FreezeError, match="is not in the run store"):
        mrun.run_evidence()
    rw.write_ledger(chain.repo, [chain.entry])
    rw.artifact_file(chain.first_2024, "frames/hourly.parquet").unlink()
    with pytest.raises(mrun.FreezeError, match="holds no frames/hourly.parquet"):
        mrun.run_evidence()
    assert len(mrun._replay_runs(c, kind="evidence")) == 1


def test_the_evidence_refuses_a_data_quality_run_that_is_not_a_passing_through_2025_run(chain):
    """The evidence is made on the run the 2025 gate reads, the latest FINISHED data-quality run: one through 2024,
    or one through 2025 whose gates failed while an older one passed, refuses it, and no evidence run opens."""
    rw.log_dq(2024)
    with pytest.raises(mrun.FreezeError, match="is not a passing through-2025 run.*window ends with 2024"):
        mrun.run_evidence()
    rw.log_dq(2025, gates="failed", hq_revised_records=1)
    with pytest.raises(mrun.FreezeError, match="is not a passing through-2025 run.*gates are 'failed'"):
        mrun.run_evidence()
    assert mrun._replay_runs(tracking.client(), kind="evidence") == []


def test_the_evidence_refuses_a_dirty_tree(chain, monkeypatch):
    """An uncommitted change refuses the evidence before anything is read, and no evidence run opens."""
    (chain.repo / "notes.txt").write_text("an untracked file\n")
    for name in ("read_ledger", "load_raw", "frozen_for_2025"):
        monkeypatch.setattr(mrun, name, reached(name))
    with pytest.raises(mrun.FreezeError, match="uncommitted changes"):
        mrun.run_evidence()
    assert mrun._replay_runs(tracking.client(), kind="evidence") == []


def test_the_evidence_replay_receives_no_record_of_2025(chain, monkeypatch):
    """On the 2025 vintage, holding 2025's demand and station records and runs: the replay of 2024 the evidence makes
    receives none stamped after data_last(2024), and the counts of those dropped are logged."""
    raw25, obs25 = rw.vintage(2025)
    end = data_last(2024)
    runs = qrun.load_runs(tracking.client().get_run(chain.dq25))
    later = int((raw25["ts_utc"] > end).sum() + (obs25["ts_utc"] > end).sum())
    later_points = int((runs["available_utc"] > end).sum())
    assert later > 0 and later_points > 0
    seen = {}
    real = mrun.replay

    def spy(raw_demand, raw_temperature, *args, **kwargs):
        seen.update(demand=raw_demand["ts_utc"].max(), temperature=raw_temperature["ts_utc"].max(),
                    runs=kwargs["runs"]["available_utc"].max(), data_end=pd.Timestamp(args[5]))
        return real(raw_demand, raw_temperature, *args, **kwargs)

    monkeypatch.setattr(mrun, "replay", spy)
    assert mrun.run_evidence() == 0
    assert max(seen["demand"], seen["temperature"], seen["runs"]) <= end == seen["data_end"]
    (ev,) = mrun._replay_runs(tracking.client(), kind="evidence")
    assert ev.data.params["records_after_2024_dropped"] == str(later)
    assert ev.data.params["forecast_points_after_2024_dropped"] == str(later_points)
