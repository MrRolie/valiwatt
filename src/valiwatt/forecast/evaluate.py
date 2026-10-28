"""Error metrics with week-block bootstrap intervals, relative-error conformal intervals, and v2's decision rule.

Hours next to each other share weather and demand, so uncertainty is estimated by resampling whole weeks (local
standard time, Monday to Sunday), never single hours.

An interval's half-width is fixed when its model is fitted, so it calibrates only on the out-of-sample rows of the days
the replay would have scored by that fit clock (`calibration_rows`).

The decision rule (what counts as v2 being better than v1, `docs/plan-v2.md` section 8), the model card's status
(section 8.5) and the evaluation label of a v2 replay (section 10.5) are here, in a rule file, frozen with the rest.

v3's (`docs/plan-v3.md`) are here too, beside v2's and never through them: the intervals by temperature (section 7:
`binned_half_widths`, and `relative_conformal` given `bins`, each fold re-binning its calibration window by its own
edge, at its own issue time's fit clock), and the decision rule of section 8 (`contrasts_v3`, `terms_verdict`,
`family_verdict`, the event windows, `rank_windows`, `judge_v3`, `status_v3`, `recommendation_v3`). Every v2 function
called as before computes what it computed before.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from valiwatt import config
from valiwatt.config import LOCAL_TZ
from valiwatt.forecast.guards import LookaheadError
from valiwatt.forecast.issue import MIDNIGHT, IssueSpec, score_clock
from valiwatt.forecast.model import (
    COLD_QUANTILE,
    COMPARATORS,
    NOON_KEYS,
    PRODUCTION,
    V3_COMPARATORS,
    V3_PRODUCTION,
    bin_edge,  # noqa: F401  the bin edge rule and its check, model.py's (a fit places the edge): one home
    check_bin_edge,  # noqa: F401
)
from valiwatt.forecast.walkforward import folds as month_folds

ALPHA = 0.10  # nominal 90 % intervals
CALIBRATION_FOLDS = 12
N_BOOT = 1000
# When the replay scores a day, the data not ending before it: issue.score_clock with no end of the data.
_NO_DATA_END = pd.Timestamp.max.tz_localize("UTC")
SEASONS = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
           6: "summer", 7: "summer", 8: "summer", 9: "fall", 10: "fall", 11: "fall"}


def season_of(day: pd.Series) -> pd.Series:
    return pd.to_datetime(day).dt.month.map(SEASONS)


def week_of(day: pd.Series) -> pd.Series:
    return pd.to_datetime(day).dt.to_period("W-SUN").astype(str)


def hourly_ape(target: pd.Series, pred: pd.Series) -> pd.Series:
    """The hourly absolute percentage error |y - f| / y, over the observed demand: the one formula `errors` and the
    decision rule's contrasts average."""
    return (target - pred).abs() / target


def errors(out: pd.DataFrame, pred_col: str) -> pd.DataFrame:
    """Per-hour error columns for one forecast column; rows without a target or forecast are dropped."""
    d = out[["target", pred_col, "day", "hour", "fold"]].dropna(subset=["target", pred_col]).copy()
    d["err"] = d["target"] - d[pred_col]
    d["ape"] = hourly_ape(d["target"], d[pred_col])
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


def calibration_rows(window: pd.DataFrame, fit_clock: pd.Timestamp) -> pd.DataFrame:
    """The out-of-sample rows a half-width fixed at `fit_clock` may calibrate on: those of the days the replay would
    have scored by then.

    A row's day is scored one hour after its last hour (`features.score_clock`, the `scored_at` column), so the day
    just before a fit's first test day is scored an hour after the fit clock (its origin), and none of its scores
    exists yet when the replay fixes the half-width there: the whole day stays out, not only an hour whose value
    changes in that hour. (Re-reading its targets at the fit clock would score a day the replay has not scored.)

    So a row calibrates only when both are at or before the fit clock: its `scored_at`, and the time the replay
    scores its day, one hour after the day's last hour (`issue.score_clock`, D+1 06:00 UTC, whatever the issue time:
    docs/plan-v3.md, section 2, never derived from D+1's origin). The two differ only
    on the last day of the data, which design code scores at the end of the data: the end of the data never counts as
    a day's scoring for calibration. Without the second condition, a freeze whose data end at its fit clock (the 2024
    freeze, on design data ending 2024-01-01 05:00 UTC) would calibrate on the eve of its replayed year, 2023-12-31,
    while one whose data run past its fit clock (the 2023 freeze) leaves 2022-12-31 out; with it, both leave their eve
    out (`docs/plan-v2.md`, section 6.3). Each row's `scored_at` is unchanged, so design rows still equal the replay's
    path, the last day included. Rows that do not say when they were scored, or which day they belong to, are
    refused."""
    _require_scored(window)
    fit_clock = pd.Timestamp(fit_clock)
    days = window["day"]
    replay_scores = days.map({d: score_clock(d, _NO_DATA_END) for d in pd.unique(days)})
    return window[(window["scored_at"] <= fit_clock) & (replay_scores <= fit_clock)]


def _require_scored(rows: pd.DataFrame) -> None:
    if "scored_at" not in rows or rows["scored_at"].isna().any():
        raise LookaheadError("out-of-sample rows without the time they were scored cannot calibrate a half-width")
    if "day" not in rows or rows["day"].isna().any():
        raise LookaheadError("out-of-sample rows without their day cannot show the replay would have scored them by "
                             "the fit clock: they calibrate no half-width")


def fit_clock_of(fold: str, issue: IssueSpec = MIDNIGHT) -> pd.Timestamp:
    """The fit clock of a monthly fold (walkforward.Fold.fit_clock): the origin of its first test day at the issue
    time given, D 05:00 UTC for v2, the eve at 17:00 UTC at noon (docs/plan-v3.md, section 6)."""
    return month_folds(fold, fold, issue=issue)[0].fit_clock


def bin_of(temps, edge: float) -> np.ndarray:
    """Each hour's interval bin against an edge (docs/plan-v3.md, section 7): "cold" strictly below it, "rest" at or
    above it, None without a finite temperature."""
    t = np.asarray(temps, dtype=float)
    return np.where(~np.isfinite(t), None, np.where(t < float(edge), "cold", "rest"))


def binned_half_widths(scores, temps, days, edge: float, alpha: float = ALPHA) -> dict:
    """The two relative half-widths of a calibration (docs/plan-v3.md, section 7): its finite scores binned by each
    one's own noon-feed temperature against `edge`, the edge of the fit being calibrated (`bin_of`; a score keeps no
    bin from the fold that scored it), each bin's conformal quantile (`conformal_quantile`).

    The cold bin falls back to the pooled half-width of every finite score when it holds fewer than MIN_COLD_HOURS
    scores or scores from fewer than MIN_COLD_WEEKS Monday-to-Sunday weeks (`week_of` of `days`, each score's day), as
    v2's cold tail; the fallback is recorded. Returns bin_edge, q_cold, q_rest, q_pooled, cold_n, cold_weeks and
    fallback, the interval layer a fit stores and `check_interval_layer` checks."""
    edge = float(edge)
    if not np.isfinite(edge):
        raise ValueError(f"a bin edge must be finite, got {edge}")
    s = np.asarray(scores, dtype=float).ravel()
    t = np.asarray(temps, dtype=float).ravel()
    d = pd.Series(pd.to_datetime(np.asarray(days).ravel()))
    if not (len(s) == len(t) == len(d)):
        raise ValueError(f"{len(s)} scores, {len(t)} temperatures and {len(d)} days: one of each per score")
    scored = np.isfinite(s)
    b = bin_of(t, edge)
    cold, rest = scored & (b == "cold"), scored & (b == "rest")
    cold_n = int(cold.sum())
    cold_weeks = int(week_of(d[cold]).nunique()) if cold_n else 0
    fallback = bool(cold_n < MIN_COLD_HOURS or cold_weeks < MIN_COLD_WEEKS)
    q_pooled = conformal_quantile(s, alpha)
    return {"bin_edge": edge, "q_cold": q_pooled if fallback else conformal_quantile(s[cold], alpha),
            "q_rest": conformal_quantile(s[rest], alpha), "q_pooled": q_pooled, "cold_n": cold_n,
            "cold_weeks": cold_weeks, "fallback": fallback}


def check_interval_layer(layer: dict) -> dict:
    """Refuse (ValueError) an interval layer (`binned_half_widths`) that does not follow its rule: every half-width and
    the edge finite, the counts whole, the fallback recorded exactly when the cold bin holds fewer than MIN_COLD_HOURS
    scores or MIN_COLD_WEEKS weeks, and a cold bin that fell back holding the pooled half-width. Returns it with its
    numbers as floats, its counts as ints and its fallback as a bool, in one key order: what a frozen model stores and
    hashes (docs/plan-v3.md, section 4.4)."""
    keys = ("bin_edge", "q_cold", "q_rest", "q_pooled", "cold_n", "cold_weeks", "fallback")
    if not isinstance(layer, dict) or set(layer) != set(keys):
        raise ValueError(f"an interval layer holds exactly {list(keys)}, got {layer!r}")
    out = {}
    for k in keys[:4]:
        v = layer[k]
        if isinstance(v, bool) or not isinstance(v, (int, float, np.integer, np.floating)) or not np.isfinite(v):
            raise ValueError(f"interval layer {k}={v!r} is not a finite number")
        out[k] = float(v)
    for k in ("cold_n", "cold_weeks"):
        v = layer[k]
        if isinstance(v, bool) or not isinstance(v, (int, np.integer)) or v < 0:
            raise ValueError(f"interval layer {k}={v!r} is not a whole count")
        out[k] = int(v)
    if not isinstance(layer["fallback"], (bool, np.bool_)):
        raise ValueError(f"interval layer fallback={layer['fallback']!r} is not a boolean")
    out["fallback"] = bool(layer["fallback"])
    rule = out["cold_n"] < MIN_COLD_HOURS or out["cold_weeks"] < MIN_COLD_WEEKS
    if out["fallback"] != rule:
        raise ValueError(f"interval layer fallback={out['fallback']} with {out['cold_n']} cold scores in "
                         f"{out['cold_weeks']} weeks: the rule gives {rule}")
    if out["fallback"] and out["q_cold"] != out["q_pooled"]:
        raise ValueError(f"a cold bin that fell back holds the pooled half-width {out['q_pooled']!r}, not "
                         f"{out['q_cold']!r}")
    return out


def relative_conformal(out: pd.DataFrame, pred_col: str, alpha: float = ALPHA,
                       calibration_folds: int = CALIBRATION_FOLDS, absolute: bool = False,
                       groups: pd.Series | None = None, *, issue: IssueSpec = MIDNIGHT,
                       bins: tuple[str, dict] | None = None) -> pd.DataFrame:
    """Interval for each fold from the out-of-sample scores of the previous `calibration_folds` folds only, those
    scored by the fold's fit clock (`calibration_rows`), the origin of its first test day at `issue` (`fit_clock_of`;
    v2's, MIDNIGHT, unless given).

    Each row needs its `scored_at` and its `day`, which pass through to `calibration_rows`. Relative score
    |y - f| / f (half-width q * f) by default; absolute score |y - f| (half-width q) as sensitivity.
    With `groups` (e.g. the season of each hour), the quantile is taken within the row's own group (Mondrian).

    With `bins` = (temperature column, {fold: bin edge}), v3's intervals by temperature (docs/plan-v3.md, section 7):
    each fold bins its own calibration window, and its own hours, by each hour's temperature against the fold's own
    edge, the edge of the fit being calibrated, never the edge of the fold that scored the hour
    (`binned_half_widths`, with its fallback); the output adds each hour's bin, the edge and the layer (q_cold,
    q_rest, q_pooled, cold_n, cold_weeks, fallback), q being the bin's half-width. Relative scores only, no groups."""
    _require_scored(out)
    if bins is not None and (groups is not None or absolute):
        raise ValueError("binned intervals take relative scores and no groups")
    temp_col, edges = bins if bins is not None else (None, None)
    cols = ["target", pred_col, "fold", "scored_at", "day"] + ([temp_col] if temp_col else [])
    d = out[cols].copy()
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
        window = calibration_rows(d[d["fold"].isin(folds[i - calibration_folds:i])], fit_clock_of(fold, issue))
        if bins is not None:
            if fold not in edges:
                raise ValueError(f"fold {fold} has no bin edge")
            part = d[d["fold"] == fold]
            layer = binned_half_widths(window["score"], window[temp_col], window["day"], edges[fold], alpha)
            b = bin_of(part[temp_col], layer["bin_edge"])
            q = np.where(b == "cold", layer["q_cold"], np.where(b == "rest", layer["q_rest"], np.nan))
            half = q * part[pred_col]
            rows.append(pd.DataFrame({"fold": fold, "q": q, "lower": part[pred_col] - half,
                                      "upper": part[pred_col] + half,
                                      "calibration_n": int(np.isfinite(window["score"].to_numpy()).sum()),
                                      "bin": pd.Series(b, index=part.index, dtype=object), "edge_c": layer["bin_edge"],
                                      **{k: layer[k] for k in ("q_cold", "q_rest", "q_pooled", "cold_n",
                                                               "cold_weeks", "fallback")}}, index=part.index))
            continue
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
    scored = y.notna() & out.loc[iv.index, pred_col].notna()
    if bins is not None:
        # An hour without a bin (no temperature) has no half-width: its coverage is missing, never a miss. v2's path
        # keeps v2's mask: an undefined bound (inf * 0) counts as uncovered, as before.
        scored &= iv["lower"].notna()
    iv["covered"] = inside.where(scored)
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


# ---------------------------------------------------------------------------------------------- the decision rule
#
# What counts as v2 being better than v1 (`docs/plan-v2.md`, section 8), the model card's status (section 8.5) and the
# evaluation label of a v2 replay (section 10.5): one place, in a rule file, frozen with the rest. All pure.

# COLD_QUANTILE (model.py, 0.05): cold_c is this quantile of the forecast temperatures of the design hours (inputs only)
MIN_COLD_HOURS = 120  # below it, or below MIN_COLD_WEEKS, the cold tail is not testable on that year, never passed
MIN_COLD_WEEKS = 4
PEAK_HOURS = 5  # the busiest hours of the year, reported beside the verdicts and never judged
PRINT_DECIMALS_PP = 2  # the bounds are judged as printed: two decimals of a percentage point
ADDITIVE_TOLERANCE = 1e-12
STATES = ("better", "not_separated", "worse")
VERDICTS = ("better", "better_cold_untested", "not_shown_better", "worse")
STATUSES = ("candidate", "confirmed", "input_only", "not_confirmed", "post_hoc", "unfinished")
STANDINGS = ("clean", "post_hoc")  # the two a 2025 start can have (plan section 11.3.5)
_BETTER = ("better", "better_cold_untested")  # "better on average, the cold tail not testable" counts as better

# The three forecasts compared (plan section 8.1): v2, v1's form on the forecast, v1 as shipped.
_V2, _FAMILY, _SHIPPED = PRODUCTION, COMPARATORS["family"], COMPARATORS["shipped"]
_COMPARED = (_V2, _FAMILY, _SHIPPED)


def cold_threshold(temps: pd.Series) -> float:
    """cold_c: the COLD_QUANTILE quantile of the finite forecast temperatures given (inputs only, never the outcome)."""
    t = np.asarray(temps, dtype=float)
    t = t[np.isfinite(t)]
    if t.size == 0:
        raise ValueError("no finite forecast temperature to take the cold threshold on")
    return float(np.quantile(t, COLD_QUANTILE))


def common_hours(frame: pd.DataFrame, cols) -> pd.Index:
    """The hours with a finite target and every one of `cols` finite: the one set a comparison is scored on."""
    ok = np.isfinite(frame["target"].to_numpy(dtype=float))
    for c in cols:
        ok &= np.isfinite(frame[c].to_numpy(dtype=float))
    return frame.index[ok]


def state(d: dict) -> str:
    """The state of a paired difference (a minus b; below zero, a has the lower error), judged on its bounds as printed,
    in percentage points to PRINT_DECIMALS_PP decimals, so no printed number contradicts its state."""
    low, high = float(d["ci95_low"]), float(d["ci95_high"])
    if not (np.isfinite(low) and np.isfinite(high)):
        raise ValueError(f"a contrast's interval must be finite to have a state, got [{low}, {high}]")
    if round(100 * high, PRINT_DECIMALS_PP) < 0:
        return "better"
    if round(100 * low, PRINT_DECIMALS_PP) > 0:
        return "worse"
    return "not_separated"


def _ape(frame: pd.DataFrame, hours: pd.Index, key: str) -> pd.DataFrame:
    """The hourly APE of one model on exactly `hours`, as `errors` computes it, with each hour's week."""
    target = frame.loc[hours, "target"].astype(float)
    pred = frame.loc[hours, f"pred_{key}"].astype(float)
    return pd.DataFrame({"ape": hourly_ape(target, pred), "week": week_of(frame.loc[hours, "day"])}, index=hours)


def _contrast(frame: pd.DataFrame, hours: pd.Index, a: str, b: str, n_boot: int, seed: int) -> dict:
    """APE of `a` minus APE of `b` on exactly `hours`, with its state."""
    d = paired_difference(_ape(frame, hours, a), _ape(frame, hours, b), "ape", n_boot, seed)
    if d["hours"] != len(hours):
        raise ValueError(f"the contrast {a} - {b} was scored on {d['hours']} hours, not the {len(hours)} common ones")
    return d | {"state": state(d)}


def _reason(frame: pd.DataFrame, hour, key: str) -> str:
    why = frame.at[hour, f"why_{key}"] if f"why_{key}" in frame else None
    return why if isinstance(why, str) and why else "no forecast"


def _peak(frame: pd.DataFrame, keys=None, temp_col: str = "temp_forecast") -> list[dict]:
    """The PEAK_HOURS busiest hours of the whole frame by target, among every hour with a finite target, whatever
    forecasts exist; a missing forecast is NaN with its reason. The forecasts of `keys` (v2's three unless given) and
    the temperature of `temp_col` are shown."""
    y = frame["target"].astype(float)
    rows = []
    for hour in y[np.isfinite(y)].nlargest(PEAK_HOURS).index:
        row = {"utc": pd.Timestamp(hour).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
               "target": float(frame.at[hour, "target"]), temp_col: float(frame.at[hour, temp_col])}
        missing = {}
        for key in (_COMPARED if keys is None else keys):
            value = float(frame.at[hour, f"pred_{key}"])
            row[f"pred_{key}"] = value if np.isfinite(value) else float("nan")
            if not np.isfinite(value):
                missing[key] = _reason(frame, hour, key)
        row["missing"] = missing
        rows.append(row)
    return rows


def contrasts(frame: pd.DataFrame, cold_c: float, n_boot: int = N_BOOT, seed: int = 0) -> dict:
    """The three contrasts of plan section 8.1 on the common hours, the cold tail (section 8.2) and the busiest hours.

    frame: index hour-ending UTC; columns target, day, temp_forecast, pred_gam_forecast, pred_forecast,
    pred_persistence and, when the caller has them, why_<key> (the reason a model has no forecast for the hour).
    Each contrast is the paired difference in hourly absolute percentage error (week-block bootstrap), on the hours
    where the target and all three forecasts exist: shipped = v2 - v1 as shipped, family = v2 - v1's form on the
    forecast, input = v1's form on the forecast - v1 as shipped (reported, never judged); on one set of hours they are
    additive. The cold hours are the common hours whose forecast temperature lies below `cold_c`."""
    cold_c = float(cold_c)
    if not np.isfinite(cold_c):
        raise ValueError(f"the cold threshold must be finite, got {cold_c}")
    hours = common_hours(frame, [f"pred_{k}" for k in _COMPARED])
    if len(hours) == 0:
        raise ValueError("no hour has a target and all three forecasts: nothing to compare")
    shipped = _contrast(frame, hours, _V2, _SHIPPED, n_boot, seed)
    family = _contrast(frame, hours, _V2, _FAMILY, n_boot, seed)
    input_ = _contrast(frame, hours, _FAMILY, _SHIPPED, n_boot, seed)
    additive = shipped["difference"] - family["difference"] - input_["difference"]
    if not abs(additive) < ADDITIVE_TOLERANCE:
        raise ValueError(f"the contrasts are not additive on one set of hours (shipped - family - input = {additive})")

    temp = frame.loc[hours, "temp_forecast"].to_numpy(dtype=float)
    cold = hours[temp < cold_c]
    cold_weeks = int(week_of(frame.loc[cold, "day"]).nunique()) if len(cold) else 0
    testable = bool(len(cold) >= MIN_COLD_HOURS and cold_weeks >= MIN_COLD_WEEKS)
    tail = {"threshold_c": cold_c, "hours": int(len(cold)), "weeks": cold_weeks, "testable": testable,
            "shipped": _contrast(frame, cold, _V2, _SHIPPED, n_boot, seed) if len(cold) else None,
            "family": _contrast(frame, cold, _V2, _FAMILY, n_boot, seed) if len(cold) else None}
    return {"hours": int(len(hours)), "weeks": int(week_of(frame.loc[hours, "day"]).nunique()),
            "shipped": shipped, "family": family, "input": input_, "cold": tail, "peak": _peak(frame),
            "additive_check": float(additive)}


def _state_of(d) -> str:
    s = d["state"]
    if s not in STATES:
        raise ValueError(f"unknown state {s!r}")
    return s


def _verdict(c: dict, x: str) -> str:
    s = _state_of(c[x])
    if s == "worse":
        return "worse"
    if s == "not_separated":
        return "not_shown_better"
    if not c["cold"]["testable"]:
        return "better_cold_untested"
    return "not_shown_better" if _state_of(c["cold"][x]) == "worse" else "better"


def verdict(c: dict) -> dict:
    """The verdict of each judged comparison (plan section 8.3), each from its own contrast and cold tail only:
    better (the contrast is better and its cold tail is not worse), better_cold_untested (better, the cold tail not
    testable), not_shown_better (not separated, or better with a worse cold tail), worse."""
    return {"shipped": _verdict(c, "shipped"), "family": _verdict(c, "family")}


def status(test_of_record: dict | None) -> str:
    """The model card's status (plan section 8.5), from the 2025 test of record only, never a later rerun.

    test_of_record: None when no 2025 replay has started; otherwise {"finished": bool, "standing": one of STANDINGS,
    "verdict": verdict() of its replay, or None when it did not finish}. Whether it finished is read first: one that
    did not finish is unfinished whatever its standing, a start that stopped before its standing was logged included;
    a finished one must have its standing."""
    if test_of_record is None:
        return "candidate"
    finished = test_of_record.get("finished")
    if not isinstance(finished, (bool, np.bool_)):
        raise ValueError(f"the test of record must say whether it finished, got {finished!r}")
    if not finished:
        return "unfinished"
    standing = test_of_record.get("standing")
    if standing not in STANDINGS:
        raise ValueError(f"the test of record's standing must be one of {STANDINGS}, got {standing!r}")
    if standing == "post_hoc":
        return "post_hoc"
    v = test_of_record.get("verdict")
    if not isinstance(v, dict) or v.get("shipped") not in VERDICTS or v.get("family") not in VERDICTS:
        raise ValueError(f"a finished clean test of record needs both verdicts, got {v!r}")
    if v["shipped"] in _BETTER:
        return "confirmed" if v["family"] in _BETTER else "input_only"
    return "not_confirmed"


def evaluation_label(year: int, earlier_starts: list, gate: dict | None = None) -> str:
    """The evaluation label of a v2 replay (plan section 10.5), the one function that computes it.

    A year config.EVALUATION does not label held out (2023, the rehearsal; 2024, seen by the developers) takes its
    label from there. For the held-out year (2025): earlier_starts is every 2025 start before this one, finished or
    not, under any freeze commit, each with "started_utc" and "finished"; gate is this start's gate record ("causes",
    a list of "a" and "f"; "ruling", None or a dict whose "start" is the started_utc of the stopped start it names, ""
    when none; "standing", one of STANDINGS). This start is the test of record when no start came before it, or when
    none of them finished and the gate applied the owner's ruling naming the latest of them; the test of record is
    held_out when its standing is clean and post_hoc when it is post_hoc, whatever the cause. After a finished start,
    a start is a rerun: seen_by_developers. A start after stopped ones without such a ruling cannot have opened, and
    is refused (plan section 11.3.5)."""
    try:
        label = config.EVALUATION[year]
    except KeyError:
        raise ValueError(f"no evaluation label for {year}") from None
    if label != "held_out":
        return label
    starts = list(earlier_starts)
    for s in starts:
        if not isinstance(s.get("started_utc"), str) or not isinstance(s.get("finished"), (bool, np.bool_)):
            raise ValueError(f"an earlier start needs its started_utc and whether it finished, got {s!r}")
    if any(s["finished"] for s in starts):
        return "seen_by_developers"
    if starts:
        latest = max(s["started_utc"] for s in starts)
        ruling = gate.get("ruling") if gate is not None else None
        causes = (gate.get("causes") or []) if gate is not None else []
        if not ("f" in causes and isinstance(ruling, dict) and ruling.get("start") == latest):
            raise ValueError(f"a {year} start after the stopped start of {latest} needs the owner's ruling naming it "
                             f"in its gate's record: such a start cannot have opened")
    if gate is None:
        raise ValueError(f"the {year} test of record needs its gate's record to be labelled")
    standing = gate.get("standing")
    if standing not in STANDINGS:
        raise ValueError(f"the {year} test of record's standing must be one of {STANDINGS}, got {standing!r}")
    return "held_out" if standing == "clean" else "post_hoc"


# ------------------------------------------------------------------------------------------- v3's decision rule
#
# What counts as the terms, and the family, being better at noon (`docs/plan-v3.md`, section 8), the model card's
# status and the recommendation (section 8.5): one place, in a rule file, frozen with the rest, beside v2's. All pure.
# The bootstrap, the states, the cold tail's minimums and v2's verdict are v2's, reused.

# Hydro-Québec's Rate Flex D peak hours (section 2): the hours starting at 06:00 to 09:00 and 16:00 to 19:00 local
# clock time, daylight time included (ending at 07:00 to 10:00 and 17:00 to 20:00), from December 1 to March 31, less
# the excluded days that fall inside the winter: December 24, 25, 26 and 31, January 1 and 2, Good Friday, Easter
# Monday.
WINDOWS = {"morning": (6, 7, 8, 9), "evening": (16, 17, 18, 19)}  # the local hour each window's hours start in
EVENT_HOURS = WINDOWS["morning"] + WINDOWS["evening"]
WINTER_MONTHS = (12, 1, 2, 3)
EXCLUDED_FIXED = ((12, 24), (12, 25), (12, 26), (12, 31), (1, 1), (1, 2))
RATE_EVENTS = 30  # at most 120 event hours a winter, 4 hours each (art. 2.70)
TERMS_VERDICTS = ("better", "better_windows_cold_untested", "not_shown_better", "worse", "not_testable")
TERMS_BETTER = ("better", "better_windows_cold_untested")  # the terms' "not testable" counts as not better
STATUSES_V3 = ("candidate", "both_better", "terms_only", "family_only", "neither", "post_hoc", "unfinished")
RECOMMENDATIONS = ("review_family", "keep_v2_noon")  # in this order when both: the family first
SUBSETS = ("all", "cold", "event_windows")

# The four forecasts (section 8.1): v3, v2's form at noon, v1's form at noon, and v2's form at midnight.
_V3, _V2_NOON, _V1_NOON = V3_PRODUCTION, V3_COMPARATORS["terms"], V3_COMPARATORS["family"]
_V2_MIDNIGHT = V3_COMPARATORS["horizon"]
_V3_PAIRS = {"terms": (_V3, _V2_NOON), "family": (_V2_NOON, _V1_NOON), "whole": (_V3, _V1_NOON)}
_HOUR = pd.Timedelta(hours=1)


def winter_of(day) -> int | None:
    """The winter a date belongs to, named by the year of its December (2023 for 2023-12-01 to 2024-03-31); None
    outside December to March."""
    d = pd.Timestamp(day)
    return d.year if d.month == 12 else d.year - 1 if d.month in WINTER_MONTHS else None


def easter_sunday(year: int) -> pd.Timestamp:
    """Easter Sunday of a Gregorian year, by the anonymous Gregorian algorithm (Meeus/Jones/Butcher): no calendar
    dependency."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    g = (b - (b + 8) // 25 + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month, day = divmod(h + l_ - 7 * m + 114, 31)
    return pd.Timestamp(year, month, day + 1)


def excluded_days(winter: int) -> frozenset:
    """The excluded days of a winter that fall inside it (section 2): the six fixed dates, and Good Friday and Easter
    Monday of the next year's Easter, each only when its own date is on or before March 31 (date membership,
    docs/plan-v3.md section 18: a winter can count Good Friday and not Easter Monday)."""
    fixed = [pd.Timestamp(winter if m == 12 else winter + 1, m, d) for m, d in EXCLUDED_FIXED]
    sunday = easter_sunday(winter + 1)
    movable = [sunday - pd.Timedelta(days=2), sunday + pd.Timedelta(days=1)]
    return frozenset(fixed + [d for d in movable if winter_of(d) == winter])


def winter_days(winter: int) -> pd.DatetimeIndex:
    """The days from December 1 to March 31 of a winter, a leap day included, less its excluded days."""
    days = pd.date_range(pd.Timestamp(winter, 12, 1), pd.Timestamp(winter + 1, 3, 31), freq="D")
    return days[~days.isin(list(excluded_days(winter)))]


def winter_windows(winter: int) -> int:
    """W: twice the winter's days that are not excluded days (`winter_days`): its morning and evening windows."""
    return 2 * len(winter_days(winter))


def windows_k(w: int, W: int) -> int:
    """k = floor(RATE_EVENTS * w / W + 0.5), at most w (section 8.3): the windows a part of a winter ranks, in exact
    integer arithmetic so that a half rounds up."""
    w, W = int(w), int(W)
    if W <= 0 or w < 0:
        raise ValueError(f"windows need W > 0 and w >= 0, got w={w}, W={W}")
    return min(w, (2 * RATE_EVENTS * w + W) // (2 * W))


def event_windows(index: pd.DatetimeIndex) -> pd.DataFrame:
    """For each hour-ending UTC label, the local date and the hour its hour starts in (as features.build's `hour`
    column, daylight time included), and its event window: "morning" or "evening" inside the rate's peak hours on a
    winter day that is not excluded (section 8.2), else None. Hours starting 06:00 to 09:00 and 16:00 to 19:00 never
    straddle a local date, so the window's date is its hours' local date."""
    idx = pd.DatetimeIndex(index)
    if idx.tz is None:
        raise ValueError("event windows need hour-ending UTC labels, tz-aware")
    start = (idx - _HOUR).tz_convert(LOCAL_TZ)
    date = pd.DatetimeIndex(start.tz_localize(None).normalize())
    hour = np.asarray(start.hour)
    winters = {y: excluded_days(y) for y in {winter_of(d) for d in pd.unique(date)} if y is not None}
    in_winter = np.array([(w := winter_of(d)) is not None and d not in winters[w] for d in date], dtype=bool)
    window = np.full(len(idx), None, dtype=object)
    for name, hours in WINDOWS.items():
        window[in_winter & np.isin(hour, hours)] = name
    return pd.DataFrame({"date": date, "hour": hour, "window": pd.Series(window, index=index, dtype=object)},
                        index=index)


def event_window_mask(index: pd.DatetimeIndex) -> np.ndarray:
    """The hours inside the rate's peak hours (`event_windows`)."""
    return event_windows(index)["window"].notna().to_numpy()


def _subset(frame: pd.DataFrame, hours: pd.Index, judged: bool = True) -> dict:
    """A subset's size, in hours and Monday-to-Sunday weeks, and whether it is testable (at least MIN_COLD_HOURS hours
    in at least MIN_COLD_WEEKS weeks, v2's cold-tail rule), for the subsets so ruled."""
    weeks = int(week_of(frame.loc[hours, "day"]).nunique()) if len(hours) else 0
    out = {"hours": int(len(hours)), "weeks": weeks}
    if judged:
        out["testable"] = bool(len(hours) >= MIN_COLD_HOURS and weeks >= MIN_COLD_WEEKS)
    return out


def _subsets(frame: pd.DataFrame, hours: pd.Index, cold_c: float) -> dict:
    """The three subsets of section 8.2 within `hours`: all of them; the cold tail, whose noon-feed temperature
    (`temp_forecast`) lies strictly below cold_c; the event windows (`event_window_mask`)."""
    temp = frame.loc[hours, "temp_forecast"].to_numpy(dtype=float)
    return {"all": hours, "cold": hours[temp < cold_c], "event_windows": hours[event_window_mask(hours)]}


def contrasts_v3(frame: pd.DataFrame, cold_c: float, n_boot: int = N_BOOT, seed: int = 0) -> dict:
    """The contrasts of docs/plan-v3.md section 8.1 on each subset of section 8.2, and the busiest hours.

    frame: index hour-ending UTC; columns target, day, temp_forecast (the noon feed's temperature, from the noon
    table), pred_v3, pred_v2_noon, pred_v1_noon, pred_v2_midnight and, when the caller has them, why_<key>. Each
    contrast is v2's: the paired difference in hourly APE with its week-block bootstrap and state (`_contrast`).

    - On the common set (a target and the three noon forecasts): terms = v3 - v2_noon, family = v2_noon - v1_noon and
      whole = v3 - v1_noon, on all common hours, the cold tail and the event windows; on each set terms + family =
      whole (`additive_check`; reported, never judged). A subset without hours has no contrast (None).
    - horizon = v2_noon - v2_midnight on its own set, the hours with a target and both ("all"), and on each subset of
      section 8.2 intersected with those hours: the common set's cold tail and event windows, each kept to the hours
      the horizon has (section 8.1). An hour the horizon has and the common set lacks (no v1-noon forecast) enters the
      horizon's "all" only, never its cold tail or its event windows. Reported, never judged.
    - peak: the busiest hours (v2's `_peak`) with all four forecasts and the noon feed's temperature."""
    cold_c = float(cold_c)
    if not np.isfinite(cold_c):
        raise ValueError(f"the cold threshold must be finite, got {cold_c}")
    hours = common_hours(frame, [f"pred_{k}" for k in NOON_KEYS])
    if len(hours) == 0:
        raise ValueError("no hour has a target and all three noon forecasts: nothing to compare")
    subsets = _subsets(frame, hours, cold_c)
    out = {"hours": int(len(hours)), "weeks": int(week_of(frame.loc[hours, "day"]).nunique()), "cold_c": cold_c,
           "subsets": {k: _subset(frame, h, judged=k != "all") for k, h in subsets.items()},
           "additive_check": {}}
    for name, (a, b) in _V3_PAIRS.items():
        out[name] = {k: _contrast(frame, h, a, b, n_boot, seed) if len(h) else None for k, h in subsets.items()}
    for k, h in subsets.items():
        if not len(h):
            continue
        additive = out["terms"][k]["difference"] + out["family"][k]["difference"] - out["whole"][k]["difference"]
        if not abs(additive) < ADDITIVE_TOLERANCE:
            raise ValueError(f"the contrasts are not additive on the {k} hours (terms + family - whole = {additive})")
        out["additive_check"][k] = float(additive)
    hz = common_hours(frame, [f"pred_{_V2_NOON}", f"pred_{_V2_MIDNIGHT}"])
    # Section 8.1: the subsets of section 8.2 (of the common hours) intersected with the horizon's hours, in the
    # frame's order; and the horizon's own set whole.
    hz_subsets = {"all": hz, **{k: h[h.isin(hz)] for k, h in subsets.items() if k != "all"}}
    out["horizon"] = {"subsets": {k: _subset(frame, h, judged=k != "all") for k, h in hz_subsets.items()},
                      "contrast": {k: _contrast(frame, h, _V2_NOON, _V2_MIDNIGHT, n_boot, seed) if len(h) else None
                                   for k, h in hz_subsets.items()}}
    out["peak"] = _peak(frame, keys=(*NOON_KEYS, _V2_MIDNIGHT))
    return out


def terms_verdict(c: dict) -> str:
    """The terms verdict (section 8.3), from contrasts_v3, checked in this order: the event windows not testable ->
    not_testable; their contrast worse -> worse; not separated -> not_shown_better; better, but the all-hours
    contrast worse, or the cold tail testable and its contrast worse -> not_shown_better (a gain in the windows paid
    for elsewhere); better, the cold tail not testable -> better_windows_cold_untested; else better."""
    if not c["subsets"]["event_windows"]["testable"]:
        return "not_testable"
    s = _state_of(c["terms"]["event_windows"])
    if s == "worse":
        return "worse"
    if s == "not_separated":
        return "not_shown_better"
    cold_testable = c["subsets"]["cold"]["testable"]
    if _state_of(c["terms"]["all"]) == "worse" or (cold_testable and _state_of(c["terms"]["cold"]) == "worse"):
        return "not_shown_better"
    return "better" if cold_testable else "better_windows_cold_untested"


def family_verdict(c: dict) -> str:
    """The family verdict (section 8.3): v2's rule (`_verdict`) on the family contrast at noon, all common hours
    deciding and the cold tail guarding; its states are v2's VERDICTS."""
    return _verdict({"family": c["family"]["all"],
                     "cold": {"testable": c["subsets"]["cold"]["testable"], "family": c["family"]["cold"]}}, "family")


def verdict_v3(c: dict) -> dict:
    """The two verdicts of section 8.3, each from its own contrasts only."""
    return {"terms": terms_verdict(c), "family": family_verdict(c)}


def _window_key(day: pd.Timestamp, window: str) -> str:
    return f"{pd.Timestamp(day).strftime('%Y-%m-%d')} {window}"


def _ranked(table: pd.DataFrame, col: str) -> list[str]:
    """Windows by `col`, highest first, ties by the earlier date, then the morning before the evening."""
    order = table.assign(_v=-table[col], _m=(table["window"] != "morning").astype(int))
    return list(order.sort_values(["_v", "date", "_m"], kind="mergesort")["key"])


def rank_windows(frame: pd.DataFrame, first_day, last_day, keys=NOON_KEYS) -> list[dict]:
    """The window ranking of section 8.3, for one scored period, its days `first_day` to `last_day` (local dates).

    Each winter's part inside the period is ranked on its own, never joined to another period's days. A part's
    windows are the morning and evening windows (`event_windows`) of each of its days that is not excluded; a window
    is eligible when its four hours are in the frame with a finite target and a finite forecast of each key, and the
    others are listed with their reason. The w eligible windows are ranked by mean actual demand and by each key's
    mean forecast (`_ranked`); k = windows_k(w, W), W = winter_windows of the whole winter. For each key: how many of
    its k highest are among the k highest by actual demand. v2's form at midnight is not ranked: it is issued after
    the notice."""
    first, last = pd.Timestamp(first_day).normalize(), pd.Timestamp(last_day).normalize()
    ew = event_windows(frame.index)
    ew = ew[ew["window"].notna() & (ew["date"] >= first) & (ew["date"] <= last)]
    parts = []
    for winter in sorted({winter_of(d) for d in pd.date_range(first, last, freq="D")} - {None}):
        part_first = max(first, pd.Timestamp(winter, 12, 1))
        part_last = min(last, pd.Timestamp(winter + 1, 3, 31))
        days = winter_days(winter)
        days = days[(days >= part_first) & (days <= part_last)]
        rows, ineligible = [], []
        for day in days:
            for window, hours in WINDOWS.items():
                labels = ew.index[(ew["date"] == day) & (ew["window"] == window)]
                reason = None
                if len(labels) != len(hours):
                    reason = f"{len(labels)} of its {len(hours)} hours in the period's data"
                elif not np.isfinite(frame.loc[labels, "target"].to_numpy(dtype=float)).all():
                    reason = "no target"
                else:
                    for key in keys:
                        if not np.isfinite(frame.loc[labels, f"pred_{key}"].to_numpy(dtype=float)).all():
                            reason = f"no forecast from {key}"
                            break
                if reason is not None:
                    ineligible.append({"day": day.strftime("%Y-%m-%d"), "window": window, "reason": reason})
                    continue
                row = {"key": _window_key(day, window), "date": day, "window": window,
                       "actual": float(frame.loc[labels, "target"].astype(float).mean())}
                row |= {key: float(frame.loc[labels, f"pred_{key}"].astype(float).mean()) for key in keys}
                rows.append(row)
        table = pd.DataFrame(rows, columns=["key", "date", "window", "actual", *keys])
        W, w = winter_windows(winter), len(table)
        k = windows_k(w, W)
        top = {"actual": _ranked(table, "actual")[:k]} | {key: _ranked(table, key)[:k] for key in keys}
        parts.append({"winter": f"{winter}-{str(winter + 1)[-2:]}", "first_day": part_first.strftime("%Y-%m-%d"),
                      "last_day": part_last.strftime("%Y-%m-%d"), "W": W, "w": w, "k": k,
                      "overlap": {key: len(set(top[key]) & set(top["actual"])) for key in keys}, "top": top,
                      "ineligible": ineligible})
    return parts


def judge_v3(frame: pd.DataFrame, cold_c: float, first_day, last_day, n_boot: int = N_BOOT, seed: int = 0) -> dict:
    """The one function of section 8.3: the contrasts (`contrasts_v3`), their verdicts (`verdict_v3`) and the window
    ranking of the scored period (`rank_windows`)."""
    c = contrasts_v3(frame, cold_c, n_boot, seed)
    return {"contrasts": c, "verdict": verdict_v3(c), "ranking": rank_windows(frame, first_day, last_day)}


def status_v3(test_of_record: dict | None) -> str:
    """The model card's status (docs/plan-v3.md, section 8.5), from v3's 2025 test of record only, as v2's `status`:
    candidate before it starts, unfinished when it did not finish, post_hoc when its standing is; from a finished
    clean one, both_better, terms_only, family_only or neither by its two verdicts, "better in the event windows, the
    cold tail not testable" and the family's "better on average, the cold tail not testable" counting as better and the
    terms' "not testable" as not. test_of_record: {"finished", "standing", "verdict": verdict_v3() or None}."""
    if test_of_record is None:
        return "candidate"
    finished = test_of_record.get("finished")
    if not isinstance(finished, (bool, np.bool_)):
        raise ValueError(f"the test of record must say whether it finished, got {finished!r}")
    if not finished:
        return "unfinished"
    standing = test_of_record.get("standing")
    if standing not in STANDINGS:
        raise ValueError(f"the test of record's standing must be one of {STANDINGS}, got {standing!r}")
    if standing == "post_hoc":
        return "post_hoc"
    v = test_of_record.get("verdict")
    if not isinstance(v, dict) or v.get("terms") not in TERMS_VERDICTS or v.get("family") not in VERDICTS:
        raise ValueError(f"a finished clean test of record needs both verdicts, got {v!r}")
    terms, family = v["terms"] in TERMS_BETTER, v["family"] in _BETTER
    return {(True, True): "both_better", (True, False): "terms_only", (False, True): "family_only",
            (False, False): "neither"}[(terms, family)]


def recommendation_v3(test_of_record: dict | None) -> list[str]:
    """What the report recommends, in the monitor's form (section 8.5), from v3's status (`status_v3`): only a
    finished clean test of record recommends; review_family when the family verdict is not better, keep_v2_noon (the
    noon forecast without the terms) when the terms verdict is not, both in that order. Nothing switches on its own."""
    st = status_v3(test_of_record)
    return {"terms_only": ["review_family"], "family_only": ["keep_v2_noon"],
            "neither": ["review_family", "keep_v2_noon"]}.get(st, [])
