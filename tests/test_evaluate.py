"""Conformal quantile, calibration on earlier folds only, and bootstrap intervals that widen with fewer weeks."""

import numpy as np
import pandas as pd
import pytest

from valiwatt.forecast.evaluate import (
    conformal_quantile,
    errors,
    mean_with_ci,
    paired_difference,
    relative_conformal,
    weekly_stats,
)


def oos(months=24, seed=0, scale=0.03):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-01T06:00Z", periods=months * 720, freq="h")
    day = (idx - pd.Timedelta(hours=6)).tz_convert(None).normalize()
    pred = 20_000 + 3_000 * np.sin(np.arange(len(idx)) * 2 * np.pi / 24)
    target = pred * (1 + rng.normal(0, scale, len(idx)))
    fold = pd.Series(day).dt.to_period("M").astype(str).to_numpy()
    return pd.DataFrame({"target": target, "pred": pred, "day": day, "hour": idx.hour, "fold": fold}, index=idx)


def test_conformal_quantile_is_the_finite_sample_order_statistic():
    s = np.arange(1, 101, dtype=float)  # n = 100, alpha 0.1 -> ceil(101 * 0.9) = 91st smallest
    assert conformal_quantile(s, 0.1) == 91.0
    assert conformal_quantile(np.array([1.0, 2.0]), 0.1) == float("inf")


def test_intervals_reach_nominal_coverage_on_exchangeable_errors():
    out = oos()
    iv = relative_conformal(out, "pred")
    assert iv["fold"].min() > out["fold"].min()  # the first 12 folds only calibrate
    assert 0.88 < iv["covered"].mean() < 0.92


def test_calibration_uses_only_earlier_folds():
    out = oos()
    base = relative_conformal(out, "pred")
    future = out.copy()
    last = future["fold"] == future["fold"].max()
    future.loc[last, "target"] = future.loc[last, "pred"] * 3  # wreck the last fold's outcomes
    after = relative_conformal(future, "pred")
    first_q = lambda iv: iv.groupby("fold")["q"].first()
    pd.testing.assert_series_equal(first_q(base), first_q(after))


def test_inflated_errors_drop_coverage():
    out = oos()
    late = out["fold"] >= "2022-07"
    out.loc[late, "target"] = out.loc[late, "pred"] * (1 + (out.loc[late, "target"] / out.loc[late, "pred"] - 1) * 3)
    iv = relative_conformal(out, "pred")
    assert iv.loc[iv["fold"] >= "2022-07", "covered"].mean() < 0.7


def test_bootstrap_interval_contains_the_mean_and_shrinks_with_more_weeks():
    d = errors(oos(), "pred")
    small = mean_with_ci(d[d["fold"] <= "2021-02"], "ape")
    large = mean_with_ci(d, "ape")
    assert small["ci95_low"] <= small["mean"] <= small["ci95_high"]
    assert (large["ci95_high"] - large["ci95_low"]) < (small["ci95_high"] - small["ci95_low"])


def test_paired_difference_separates_a_worse_forecast_only():
    out = oos()
    out["worse"] = out["pred"] * 1.02
    out["same"] = out["pred"]
    a, b, c = errors(out, "pred"), errors(out, "worse"), errors(out, "same")
    assert paired_difference(b, a)["separated"] and paired_difference(b, a)["difference"] > 0
    assert not paired_difference(c, a)["separated"]


def test_weekly_stats_assign_seasons():
    d = errors(oos(), "pred")
    w = weekly_stats(d)
    assert set(w["season"]) == {"winter", "spring", "summer", "fall"}
    assert (w["hours"] <= 168).all()
