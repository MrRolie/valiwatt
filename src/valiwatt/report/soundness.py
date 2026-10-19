"""`valiwatt soundness`: the model's factors interpreted and checked, added after the freeze, deciding nothing.

The owner's ruling of 2026-10-17 (AGENTS.md, "Conceptual soundness, added after the freeze"): the report interprets and
checks each hour's temperature curve, its shape and size, their stability across folds and freezes, the lags, and
sensitivity in the sense of SR 11-7, from the frozen artifacts and the validation runs, with no refit of a frozen model
and no rule change. A failed check is a finding the report states, labelled added after the freeze, never a gate: no
measure here feeds a gate, a threshold, a status, a verdict or a recommendation, and the 2025 verdict stays as
docs/plan-v2.md pre-registers it.

The measures (this module is not a rule file: monitor/rules.py NOT_RULES; it only calls the rule files' functions):

1. Shape. Each hour's curve on a 1 °C grid over its training range: below the heating base (v1's base, BASE_C), each
   step colder never lowers the curve by more than TOLERANCE_MW; above it, each step warmer never lowers it by more
   than TOLERANCE_MW. For the curve given the lags (the frozen model's) and the curve of the fit without the lags.
2. Size. The cold slopes in MW per °C colder over COLD_SPANS, the warm slope over WARM_SPAN, per hour, both kinds; a
   cold slope that is not positive is a finding. Beside them, PUBLIC_FIGURE, quoted, and how it is defined.
3. Stability. Between the two freezes, each hour's curve difference over their common training range and the change
   of its cold slope; across the walk-forward folds, the cold slope and the lags of the GAM refitted at the ruled
   settings on each fold's training rows (`fold_fits`, proven to reproduce validation's out-of-sample forecasts to
   CONTROL_MW on every hour, `control`, or the run stops), each hour's spread, and the folds and freezes whose slope
   leaves it by more than FOLD_FACTOR times its width.
4. The lags, read: both freezes', their spread across the folds, their sum (below 1: a one-off shock fades), and the
   measured difference between the cold slopes of the two curve kinds.
5. Sensitivity. (a) Input: the frozen 2024 GAM's forecast with the temperature forecast moved by -SHIFT_C and +SHIFT_C
   on its own training rows of 2023 (in sample), mean and by season, in MW and in %, held to its curves: an additive
   model's forecast moves by exactly its curve's difference, to CONSISTENCY_MW. (b) Parameters: the walk-forward MAPE on
   validation's folds and rows with each setting of VARIANTS changed alone, beside the ruled one, the difference with
   evaluate.paired_difference's week-block interval (in sample for the choice).

The settings below were fixed before any of these measures was computed on the real runs (the developers had looked
at the frozen 2024 run's partial-effects table before them; the report says so).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from valiwatt.forecast import model
from valiwatt.forecast import walkforward as wf
from valiwatt.forecast.evaluate import N_BOOT, errors, paired_difference, season_of
from valiwatt.forecast.features import BASE_C
from valiwatt.forecast.guards import require_design_window, require_embargo, require_known_at_origin

EXPERIMENT = "valiwatt-soundness"
KIND = "soundness"
LABEL = "added_after_freeze"  # every result here: added after the freeze, deciding nothing
INPUT_LABEL = "in_sample_training"  # the frozen model's own training rows
PARAMETER_LABEL = "in_sample_for_choice"  # validation's folds and rows (config.IN_SAMPLE_LABEL)

# Fixed before the measures were computed on the real runs.
HEATING_BASE_C = BASE_C  # v1's base, 18 °C: fixed in v1's plan before any v2 curve existed; every curve is drawn about it
# A 1 °C step may move a curve against its side by at most this, in MW: one unit of the whole MW the report prints the
# curves in, so that the check is no finer than the page that states it.
TOLERANCE_MW = 1.0
GRID_STEP_C = 1.0
COLD_SPANS = ((-20.0, -5.0), (-5.0, 10.0))  # MW per °C colder between these temperatures
WARM_SPAN = (18.0, 28.0)  # MW per °C warmer
# A slope is flagged when outside the folds' 5th-95th percentile by more than this times the band's width: about a
# tenth of the folds lie outside that band by construction, so the flag needs a distance past its edge, and one scaled
# by the band itself holds at every hour whatever its spread; half a width is that distance, fixed in advance.
FOLD_FACTOR = 0.5
SPREAD = (0.05, 0.50, 0.95)
SHIFT_C = 1.0
CONSISTENCY_MW = 1e-6
CONTROL_MW = 1e-6
VARIANTS = (("n_knots", 8), ("n_knots", 12), ("ridge_alpha", 0.5), ("ridge_alpha", 2.0))
FREEZES = (2023, 2024)
KINDS = ("production", "no_lag")
SCENARIO = "forecast"  # the production model's input (model.MODELS[model.PRODUCTION])

# An independent public figure for Québec's demand sensitivity to temperature (measure 2): its one home. Quoted
# verbatim from the page at `url` (read 2026-10-17); docs/public-figures.md gives its provenance, and a test holds the
# two together. It names no temperature it is per degree of, no hours and no year: the comparison is not like for like.
PUBLIC_FIGURE = {
    "quote": ("Une variation d’un degré Celsius représente 500 MW d’impact sur la charge à alimenter, dit la "
              "porte-parole de la société d'État, Caroline Des Rosiers."),
    "mw_per_c": 500.0,
    "speaker": "Caroline Des Rosiers, porte-parole d'Hydro-Québec",
    "outlet": "Radio-Canada",
    "author": "Thomas Gerbet",
    "title": "Grand froid : le jour où Hydro-Québec a atteint sa pointe de frayeur",
    "published": "2025-01-20",
    "read": "2026-10-17",
    "url": "https://ici.radio-canada.ca/nouvelle/2133488/froid-pointe-hydro-quebec-frayeur-record-postes-documents",
}


class SoundnessError(RuntimeError):
    """A measure that cannot be computed as specified: its inputs are not what they claim (the run stops)."""


# ------------------------------------------------------------------------------------------------------ the curves

def training_range(knots) -> tuple[float, float]:
    k = np.asarray(knots, dtype=float)
    return float(k[0]), float(k[-1])


def curves_from_table(table: pd.DataFrame, frozen, atol: float = CONSISTENCY_MW) -> dict[str, pd.DataFrame]:
    """The two curve kinds of a freeze on the frozen run's partial-effects grid (freeze.partial_effects_table): the
    curve given the lags, recomputed from the frozen model's stored values, which must equal the table's effect_mw
    to `atol` (else SoundnessError: the table is not this model's), and the curve of the fit without the lags, from the
    table (its fit is not stored). Each a frame hour, temp_c, effect_mw."""
    t = table.sort_values(["hour", "temp_c"]).reset_index(drop=True)
    temps = np.sort(t["temp_c"].unique().astype(float))
    mine = frozen.partial_effects(temps).set_index(["hour", "temp_c"])["effect_mw"]
    theirs = t.set_index(["hour", "temp_c"])["effect_mw"]
    diff = (mine.reindex(theirs.index) - theirs).abs()
    if diff.isna().any() or float(diff.max()) > atol:
        raise SoundnessError(f"the frozen run's partial-effects table is not its model's curves: they differ by up to "
                             f"{float(diff.max()):.3g} MW")
    return {"production": t[["hour", "temp_c", "effect_mw"]].copy(),
            "no_lag": t[["hour", "temp_c", "effect_mw_nolag"]].rename(columns={"effect_mw_nolag": "effect_mw"})}


def _by_hour(curve: pd.DataFrame) -> dict[int, pd.Series]:
    return {int(h): g.set_index("temp_c")["effect_mw"].astype(float).sort_index() for h, g in curve.groupby("hour")}


def shape_breaks(curve: pd.DataFrame, lo: float, hi: float, base: float = HEATING_BASE_C,
                 tol: float = TOLERANCE_MW) -> list[dict]:
    """The 1 °C steps of each hour's curve, inside the training range [lo, hi], that break its side: below the base, a
    step colder that lowers the curve by more than `tol` (heating); above it, a step warmer that lowers it by more than
    `tol` (cooling). Each break: its hour, the step's colder and warmer temperatures, its side and its size (the
    change in MW along the step, negative: demand lowered)."""
    if float(base) != round(float(base)):
        raise SoundnessError(f"the heating base {base} °C is not on the 1 °C grid")
    out = []
    for h, e in _by_hour(curve).items():
        grid = [float(x) for x in e.index if np.ceil(lo) <= x <= np.floor(hi)]
        for a, b in zip(grid, grid[1:]):
            if b - a != GRID_STEP_C:
                continue
            if b <= base:
                change, side = e[a] - e[b], "cold"  # one step colder, from b to a
            elif a >= base:
                change, side = e[b] - e[a], "warm"  # one step warmer, from a to b
            else:
                continue
            if change < -tol:
                out.append({"hour": h, "colder_c": a, "warmer_c": b, "side": side, "size_mw": float(change)})
    return out


def lowest(curve: pd.DataFrame, lo: float, hi: float) -> list[dict]:
    """Per hour, the whole degree of the training range where its curve is lowest (a reading, no check): where neither
    heating nor cooling adds to demand, as the curve has it."""
    out = []
    for h, e in _by_hour(curve).items():
        g = e[(e.index >= np.ceil(lo)) & (e.index <= np.floor(hi))]
        out.append({"hour": h, "at_c": float(g.idxmin()), "effect_mw": float(g.min())})
    return out


def slope(e: pd.Series, a: float, b: float, side: str) -> float | None:
    """MW per °C colder over (a, b) (side "cold"), or per °C warmer (side "warm"), from a curve indexed by
    temperature; None when an end is not on it."""
    if a not in e.index or b not in e.index:
        return None
    d = (e[a] - e[b]) if side == "cold" else (e[b] - e[a])
    return float(d / (b - a))


def span_key(a: float, b: float) -> str:
    return f"{a:g}..{b:g}"


def slopes(curve: pd.DataFrame, lo: float, hi: float) -> list[dict]:
    """Per hour: each cold slope of COLD_SPANS and the warm slope of WARM_SPAN, and whether each span lies inside the
    training range [lo, hi]."""
    rows = []
    for h, e in _by_hour(curve).items():
        row = {"hour": h}
        for a, b in COLD_SPANS:
            row[f"cold {span_key(a, b)}"] = slope(e, a, b, "cold")
        row[f"warm {span_key(*WARM_SPAN)}"] = slope(e, *WARM_SPAN, "warm")
        rows.append(row)
    return rows


def spans_inside(lo: float, hi: float) -> dict:
    return {f"cold {span_key(a, b)}": bool(lo <= a and b <= hi) for a, b in COLD_SPANS} | {
        f"warm {span_key(*WARM_SPAN)}": bool(lo <= WARM_SPAN[0] and WARM_SPAN[1] <= hi)}


def cold_key() -> str:
    return f"cold {span_key(*COLD_SPANS[0])}"


def stats(values) -> dict | None:
    x = np.asarray([v for v in values if v is not None], dtype=float)
    if not x.size:
        return None
    return {"min": float(x.min()), "median": float(np.median(x)), "max": float(x.max())}


def not_positive(rows: list[dict]) -> list[dict]:
    """The hours whose cold slopes are not positive (colder does not raise demand): a finding."""
    return [{"hour": r["hour"], "span": k, "slope": r[k]} for r in rows for k in r
            if k.startswith("cold ") and r[k] is not None and r[k] <= 0]


def freeze_difference(c23: pd.DataFrame, c24: pd.DataFrame, range23, range24) -> dict:
    """Each hour's curve difference between the two freezes (2024 minus 2023) over their common training range, its
    largest absolute value in MW and where, and the change of the cold slope in percent of 2023's."""
    lo, hi = max(range23[0], range24[0]), min(range23[1], range24[1])
    a, b = _by_hour(c23), _by_hour(c24)
    rows = []
    for h in sorted(a):
        grid = [x for x in a[h].index if np.ceil(lo) <= x <= np.floor(hi) and x in b[h].index]
        d = (b[h][grid] - a[h][grid]).abs()
        s23, s24 = slope(a[h], *COLD_SPANS[0], "cold"), slope(b[h], *COLD_SPANS[0], "cold")
        rows.append({"hour": h, "max_abs_mw": float(d.max()), "at_c": float(d.idxmax()),
                     "slope_first": s23, "slope_last": s24,
                     "slope_change_pct": None if not s23 or s24 is None else float(100 * (s24 - s23) / s23)})
    return {"common_range": [float(lo), float(hi)], "rows": rows}


# ---------------------------------------------------------------------------------------- the GAM, refitted per fold

def variant_knots(temps, n_knots: int = model.N_KNOTS) -> np.ndarray:
    """The base knots of the ruled placement with `n_knots` knots: scikit-learn's quantile placement, as
    model._place_knots, held to the quantile rule (np.percentile on its own) to model.KNOT_TOLERANCE."""
    from sklearn.preprocessing import SplineTransformer

    t = np.asarray(temps, dtype=float)
    sp = SplineTransformer(n_knots=int(n_knots), degree=model.DEGREE, knots=model.KNOTS,
                           extrapolation=model.EXTRAPOLATION, include_bias=model.INCLUDE_BIAS).fit(t.reshape(-1, 1))
    k = np.array(sp.bsplines_[0].t[model.DEGREE:-model.DEGREE], dtype=float)
    rule = np.percentile(t[np.isfinite(t)], np.linspace(0, 100, int(n_knots)))
    if k.shape != rule.shape or not np.all(np.abs(k - rule) <= model.KNOT_TOLERANCE):
        raise SoundnessError(f"the {n_knots} knots placed are not the quantile rule on the fold's rows")
    return k


def fold_fits(f: pd.DataFrame, fold_list: list, train_at=None, n_knots: int = model.N_KNOTS,
              alphas=(model.RIDGE_ALPHA,), scenario: str = SCENARIO) -> dict:
    """The production GAM refitted on each fold's training rows, as walkforward.run and model.GAMForecaster fit it,
    with `n_knots` knots and each ridge alpha of `alphas` (the ruled ones by default): the same rows (from `train_at`
    at the fold's fit clock when given, else from f), the same guards, the same usable rows, the knots placed on them,
    model.gam_design, StandardScaler, Ridge, and model.gam_predict on the fold's test rows. Returns {alpha: {"out":
    frame of target, fold, day, hour, pred; "fits": per fold, its knots, coefficients, scales}}."""
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler

    temp = f"temp_{scenario}"
    require_design_window(f, "soundness rows")  # as run_validation refuses its tables
    parts = {a: [] for a in alphas}
    fits = {a: [] for a in alphas}
    for fold in fold_list:
        train, test = wf.split(f, fold)
        if train_at is not None:
            train, _ = wf.split(train_at(fold.fit_clock), fold)
        if train.empty or test.empty:
            raise SoundnessError(f"fold {fold.test_month} has no training or test rows")
        require_embargo(train, fold.test_first_day, wf.EMBARGO_DAYS)
        require_known_at_origin(train, f"soundness fold {fold.test_month}, training rows")
        require_known_at_origin(test, f"soundness fold {fold.test_month}, test rows")
        require_design_window(train, f"soundness fold {fold.test_month}")
        model.require_input_known(train, scenario, "GAM")
        ok = model.usable_rows(model.design(train, scenario), train["target"])
        rows = train[ok]
        knots = variant_knots(rows[temp].to_numpy(dtype=float), n_knots)
        if int(n_knots) == model.N_KNOTS:
            model.check_knots(knots, rows[temp].to_numpy(dtype=float))
        X = model.gam_design(rows, scenario, knots)
        scaler = StandardScaler().fit(X)
        Z = scaler.transform(X)
        y = rows["target"].to_numpy(dtype=float)
        tok = model.usable_rows(model.design(test, scenario))
        Xt = model.gam_design(test[tok], scenario, knots) if tok.any() else None
        lo, hi = training_range(knots)
        for a in alphas:
            ridge = Ridge(alpha=float(a)).fit(Z, y)
            center, scale = np.asarray(scaler.mean_, dtype=float), np.asarray(scaler.scale_, dtype=float)
            coef, intercept = np.asarray(ridge.coef_, dtype=float), float(ridge.intercept_)
            pred = pd.Series(np.nan, index=test.index)
            if Xt is not None:
                pred[tok] = model.gam_predict(Xt, center, scale, coef, intercept)
            parts[a].append(pd.DataFrame({"target": test["target"], "fold": str(fold.test_month), "day": test["day"],
                                          "hour": test["hour"], "pred": pred}, index=test.index))
            fits[a].append({"fold": str(fold.test_month), "knots": knots, "coef": coef, "scale": scale,
                            "train_min_c": lo, "train_max_c": hi})
    return {a: {"out": pd.concat(parts[a]), "fits": fits[a]} for a in alphas}


def control(out: pd.DataFrame, oos: pd.DataFrame, key: str = model.PRODUCTION, atol: float = CONTROL_MW) -> dict:
    """The refit variant at the ruled settings reproduces validation's out-of-sample forecasts of `key`: the same
    hours, the same hours without a forecast, and every forecast within `atol` MW. Otherwise SoundnessError: no
    measure is built on a variant that does not reproduce validation."""
    col = f"pred_{key}"
    if not out.index.equals(oos.index):
        raise SoundnessError(f"the refit variant forecasts {len(out)} hours, validation {len(oos)}: not the same hours")
    a, b = out["pred"].to_numpy(dtype=float), oos[col].to_numpy(dtype=float)
    if not np.array_equal(np.isnan(a), np.isnan(b)):
        raise SoundnessError("the refit variant and validation lack forecasts on different hours")
    scored = ~np.isnan(b)
    gap = float(np.max(np.abs(a[scored] - b[scored]))) if scored.any() else 0.0
    if not scored.any() or gap > atol:
        raise SoundnessError(f"the refit variant does not reproduce validation's {key} forecasts: they differ by up to "
                             f"{gap:.3g} MW (tolerance {atol:g} MW)")
    return {"hours": int(scored.sum()), "max_abs_mw": gap, "tolerance_mw": atol}


def fold_curve(fit: dict, temps) -> pd.DataFrame:
    """A refitted fold's production curves at `temps`, relative to BASE_C (model.gam_partial_effects)."""
    return model.gam_partial_effects(fit["knots"], fit["coef"], fit["scale"], np.asarray(temps, dtype=float),
                                     ref_c=BASE_C)


SPAN_TEMPS = tuple(sorted({x for span in COLD_SPANS for x in span} | set(WARM_SPAN)))


def fold_measures(fits: list[dict]) -> list[dict]:
    """Per refitted fold: each hour's cold slope (the first of COLD_SPANS) and its lags in MW per MW."""
    out = []
    for fit in fits:
        curve = fold_curve(fit, SPAN_TEMPS)
        hours = _by_hour(curve)
        out.append({"fold": fit["fold"], "train_min_c": fit["train_min_c"], "train_max_c": fit["train_max_c"],
                    "slope": {h: slope(e, *COLD_SPANS[0], "cold") for h, e in hours.items()},
                    **model.gam_lag_coefficients(fit["coef"], fit["scale"])})
    return out


def outside_by(x: float, lo: float, hi: float) -> float:
    return max(lo - x, x - hi, 0.0)


def spread(folds: list[dict], frozen_slopes: dict, factor: float = FOLD_FACTOR) -> dict:
    """Per hour, the spread (SPREAD quantiles) of the folds' cold slopes; the folds, and the freezes
    (`frozen_slopes`: {year: {hour: slope}}), whose slope lies outside the 5th-95th percentile by more than `factor`
    times its width; and the spread of the lags and of their sum."""
    hours = sorted(folds[0]["slope"]) if folds else []
    rows, flagged, frozen_flagged = [], [], []
    for h in hours:
        x = np.array([f["slope"][h] for f in folds], dtype=float)
        p5, p50, p95 = (float(v) for v in np.quantile(x, SPREAD))
        width = p95 - p5
        rows.append({"hour": h, "p5": p5, "p50": p50, "p95": p95})
        for f in folds:
            by = outside_by(f["slope"][h], p5, p95)
            if by > factor * width:
                flagged.append({"fold": f["fold"], "hour": h, "slope": f["slope"][h], "outside_mw": by})
        for year, s in frozen_slopes.items():
            if s.get(h) is not None and outside_by(s[h], p5, p95) > factor * width:
                frozen_flagged.append({"year": int(year), "hour": h, "slope": s[h],
                                       "outside_mw": outside_by(s[h], p5, p95)})
    lags = {}
    for k in ("lag24", "lag168", "sum"):
        x = np.array([f["lag24"] + f["lag168"] if k == "sum" else f[k] for f in folds], dtype=float)
        lags[k] = dict(zip(("p5", "p50", "p95"), (float(v) for v in np.quantile(x, SPREAD)))) if x.size else None
    return {"folds": len(folds), "factor": factor, "rows": rows, "flagged": flagged, "frozen_flagged": frozen_flagged,
            "lags": lags}


# ---------------------------------------------------------------------------------------------------- the lags

# The rise in yesterday's demand, in MW, whose carry into today's forecast the report states: an illustration's scale.
CARRY_RISE_MW = 1000


def lag_reading(lags: dict, folds: list[dict]) -> dict:
    """Each freeze's lags in MW per MW, their sum, what a 1,000 MW rise in yesterday's demand carries into today's
    forecast, and the findings: a lag that is not positive, or lags whose sum is not below 1 (a one-off shock would not
    fade)."""
    out = {}
    for year, lag in lags.items():
        total = lag["lag24"] + lag["lag168"]
        out[str(year)] = {**lag, "sum": total, "carry_per_1000_mw": CARRY_RISE_MW * lag["lag24"],
                          "positive": bool(lag["lag24"] > 0 and lag["lag168"] > 0), "fades": bool(total < 1)}
    neg = [f["fold"] for f in folds if not (f["lag24"] > 0 and f["lag168"] > 0)]
    keep = [f["fold"] for f in folds if f["lag24"] + f["lag168"] >= 1]
    return {"freezes": out, "folds_not_positive": neg, "folds_not_fading": keep}


def flattening(slope_rows: dict) -> list[dict]:
    """Per hour, the cold slope without the lags, given the lags, their difference and ratio (measured, frozen 2024)."""
    out = []
    for p, n in zip(slope_rows["production"], slope_rows["no_lag"]):
        a, b = p[cold_key()], n[cold_key()]
        out.append({"hour": p["hour"], "production": a, "no_lag": b,
                    "difference": None if a is None or b is None else b - a,
                    "ratio": None if a is None or not b else a / b})
    return out


# ------------------------------------------------------------------------------------------------ the sensitivities

def curve_values(frozen, hours, temps) -> np.ndarray:
    """Each row's curve value at its own hour and temperature, relative to BASE_C, from the frozen model's stored
    values: the sum over its basis of coef / scale times (B_k(T) - B_k(BASE_C)), as model.gam_partial_effects."""
    spec = frozen.gam_spec()
    spline = {k: spec[k] for k in ("degree", "extrapolation", "include_bias")}
    knots = np.asarray(frozen.knots)
    B = model.gam_basis(np.asarray(temps, dtype=float), knots, **spline) - \
        model.gam_basis(np.array([BASE_C]), knots, **spline)
    nb = B.shape[1]
    first = len(spec["day_types"]) * 24
    w = (np.asarray(frozen.beta, dtype=float) / np.asarray(frozen.scale, dtype=float))[first:first + 24 * nb]
    w = w.reshape(24, nb)
    h = np.asarray(hours, dtype=int)
    return np.einsum("ij,ij->i", B, w[h])


def input_sensitivity(frozen, rows: pd.DataFrame, shift: float = SHIFT_C, atol: float = CONSISTENCY_MW,
                      scenario: str = SCENARIO) -> dict:
    """The frozen GAM's forecast on `rows` with temp_<scenario> moved by -shift and +shift: on the rows forecast both
    ways, the mean change in MW and in % of the forecast, overall and by season, and the change per °C. Held to the
    curves: each row's change must be its hour's curve difference over the move, to `atol` MW (an additive model's
    identity; `consistent` false, with the largest gap, is a finding)."""
    temp = f"temp_{scenario}"
    base = frozen.forecast(rows)
    out = {"rows": 0, "shift_c": shift, "label": INPUT_LABEL, "moves": {}}
    worst = 0.0
    for sign in (-1, 1):
        moved = rows.copy()
        moved[temp] = moved[temp] + sign * shift
        new = frozen.forecast(moved)
        ok = base.notna() & new.notna()
        change = (new - base)[ok]
        hours = model.local_hour(rows.loc[ok]).astype(int).to_numpy()
        t = rows.loc[ok, temp].to_numpy(dtype=float)
        expected = curve_values(frozen, hours, t + sign * shift) - curve_values(frozen, hours, t)
        worst = max(worst, float(np.max(np.abs(change.to_numpy() - expected))) if len(change) else 0.0)
        pct = 100 * change / base[ok]
        seasons = season_of(rows.loc[ok, "day"])
        out["rows"] = int(ok.sum())
        out["moves"][f"{sign * shift:+g}"] = {
            "mean_mw": float(change.mean()), "mean_pct": float(pct.mean()),
            "per_c_mw": float(change.mean() / (sign * shift)),
            "by_season": {s: {"mean_mw": float(change[seasons == s].mean()), "mean_pct": float(pct[seasons == s].mean()),
                              "rows": int((seasons == s).sum())} for s in ("winter", "spring", "summer", "fall")
                          if (seasons == s).any()}}
    out["consistency_max_abs_mw"] = worst
    out["tolerance_mw"] = atol
    out["consistent"] = bool(worst <= atol)
    return out


def parameter_sensitivity(ruled: pd.DataFrame, variants: dict, n_boot: int = N_BOOT) -> dict:
    """The walk-forward MAPE of each variant (`variants`: {(setting, value): out frame}) beside the ruled fit's
    (`ruled`), on the same folds and rows: the difference (variant minus ruled) with evaluate.paired_difference's
    week-block interval."""
    e0 = errors(ruled, "pred")
    rows = []
    for (setting, value), out in variants.items():
        e = errors(out, "pred")
        if not e.index.equals(e0.index):
            raise SoundnessError(f"the variant {setting} = {value} is scored on other hours than the ruled fit")
        rows.append({"setting": setting, "value": value, "mape": float(e["ape"].mean()),
                     **paired_difference(e, e0, n_boot=n_boot)})
    return {"label": PARAMETER_LABEL, "ruled": {"n_knots": model.N_KNOTS, "ridge_alpha": model.RIDGE_ALPHA,
                                                "mape": float(e0["ape"].mean()), "hours": int(len(e0))},
            "variants": rows}


# ------------------------------------------------------------------------------------------------------ the whole

def settings() -> dict:
    return {"heating_base_c": HEATING_BASE_C, "tolerance_mw": TOLERANCE_MW, "grid_step_c": GRID_STEP_C,
            "cold_spans": [list(s) for s in COLD_SPANS], "warm_span": list(WARM_SPAN), "fold_factor": FOLD_FACTOR,
            "spread": list(SPREAD), "shift_c": SHIFT_C, "consistency_mw": CONSISTENCY_MW, "control_mw": CONTROL_MW,
            "variants": [list(v) for v in VARIANTS], "ref_c": BASE_C}


def frozen_measures(frozen: dict, tables: dict) -> dict:
    """Measures 1, 2, 3(a) and the freezes' half of 4, from the frozen artifacts only: `frozen` {year: FrozenModel},
    `tables` {year: its run's partial-effects table}."""
    out = {"freezes": {}, "shape": {}, "slopes": {}, "slope_stats": {}, "not_positive": {}}
    curves = {}
    for year in sorted(frozen):
        m = frozen[year]
        lo, hi = training_range(m.knots)
        curves[year] = curves_from_table(tables[year], m)
        out["freezes"][str(year)] = {"train_last_day": m.train_last_day, "sha256": m.sha256(),
                                     "training_range": [lo, hi], "spans_inside": spans_inside(lo, hi),
                                     **m.lag_coefficients()}
        out["shape"][str(year)] = {k: shape_breaks(curves[year][k], lo, hi) for k in KINDS}
        out["slopes"][str(year)] = {k: slopes(curves[year][k], lo, hi) for k in KINDS}
        out["slope_stats"][str(year)] = {k: {col: stats(r[col] for r in out["slopes"][str(year)][k])
                                             for col in out["slopes"][str(year)][k][0] if col != "hour"}
                                         for k in KINDS}
        out["not_positive"][str(year)] = {k: not_positive(out["slopes"][str(year)][k]) for k in KINDS}
        out.setdefault("lowest", {})[str(year)] = {k: lowest(curves[year][k], lo, hi) for k in KINDS}
    y0, y1 = sorted(frozen)
    out["between_freezes"] = {k: freeze_difference(curves[y0][k], curves[y1][k],
                                                   out["freezes"][str(y0)]["training_range"],
                                                   out["freezes"][str(y1)]["training_range"]) for k in KINDS}
    out["flattening"] = flattening(out["slopes"][str(y1)])
    return out


def findings(R: dict) -> list[dict]:
    """Every check that failed, as the report states it: a finding, added after the freeze, deciding nothing."""
    out = []
    for year, kinds in R["shape"].items():
        for kind, breaks in kinds.items():
            if breaks:
                worst = min(breaks, key=lambda b: b["size_mw"])
                out.append({"measure": "shape", "year": int(year), "kind": kind, "count": len(breaks),
                            "sides": sorted({b["side"] for b in breaks}), "worst": worst})
    for year, kinds in R["not_positive"].items():
        for kind, rows in kinds.items():
            if rows:
                out.append({"measure": "size", "year": int(year), "kind": kind, "count": len(rows),
                            "hours": sorted({r["hour"] for r in rows})})
    st = R.get("stability")
    if st is not None:
        if st["flagged"]:
            out.append({"measure": "stability_folds", "count": len(st["flagged"]),
                        "folds": sorted({f["fold"] for f in st["flagged"]})})
        if st["frozen_flagged"]:
            out.append({"measure": "stability_freezes", "count": len(st["frozen_flagged"]),
                        "years": sorted({f["year"] for f in st["frozen_flagged"]})})
    L = R.get("lags")
    if L is not None:
        for year, x in L["freezes"].items():
            if not x["positive"] or not x["fades"]:
                out.append({"measure": "lags", "year": int(year), "positive": x["positive"], "fades": x["fades"]})
        if L["folds_not_positive"] or L["folds_not_fading"]:
            out.append({"measure": "lags_folds", "not_positive": L["folds_not_positive"],
                        "not_fading": L["folds_not_fading"]})
    S = R.get("input")
    if S is not None and not S["consistent"]:
        out.append({"measure": "input", "max_abs_mw": S["consistency_max_abs_mw"]})
    return out


def variant_fits(f: pd.DataFrame, fold_list: list, by_alpha: dict, train_at=None) -> dict:
    """Each setting of VARIANTS changed alone, the others the ruled ones: {(setting, value): {"out", "fits"}}. A ridge
    alpha's is its fit in `by_alpha` (fold_fits with the ruled knots and every alpha of VARIANTS); a number of knots'
    is fold_fits refitted with that many knots and the ruled alpha."""
    out = {}
    for setting, value in VARIANTS:
        if setting == "ridge_alpha":
            out[(setting, value)] = by_alpha[value]
        else:
            out[(setting, value)] = fold_fits(f, fold_list, train_at, int(value), (model.RIDGE_ALPHA,))[
                model.RIDGE_ALPHA]
    return out


def measure(frozen: dict, tables: dict, f: pd.DataFrame, fold_list: list, oos: pd.DataFrame,
            input_rows: pd.DataFrame, train_at=None, n_boot: int = N_BOOT) -> dict:
    """Every measure, pure: the frozen artifacts' (`frozen`, `tables`), the folds' refits on validation's rows (f,
    fold_list, train_at; the control against validation's out-of-sample frame `oos` first) and the frozen latest
    GAM's input sensitivity on `input_rows`, its own training rows of its last training year."""
    R = {"label": LABEL, "settings": settings(), **frozen_measures(frozen, tables)}
    alphas = tuple(sorted({model.RIDGE_ALPHA} | {v for s, v in VARIANTS if s == "ridge_alpha"}))
    ruled_knots = fold_fits(f, fold_list, train_at, model.N_KNOTS, alphas)
    ruled = ruled_knots[model.RIDGE_ALPHA]
    R["control"] = control(ruled["out"], oos)
    folds = fold_measures(ruled["fits"])
    last = max(frozen)
    frozen_slopes = {str(y): {r["hour"]: r[cold_key()] for r in R["slopes"][str(y)]["production"]} for y in frozen}
    R["stability"] = spread(folds, frozen_slopes)
    R["stability"]["by_fold"] = [{"fold": x["fold"], "lag24": x["lag24"], "lag168": x["lag168"],
                                  "train_min_c": x["train_min_c"], "train_max_c": x["train_max_c"]} for x in folds]
    R["lags"] = lag_reading({y: frozen[y].lag_coefficients() for y in frozen}, folds)
    R["lags"]["folds"] = R["stability"]["lags"]
    variants = variant_fits(f, fold_list, ruled_knots, train_at)
    R["parameters"] = parameter_sensitivity(ruled["out"], {k: v["out"] for k, v in variants.items()}, n_boot)
    R["input"] = input_sensitivity(frozen[last], input_rows)
    R["input"]["year"] = int(input_rows["day"].min().year) if len(input_rows) else None
    R["findings"] = findings(R)
    return R


# ------------------------------------------------------------------------------------------------------ the run

def input_rows_of(table: pd.DataFrame, frozen) -> pd.DataFrame:
    """The frozen model's own training rows of its last training year (in sample): from 1 January of that year to its
    training end."""
    end = pd.Timestamp(frozen.train_last_day)
    rows = table[(table["day"] >= pd.Timestamp(f"{end.year}-01-01")) & (table["day"] <= end)]
    require_design_window(rows, "soundness input sensitivity")
    return rows


def _table(c, run, path: str) -> pd.DataFrame:
    with tempfile.TemporaryDirectory() as tmp:
        return pd.read_csv(Path(c.download_artifacts(run.info.run_id, path, tmp)))


def design_inputs(dq, weather: pd.DataFrame, last: int) -> dict:
    """The design data validation read, through its own loaders and guards: the test rows of every fold
    (validate.design_features), the training table at a fit clock (`train_at`), validation's folds, and the training
    table the model frozen for `last` was fitted on (freeze.training_table), the raw records cut at the end of the
    design window (features.AsOf) and the NBM runs of the design years only (validate.forecast_inputs refuses a run
    available later)."""
    from valiwatt.forecast import freeze as fz
    from valiwatt.forecast import validate as vd
    from valiwatt.forecast.features import AsOf
    from valiwatt.monitor import run as mrun
    from valiwatt.quality import run as qrun

    raw_demand, _ = mrun.load_raw(dq)
    nbm_runs = qrun.load_runs(dq, years=vd.DESIGN_YEARS)
    asof = AsOf(raw_demand)
    primary = vd.temperature_sources(weather)["primary"]
    forecasts = vd.forecast_inputs(nbm_runs, vd.design_days())
    return {"f": vd.design_features(asof, primary, forecasts),
            "train_at": lambda clock: asof.table(clock, primary, forecasts),
            "folds": wf.folds(vd.FIRST_FOLD, vd.LAST_FOLD),
            "table": fz.training_table(asof, primary, last, forecasts)}


def run_soundness(n_boot: int = N_BOOT) -> int:
    """The tracked soundness run: the chain's frozen models (the aliases replay-2023 and replay-2024, each from the
    validation run of this data-quality run and these rules) and the design data validation read, through validation's
    loaders and guards; nothing after the design window is read, no frozen model refitted."""
    import mlflow

    from valiwatt.forecast import freeze as fz
    from valiwatt.forecast import validate as vd
    from valiwatt.monitor.rules import rules_sha256
    from valiwatt.monitor.run import check_lineage
    from valiwatt.quality.checks import jsonable
    from valiwatt.tracking import client, start_run

    dq, _, weather = vd.load_inputs()
    val, oos = vd.load_validation(dq)
    c = client()
    frozen, tables, runs_of = {}, {}, {}
    for year in FREEZES:
        m, frun = fz.load_frozen(year)
        check_lineage(year, val, frun, {"rules_sha256": rules_sha256()})
        if frun.data.tags.get("dq_run_id") != dq.info.run_id:
            raise SoundnessError(f"frozen run {frun.info.run_id} was built on another data-quality run")
        if m.family != "gam":
            raise SoundnessError(f"the production model frozen for {year} is not a GAM")
        frozen[year], tables[year], runs_of[year] = m, _table(c, frun, "partial_effects.csv"), frun
    last = max(FREEZES)
    d = design_inputs(dq, weather, last)
    require_design_window(d["f"], "soundness test rows")
    require_known_at_origin(d["f"], "soundness test rows")
    R = measure(frozen, tables, d["f"], d["folds"], oos, input_rows_of(d["table"], frozen[last]),
                train_at=d["train_at"], n_boot=n_boot)
    tags = {"kind": KIND, "dq_run_id": dq.info.run_id, "validation_run_id": val.info.run_id,
            "rules_sha256": rules_sha256(), "label": LABEL,
            **{f"frozen_run_id_{y}": runs_of[y].info.run_id for y in FREEZES},
            **{f"coefficient_sha256_{y}": frozen[y].sha256() for y in FREEZES}}
    with start_run(EXPERIMENT, KIND, tags=tags) as run:
        mlflow.log_params({k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in settings().items()}
                          | {"n_boot": n_boot})
        mlflow.log_metrics({"findings": len(R["findings"]), "control_max_abs_mw": R["control"]["max_abs_mw"]})
        R["run_id"] = run.info.run_id
        mlflow.log_text(json.dumps(jsonable(R), indent=2, ensure_ascii=False), "results/soundness.json")
        mlflow.set_tag("status", "complete")
    print(f"soundness run {run.info.run_id}: {len(R['findings'])} findings, added after the freeze, deciding nothing; "
          f"control {R['control']['hours']} hours within {R['control']['max_abs_mw']:.2g} MW")
    return 0


# --------------------------------------------------------------------------------------------- what the report reads

def failed(results: dict) -> dict:
    """Every check of the results that failed, read from its measure's own numbers and not from the run's list of
    findings: {(measure, year, kind): count}, keyed as `findings` keys them (year and kind None where it has none)."""
    out = {}
    for year, kinds in results["shape"].items():
        for kind, breaks in kinds.items():
            if breaks:
                out[("shape", int(year), kind)] = len(breaks)
    for year, kinds in results["slopes"].items():
        for kind, rows in kinds.items():
            n = sum(1 for r in rows for k, x in r.items() if k.startswith("cold ") and x is not None and x <= 0)
            if n:
                out[("size", int(year), kind)] = n
    st = results["stability"]
    if st["flagged"]:
        out[("stability_folds", None, None)] = len(st["flagged"])
    if st["frozen_flagged"]:
        out[("stability_freezes", None, None)] = len(st["frozen_flagged"])
    for year, x in results["freezes"].items():
        if not (x["lag24"] > 0 and x["lag168"] > 0 and x["lag24"] + x["lag168"] < 1):
            out[("lags", int(year), None)] = 1
    if any(not (x["lag24"] > 0 and x["lag168"] > 0 and x["lag24"] + x["lag168"] < 1) for x in st["by_fold"]):
        out[("lags_folds", None, None)] = 1
    S = results["input"]
    if not S["consistency_max_abs_mw"] <= S["tolerance_mw"]:
        out[("input", None, None)] = 1
    return out


def check_findings(results: dict) -> None:
    """The run's findings are its failed checks, each once with its count, and nothing else (SoundnessError
    otherwise): a check that fails and is not stated as a finding would print beside "no check failed"."""
    stated = [((x["measure"], x.get("year"), x.get("kind")), x.get("count", 1)) for x in results["findings"]]
    want = failed(results)
    if len(dict(stated)) != len(stated) or dict(stated) != want:
        missing = sorted(set(want) - set(dict(stated)), key=str)
        raise SoundnessError(f"the soundness results' findings are not their failed checks (not stated: {missing})")


def view(results: dict, run_id: str) -> dict:
    """The soundness results as the report shows them, refused (SoundnessError) unless they are the run's own
    (`run_id`), carry the label added after the freeze and hold every measure."""
    if results.get("run_id") != run_id:
        raise SoundnessError(f"the soundness results name run {results.get('run_id')!r}, not {run_id!r}")
    if results.get("label") != LABEL:
        raise SoundnessError(f"the soundness results carry the label {results.get('label')!r}, not {LABEL!r}: "
                             "no soundness finding is shown without it")
    missing = [k for k in ("settings", "freezes", "shape", "slopes", "slope_stats", "lowest", "between_freezes",
                           "flattening",
                           "control", "stability", "lags", "parameters", "input", "findings") if k not in results]
    if missing:
        raise SoundnessError(f"the soundness results lack {missing}")
    check_findings(results)
    years = sorted(results["freezes"], key=int)
    cold, warm = cold_key(), f"warm {span_key(*WARM_SPAN)}"
    reading = {}
    for kind in KINDS:
        rows = [r for r in results["slopes"][years[-1]][kind] if r[cold] is not None]
        hi, lo = (max(rows, key=lambda r: r[cold]), min(rows, key=lambda r: r[cold])) if rows else (None, None)
        reading[kind] = {"most": hi and hi["hour"], "least": lo and lo["hour"],
                         "lowest": stats(r["at_c"] for r in results["lowest"][years[-1]][kind])}
    by_hour = {y: {k: {} for k in KINDS} for y in years}
    for y in years:
        for k in KINDS:
            for b in results["shape"][y][k]:
                by_hour[y][k].setdefault(b["hour"], []).append(b)
    flat = results["flattening"]
    between = {k: {"max_abs_mw": max(r["max_abs_mw"] for r in d["rows"]),
                   "change_pct": stats(r["slope_change_pct"] for r in d["rows"])}
               for k, d in results["between_freezes"].items()}
    return {**results, "years": [int(y) for y in years], "last": int(years[-1]), "first": int(years[0]),
            "cold": cold, "cold2": f"cold {span_key(*COLD_SPANS[1])}", "warm": warm, "reading": reading,
            "breaks_by_hour": by_hour, "between": between,
            "flat": {"difference": stats(r["difference"] for r in flat), "ratio": stats(r["ratio"] for r in flat)},
            "public": PUBLIC_FIGURE}
