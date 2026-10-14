"""The replay's breaks: each monitor fires on its injected fault, stays quiet on clean data, and never acts."""

import numpy as np
import pandas as pd
import pytest

from replay_world import FIRST, LAST, frozen_and_reference, respond, thresholds, world
from valiwatt.forecast.features import build
from valiwatt.forecast.guards import LookaheadError
from valiwatt.monitor import replay as rp
from valiwatt.monitor.clock import Clock, RawFeed
from valiwatt.monitor.rules import FAILURES, INVESTIGATE, RECALIBRATE, RETRAIN, TRIM_DAYS, trimmed, two_week_rule_alone
from valiwatt.quality.demand import build_demand

START, END = "2022-01-03", "2022-02-06"  # five Monday-to-Sunday weeks


def run(raw=None, obs=None, model=None, start=START, end=END, th=None, data_end=LAST):
    raw0, obs0 = world()
    frozen, ref = frozen_and_reference()
    return rp.replay(raw0 if raw is None else raw, obs0 if obs is None else obs, model or frozen, th or thresholds(),
                     ref, start, end, data_end)


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
    state = rp._State(RawFeed(raw, clock, "demand"), RawFeed(obs, clock, "temperature"), clock,
                      scale_last=pd.Timestamp("2022-01-01T05:00Z"))
    with pytest.raises(LookaheadError):
        rp.issue(state, frozen, pd.Timestamp("2022-01-05"))


def test_a_drift_reference_holding_replayed_days_is_refused():
    raw, obs = world()
    frozen, ref = frozen_and_reference()
    leaky = pd.concat([ref, pd.DataFrame({"day": [pd.Timestamp("2022-01-04")], "forecast": [20_000.0]})])
    with pytest.raises(LookaheadError):
        rp.replay(raw, obs, frozen, thresholds(), leaky, START, END, LAST)


def test_thresholds_built_on_the_replayed_year_are_refused():
    th = dict(thresholds(), last_year=2022)
    with pytest.raises(LookaheadError):
        run(th=th, end="2022-01-09")
    with pytest.raises(LookaheadError):
        run(th={k: v for k, v in thresholds().items() if k != "last_year"}, end="2022-01-09")


@pytest.mark.parametrize("day", ["2022-01-15", "2022-01-16"])
def test_a_one_day_shock_never_yields_retrain(day):
    """A shock at the end of a week is the worst case: through the 24 h and 168 h lags it also spoils the next day and
    the same day a week later. On the Sunday, MAPE is out of band in both weeks, so the two-week rule alone would
    retrain; with the two most extreme days of each week set aside, it does not."""
    raw, _ = world()
    shocked = rp.lst_day(pd.DatetimeIndex(raw["ts_utc"])) == pd.Timestamp(day)
    r = run(raw=raw.assign(mw=raw["mw"] * np.where(shocked, 1.35, 1.0)))
    wk = {w["week_start"].date().isoformat(): w for w in r.weeks}
    assert wk["2022-01-10"]["flags"]["mape_high"]  # the break bites
    if day == "2022-01-16":
        assert two_week_rule_alone(wk["2022-01-17"], wk["2022-01-10"])  # the two-week rule alone would retrain
    assert RETRAIN not in [rec for _, rec in recommendations(r)]
    assert RECALIBRATE not in [rec for _, rec in recommendations(r)]


def test_a_depressed_week_and_its_lag_echo_do_not_yield_retrain():
    """One low week (bias out low) is echoed the next week through the 168 h lag (bias out high): opposite failures,
    not the same failure twice."""
    raw, _ = world()
    week = (raw["ts_utc"] > pd.Timestamp("2022-01-10T05:00Z")) & (raw["ts_utc"] <= pd.Timestamp("2022-01-17T05:00Z"))
    r = run(raw=raw.assign(mw=raw["mw"] * np.where(week, 0.95, 1.0)))
    wk = {w["week_start"].date().isoformat(): w for w in r.weeks}
    assert wk["2022-01-10"]["flags"]["bias_low"]  # the break bites
    assert RETRAIN not in [rec for _, rec in recommendations(r)]


def test_two_contaminated_days_never_lift_the_trimmed_statistic_past_the_untouched_days():
    """The guarantee behind the rules: with at most TRIM_DAYS days changed (in any direction, by any amount), the
    trimmed statistic is no further out than the mean of the untouched days."""
    rng = np.random.default_rng(0)
    for _ in range(2000):
        n = int(rng.integers(5, 8))
        clean = pd.DataFrame({"mape": rng.gamma(2, 0.01, n), "rel_bias": rng.normal(0, 0.02, n),
                              "covered": rng.uniform(0.5, 1, n)})
        hit = rng.choice(n, size=int(rng.integers(0, TRIM_DAYS + 1)), replace=False)
        dirty = clean.copy()
        for col in dirty:
            dirty.loc[hit, col] = rng.normal(0, 5, len(hit))
        untouched = clean.drop(index=hit)
        for failure, (stat, _, side) in FAILURES.items():
            assert side * trimmed(dirty, failure) <= side * untouched[stat].mean() + 1e-12


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

    def acting(state, day, issued, final=False):
        object.__setattr__(model, "q", model.q * 1.5)  # a monitor that widens the intervals itself
        return real(state, day, issued, final=final)

    monkeypatch.setattr(rp, "score_day", acting)
    with pytest.raises(rp.MonitorActedError):
        run(model=model, end="2022-01-09")
    with pytest.raises(ValueError):
        model.beta[0] = 0.0  # the coefficients are read-only


def test_partial_weeks_have_no_drift_statistic(clean):
    r = run(start="2022-01-02", end="2022-01-11")
    first, last = r.weeks[0], r.weeks[-1]
    assert first["hours"] < 120 and last["hours"] < 120
    assert all(np.isnan(v) for w in (first, last) for v in w["drift"].values())
    assert not any(any(w["drift_flags"].values()) for w in (first, last))


def test_the_last_day_is_judged_at_the_end_of_the_data_and_says_so():
    end = rp.origin(pd.Timestamp("2022-01-10"))  # the data end with 2022-01-09
    r = run(end="2022-01-09", data_end=end)
    assert r.days[-1]["judged_at_data_end"] and not any(d["judged_at_data_end"] for d in r.days[:-1])
    assert r.days[-1]["spike_unscored"] == [end]


def test_duplicated_temperatures_and_an_off_grid_demand_record_fail_their_day():
    raw, obs = world()
    t = pd.Timestamp("2022-01-25T15:00Z")
    obs2 = pd.concat([obs, obs[obs["ts_utc"] == t].assign(temp_c=25.0)])
    raw2 = raw.copy()
    raw2.loc[raw2["ts_utc"] == pd.Timestamp("2022-01-27T15:00Z"), "ts_utc"] = pd.Timestamp("2022-01-27T15:30Z")
    r = run(raw=raw2, obs=obs2)
    kinds = {d["day"].date().isoformat(): sorted(p["kind"] for p in d["problems"]) for d in r.days if d["failed"]}
    assert kinds == {"2022-01-25": ["temperature_duplicate"], "2022-01-27": ["missing_hour", "off_grid_record"]}


def test_records_after_a_cut_change_nothing_judged_before_it(clean):
    """The clock break at replay level: corrupt every record after a cut (a huge spike, a duplicate, a drifted
    sensor); every day scored before the cut, and every forecast issued before it, stays identical."""
    raw, obs = world()
    cut = pd.Timestamp("2022-01-24T05:00Z")  # the origin of 2022-01-24
    after = raw["ts_utc"] > cut
    dirty = pd.concat([raw.assign(mw=np.where(after, raw["mw"] * 3, raw["mw"])),
                       raw[raw["ts_utc"] == cut + pd.Timedelta(hours=2)]])
    warm = obs.assign(temp_c=np.where(obs["ts_utc"] > cut, obs["temp_c"] + 30, obs["temp_c"]))
    r = run(raw=dirty, obs=warm)
    # Days up to 2022-01-22 are scored by 2022-01-23T06:00Z, before the cut; day 2022-01-24 is issued at the cut.
    scored_before = (clean.hourly["day"] < pd.Timestamp("2022-01-23")).to_numpy()
    pd.testing.assert_frame_equal(r.hourly[scored_before], clean.hourly[scored_before])
    issued_by_cut = (clean.hourly["day"] <= pd.Timestamp("2022-01-24")).to_numpy()
    cols = ["forecast", "lower", "upper", "lag24", "lag168", "temp_persistence"]
    pd.testing.assert_frame_equal(r.hourly.loc[issued_by_cut, cols], clean.hourly.loc[issued_by_cut, cols])
    assert not r.hourly.loc[~issued_by_cut, "forecast"].equals(clean.hourly.loc[~issued_by_cut, "forecast"])
    judged = [d for d in clean.days if d["day"] < pd.Timestamp("2022-01-23")]
    assert [d for d in r.days if d["day"] < pd.Timestamp("2022-01-23")] == judged
    assert r.weeks[:2] == clean.weeks[:2] and r.decisions[:2] == clean.decisions[:2]
