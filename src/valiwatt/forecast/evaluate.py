"""Error metrics with week-block bootstrap intervals, and relative-error conformal intervals.

Hours next to each other share weather and demand, so uncertainty is estimated by resampling whole weeks (local
standard time, Monday to Sunday), never single hours.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

ALPHA = 0.10  # nominal 90 % intervals
CALIBRATION_FOLDS = 12
N_BOOT = 1000
SEASONS = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
           6: "summer", 7: "summer", 8: "summer", 9: "fall", 10: "fall", 11: "fall"}


def season_of(day: pd.Series) -> pd.Series:
    return pd.to_datetime(day).dt.month.map(SEASONS)


def week_of(day: pd.Series) -> pd.Series:
    return pd.to_datetime(day).dt.to_period("W-SUN").astype(str)


def errors(out: pd.DataFrame, pred_col: str) -> pd.DataFrame:
    """Per-hour error columns for one forecast column; rows without a target or forecast are dropped."""
    d = out[["target", pred_col, "day", "hour", "fold"]].dropna(subset=["target", pred_col]).copy()
    d["err"] = d["target"] - d[pred_col]
    d["ape"] = d["err"].abs() / d["target"]
    d["rel_bias"] = d["err"] / d["target"]
    d["week"] = week_of(d["day"])
    d["season"] = season_of(d["day"])
    d["year"] = pd.to_datetime(d["day"]).dt.year
    return d


def _weights(n_blocks: int, n_boot: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.multinomial(n_blocks, np.full(n_blocks, 1.0 / n_blocks), size=n_boot).astype(float)


def mean_with_ci(d: pd.DataFrame, value: str, n_boot: int = N_BOOT, seed: int = 0) -> dict:
    """Mean of `value` over hours, with a 95 % week-block bootstrap interval."""
    g = d.groupby("week")[value].agg(["sum", "count"])
    if g["count"].sum() == 0:
        raise ValueError(f"no hours to average for {value!r}")
    W = _weights(len(g), n_boot, seed)
    boot = (W @ g["sum"].to_numpy()) / (W @ g["count"].to_numpy())
    lo, hi = np.quantile(boot, [0.025, 0.975])
    return {"mean": float(g["sum"].sum() / g["count"].sum()), "ci95_low": float(lo), "ci95_high": float(hi),
            "hours": int(g["count"].sum()), "weeks": len(g)}


def by_group(d: pd.DataFrame, group: str, values=("ape", "rel_bias"), n_boot: int = N_BOOT) -> list[dict]:
    rows = []
    for key, part in d.groupby(group, sort=True):
        row = {"group": group, "key": str(key)}
        for v in values:
            if v in part:
                r = mean_with_ci(part, v, n_boot)
                row.update({v: r["mean"], f"{v}_low": r["ci95_low"], f"{v}_high": r["ci95_high"]})
        row["hours"] = len(part)
        rows.append(row)
    return rows


def paired_difference(a: pd.DataFrame, b: pd.DataFrame, value: str = "ape", n_boot: int = N_BOOT, seed: int = 0) -> dict:
    """mean(a) - mean(b) on the hours both have, with a week-block bootstrap interval."""
    j = a[[value, "week"]].join(b[[value]], how="inner", rsuffix="_b")
    j["diff"] = j[value] - j[f"{value}_b"]
    r = mean_with_ci(j.rename(columns={"diff": "d"}), "d", n_boot, seed)
    return {"difference": r["mean"], "ci95_low": r["ci95_low"], "ci95_high": r["ci95_high"], "hours": r["hours"],
            "separated": bool(r["ci95_low"] > 0 or r["ci95_high"] < 0)}


def conformal_quantile(scores: np.ndarray, alpha: float = ALPHA) -> float:
    """The ceil((n+1)(1-alpha))-th smallest score: the split-conformal quantile."""
    s = np.sort(scores[np.isfinite(scores)])
    n = len(s)
    k = math.ceil((n + 1) * (1 - alpha))
    return float("inf") if k > n else float(s[k - 1])


def relative_conformal(out: pd.DataFrame, pred_col: str, alpha: float = ALPHA,
                       calibration_folds: int = CALIBRATION_FOLDS, absolute: bool = False,
                       groups: pd.Series | None = None) -> pd.DataFrame:
    """Interval for each fold from the out-of-sample scores of the previous `calibration_folds` folds only.

    Relative score |y - f| / f (half-width q * f) by default; absolute score |y - f| (half-width q) as sensitivity.
    With `groups` (e.g. the season of each hour), the quantile is taken within the row's own group (Mondrian)."""
    d = out[["target", pred_col, "fold"]].copy()
    d["group"] = "all" if groups is None else groups.reindex(d.index)
    if absolute:
        d["score"] = (d["target"] - d[pred_col]).abs()
    else:
        d["score"] = (d["target"] - d[pred_col]).abs() / d[pred_col]
    folds = sorted(d["fold"].unique())
    rows = []
    for i, fold in enumerate(folds):
        if i < calibration_folds:
            continue
        window = d[d["fold"].isin(folds[i - calibration_folds:i])]
        for g, part in d[d["fold"] == fold].groupby("group"):
            calib = window.loc[window["group"] == g, "score"].to_numpy()
            q = conformal_quantile(calib, alpha)
            half = q if absolute else q * part[pred_col]
            rows.append(pd.DataFrame({"fold": fold, "q": q, "lower": part[pred_col] - half,
                                      "upper": part[pred_col] + half,
                                      "calibration_n": int(np.isfinite(calib).sum())}, index=part.index))
    iv = pd.concat(rows)
    y = out.loc[iv.index, "target"]
    inside = ((y >= iv["lower"]) & (y <= iv["upper"])).astype(float)
    iv["covered"] = inside.where(y.notna() & out.loc[iv.index, pred_col].notna())
    return iv


def weekly_stats(d: pd.DataFrame, covered: pd.Series | None = None) -> pd.DataFrame:
    """Weekly MAPE, signed relative bias and (if given) interval coverage; one row per local-standard week."""
    x = d.copy()
    if covered is not None:
        x["covered"] = covered.reindex(x.index).astype(float)
    agg = {"ape": "mean", "rel_bias": "mean", "day": "min"}
    if covered is not None:
        agg["covered"] = "mean"
    w = x.groupby("week").agg(agg).rename(columns={"ape": "mape", "day": "first_day"})
    w["hours"] = x.groupby("week").size()
    thursday = pd.to_datetime(w["first_day"]) + pd.to_timedelta((3 - pd.to_datetime(w["first_day"]).dt.dayofweek) % 7, "D")
    w["season"] = thursday.dt.month.map(SEASONS)
    return w
