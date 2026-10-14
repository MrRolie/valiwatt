"""The explainable model recovers known structure, never imputes, and the baseline is the week-earlier value."""

import numpy as np
import pandas as pd

from conftest import synth_demand, synth_obs
from valiwatt.forecast.features import build
from valiwatt.forecast.model import OLSForecaster, baseline, design
from valiwatt.quality.demand import build_demand

FIRST, LAST = pd.Timestamp("2021-01-01T06:00Z"), pd.Timestamp("2021-07-01T05:00Z")


def frame():
    demand = build_demand(synth_demand(FIRST, LAST), FIRST, LAST).frame
    temp = synth_obs(FIRST, LAST).set_index("ts_utc")["temp_c"]
    return build(demand, temp)


def test_model_recovers_a_planted_heating_effect():
    f = frame()
    rng = np.random.default_rng(3)
    # Plant demand = cell mean + 400 MW per heating degree at night + noise; lags carry no signal.
    f["target"] = 18_000 + 400 * f["hdh_observed"] * f["hour"].isin(range(0, 6)) + rng.normal(0, 50, len(f))
    m = OLSForecaster("observed").fit(f.iloc[200:])
    assert abs(m.coefficients()["heating:night"] - 400) < 15


def test_rows_with_a_missing_input_are_not_predicted():
    f = frame()
    m = OLSForecaster("observed").fit(f.iloc[200:])
    f.loc[f.index[300], "lag24"] = np.nan
    pred = m.predict(f)
    assert np.isnan(pred.iloc[300]) and np.isfinite(pred.iloc[301])


def test_baseline_is_the_week_earlier_value():
    f = frame()
    t = f.index[400]
    assert baseline(f)[t] == f.loc[t - pd.Timedelta(hours=168), "target"]


def test_design_has_one_named_column_per_coefficient():
    X = design(frame(), "persistence")
    assert X.columns.is_unique and "lag24" in X.columns and any(c.startswith("heating:") for c in X.columns)
    # Each row falls in exactly one hour-by-day-type cell.
    cells = X[[c for c in X.columns if c.startswith("cell:")]]
    assert (cells.sum(axis=1) == 1).all()
