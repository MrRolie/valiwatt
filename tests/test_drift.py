"""The drift statistic matches Evidently, uses only earlier years, and fires on a shift but not on the seasons."""

import numpy as np
import pandas as pd
import pytest

from valiwatt.monitor.drift import evidently_value_drift, normed_wasserstein, seasonal_reference, weekly_drift


def seasonal_temps(years=(2019, 2020, 2021, 2022), seed=0):
    days = pd.date_range(f"{years[0]}-01-01", f"{years[-1]}-12-31", freq="h")
    rng = np.random.default_rng(seed)
    doy = days.dayofyear.to_numpy()
    t = 5 - 15 * np.cos(2 * np.pi * doy / 365.25) + 4 * np.sin(2 * np.pi * days.hour.to_numpy() / 24) + rng.normal(0, 3, len(days))
    return pd.Series(t), pd.Series(days.normalize())


def test_statistic_matches_evidently():
    rng = np.random.default_rng(1)
    ref, cur = rng.normal(0, 5, 3000), rng.normal(2, 5, 168)
    assert normed_wasserstein(cur, ref) == pytest.approx(evidently_value_drift(cur, ref, 0.1), rel=1e-6)


def test_reference_takes_only_earlier_years():
    v, d = seasonal_temps()
    week = pd.Timestamp("2021-01-11")
    ref = seasonal_reference(pd.Series(d.dt.year.to_numpy(), index=v.index), d, week)
    assert set(np.unique(ref)) == {2019, 2020}


def test_seasonal_reference_stays_quiet_on_clean_data_and_fires_on_a_shift():
    v, d = seasonal_temps()
    weeks = [pd.Timestamp("2022-01-10"), pd.Timestamp("2022-07-11"), pd.Timestamp("2022-10-10")]
    clean = weekly_drift(v, d, weeks)
    shifted = v.copy()
    shifted[(d >= weeks[1]) & (d < weeks[1] + pd.Timedelta(days=7))] += 8.0
    broken = weekly_drift(shifted, d, weeks)
    assert clean.max() < 0.5
    assert broken[weeks[1]] > 1.0 and broken[weeks[0]] == pytest.approx(clean[weeks[0]])


def test_whole_year_reference_would_fire_every_season():
    v, d = seasonal_temps()
    winter = v[(d >= "2022-01-10") & (d < "2022-01-17")].to_numpy()
    whole_year = v[d.dt.year < 2022].to_numpy()
    assert normed_wasserstein(winter, whole_year) > 1.0  # why the reference is seasonal


@pytest.mark.parametrize("week", ["2023-01-02", "2024-03-04", "2024-02-26", "2023-12-25"])
def test_every_earlier_year_gives_the_same_calendar_window(week):
    """The reference is exactly the days within 17 days of the week's Thursday's calendar date in each earlier year
    (Feb 29 read as Feb 28), across New Year and leap days alike: 35 days per year."""
    days = pd.Series(pd.date_range("2018-01-01", "2024-12-31", freq="D"))
    week = pd.Timestamp(week)
    centre = week + pd.Timedelta(days=3)
    picked = set(seasonal_reference(days, days, week))
    expected = set()
    for y in range(2017, week.year + 1):
        anchor = pd.Timestamp(year=y, month=centre.month,
                              day=min(centre.day, pd.Timestamp(year=y, month=centre.month, day=1).days_in_month))
        window = pd.date_range(anchor - pd.Timedelta(days=17), anchor + pd.Timedelta(days=17), freq="D")
        if 2018 < y < week.year - 1:
            assert len(window) == 35
        expected |= {d for d in window if d.year < week.year and d >= days.iloc[0]}
    assert picked == expected


def test_a_short_week_has_no_drift_statistic():
    rng = np.random.default_rng(2)
    assert np.isnan(normed_wasserstein(rng.normal(0, 1, 48), rng.normal(0, 1, 3000), min_current=120))
    assert np.isfinite(normed_wasserstein(rng.normal(0, 1, 168), rng.normal(0, 1, 3000), min_current=120))
