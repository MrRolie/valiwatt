"""A small synthetic world for replay tests: two years of history, a frozen model, and thresholds derived from the
world's own validation weeks by the same quantile rule the real validation uses.

Demand follows a daily cycle, a weekly cycle, heating below 18 °C, hourly noise, and a day-level error that persists
from day to day (as weather regimes do), so a day's error is several times noisier than a week's, as in the real data.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from conftest import synth_obs
from valiwatt.breaks import SYNTHETIC_WORLD
from valiwatt.forecast import walkforward as wf
from valiwatt.forecast.evaluate import errors, weekly_stats
from valiwatt.forecast.features import build
from valiwatt.forecast.freeze import fit_frozen
from valiwatt.monitor.rules import MIN_WEEK_HOURS, QUANTILES
from valiwatt.quality.demand import build_demand

FIRST, LAST = pd.Timestamp("2020-01-01T06:00Z"), pd.Timestamp("2022-04-01T05:00Z")
REPLAY_YEAR = 2022
DAY_SIGMA, DAY_PHI = 0.02, 0.6


def world(seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    obs = synth_obs(FIRST, LAST, seed=seed)
    idx = pd.DatetimeIndex(obs["ts_utc"])
    h = np.arange(len(idx))
    rng = np.random.default_rng(seed + 1)
    day = (idx - pd.Timedelta(hours=6)).normalize()
    codes, uniq = pd.factorize(day)
    eps = np.zeros(len(uniq))
    for i in range(1, len(uniq)):
        eps[i] = DAY_PHI * eps[i - 1] + rng.normal(0, DAY_SIGMA * np.sqrt(1 - DAY_PHI ** 2))
    mw = (18_000 + 2_500 * np.sin(2 * np.pi * h / 24) - 900 * (idx.dayofweek >= 5)
          + 350 * np.maximum(18 - obs["temp_c"].to_numpy(), 0) + rng.normal(0, 60, len(idx)))
    mw = mw * (1 + eps[codes])
    raw = pd.DataFrame({"ts_utc": idx, "mw": mw, "source_line": np.arange(2, len(idx) + 2)})
    return raw, obs


def respond(raw: pd.DataFrame, obs: pd.DataFrame, new_obs: pd.DataFrame) -> pd.DataFrame:
    """Demand as it would be under a changed temperature (the world's heating response, applied to the change)."""
    old = np.maximum(18 - obs["temp_c"].to_numpy(), 0)
    new = np.maximum(18 - new_obs["temp_c"].to_numpy(), 0)
    return raw.assign(mw=raw["mw"] + 350 * (new - old))


@lru_cache(maxsize=1)
def frozen_reference_thresholds():
    """The frozen model, the drift reference, and thresholds from the 2021 out-of-sample weeks (one set for every
    season: the world has no seasonal error pattern). The drift alarm is added by `thresholds()`."""
    raw, obs = world()
    f = build(build_demand(raw, FIRST, LAST).frame, obs.set_index("ts_utc")["temp_c"])
    oos = wf.run(f, wf.folds(f"{REPLAY_YEAR - 1}-01", f"{REPLAY_YEAR - 1}-12"), scenarios=("persistence",))
    frozen, _ = fit_frozen(f, oos, REPLAY_YEAR)
    ref = oos[["day"]].assign(forecast=oos["pred_persistence"])
    e = errors(oos, "pred_persistence")
    covered = ((e["err"].abs() / oos.loc[e.index, "pred_persistence"]) <= frozen.q).astype(float)
    w = weekly_stats(e, covered)
    w = w[w["hours"] >= MIN_WEEK_HOURS]
    season = {"mape_high": float(w["mape"].quantile(QUANTILES["mape_high"])),
              "bias_low": float(w["rel_bias"].quantile(QUANTILES["bias_low"])),
              "bias_high": float(w["rel_bias"].quantile(QUANTILES["bias_high"])),
              "coverage_low": float(w["covered"].quantile(QUANTILES["coverage_low"])), "weeks": int(len(w))}
    thresholds = {"performance": {s: dict(season) for s in ("winter", "spring", "summer", "fall")},
                  "last_year": REPLAY_YEAR - 1, "weekly_validation": [{"first_day": str(d)} for d in w["first_day"]]}
    return frozen, ref, thresholds


def frozen_and_reference():
    frozen, ref, _ = frozen_reference_thresholds()
    return frozen, ref


def thresholds():
    """The world's thresholds, with the drift alarm fixed by hand (SYNTHETIC_WORLD, which the report prints) at a level
    clean weeks stay under, read when called."""
    th = frozen_reference_thresholds()[2]
    return {"performance": th["performance"],
            "drift": {"temperature_high": SYNTHETIC_WORLD["drift_alarm"], "forecast_high": SYNTHETIC_WORLD["drift_alarm"]},
            "last_year": th["last_year"], "weekly_validation": th["weekly_validation"]}
