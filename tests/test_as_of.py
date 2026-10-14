"""Design code sees each decision time as the replay sees its clock.

A synthetic world holds two misplaced records. The hour S has no record, and its value arrives months later as a
second record stamped T, exactly at an origin and at the 2020 fit clock (the 2020-01 fold's and the 2020 frozen
model's), as on the real data. The hour S2 has no record either, and its value arrives as a second record stamped T2,
the hour its day is scored. The data-quality repair moves each value back, but only once its record and the hour after
it are in view. The design tables (`validate.validation_inputs`, `freeze.frozen_table`) must give, at every origin,
the lags and targets the replay path gives there; no fit before T may train on the moved value; and no half-width
fixed at T may calibrate on a day the replay scores after T.
"""

import numpy as np
import pandas as pd
import pytest

from conftest import synth_demand, synth_obs
from valiwatt.config import DEMAND_FIRST, stations
from valiwatt.forecast import freeze as fz
from valiwatt.forecast import model as mdl
from valiwatt.forecast import validate as vd
from valiwatt.forecast import walkforward as wf
from valiwatt.forecast.evaluate import conformal_quantile, relative_conformal, season_of
from valiwatt.forecast.features import AsOf, build
from valiwatt.forecast.freeze import FrozenModel
from valiwatt.forecast.guards import LookaheadError
from valiwatt.monitor import replay as rp
from valiwatt.monitor.clock import Clock, RawFeed
from valiwatt.quality.demand import build_demand

# The world starts where the replay's grid starts, so each moved value has one empty hour to go to.
FIRST, LAST = DEMAND_FIRST, pd.Timestamp("2020-03-01T05:00Z")
H = pd.Timedelta(hours=1)
S = pd.Timestamp("2019-10-15T12:00Z")  # a daily peak: the value moved here fits here and nowhere else
T = pd.Timestamp("2020-01-01T05:00Z")  # the origin of 2020-01-01: the 2020-01 fold's and the 2020 frozen model's fit
S2 = pd.Timestamp("2020-02-11T00:00Z")  # a daily trough on local standard day 2020-02-10
T2 = pd.Timestamp("2020-02-11T06:00Z")  # the hour day 2020-02-10 is scored


@pytest.fixture(scope="module")
def world():
    raw = synth_demand(FIRST, LAST)
    moved = {s: float(raw.loc[raw["ts_utc"] == s, "mw"].iloc[0]) for s in (S, S2)}
    raw = raw[~raw["ts_utc"].isin([S, S2])]
    late = pd.DataFrame({"ts_utc": [T, T2], "mw": [moved[S], moved[S2]],
                         "source_line": raw["source_line"].max() + np.array([1, 2])})
    raw = pd.concat([raw, late], ignore_index=True)
    obs = synth_obs(FIRST, LAST)
    temp = obs.set_index("ts_utc")["temp_c"]
    weather = pd.DataFrame({f"{s.key}.temp_c": temp for s in stations()})
    return raw, obs, temp, weather, moved


@pytest.fixture(scope="module")
def inputs(world):
    """The tables validation runs on, built by the helper `valiwatt validate` calls."""
    raw, obs, _, weather, _ = world
    return vd.validation_inputs(raw, obs, weather, last=LAST)


def lst_day(t):
    return (t - H + pd.Timedelta(hours=-5)).tz_convert(None).normalize()


def test_the_world_holds_two_misplaced_records_each_repaired_only_after_it_arrives(world):
    raw, _, _, _, moved = world
    full = build_demand(raw, FIRST, LAST)
    assert [(e["moved_to_utc"], e["moved_mw"]) for e in full.misplaced_events] == [(S, moved[S]), (S2, moved[S2])]
    for t in (T, T2):
        before = build_demand(raw[raw["ts_utc"] <= t], FIRST, t)
        after = build_demand(raw[raw["ts_utc"] <= t + H], FIRST, t + H)
        assert len(after.misplaced_events) == len(before.misplaced_events) + 1
        assert before.frame.loc[t, "status"] == "unresolved_duplicate" and after.frame.loc[t, "status"] == "ok"


def replay_day(raw, obs, day, cols):
    """Day D's rows on the replay path: issued at its origin, then scored when the replay scores it. The scoring
    clock is the replay's own schedule (monitor/replay.py, replay), never the code under test."""
    clock = Clock(rp.origin(day))
    state = rp._State(RawFeed(raw, clock, "demand"), RawFeed(obs, clock, "temperature"), clock,
                      scale_last=rp.origin(day))
    model = FrozenModel(columns=cols, beta=np.zeros(len(cols)), q=0.1, replay_year=day.year,
                        train_last_day=str(day.date()), n_train=0)
    issued = rp.issue(state, model, day)
    at = rp.origin(day + pd.Timedelta(days=1)) + H
    final = at > LAST
    at = min(at, LAST)
    clock.advance(at)
    scored, _ = rp.score_day(state, day, issued, final=final)
    return issued, scored, at


def same(a: pd.Series, b: pd.Series) -> bool:
    return bool(np.allclose(a.to_numpy(float), b.to_numpy(float), equal_nan=True, rtol=0, atol=1e-9))


def test_design_rows_equal_the_replay_path_at_each_origin(world, inputs):
    raw, obs, temp, _, _ = world
    events = (S, T, S2, T2)
    around = {lst_day(t + k * H) for t in events for k in (0, 1, 24, 25, 168)}
    around |= {d + pd.Timedelta(days=k) for d in list(around) for k in (-1, 1)}
    rng = np.random.default_rng(7)
    days = pd.date_range("2019-01-09", "2020-02-29", freq="D")
    # The last day is scored at the end of the data, as the replay scores its last day.
    sample = sorted(around | set(days[rng.choice(len(days), 20, replace=False)]) | {days[-1]})
    f = inputs["f_primary"]
    cols = mdl.design(f.iloc[:24], "persistence").columns
    for day in sample:
        issued, scored, at = replay_day(raw, obs, day, cols)
        rows = f[f["day"] == day]
        assert list(rows.index) == list(issued.index)
        assert (rows["scored_at"] == at).all(), day
        for c in ("lag24", "lag168", "temp_persistence"):
            assert same(rows[c], issued[c]), (day, c)
        assert same(rows["target"], scored["target"]), day
    # The comparison can fail: the full-window series differs from the replay at each event.
    full = build(build_demand(raw, FIRST, LAST).frame, temp)
    assert np.isfinite(full.loc[S, "target"]) and np.isnan(f.loc[S, "target"])
    assert np.isfinite(full.loc[S + 24 * H, "lag24"]) and np.isnan(f.loc[S + 24 * H, "lag24"])
    # T is read at 2020-01-01's origin, when it is still an unresolved duplicate; it is scored an hour later.
    assert np.isfinite(full.loc[T + 24 * H, "lag24"]) and np.isnan(f.loc[T + 24 * H, "lag24"])
    assert np.isfinite(f.loc[T, "target"])
    # S2's day is scored at T2, when T2 has arrived but not the hour after it that resolves the repair.
    assert np.isfinite(full.loc[S2, "target"]) and np.isnan(f.loc[S2, "target"])


def test_a_fit_sees_the_same_table_as_the_replay_at_its_clock(world, inputs):
    raw, obs, _, _, _ = world
    for clock in (wf.folds("2019-12", "2019-12")[0].fit_clock, fz.fit_clock(2020),
                  wf.folds("2020-02", "2020-02")[0].fit_clock):
        c = Clock(clock)
        frame, t, _, _ = rp._State(RawFeed(raw, c, "demand"), RawFeed(obs, c, "temperature"), c, clock).canonical()
        replayed = build(frame, t)
        mine = inputs["train_primary"](clock)
        assert list(mine.index) == list(replayed.index)
        for col in ("target", "lag24", "lag168"):
            assert same(mine[col], replayed[col]), (clock, col)


def test_a_clock_after_the_last_record_loaded_is_refused(world):
    with pytest.raises(LookaheadError):
        AsOf(world[0], FIRST, LAST).frame(LAST + H)


def test_a_repaired_value_does_not_reach_lags_or_training_before_its_record_arrives(world, inputs, monkeypatch):
    raw, _, temp, weather, moved = world
    # The fault: in the full-window series, the value stamped T already sits at S.
    full = build(build_demand(raw, FIRST, LAST).frame, temp)
    assert full.loc[S, "target"] == moved[S] and full.loc[S + 168 * H, "lag168"] == moved[S]

    f = inputs["f_primary"]
    assert np.isnan(f.loc[S, "target"])
    assert np.isnan(f.loc[S + 24 * H, "lag24"]) and np.isnan(f.loc[S + 168 * H, "lag168"])

    seen = []
    real_fit = mdl.OLSForecaster.fit

    def spy(self, train):
        seen.append(train)
        return real_fit(self, train)

    monkeypatch.setattr(wf.OLSForecaster, "fit", spy)
    fl = wf.folds("2019-12", "2020-02")
    out = wf.run(f, fl, scenarios=("persistence",), train_at=inputs["train_primary"])
    trained = dict(zip((str(fold.test_month) for fold in fl), seen, strict=True))
    for month in ("2019-12", "2020-01"):  # fitted before T is resolved (2020-01: at T itself)
        tr = trained[month]
        assert np.isnan(tr.loc[S, "target"]) and np.isnan(tr.loc[S + 24 * H, "lag24"])
        assert np.isnan(tr.loc[S + 168 * H, "lag168"])
    tr = trained["2020-02"]  # fitted after: the repair is in view, as in the replay
    assert tr.loc[S, "target"] == moved[S] and tr.loc[S + 168 * H, "lag168"] == moved[S]
    assert (out["scored_at"] == f.loc[out.index, "scored_at"]).all()  # each row keeps when it was scored

    frozen = fz.frozen_table(raw, weather, 2020, last=LAST)
    assert frozen.index.max() == fz.fit_clock(2020) == T
    assert np.isnan(frozen.loc[T, "target"])  # at its fit clock, T is an unresolved duplicate
    assert np.isnan(frozen.loc[S, "target"]) and np.isnan(frozen.loc[S + 24 * H, "lag24"])


def test_a_half_width_fixed_at_a_fit_clock_calibrates_only_on_the_days_scored_by_then(world, inputs):
    """T arrives exactly at the 2020 fit clock and is resolved an hour later, when day 2019-12-31 is scored. When the
    frozen model's half-width and the 2020-01 fold's interval are fixed, at T, that day has no score yet: none of its
    hours, T's among them, may calibrate them."""
    raw, _, _, weather, _ = world
    f = inputs["f_primary"]
    eve = pd.Timestamp("2019-12-31")
    assert fz.fit_clock(2020) == wf.folds("2020-01", "2020-01")[0].fit_clock == T
    assert (f.loc[f["day"] == eve, "scored_at"] == T + H).all() and np.isfinite(f.loc[T, "target"])

    oos = f.loc[(f["day"] >= pd.Timestamp("2019-01-01")) & (f["day"] <= pd.Timestamp("2020-01-31")),
                ["target", "day", "scored_at"]].copy()
    oos["fold"] = oos["day"].dt.to_period("M").astype(str)
    oos["pred_persistence"] = f["lag24"]  # a forecast known at each origin
    # Far off on 2019-12-31, so whether that day's scores enter moves the half-width.
    oos.loc[oos["day"] == eve, "pred_persistence"] = oos.loc[oos["day"] == eve, "target"] / 2

    before = oos["day"] < pd.Timestamp("2020-01-01")
    by_then = before & (oos["day"] < eve)
    rel = ((oos["target"] - oos["pred_persistence"]).abs() / oos["pred_persistence"]).to_numpy()
    expected = conformal_quantile(rel[by_then.to_numpy()])
    assert expected != conformal_quantile(rel[before.to_numpy()])  # the control: the day would move it

    frozen, _ = fz.fit_frozen(fz.frozen_table(raw, weather, 2020, last=LAST), oos, 2020)
    assert frozen.q == expected and frozen.calibration_scores == int(np.isfinite(rel[by_then.to_numpy()]).sum())

    absolute = (oos["target"] - oos["pred_persistence"]).abs().to_numpy()
    winter = (season_of(oos["day"]) == "winter").to_numpy()
    for kwargs, scores, rows in (({}, rel, True), ({"absolute": True}, absolute, True),
                                 ({"groups": season_of(oos["day"])}, rel, winter)):
        iv = relative_conformal(oos, "pred_persistence", **kwargs)
        q = iv.loc[iv["fold"] == "2020-01", "q"].unique()
        assert conformal_quantile(scores[(by_then & rows).to_numpy()]) != \
            conformal_quantile(scores[(before & rows).to_numpy()]), kwargs
        assert list(q) == [conformal_quantile(scores[(by_then & rows).to_numpy()])], kwargs
