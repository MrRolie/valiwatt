"""How well station temperatures stand for province-wide demand, measured on 2019-2023 only.

Demand and temperature are aggregated to days in local standard time (UTC-5). Each demand value covers the hour ending
at its timestamp, so it belongs to the day in which that hour starts. Temperature is the observation at the same
timestamps. A day counts only when all 24 demand hours are usable and the temperature source reports at least
MIN_TEMP_HOURS of them.

These are associations between demand and OBSERVED weather. A forecast made in advance would only have a weather
forecast; nothing here measures forecast skill.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from valiwatt.config import DEMAND_FIRST, DESIGN_LAST, LST_OFFSET
from valiwatt.quality.demand import USABLE

BASE_C = 18.0  # heating and cooling degree-day base, the usual convention
MIN_TEMP_HOURS = 20
WINTER_MONTHS = (12, 1, 2)


class LookaheadError(ValueError):
    """Design-window analysis received hours after the design window."""


def lst_day(ts_hour_ending: pd.DatetimeIndex) -> pd.Index:
    """Local-standard-time day of the hour that ends at each timestamp."""
    start = ts_hour_ending - pd.Timedelta(hours=1)
    return pd.Index((start + LST_OFFSET).tz_convert(None).normalize(), name="day")


def daily_demand(demand: pd.DataFrame) -> pd.Series:
    if demand.index.max() > DESIGN_LAST:
        raise LookaheadError(f"demand runs to {demand.index.max()}, after the design window ({DESIGN_LAST})")
    usable = demand["status"].isin(USABLE)
    mw = demand["mw"].where(usable)
    g = mw.groupby(lst_day(demand.index))
    daily = g.mean()[g.count() == 24]
    return daily


def daily_temperature(temp: pd.Series) -> pd.Series:
    if temp.index.max() > DESIGN_LAST:
        raise LookaheadError(f"temperature runs to {temp.index.max()}, after the design window ({DESIGN_LAST})")
    g = temp.groupby(lst_day(temp.index))
    return g.mean()[g.count() >= MIN_TEMP_HOURS]


def design_matrix(days: pd.Index, temp_c: np.ndarray, winter_only: bool = False) -> np.ndarray:
    hdd = np.maximum(BASE_C - temp_c, 0.0)
    cdd = np.maximum(temp_c - BASE_C, 0.0)
    dow = pd.get_dummies(pd.Index(days).dayofweek, drop_first=True).to_numpy(dtype=float)
    cols = [np.ones(len(days)), hdd] + ([] if winter_only else [cdd])
    return np.column_stack(cols + [dow])


def ols(X: np.ndarray, y: np.ndarray) -> dict:
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    fitted = X @ beta
    resid = y - fitted
    ss_res = float(resid @ resid)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return {"beta": beta, "fitted": fitted, "r2": 1 - ss_res / ss_tot, "rmse": float(np.sqrt(ss_res / len(y)))}


def representativeness(demand: pd.DataFrame, temps: dict[str, pd.Series]) -> dict:
    """R^2 of daily demand on degree-days and day of week, per temperature source, on the days every source covers."""
    dd = daily_demand(demand)
    td = pd.DataFrame({k: daily_temperature(t) for k, t in temps.items()})
    common = dd.index.intersection(td.dropna().index)
    y = dd.loc[common].to_numpy()
    winter = common.month.isin(WINTER_MONTHS)
    rows = []
    for k in temps:
        x = td.loc[common, k].to_numpy()
        full = ols(design_matrix(common, x), y)
        win = ols(design_matrix(common[winter], x[winter], winter_only=True), y[winter])
        rows.append({"source": k, "r2": full["r2"], "rmse_mw": full["rmse"], "r2_winter": win["r2"],
                     "rmse_winter_mw": win["rmse"], "hdd_slope_mw_per_c": full["beta"][1],
                     "cdd_slope_mw_per_c": full["beta"][2]})
    corr = td.loc[common].corr()
    return {
        "first_day": common.min(),
        "last_day": common.max(),
        "days": len(common),
        "winter_days": int(winter.sum()),
        "demand_days_available": len(dd),
        "base_c": BASE_C,
        "by_source": rows,
        "temperature_correlation": {a: {b: corr.loc[a, b] for b in corr.columns} for a in corr.index},
        "daily": pd.DataFrame({"demand_mw": dd.loc[common]}).join(td.loc[common]),
    }


def residual_by_month(daily: pd.DataFrame, source: str, fit_year: int) -> pd.DataFrame:
    """Fit daily demand on degree-days over one year, then the monthly mean residual (% of fitted) for every month.

    `vs_fit_year_pp` subtracts the same calendar month's residual in the fit year, which cancels the model's seasonal
    misfit. A persistent residual says demand moved for reasons the weather model does not carry (activity, load
    growth); it does not say which."""
    days = daily.index
    X = design_matrix(days, daily[source].to_numpy())
    y = daily["demand_mw"].to_numpy()
    fit_mask = days.year == fit_year
    beta, *_ = np.linalg.lstsq(X[fit_mask], y[fit_mask], rcond=None)
    fitted = X @ beta
    pct = pd.Series((y - fitted) / fitted * 100.0, index=days)
    g = pct.groupby(days.to_period("M"))
    out = pd.DataFrame({"mean_residual_pct": g.mean(), "days": g.count()})
    base = out.loc[out.index.year == fit_year, "mean_residual_pct"]
    base.index = base.index.month
    out["vs_fit_year_pp"] = out["mean_residual_pct"] - out.index.month.map(base).to_numpy()
    out.index = out.index.astype(str)
    return out


def top_hours(demand: pd.DataFrame, temp: pd.Series, n: int = 5) -> list[dict]:
    d = demand.loc[demand.index <= DESIGN_LAST]
    d = d[d["status"].isin(USABLE)].nlargest(n, "mw")
    return [
        {"utc": t, "hour_ending_lst": (t + LST_OFFSET).tz_convert(None), "mw": r.mw, "temp_c_primary": temp.get(t)}
        for t, r in d.iterrows()
    ]


def design_window(frame: pd.DataFrame | pd.Series):
    return frame.loc[(frame.index >= DEMAND_FIRST) & (frame.index <= DESIGN_LAST)]


def r2_difference(daily: pd.DataFrame, a: str, b: str, winter_only: bool = False, n_boot: int = 1000,
                  seed: int = 0) -> dict:
    """R^2(b) - R^2(a) on the same days, with a 95% interval from a block bootstrap over calendar weeks.

    Days next to each other share weather and demand, so resampling single days would understate the uncertainty;
    whole weeks are resampled instead, and both models are refitted on every resample."""
    d = daily
    if winter_only:
        d = d[d.index.month.isin(WINTER_MONTHS)]
    y = d["demand_mw"].to_numpy()
    Xa = design_matrix(d.index, d[a].to_numpy(), winter_only)
    Xb = design_matrix(d.index, d[b].to_numpy(), winter_only)
    point = ols(Xb, y)["r2"] - ols(Xa, y)["r2"]
    weeks = pd.factorize(d.index.to_period("W"))[0]
    blocks = [np.flatnonzero(weeks == w) for w in np.unique(weeks)]
    rng = np.random.default_rng(seed)
    diffs = np.empty(n_boot)
    for i in range(n_boot):
        idx = np.concatenate([blocks[j] for j in rng.integers(0, len(blocks), len(blocks))])
        diffs[i] = ols(Xb[idx], y[idx])["r2"] - ols(Xa[idx], y[idx])["r2"]
    lo, hi = np.quantile(diffs, [0.025, 0.975])
    return {"a": a, "b": b, "winter_only": winter_only, "difference": point, "ci95_low": lo, "ci95_high": hi,
            "share_at_or_below_zero": float((diffs <= 0).mean()), "resamples": n_boot, "blocks": len(blocks),
            "separated": bool(lo > 0 or hi < 0)}
