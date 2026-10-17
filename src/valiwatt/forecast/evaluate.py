"""Error metrics with week-block bootstrap intervals, relative-error conformal intervals, and v2's decision rule.

Hours next to each other share weather and demand, so uncertainty is estimated by resampling whole weeks (local
standard time, Monday to Sunday), never single hours.

An interval's half-width is fixed when its model is fitted, so it calibrates only on the out-of-sample rows of the days
the replay would have scored by that fit clock (`calibration_rows`).

The decision rule (what counts as v2 being better than v1, `docs/plan-v2.md` section 8), the model card's status
(section 8.5) and the evaluation label of a v2 replay (section 10.5) are here, in a rule file, frozen with the rest.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from valiwatt import config
from valiwatt.forecast.features import day_origin
from valiwatt.forecast.guards import LookaheadError
from valiwatt.forecast.model import COMPARATORS, PRODUCTION
from valiwatt.forecast.walkforward import folds as month_folds

ALPHA = 0.10  # nominal 90 % intervals
CALIBRATION_FOLDS = 12
N_BOOT = 1000
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
    scores its day, one hour after the day's last hour (`features.day_origin(day + 1 day) + 1 h`). The two differ only
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
    replay_scores = days.map({d: day_origin(pd.Timestamp(d) + pd.Timedelta(days=1)) + pd.Timedelta(hours=1)
                              for d in pd.unique(days)})
    return window[(window["scored_at"] <= fit_clock) & (replay_scores <= fit_clock)]


def _require_scored(rows: pd.DataFrame) -> None:
    if "scored_at" not in rows or rows["scored_at"].isna().any():
        raise LookaheadError("out-of-sample rows without the time they were scored cannot calibrate a half-width")
    if "day" not in rows or rows["day"].isna().any():
        raise LookaheadError("out-of-sample rows without their day cannot show the replay would have scored them by "
                             "the fit clock: they calibrate no half-width")


def relative_conformal(out: pd.DataFrame, pred_col: str, alpha: float = ALPHA,
                       calibration_folds: int = CALIBRATION_FOLDS, absolute: bool = False,
                       groups: pd.Series | None = None) -> pd.DataFrame:
    """Interval for each fold from the out-of-sample scores of the previous `calibration_folds` folds only, those
    scored by the fold's fit clock (`calibration_rows`).

    Each row needs its `scored_at` and its `day`, which pass through to `calibration_rows`. Relative score
    |y - f| / f (half-width q * f) by default; absolute score |y - f| (half-width q) as sensitivity.
    With `groups` (e.g. the season of each hour), the quantile is taken within the row's own group (Mondrian)."""
    _require_scored(out)
    d = out[["target", pred_col, "fold", "scored_at", "day"]].copy()
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
        window = calibration_rows(d[d["fold"].isin(folds[i - calibration_folds:i])],
                                  month_folds(fold, fold)[0].fit_clock)
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


# ---------------------------------------------------------------------------------------------- the decision rule
#
# What counts as v2 being better than v1 (`docs/plan-v2.md`, section 8), the model card's status (section 8.5) and the
# evaluation label of a v2 replay (section 10.5): one place, in a rule file, frozen with the rest. All pure.

COLD_QUANTILE = 0.05  # cold_c: this quantile of the forecast temperatures of the design hours (inputs only)
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


def _peak(frame: pd.DataFrame) -> list[dict]:
    """The PEAK_HOURS busiest hours of the whole frame by target, among every hour with a finite target, whatever
    forecasts exist; a missing forecast is NaN with its reason."""
    y = frame["target"].astype(float)
    rows = []
    for hour in y[np.isfinite(y)].nlargest(PEAK_HOURS).index:
        row = {"utc": pd.Timestamp(hour).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
               "target": float(frame.at[hour, "target"]), "temp_forecast": float(frame.at[hour, "temp_forecast"])}
        missing = {}
        for key in _COMPARED:
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
