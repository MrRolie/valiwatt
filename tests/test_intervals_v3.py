"""v3's intervals by temperature (docs/plan-v3.md, section 7): two bins against the edge of the fit being calibrated,
the cold bin's fallback to the pooled half-width, the noon fit clock, and a scoring time that never moves with it."""

import numpy as np
import pandas as pd
import pytest

from valiwatt.forecast.evaluate import (
    MIN_COLD_HOURS,
    MIN_COLD_WEEKS,
    bin_of,
    binned_half_widths,
    calibration_rows,
    check_interval_layer,
    conformal_quantile,
    fit_clock_of,
    relative_conformal,
)
from valiwatt.forecast.features import scored_at
from valiwatt.forecast.issue import MIDNIGHT, NOON, day_edge, lst_day

H = pd.Timedelta(hours=1)
MONDAY = pd.Timestamp("2024-01-01")  # a Monday


def scores_in(cold: int, weeks: int, rest: int = 600) -> tuple[np.ndarray, np.ndarray, pd.Series]:
    """`cold` scores at -20 C spread over `weeks` Monday-to-Sunday weeks, and `rest` at 0 C over 10 weeks; the cold
    ones far larger, so that their own half-width differs from the pooled one."""
    days = [MONDAY + pd.Timedelta(weeks=i % weeks, days=i % 7) for i in range(cold)]
    days += [MONDAY + pd.Timedelta(weeks=i % 10, days=i % 7) for i in range(rest)]
    s = np.r_[np.linspace(0.05, 0.15, cold), np.linspace(0.001, 0.03, rest)]
    t = np.r_[np.full(cold, -20.0), np.full(rest, 0.0)]
    return s, t, pd.Series(days)


@pytest.mark.parametrize(("cold", "weeks", "fallback"), [(119, 6, True), (120, 3, True), (120, 4, False),
                                                         (500, 4, False), (0, 4, True)])
def test_a_cold_bin_of_119_scores_or_of_120_in_3_weeks_takes_the_pooled_half_width(cold, weeks, fallback):
    """Section 12: fewer than 120 cold scores, or 120 from 3 weeks, fall back to the pooled half-width and record it;
    120 from 4 weeks keep their own. The rest keep theirs either way."""
    s, t, d = scores_in(cold, weeks)
    layer = binned_half_widths(s, t, d, edge=-5.0)
    assert (layer["cold_n"], layer["cold_weeks"], layer["fallback"]) == (cold, weeks if cold else 0, fallback)
    assert layer["q_pooled"] == conformal_quantile(s) and layer["q_rest"] == conformal_quantile(s[cold:])
    own = conformal_quantile(s[:cold])
    assert layer["q_cold"] == (layer["q_pooled"] if fallback else own)
    if cold >= 119:
        assert own != layer["q_pooled"]  # the fallback is seen in the number, not only in the flag
    assert check_interval_layer(layer) == layer and (MIN_COLD_HOURS, MIN_COLD_WEEKS) == (120, 4)


def test_an_interval_layer_that_does_not_follow_its_rule_is_refused():
    s, t, d = scores_in(120, 4)
    good = binned_half_widths(s, t, d, edge=-5.0)
    fell = binned_half_widths(*scores_in(119, 6), edge=-5.0)
    for bad in (good | {"fallback": True}, fell | {"fallback": False}, fell | {"q_cold": fell["q_cold"] * 2},
                good | {"cold_n": 119}, good | {"cold_weeks": 3}, good | {"q_rest": float("inf")},
                good | {"bin_edge": float("nan")}, good | {"cold_n": 120.0}, good | {"fallback": 1},
                {k: v for k, v in good.items() if k != "q_pooled"}, good | {"extra": 1}):
        with pytest.raises(ValueError):
            check_interval_layer(bad)


def test_cold_is_strictly_below_the_edge():
    """A score at the edge is in the rest; a score without a temperature is in neither bin, only in the pooled one;
    a score that is not finite is in none."""
    assert list(bin_of([-5.0001, -5.0, 3.0, np.nan], -5.0)) == ["cold", "rest", "rest", None]
    s, t, d = scores_in(120, 4)
    at_edge = binned_half_widths(s, np.where(t < 0, -5.0, t), d, edge=-5.0)
    assert (at_edge["cold_n"], at_edge["fallback"]) == (0, True)
    blind = binned_half_widths(np.r_[s, 9.0, np.nan], np.r_[t, np.nan, -30.0], pd.concat([d, d.iloc[:2]]), -5.0)
    assert blind["cold_n"] == 120 and blind["q_rest"] == conformal_quantile(s[120:])
    assert blind["q_pooled"] == conformal_quantile(np.r_[s, 9.0])
    with pytest.raises(ValueError):
        binned_half_widths(s, t, d, edge=float("nan"))


def oos(first_month: str = "2023-01", months: int = 14) -> pd.DataFrame:
    """Out-of-sample rows, one per hour from `first_month`, each day scored as design code scores it, a forecast of
    20,000 MW and a mild noon-feed temperature; every score 0.02 unless set."""
    first = pd.Period(first_month, "M")
    idx = pd.date_range(day_edge(first.start_time) + H, day_edge((first + months).start_time), freq="h")
    out = pd.DataFrame({"pred": 20_000.0, "day": lst_day(idx), "temp_forecast": 0.0}, index=idx)
    out["fold"] = out["day"].dt.to_period("M").astype(str)
    out["scored_at"] = scored_at(out["day"], idx.max())
    out["target"] = out["pred"] * 1.02
    return out


def test_two_folds_whose_edges_differ_bin_the_same_score_by_the_edge_of_the_fit_being_calibrated():
    """Section 12: the hours at -10 C of February to December 2023 are in both folds' windows. 2024-01's fit has its
    edge at -5 C, so they are cold there and its cold half-width is theirs (0.5); 2024-02's has its edge at -15 C, so
    they are rest there and its cold half-width is that of the -20 C hours only (0.01). The edges of the folds that
    scored them (here absurd, +100 C) are never read; each fold's own hours are binned by its own edge too."""
    out = oos()
    h = (out.index - H).hour
    shared = (out["day"] >= "2023-02-01") & (out["day"] <= "2023-12-31")
    out.loc[shared & (h == 3), ["temp_forecast", "target"]] = [-20.0, 20_000 * 1.01]
    out.loc[shared & (h == 4), ["temp_forecast", "target"]] = [-10.0, 20_000 * 1.5]
    out.loc[(out["day"] >= "2024-01-01") & (h == 4), "temp_forecast"] = -10.0  # the folds' own hours at -10 C
    edges = {fold: 100.0 for fold in out["fold"].unique()} | {"2024-01": -5.0, "2024-02": -15.0}
    iv = relative_conformal(out, "pred", bins=("temp_forecast", edges))
    by = iv.groupby("fold").first()
    assert list(by.index) == ["2024-01", "2024-02"]
    assert by.loc["2024-01", "q_cold"] == pytest.approx(0.5) and by.loc["2024-02", "q_cold"] == pytest.approx(0.01)
    assert by.loc["2024-02", "q_rest"] == pytest.approx(0.02) and not by["fallback"].any()
    assert list(by["edge_c"]) == [-5.0, -15.0]
    for fold in ("2024-01", "2024-02"):
        months = [str(m) for m in pd.period_range(pd.Period(fold) - 12, pd.Period(fold) - 1, freq="M")]
        window = calibration_rows(out[out["fold"].isin(months)], fit_clock_of(fold))
        score = (window["target"] - window["pred"]).abs() / window["pred"]
        expected = binned_half_widths(score, window["temp_forecast"], window["day"], edges[fold])
        part = iv[iv["fold"] == fold]
        assert {k: part[k].iloc[0] for k in ("q_cold", "q_rest", "q_pooled", "cold_n", "cold_weeks")} == \
            {k: expected[k] for k in ("q_cold", "q_rest", "q_pooled", "cold_n", "cold_weeks")}, fold
    own = iv[(iv.index - H).hour == 4]
    assert set(own.loc[own["fold"] == "2024-01", "bin"]) == {"cold"} and (own.loc[own["fold"] == "2024-01", "q"]
                                                                           == by.loc["2024-01", "q_cold"]).all()
    assert set(own.loc[own["fold"] == "2024-02", "bin"]) == {"rest"} and (own.loc[own["fold"] == "2024-02", "q"]
                                                                           == by.loc["2024-02", "q_rest"]).all()
    np.testing.assert_allclose(iv["lower"], 20_000 - iv["q"] * 20_000, rtol=1e-12)


def test_a_fold_whose_cold_bin_falls_back_says_so_on_every_interval():
    out = oos()
    h = (out.index - H).hour
    few = (out["day"] >= "2023-03-01") & (out["day"] <= "2023-03-14") & (h == 3)  # 14 cold hours in 3 weeks
    out.loc[few, "temp_forecast"] = -20.0
    iv = relative_conformal(out, "pred", bins=("temp_forecast", {"2024-01": -5.0, "2024-02": -5.0}))
    assert iv["fallback"].all() and (iv["q_cold"] == iv["q_pooled"]).all() and (iv["cold_n"] == 14).all()


def test_binned_intervals_need_each_fold_s_edge_and_relative_scores():
    out = oos()
    with pytest.raises(ValueError, match="bin edge"):
        relative_conformal(out, "pred", bins=("temp_forecast", {"2024-01": -5.0}))
    with pytest.raises(ValueError):
        relative_conformal(out, "pred", absolute=True, bins=("temp_forecast", {"2024-01": -5.0, "2024-02": -5.0}))
    with pytest.raises(ValueError):
        relative_conformal(out, "pred", groups=out["fold"], bins=("temp_forecast", {"2024-01": -5.0}))


def test_v2_s_conformal_is_v2_s_at_midnight():
    out = oos(months=15)
    out["target"] = out["pred"] * (1 + np.random.default_rng(0).normal(0, 0.03, len(out)))
    pd.testing.assert_frame_equal(relative_conformal(out, "pred"), relative_conformal(out, "pred", issue=MIDNIGHT))
    assert "bin" not in relative_conformal(out, "pred").columns


def test_the_noon_fit_clock_keeps_the_day_before_the_eve_and_leaves_the_eve_out():
    """Section 7: at a noon fit clock (the eve at 17:00 UTC) the calibration set is v2's: the eve is scored at the
    first test day's 06:00 UTC, after the clock, and stays out; the day before it is in. 2024-12-31 stays out of the
    freeze for 2025."""
    for year in (2024, 2025):
        clock = fit_clock_of(f"{year}-01", NOON)
        assert clock == pd.Timestamp(f"{year - 1}-12-31T17:00Z") and fit_clock_of(f"{year}-01") == \
            pd.Timestamp(f"{year}-01-01T05:00Z")
        eve = pd.Timestamp(f"{year - 1}-12-31")
        idx = pd.date_range(day_edge(eve - pd.Timedelta(days=30)) + H, day_edge(eve + pd.Timedelta(days=1)), freq="h")
        rows = pd.DataFrame({"day": lst_day(idx)}, index=idx)
        rows["scored_at"] = scored_at(rows["day"], idx.max())  # the data end at v2's clock: the eve scored there
        for c in (clock, fit_clock_of(f"{year}-01")):
            kept = calibration_rows(rows, c)
            assert not (kept["day"] == eve).any() and int((kept["day"] == eve - pd.Timedelta(days=1)).sum()) == 24


def test_a_day_is_scored_at_d_plus_1_0600_utc_whatever_the_issue_time():
    """The replay scores day D one hour after its last hour, D+1 06:00 UTC, never from D+1's origin (section 2): a
    row of D calibrates a half-width fixed at that hour and not one fixed a minute before, at either issue time."""
    day = pd.Timestamp("2024-03-14")
    rows = pd.DataFrame({"day": [day], "scored_at": [pd.Timestamp("2024-03-01T00:00Z")]}, index=["r"])
    scored = pd.Timestamp("2024-03-15T06:00Z")
    assert len(calibration_rows(rows, scored)) == 1
    assert len(calibration_rows(rows, scored - pd.Timedelta(minutes=1))) == 0
    next_day = day + pd.Timedelta(days=1)
    assert scored == day_edge(next_day) + H and scored != day_edge(next_day) + NOON.origin_offset + H
