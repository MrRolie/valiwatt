"""The reproduction check (docs/plan-v3.md, section 17 step 2 and section 12, "Reproduction"): v2's form at midnight,
in v3's harness, on a different set of 2020-2023 hours than v2's recorded validation, or more than 1e-6 MW from it on
one hour, and the run refuses. Synthetic tables in the read of v2's store's shape (columns ts and forecast)."""

import numpy as np
import pandas as pd
import pytest

from valiwatt.forecast.validate_v3 import REPRODUCTION_TOLERANCE_MW, ReproductionError, check_reproduction

H = pd.Timedelta(hours=1)
# v2's validation hours: the LST days 2020-01-01 to 2023-12-31, labels 2020-01-01 06:00 to 2024-01-01 05:00 UTC.
FIRST, LAST = pd.Timestamp("2020-01-01T06:00Z"), pd.Timestamp("2024-01-01T05:00Z")


def tables(seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """v3's out-of-sample frame (2019-12 to 2024: hours outside v2's years on both ends) and v2's recorded forecasts of
    the same 2020-2023 hours, equal to the bit; a few hours without a forecast on both sides."""
    idx = pd.date_range(FIRST - 48 * H, LAST + 72 * H, freq="h", name="ts_utc")
    rng = np.random.default_rng(seed)
    pred = pd.Series(20_000 + rng.normal(0, 2_000, len(idx)), index=idx)
    pred.iloc[[100, 5_000, 20_000]] = np.nan
    oos = pd.DataFrame({"pred_v2_midnight": pred, "target": pred + 50})
    inside = (idx >= FIRST) & (idx <= LAST)
    recorded = pd.DataFrame({"ts": idx[inside], "forecast": pred[inside].to_numpy()})
    return oos, recorded.sample(frac=1.0, random_state=seed).reset_index(drop=True)  # row order never matters


def test_the_same_hours_and_values_pass_and_say_what_was_compared():
    oos, recorded = tables()
    r = check_reproduction(oos, recorded)
    assert r["hours"] == int(oos.loc[FIRST:LAST, "pred_v2_midnight"].notna().sum()) == 35_064 - 3  # 1,461 days of 24 hours, three without a forecast
    assert r["max_abs_mw"] == 0.0 and r["tolerance_mw"] == REPRODUCTION_TOLERANCE_MW == 1e-6
    assert (r["first_day"], r["last_day"], r["status"]) == ("2020-01-01", "2023-12-31", "passed")


def test_a_difference_of_5e_7_mw_passes_and_one_of_2e_6_is_refused():
    oos, recorded = tables()
    hour = pd.Timestamp("2022-07-14T15:00Z")
    near = recorded.copy()
    near.loc[near["ts"] == hour, "forecast"] += 5e-7
    assert check_reproduction(oos, near)["max_abs_mw"] == pytest.approx(5e-7, rel=1e-3)
    far = recorded.copy()
    far.loc[far["ts"] == hour, "forecast"] += 2e-6
    with pytest.raises(ReproductionError, match="more than 1e-06 MW on 1 hours") as e:
        check_reproduction(oos, far)
    assert "2022-07-14 15:00:00+00:00" in str(e.value)


@pytest.mark.parametrize("change", ["missing", "extra", "nan_in_v3", "nan_in_v2"])
def test_another_set_of_hours_with_a_forecast_is_refused(change):
    """An hour v2 recorded and v3 lacks, an hour v3 forecasts and v2 never recorded, and a NaN against a value on
    either side: the sets of hours with a forecast differ."""
    oos, recorded = tables()
    hour = pd.Timestamp("2021-02-03T12:00Z")
    if change == "missing":
        recorded = recorded[recorded["ts"] != hour]
        oos = oos.copy()
        oos.loc[hour, "pred_v2_midnight"] = 21_000.0  # v3 forecasts it, v2 has no row for it
    elif change == "extra":
        recorded = pd.concat([recorded, pd.DataFrame({"ts": [pd.Timestamp("2021-02-03T12:00Z")],
                                                      "forecast": [1.0]})]).drop_duplicates("ts", keep="last")
        oos = oos.copy()
        oos.loc[hour, "pred_v2_midnight"] = np.nan  # v2 recorded a forecast, v3 has none
    elif change == "nan_in_v3":
        oos = oos.copy()
        oos.loc[hour, "pred_v2_midnight"] = np.nan
    else:
        recorded = recorded.copy()
        recorded.loc[recorded["ts"] == hour, "forecast"] = np.nan
    with pytest.raises(ReproductionError, match="another set of 2020-2023 hours"):
        check_reproduction(oos, recorded)


def test_hours_outside_2020_2023_are_not_compared():
    """v3 forecasts 2019-12 and 2024 too; v2's validation years are 2020-2023 (the label 2024-01-01 05:00 UTC is the
    last hour of 2023-12-31, and 2020-01-01 06:00 UTC its first)."""
    oos, recorded = tables()
    oos = oos.copy()
    oos.loc[FIRST - H, "pred_v2_midnight"] += 1_000.0  # 2019-12-31's last hour
    oos.loc[LAST + H, "pred_v2_midnight"] += 1_000.0  # 2024-01-01's first hour
    assert check_reproduction(oos, recorded)["max_abs_mw"] == 0.0
    edge = recorded.copy()
    edge.loc[edge["ts"] == LAST, "forecast"] += 1.0  # the last hour of 2023 is compared
    with pytest.raises(ReproductionError, match="more than"):
        check_reproduction(oos, edge)


def test_input_that_breaks_the_contract_is_refused():
    oos, recorded = tables()
    with pytest.raises(ValueError, match="columns ts and forecast"):
        check_reproduction(oos, recorded.rename(columns={"forecast": "pred_gam_forecast"}))
    with pytest.raises(ValueError, match="time zone"):
        check_reproduction(oos, recorded.assign(ts=pd.DatetimeIndex(recorded["ts"]).tz_localize(None)))
    with pytest.raises(ValueError, match="twice"):
        check_reproduction(oos, pd.concat([recorded, recorded.iloc[:1]]))
    with pytest.raises(ValueError, match="pred_v2_midnight"):
        check_reproduction(oos.drop(columns=["pred_v2_midnight"]), recorded)
