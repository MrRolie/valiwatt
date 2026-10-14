"""A small synthetic world for replay tests: two years of history, a frozen model, and fixed thresholds.

Demand follows a daily cycle, a weekly cycle and heating below 18 °C; temperature follows the seasons. Thresholds are
set by hand around the world's noise so each break is judged against known limits.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from conftest import synth_obs
from valiwatt.forecast import walkforward as wf
from valiwatt.forecast.features import build
from valiwatt.forecast.freeze import fit_frozen
from valiwatt.quality.demand import build_demand

FIRST, LAST = pd.Timestamp("2020-01-01T06:00Z"), pd.Timestamp("2022-04-01T05:00Z")
REPLAY_YEAR = 2022
SEASON_T = {"mape_high": 0.012, "bias_low": -0.006, "bias_high": 0.006, "coverage_low": 0.7}
THRESHOLDS = {"performance": {s: dict(SEASON_T) for s in ("winter", "spring", "summer", "fall")},
              "drift": {"temperature_high": 1.0, "forecast_high": 1.0}}


def world(seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    obs = synth_obs(FIRST, LAST, seed=seed)
    idx = obs["ts_utc"]
    h = np.arange(len(idx))
    rng = np.random.default_rng(seed + 1)
    dow = pd.DatetimeIndex(idx).dayofweek.to_numpy()
    mw = (18_000 + 2_500 * np.sin(2 * np.pi * h / 24) - 900 * (dow >= 5)
          + 350 * np.maximum(18 - obs["temp_c"].to_numpy(), 0) + rng.normal(0, 60, len(idx)))
    raw = pd.DataFrame({"ts_utc": idx, "mw": mw, "source_line": np.arange(2, len(idx) + 2)})
    return raw, obs


def respond(raw: pd.DataFrame, obs: pd.DataFrame, new_obs: pd.DataFrame) -> pd.DataFrame:
    """Demand as it would be under a changed temperature (the world's heating response, applied to the change)."""
    old = np.maximum(18 - obs["temp_c"].to_numpy(), 0)
    new = np.maximum(18 - new_obs["temp_c"].to_numpy(), 0)
    return raw.assign(mw=raw["mw"] + 350 * (new - old))


@lru_cache(maxsize=1)
def frozen_and_reference():
    raw, obs = world()
    first, last = FIRST, LAST
    f = build(build_demand(raw, first, last).frame, obs.set_index("ts_utc")["temp_c"])
    oos = wf.run(f, wf.folds(f"{REPLAY_YEAR - 1}-01", f"{REPLAY_YEAR - 1}-12"), scenarios=("persistence",))
    frozen, _ = fit_frozen(f, oos, REPLAY_YEAR)
    ref = oos[["day"]].assign(forecast=oos["pred_persistence"])
    return frozen, ref
