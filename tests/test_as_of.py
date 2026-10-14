"""Design code sees each decision time as the replay sees its clock.

A synthetic world holds one misplaced record: the hour S has no record, and its value arrives months later as a second
record stamped T. The data-quality repair moves it back to S, but only once T and the hour after it are in view. The
design tables (`features.AsOf`) must give, at every origin, the lags and targets the replay path gives there, and no
fit before T may train on the moved value.
"""

import numpy as np
import pandas as pd
import pytest

from conftest import synth_demand, synth_obs
from valiwatt.config import DEMAND_FIRST
from valiwatt.forecast import freeze as fz
from valiwatt.forecast import model as mdl
from valiwatt.forecast import walkforward as wf
from valiwatt.forecast.features import AsOf, build, day_origin, score_clock
from valiwatt.forecast.freeze import FrozenModel
from valiwatt.forecast.guards import LookaheadError
from valiwatt.monitor import replay as rp
from valiwatt.monitor.clock import Clock, RawFeed
from valiwatt.quality.demand import build_demand

# The world starts where the replay's grid starts, so the moved value has one empty hour to go to: S.
FIRST, LAST = DEMAND_FIRST, pd.Timestamp("2020-03-01T05:00Z")
S = pd.Timestamp("2019-10-15T12:00Z")  # a daily peak: the moved value fits here and nowhere else
T = pd.Timestamp("2020-01-20T00:00Z")  # a daily trough, after the 2019-12 fold's and the 2020 frozen model's fits
H = pd.Timedelta(hours=1)


@pytest.fixture(scope="module")
def world():
    raw = synth_demand(FIRST, LAST)
    moved = float(raw.loc[raw["ts_utc"] == S, "mw"].iloc[0])
    raw = raw[raw["ts_utc"] != S]
    late = pd.DataFrame({"ts_utc": [T], "mw": [moved], "source_line": [raw["source_line"].max() + 1]})
    raw = pd.concat([raw, late], ignore_index=True)
    obs = synth_obs(FIRST, LAST)
    return raw, obs, obs.set_index("ts_utc")["temp_c"], moved


@pytest.fixture(scope="module")
def asof(world):
    return AsOf(world[0], FIRST, LAST)


def lst_day(t):
    return (t - H + pd.Timedelta(hours=-5)).tz_convert(None).normalize()


def test_the_world_holds_one_misplaced_record_repaired_only_after_it_arrives(world):
    raw, _, _, moved = world
    full = build_demand(raw, FIRST, LAST)
    assert [(e["moved_to_utc"], e["moved_mw"]) for e in full.misplaced_events] == [(S, moved)]
    before = build_demand(raw[raw["ts_utc"] <= T], FIRST, T)
    after = build_demand(raw[raw["ts_utc"] <= T + H], FIRST, T + H)
    assert not before.misplaced_events and len(after.misplaced_events) == 1


def replay_day(raw, obs, day, cols):
    """Day D's rows on the replay path: issued at its origin, then scored at its score clock."""
    clock = Clock(day_origin(day))
    state = rp._State(RawFeed(raw, clock, "demand"), RawFeed(obs, clock, "temperature"), clock,
                      scale_last=day_origin(day))
    model = FrozenModel(columns=cols, beta=np.zeros(len(cols)), q=0.1, replay_year=day.year,
                        train_last_day=str(day.date()), n_train=0)
    issued = rp.issue(state, model, day)
    at = score_clock(day, LAST)
    clock.advance(at)
    scored, _ = rp.score_day(state, day, issued, final=at == LAST)
    return issued, scored


def same(a: pd.Series, b: pd.Series) -> bool:
    return bool(np.allclose(a.to_numpy(float), b.to_numpy(float), equal_nan=True, rtol=0, atol=1e-9))


def test_design_rows_equal_the_replay_path_at_each_origin(world, asof):
    raw, obs, temp, _ = world
    around = {lst_day(t) for t in (S, S + 24 * H, S + 168 * H, T, T + H, T + 24 * H, T + 168 * H)}
    around |= {d + pd.Timedelta(days=k) for d in list(around) for k in (-1, 1)}
    rng = np.random.default_rng(7)
    days = pd.date_range("2019-01-09", "2020-02-27", freq="D")
    sample = sorted(around | set(days[rng.choice(len(days), 20, replace=False)]))
    f = asof.issued(temp)
    cols = mdl.design(f.iloc[:24], "persistence").columns
    for day in sample:
        assert day_origin(day) == rp.origin(day)
        issued, scored = replay_day(raw, obs, day, cols)
        rows = f[f["day"] == day]
        assert list(rows.index) == list(issued.index)
        for c in ("lag24", "lag168", "temp_persistence"):
            assert same(rows[c], issued[c]), (day, c)
        assert same(rows["target"], scored["target"]), day
    # The comparison can fail: the full-window series differs from the replay at the event.
    full = build(build_demand(raw, FIRST, LAST).frame, temp)
    assert np.isfinite(full.loc[S, "target"]) and np.isnan(f.loc[S, "target"])
    assert np.isfinite(full.loc[S + 24 * H, "lag24"]) and np.isnan(f.loc[S + 24 * H, "lag24"])


def test_a_fit_sees_the_same_table_as_the_replay_at_its_clock(world, asof):
    raw, obs, temp, _ = world
    for clock in (wf.folds("2019-12", "2019-12")[0].fit_clock, fz.fit_clock(2020),
                  wf.folds("2020-02", "2020-02")[0].fit_clock):
        c = Clock(clock)
        frame, t, _, _ = rp._State(RawFeed(raw, c, "demand"), RawFeed(obs, c, "temperature"), c, clock).canonical()
        replayed = build(frame, t)
        mine = asof.table(clock, temp)
        assert list(mine.index) == list(replayed.index)
        for col in ("target", "lag24", "lag168"):
            assert same(mine[col], replayed[col]), (clock, col)


def test_a_clock_after_the_last_record_loaded_is_refused(asof):
    with pytest.raises(LookaheadError):
        asof.frame(LAST + H)


def test_a_repaired_value_does_not_reach_lags_or_training_before_its_record_arrives(world, asof, monkeypatch):
    raw, _, temp, moved = world
    # The fault: in the full-window series, the value stamped T already sits at S.
    full = build(build_demand(raw, FIRST, LAST).frame, temp)
    assert full.loc[S, "target"] == moved and full.loc[S + 168 * H, "lag168"] == moved

    f = asof.issued(temp)
    assert np.isnan(f.loc[S, "target"])
    assert np.isnan(f.loc[S + 24 * H, "lag24"]) and np.isnan(f.loc[S + 168 * H, "lag168"])

    seen = []
    real_fit = mdl.OLSForecaster.fit

    def spy(self, train):
        seen.append(train)
        return real_fit(self, train)

    monkeypatch.setattr(wf.OLSForecaster, "fit", spy)
    fl = wf.folds("2019-12", "2020-02")
    wf.run(f, fl, scenarios=("persistence",), train_at=lambda c: asof.table(c, temp))
    trained = dict(zip((str(fold.test_month) for fold in fl), seen, strict=True))
    for month in ("2019-12", "2020-01"):  # fitted before T arrives
        tr = trained[month]
        assert np.isnan(tr.loc[S, "target"]) and np.isnan(tr.loc[S + 24 * H, "lag24"])
        assert np.isnan(tr.loc[S + 168 * H, "lag168"])
    tr = trained["2020-02"]  # fitted after: the repair is in view, as in the replay
    assert tr.loc[S, "target"] == moved and tr.loc[S + 168 * H, "lag168"] == moved

    frozen = fz.training_table(asof, temp, 2020)
    assert frozen.index.max() == fz.fit_clock(2020) < T
    assert np.isnan(frozen.loc[S, "target"]) and np.isnan(frozen.loc[S + 24 * H, "lag24"])
