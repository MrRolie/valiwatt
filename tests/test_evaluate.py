"""Conformal quantile, calibration on earlier folds only, and bootstrap intervals that widen with fewer weeks."""

import numpy as np
import pandas as pd
import pytest

from valiwatt.forecast.evaluate import (
    calibration_rows,
    conformal_quantile,
    errors,
    mean_with_ci,
    paired_difference,
    relative_conformal,
    weekly_stats,
)
from valiwatt.config import DESIGN_LAST, LST_OFFSET
from valiwatt.forecast import walkforward as wf
from valiwatt.forecast.features import day_origin, scored_at
from valiwatt.forecast.guards import LookaheadError

H = pd.Timedelta(hours=1)


def oos(months=24, seed=0, scale=0.03):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-01T06:00Z", periods=months * 720, freq="h")
    day = (idx - pd.Timedelta(hours=6)).tz_convert(None).normalize()
    pred = 20_000 + 3_000 * np.sin(np.arange(len(idx)) * 2 * np.pi / 24)
    target = pred * (1 + rng.normal(0, scale, len(idx)))
    fold = pd.Series(day).dt.to_period("M").astype(str).to_numpy()
    out = pd.DataFrame({"target": target, "pred": pred, "day": day, "hour": idx.hour, "fold": fold}, index=idx)
    out["scored_at"] = scored_at(out["day"], idx.max())
    return out


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


def test_rows_without_the_time_they_were_scored_calibrate_no_half_width():
    """A half-width may calibrate only on rows scored by its fit clock; rows that do not say when they were scored
    cannot be checked, so they are refused."""
    out = oos()
    clock = out["scored_at"].iloc[len(out) // 2]
    assert len(calibration_rows(out, clock)) < len(out)  # the control: rows that say it are kept or left out
    assert len(relative_conformal(out, "pred")) > 0
    blind = out.drop(columns="scored_at")
    with pytest.raises(LookaheadError):
        calibration_rows(blind, clock)
    with pytest.raises(LookaheadError):
        relative_conformal(blind, "pred")


def test_a_row_scored_at_the_fit_clock_calibrates_and_one_scored_an_hour_later_does_not():
    """The cap on when a row was scored is `scored_at <= fit_clock`: of the rows of a day the replay scored well before
    the clock, one scored at the fit clock itself is in view when the half-width is fixed and calibrates it; one scored
    an hour later is not. The last day of the data is scored at its end, but the replay scores a day an hour after its
    last hour: the end of the data is no scoring, so that day calibrates no half-width fixed at the end of the data,
    and does once the clock reaches the hour the replay scores it."""
    clock = pd.Timestamp("2023-12-31T05:00Z")
    day = pd.Timestamp("2023-12-20")  # the replay scores it at 2023-12-21 06:00 UTC, well before the clock
    rows = pd.DataFrame({"scored_at": [clock - H, clock, clock + H], "day": day}, index=["before", "at", "after"])
    assert list(calibration_rows(rows, clock).index) == ["before", "at"]
    out = oos(months=2)
    end = out.index.max()
    last = out["day"] == out["day"].max()
    assert (out.loc[last, "scored_at"] == end).all() and end == day_origin(out["day"].max() + pd.Timedelta(days=1))
    assert len(calibration_rows(out, end)) == int((~last).sum())
    assert len(calibration_rows(out, end - H)) == int((~last).sum())
    assert len(calibration_rows(out, end + H)) == len(out)


def design_rows(first_day: str, last: pd.Timestamp = DESIGN_LAST) -> pd.DataFrame:
    """Hourly design rows from `first_day` to the end of the data, each day scored as design code scores it."""
    idx = pd.date_range(day_origin(pd.Timestamp(first_day)) + H, last, freq="h")
    rows = pd.DataFrame({"day": (idx - H + LST_OFFSET).tz_convert(None).normalize()}, index=idx)
    rows["scored_at"] = scored_at(rows["day"], idx.max())
    return rows


def test_the_eve_of_the_replayed_year_never_calibrates_a_half_width():
    """The 2024-shaped freeze: the design data end at its fit clock, so design code scores 2023-12-31 at that clock and
    v1's rule kept the eve. The 2023-shaped freeze: the data run past its fit clock, 2022-12-31 is scored an hour after
    it, and v1's rule left it out. With v2's rule neither freeze calibrates on its eve; the day before it calibrates in
    both."""
    for year in (2024, 2023):
        clock = wf.folds(f"{year}-01", f"{year}-01")[0].fit_clock
        eve = pd.Timestamp(f"{year - 1}-12-31")
        rows = design_rows(f"{year - 1}-12-01")
        window = rows[rows["day"] <= eve]  # the calibration folds end with the eve's month
        v1_kept = window[window["scored_at"] <= clock]
        assert (v1_kept["day"] == eve).any() == (year == 2024), year
        kept = calibration_rows(window, clock)
        assert not (kept["day"] == eve).any(), year
        assert int((kept["day"] == eve - pd.Timedelta(days=1)).sum()) == 24, year
        assert len(kept) == int((window["day"] < eve).sum()), year


def test_rows_without_a_day_calibrate_no_half_width():
    """The replay's scoring time of a row is read from its day; a row that does not say its day cannot be shown to
    have been scored by the fit clock, so it is refused."""
    out = oos()
    clock = out["scored_at"].iloc[len(out) // 2]
    assert 0 < len(calibration_rows(out, clock)) < len(out)  # the control: rows that say it are kept or left out
    assert len(relative_conformal(out, "pred")) > 0
    no_day = out.drop(columns="day")
    one_blank = out.assign(day=out["day"].where(out.index != out.index[10]))
    for blind in (no_day, one_blank):
        with pytest.raises(LookaheadError):
            calibration_rows(blind, clock)
        with pytest.raises(LookaheadError):
            relative_conformal(blind, "pred")


def test_an_undefined_bound_counts_as_a_miss_on_v2s_path():
    """v2's coverage (no bins) is v2's to the bit (docs/plan-v3.md, section 15): one finite calibration score makes the
    90 % quantile infinite, and a zero forecast then has bounds inf * 0 = NaN. With a finite target and forecast that
    hour is a miss, coverage 0, as v2 computed it; never a missing coverage, which would drop the miss from the weekly
    coverage. The control: every other hour of the fold, its bounds infinite, is covered."""
    out = oos(months=13)
    last = out["fold"] == out["fold"].max()
    out.loc[out.index[~last][1:], "target"] = np.nan  # one finite calibration score left
    zero = out.index[last][30]
    out.loc[zero, "pred"] = 0.0
    iv = relative_conformal(out, "pred")
    assert (iv["calibration_n"] == 1).all() and np.isinf(iv["q"]).all()
    assert np.isnan(iv.loc[zero, "lower"]) and iv.loc[zero, "covered"] == 0.0
    assert (iv.drop(index=zero)["covered"] == 1.0).all()
    d = errors(out.loc[iv.index], "pred")
    weeks = weekly_stats(d, iv["covered"])
    week = d.loc[zero, "week"]
    assert weeks.loc[week, "covered"] == pytest.approx(1 - 1 / weeks.loc[week, "hours"])


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
