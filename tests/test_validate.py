"""Validation end to end on synthetic design years: it refuses 2024 rows and logs per-season thresholds."""

import numpy as np
import pandas as pd
import pytest

from conftest import synth_demand, synth_obs
from valiwatt.forecast import validate as vd
from valiwatt.forecast.features import build
from valiwatt.forecast.guards import LookaheadError
from valiwatt.quality.demand import build_demand

FIRST, LAST = pd.Timestamp("2021-01-01T06:00Z"), pd.Timestamp("2024-01-01T05:00Z")


@pytest.fixture(scope="module")
def tables():
    raw = synth_demand(FIRST, LAST)
    obs = synth_obs(FIRST, LAST).set_index("ts_utc")["temp_c"]
    # Demand responds to cold, so the model has something to learn.
    raw["mw"] = raw["mw"] + 300 * np.maximum(18 - obs.reindex(raw["ts_utc"]).to_numpy(), 0)
    demand = build_demand(raw, FIRST, LAST).frame
    return vd.design_features(demand, obs), vd.design_features(demand, obs + 0.5)


def test_validation_runs_and_logs_thresholds_for_every_season(tables):
    fp, fpanel = tables
    v = vd.run_validation(fp, fpanel, n_boot=50, first_fold="2022-01", last_fold="2023-12")
    r = v["results"]
    assert r["folds"]["count"] == 24 and r["conformal"]["first_fold"] == "2023-01"
    assert set(r["thresholds"]) == {"2024"}  # intervals start in 2023 here, so only the 2024 replay has thresholds
    assert set(r["thresholds"]["2024"]["performance"]) == {"winter", "spring", "summer", "fall"}
    assert r["compare"]["persistence_vs_baseline"]["difference"] < 0
    assert 0.8 < r["coverage"]["relative"]["overall"]["mean"] < 0.97
    assert r["cases"]["shift_2020"]["evaluable"] is False  # no 2020 in these folds: recorded, never a silent blank


def test_validation_refuses_a_2024_row(tables):
    fp, fpanel = tables
    leaky = pd.concat([fp, fp.iloc[[-1]].assign(day=pd.Timestamp("2024-01-05"))])
    with pytest.raises(LookaheadError):
        vd.run_validation(leaky, fpanel, n_boot=10, first_fold="2022-01", last_fold="2023-12")


def test_mondrian_intervals_calibrate_within_each_group():
    from valiwatt.forecast.evaluate import relative_conformal, season_of

    idx = pd.date_range("2021-01-01T06:00Z", periods=24 * 720, freq="h")
    day = (idx - pd.Timedelta(hours=6)).tz_convert(None).normalize()
    rng = np.random.default_rng(0)
    winter = pd.Series(day).dt.month.isin([12, 1, 2]).to_numpy()
    pred = np.full(len(idx), 20_000.0)
    target = pred * (1 + rng.normal(0, np.where(winter, 0.08, 0.02)))
    out = pd.DataFrame({"target": target, "pred": pred, "day": day,
                        "fold": pd.Series(day).dt.to_period("M").astype(str).to_numpy()}, index=idx)
    pooled = relative_conformal(out, "pred")
    by_season = relative_conformal(out, "pred", groups=season_of(out["day"]))
    w = out.loc[pooled.index, "day"].dt.month.isin([12, 1, 2])
    assert pooled.loc[w.to_numpy(), "covered"].mean() < 0.8  # one pooled quantile under-covers the noisy season
    assert 0.85 < by_season.loc[w.to_numpy(), "covered"].mean() < 0.95


def test_pseudo_replay_thresholds_read_no_week_of_the_year_they_judge(tables):
    """Thresholds for replaying year Y come from validation weeks before Y only."""
    fp, fpanel = tables
    v = vd.run_validation(fp, fpanel, n_boot=10, first_fold="2021-06", last_fold="2023-12")
    th = v["results"]["thresholds"]
    assert set(th) == {"2023", "2024"}
    weeks = pd.DataFrame(th["2023"]["weekly_validation"])
    assert pd.to_datetime(weeks["first_day"]).max() < pd.Timestamp("2023-01-01")
    assert th["2023"]["last_year"] == 2022 and th["2024"]["last_year"] == 2023
