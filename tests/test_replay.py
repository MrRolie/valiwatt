"""The replay's breaks: each monitor fires on its injected fault, stays quiet on clean data, and never acts."""

import numpy as np
import pandas as pd
import pytest

from replay_world import FIRST, LAST, THRESHOLDS, frozen_and_reference, respond, world
from valiwatt.forecast.features import build
from valiwatt.forecast.guards import LookaheadError
from valiwatt.monitor import replay as rp
from valiwatt.monitor.clock import Clock, RawFeed
from valiwatt.monitor.rules import INVESTIGATE, RECALIBRATE, RETRAIN, decide
from valiwatt.quality.demand import build_demand

START, END = "2022-01-03", "2022-02-06"  # five Monday-to-Sunday weeks


def run(raw=None, obs=None, model=None, start=START, end=END):
    raw0, obs0 = world()
    frozen, ref = frozen_and_reference()
    return rp.replay(raw0 if raw is None else raw, obs0 if obs is None else obs, model or frozen, THRESHOLDS, ref,
                     start, end, LAST)


def recommendations(r):
    return [(d["week_start"].date().isoformat(), d["recommendation"]) for d in r.decisions if d["recommendation"]]


@pytest.fixture(scope="module")
def clean():
    return run()


def test_clean_world_yields_no_recommendation(clean):
    assert recommendations(clean) == []
    assert not any(d["failed"] for d in clean.days)
    assert all(w["evaluated"] for w in clean.weeks)


def test_replay_forecasts_match_the_batch_features(clean):
    """The replay rebuilds features from the raw cut at each origin; on clean data they equal the batch build."""
    raw, obs = world()
    f = build(build_demand(raw, FIRST, LAST).frame, obs.set_index("ts_utc")["temp_c"])
    frozen, _ = frozen_and_reference()
    batch = frozen.predict(f.loc[clean.hourly.index])
    assert np.allclose(clean.hourly["forecast"], batch["forecast"], rtol=0, atol=1e-6)
    assert np.allclose(clean.hourly["lag24"], f.loc[clean.hourly.index, "lag24"])


def test_reading_past_the_clock_is_refused():
    raw, _ = world()
    clock = Clock(pd.Timestamp("2022-01-10T05:00Z"))
    feed = RawFeed(raw, clock, "demand")
    assert feed.visible()["ts_utc"].max() == clock.now
    feed.window(clock.now - pd.Timedelta(hours=5), clock.now)
    with pytest.raises(LookaheadError):
        feed.window(clock.now, clock.now + pd.Timedelta(hours=1))
    with pytest.raises(LookaheadError):
        clock.advance(clock.now - pd.Timedelta(hours=1))


def test_a_forecast_issued_after_its_origin_is_refused():
    raw, obs = world()
    frozen, _ = frozen_and_reference()
    clock = Clock(rp.origin(pd.Timestamp("2022-01-05")) + pd.Timedelta(hours=1))
    state = rp._State(RawFeed(raw, clock, "demand"), RawFeed(obs, clock, "temperature"), clock)
    with pytest.raises(LookaheadError):
        rp.issue(state, frozen, pd.Timestamp("2022-01-05"))


def test_a_drift_reference_holding_replayed_days_is_refused():
    raw, obs = world()
    frozen, ref = frozen_and_reference()
    leaky = pd.concat([ref, pd.DataFrame({"day": [pd.Timestamp("2022-01-04")], "forecast": [20_000.0]})])
    with pytest.raises(LookaheadError):
        rp.replay(raw, obs, frozen, THRESHOLDS, leaky, START, END, LAST)


@pytest.mark.parametrize("day", ["2022-01-15", "2022-01-16"])
def test_a_one_day_shock_never_yields_retrain(day):
    """A shock at the end of a week is the worst case: through the 24 h and 168 h lags it also spoils the next day and
    the same day a week later, so both weeks are out of band. Most days of each week stay in band, so retrain stays
    quiet."""
    raw, _ = world()
    shocked = rp.lst_day(pd.DatetimeIndex(raw["ts_utc"])) == pd.Timestamp(day)
    r = run(raw=raw.assign(mw=raw["mw"] * np.where(shocked, 1.35, 1.0)))
    wk = {w["week_start"].date().isoformat(): w for w in r.weeks}
    assert wk["2022-01-10"]["flags"]["mape_high"] and wk["2022-01-17"]["flags"]["mape_high"]  # the break bites
    assert RETRAIN not in [rec for _, rec in recommendations(r)]
    alone = decide(wk["2022-01-17"], wk["2022-01-10"], min_breach_days=0)
    assert alone["recommendation"] == RETRAIN  # the two-week rule alone would have fired


def test_a_level_shift_yields_retrain():
    raw, _ = world()
    shifted = raw.assign(mw=raw["mw"] * np.where(raw["ts_utc"] >= pd.Timestamp("2022-01-10T05:00Z"), 1.12, 1.0))
    recs = recommendations(run(raw=shifted))
    assert (("2022-01-17", RETRAIN) in recs) or (("2022-01-24", RETRAIN) in recs)


def test_deflated_intervals_yield_recalibrate():
    frozen, _ = frozen_and_reference()
    narrow = rp.FrozenModel(columns=frozen.columns, beta=frozen.beta, q=frozen.q / 5, replay_year=frozen.replay_year,
                            train_last_day=frozen.train_last_day, n_train=frozen.n_train)
    r = run(model=narrow)
    assert [rec for _, rec in recommendations(r)][:1] == [RECALIBRATE]
    assert all(d["fired"][0]["rule"] == 3 for d in r.decisions if d["recommendation"])


def test_temperature_drift_yields_investigate_with_performance_inside():
    raw, obs = world()
    warm = obs.assign(temp_c=obs["temp_c"] + np.where(obs["ts_utc"] >= pd.Timestamp("2022-01-10T05:00Z"), 10.0, 0.0))
    r = run(raw=respond(raw, obs, warm), obs=warm)
    fired = [f for d in r.decisions for f in d["fired"]]
    assert any(f["rule"] == 4 and f["recommendation"] == INVESTIGATE for f in fired)
    assert RETRAIN not in [rec for _, rec in recommendations(r)]


def test_a_sensor_gap_fails_data_quality_and_suppresses_performance():
    raw, obs = world()
    day = rp.lst_day(pd.DatetimeIndex(obs["ts_utc"])) == pd.Timestamp("2022-01-19")
    gap = obs.assign(temp_c=obs["temp_c"].where(~(day & (pd.DatetimeIndex(obs["ts_utc"]).hour < 10))))
    r = run(obs=gap)
    failed = [d for d in r.days if d["failed"]]
    assert [d["day"].date().isoformat() for d in failed] == ["2022-01-19"]
    assert failed[0]["problems"][0]["kind"] == "temperature_silent"
    week = next(d for d in r.decisions if d["week_start"] == pd.Timestamp("2022-01-17"))
    assert week["recommendation"] == INVESTIGATE and week["rule"] == 1
    assert next(w for w in r.weeks if w["week_start"] == pd.Timestamp("2022-01-17"))["stats"] is None


def test_missing_and_duplicated_demand_records_fail_their_day():
    raw, _ = world()
    t_missing, t_dup = pd.Timestamp("2022-01-25T15:00Z"), pd.Timestamp("2022-01-27T15:00Z")
    broken = pd.concat([raw[raw["ts_utc"] != t_missing], raw[raw["ts_utc"] == t_dup].assign(mw=lambda d: d["mw"] + 50)])
    r = run(raw=broken)
    kinds = {d["day"].date().isoformat(): [p["kind"] for p in d["problems"]] for d in r.days if d["failed"]}
    assert kinds == {"2022-01-25": ["missing_hour"], "2022-01-27": ["duplicate"]}


def test_two_rules_at_once_yield_one_recommendation_with_both_pieces_of_evidence():
    """Noise grows without bias: MAPE breaks (retrain) and coverage breaks with bias inside (recalibrate)."""
    raw, _ = world()
    rng = np.random.default_rng(7)
    late = raw["ts_utc"] >= pd.Timestamp("2022-01-10T05:00Z")
    noisy = raw.assign(mw=raw["mw"] * (1 + np.where(late, rng.normal(0, 0.03, len(raw)), 0.0)))
    r = run(raw=noisy)
    both = [d for d in r.decisions if {f["rule"] for f in d["fired"]} >= {2, 3}]
    assert both, "expected a week where retrain and recalibrate both fire"
    assert both[0]["recommendation"] == RETRAIN and len(both[0]["fired"]) >= 2


def test_a_replay_that_changes_the_model_fails(monkeypatch):
    frozen, _ = frozen_and_reference()
    model = rp.FrozenModel(columns=frozen.columns, beta=frozen.beta, q=frozen.q, replay_year=frozen.replay_year,
                           train_last_day=frozen.train_last_day, n_train=frozen.n_train)
    real = rp.score_day

    def acting(state, day, issued):
        object.__setattr__(model, "q", model.q * 1.5)  # a monitor that widens the intervals itself
        return real(state, day, issued)

    monkeypatch.setattr(rp, "score_day", acting)
    with pytest.raises(rp.MonitorActedError):
        run(model=model, end="2022-01-09")
    with pytest.raises(ValueError):
        model.beta[0] = 0.0  # the coefficients are read-only
