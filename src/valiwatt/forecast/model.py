"""The models: a seasonal baseline, v1's least squares on named features, and v2's production model, the spline model
("the GAM"), exactly as the owner's ruling fixes it (`docs/plan-v2.md`, section 4).

The GAM is an additive model of the GAM family fitted by penalized least squares: v1's 72 hour-by-day-type cells, one
cubic spline curve of the temperature for each local hour of the day (11 basis columns times 24 hour indicators), and
the 24-hour and 168-hour demand lags, standardized, then ridge regression. The spline's knots follow each fit's own
usable training rows by a fixed rule (`knot_rule`), checked by the fit (`check_knots`); every other setting is fixed
here. Past the coldest and warmest training temperatures each curve continues as a straight line.

The design functions read the ruled settings, day types and hour blocks when called, unless they are given others: a
fit gives none, so it follows this module; the frozen models give their own stored ones (freeze.FrozenModel), least
squares its base temperature too, so what they forecast depends on their file alone.

v3 (`docs/plan-v3.md`, section 4) is the same path with more arguments, each defaulting to v2: the demand lags of an
issue time (`issue.NOON`: lag48 and lag168), the three terms of section 4.1 computed here from the table's raw columns
(`term_columns`, with issue.TERM_DEFINITIONS, which a frozen model stores and `require_term_definitions` checks), a
row mask of required inputs so that every noon model fits and forecasts v3's usable rows (section 4.2), and the bin
edge of the intervals (section 7), the 5th-percentile rule on the fit's own usable rows (`bin_edge`; `check_bin_edge`
is the pure check a freeze calls on its stored edge). Given none of them, every function computes what it computed
for v2. The v3 models are `MODELS_V3`, each a `ModelSpec`; v2's `MODELS` is unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.preprocessing import SplineTransformer, StandardScaler

from valiwatt.forecast.guards import LookaheadError, require_forecast_known
from valiwatt.forecast.issue import MIDNIGHT, NOON, TERM_DEFINITIONS, TERMS, IssueSpec

DAY_TYPES = ("weekday", "saturday", "sunday_holiday")
# Hours of the local day grouped where heating and cooling act differently: night, morning ramp, day, evening peak,
# late evening.
HOUR_BLOCKS = {"night": range(0, 6), "morning": range(6, 10), "day": range(10, 16), "evening": range(16, 21),
               "late": range(21, 24)}

# The ruled spline and ridge settings (the owner's ruling of 2026-10-15; plan section 4.1). The frozen model refuses
# any other value of the three spline settings.
N_KNOTS, DEGREE, RIDGE_ALPHA = 10, 3, 1.0
KNOTS, EXTRAPOLATION, INCLUDE_BIAS = "quantile", "linear", False
KNOT_TOLERANCE = 1e-12  # absolute, in degrees Celsius

LAGS = ("lag24", "lag168")  # v2's; a noon model reads issue.NOON.lag_columns
# The 5th percentile: v2's cold threshold (evaluate.cold_threshold) and v3's interval bin edge (`bin_edge`) are each
# this quantile of a set of forecast temperatures, inputs only (docs/plan-v3.md, sections 6 and 7).
COLD_QUANTILE = 0.05
TERM_PREFIX = "term:"  # a term's column name: never "lag...", so a lag column is never a term
INTERVALS = ("single", "binned")


def hour_blocks(blocks=None) -> tuple:
    """The hour blocks as (name, hours) pairs in their column order: those given (a mapping or pairs), else the
    module's HOUR_BLOCKS, read when called (never at import)."""
    blocks = HOUR_BLOCKS if blocks is None else blocks
    pairs = blocks.items() if isinstance(blocks, dict) else blocks
    return tuple((str(name), tuple(int(h) for h in hours)) for name, hours in pairs)


def block_of(hour: pd.Series, blocks=None) -> pd.Series:
    out = pd.Series(index=hour.index, dtype=object)
    for name, hours in hour_blocks(blocks):
        out[hour.isin(list(hours))] = name
    return out


def local_hour(f: pd.DataFrame) -> pd.Series:
    """The table's hour as a number (float; NaN where it is not one): the one reading of the hour that
    `calendar_known`, `design` and `gam_design` all build from, so an hour given as text ("6") is hour 6 in its cells,
    blocks and curves alike, never known yet all-zero."""
    return pd.Series(pd.to_numeric(f["hour"], errors="coerce").to_numpy(dtype=float), index=f.index)


def calendar_known(f: pd.DataFrame) -> np.ndarray:
    """The rows whose day type is present and whose hour (`local_hour`) is one of 0 to 23. Any other row is a row with
    a missing input: its cells would all be zero, and it is never forecast (`design` and `gam_design` give it NaN)."""
    return f["day_type"].notna().to_numpy() & np.isin(local_hour(f).to_numpy(), np.arange(24))


def _lags(lags=None) -> tuple[str, ...]:
    """The lag columns given, else v2's (LAGS)."""
    return LAGS if lags is None else tuple(str(c) for c in lags)


def is_lag(column: str) -> bool:
    """A demand-lag column: "lag" and a whole number of hours (lag24, lag48, lag168), never a term."""
    return re.fullmatch(r"lag\d+", str(column)) is not None


def ols_columns(*, day_types=None, blocks=None, lags=None) -> list[str]:
    """Least squares' column names, in `design`'s order: the cells of the day types (day type major, hour minor), a
    heating and a cooling column per hour block, the lags; the day types, blocks and lags given, else the module's
    (v1's form at noon gives issue.NOON's lags, docs/plan-v3.md section 4.2)."""
    day_types = DAY_TYPES if day_types is None else tuple(day_types)
    cells = [f"cell:{dt}:{h:02d}" for dt in day_types for h in range(24)]
    degree_hours = [c for name, _ in hour_blocks(blocks) for c in (f"heating:{name}", f"cooling:{name}")]
    return cells + degree_hours + list(_lags(lags))


def design(f: pd.DataFrame, scenario: str, *, day_types=None, blocks=None, base_c=None, lags=None) -> pd.DataFrame:
    """Named columns, one per coefficient (`ols_columns`). Rows with any missing input, a missing day type or an hour
    outside 0 to 23 included (`calendar_known`), come out with NaN and are never imputed.

    The day types and hour blocks are the module's unless given; the degree-hours are the table's hdh_<scenario> and
    cdh_<scenario> (features.build computes them with features.BASE_C), unless a base temperature `base_c` is given:
    then they are computed here from temp_<scenario> with it, as features.build computes them. The frozen least
    squares gives its stored day types, blocks and base (freeze.FrozenModel), so its forecast depends on its file
    alone. The lags are v2's (LAGS) unless given: a noon model gives issue.NOON's, lag48 and lag168."""
    day_types = DAY_TYPES if day_types is None else tuple(day_types)
    if base_c is None:
        hdh, cdh = f[f"hdh_{scenario}"], f[f"cdh_{scenario}"]
    else:
        temp = f[f"temp_{scenario}"]
        hdh, cdh = np.maximum(float(base_c) - temp, 0.0), np.maximum(temp - float(base_c), 0.0)
    hour = local_hour(f)
    cols = {}
    for dt in day_types:
        for h in range(24):
            cols[f"cell:{dt}:{h:02d}"] = ((f["day_type"] == dt) & (hour == h)).astype(float)
    blk = block_of(hour, blocks)
    for name, _ in hour_blocks(blocks):
        in_block = (blk == name).astype(float)
        cols[f"heating:{name}"] = hdh * in_block
        cols[f"cooling:{name}"] = cdh * in_block
    for lag in _lags(lags):
        cols[lag] = f[lag]
    X = pd.DataFrame(cols, index=f.index)
    unknown = ~calendar_known(f)
    if unknown.any():
        X.loc[unknown] = np.nan
    return X


def usable_rows(X: pd.DataFrame, y: pd.Series | None = None) -> pd.Series:
    ok = np.isfinite(X.to_numpy()).all(axis=1)
    if y is not None:
        ok &= np.isfinite(y.to_numpy())
    return pd.Series(ok, index=X.index)


def required_known(f: pd.DataFrame, required_inputs) -> np.ndarray:
    """The rows whose calendar is known (`calendar_known`) and whose every required input column is finite: v3's
    usable rows when given v3's inputs (`V3_INPUTS`), so that every noon model fits and forecasts the same rows
    (docs/plan-v3.md, sections 4.2 and 5: a row without the lag day's mean is dropped by all of them). A required
    column the table lacks is refused (KeyError), never read as present."""
    ok = calendar_known(f)
    for c in required_inputs:
        ok &= np.isfinite(f[c].to_numpy(dtype=float))
    return ok


def require_input_known(f: pd.DataFrame, scenario: str, family: str) -> None:
    """A fit's own check on the one input it reads: on a weather forecast (forecast, forecast_late), every row holding
    a finite temp_, hdh_ or cdh_ of it must carry its run's availability, at or before its origin, and its origin
    (guards.forecast_violations), whoever supplied the rows. The observed and persistence inputs check nothing."""
    require_forecast_known(f, (scenario,), f"the {family} fit on {scenario}")


@dataclass
class OLSForecaster:
    """v1's least squares on one temperature input (`scenario`). v3's v1 form at noon (docs/plan-v3.md, section 4.2)
    gives issue.NOON's `lags`, v3's `required_inputs` (its usable rows are v3's) and `binned` (it stores the bin edge
    of its own usable rows, `bin_edge`); given none, it is v1's model exactly."""

    scenario: str
    columns: list = field(default_factory=list)
    beta: np.ndarray | None = None
    n_train: int = 0
    lags: tuple | None = None
    required_inputs: tuple | None = None
    binned: bool = False
    bin_edge: float | None = None

    def _usable(self, f: pd.DataFrame, target: pd.Series | None = None) -> tuple[pd.DataFrame, pd.Series]:
        X = design(f, self.scenario, lags=self.lags)
        ok = usable_rows(X, target)
        if self.required_inputs is not None:
            ok &= required_known(f, self.required_inputs)
        return X, ok

    def fit(self, f: pd.DataFrame) -> "OLSForecaster":
        """Fit on the usable rows (target and every input of `design` present, and every required input when given),
        nothing imputed. A fit on a weather forecast first refuses any row whose forecast was not known at its origin
        (`require_input_known`). Binned, it then stores the bin edge of those rows (`bin_edge`)."""
        require_input_known(f, self.scenario, "least squares")
        X, ok = self._usable(f, f["target"])
        Xa, y = X[ok].to_numpy(), f.loc[ok, "target"].to_numpy()
        self.beta, *_ = np.linalg.lstsq(Xa, y, rcond=None)
        self.columns = list(X.columns)
        self.n_train = int(ok.sum())
        if self.binned:
            self.bin_edge = bin_edge(f.loc[ok, f"temp_{self.scenario}"].to_numpy(dtype=float))
        return self

    def predict(self, f: pd.DataFrame) -> pd.Series:
        if self.beta is None:
            raise RuntimeError("model not fitted")
        X, ok = self._usable(f)
        X = X[self.columns]
        out = pd.Series(np.nan, index=f.index)
        out[ok] = X[ok].to_numpy() @ self.beta
        return out

    def coefficients(self) -> pd.Series:
        return pd.Series(self.beta, index=self.columns)


def baseline(f: pd.DataFrame) -> pd.Series:
    """Seasonal naive: the same hour one week earlier."""
    return f["lag168"].astype(float)


# ---------------------------------------------------------------------------------------------------------- the GAM


def knot_rule(temps: np.ndarray) -> np.ndarray:
    """The 10 base knots the ruling places on these temperatures: their 0th, 11.1th, ..., 100th percentiles (linear
    interpolation), on the finite values. Computed here on its own, never through scikit-learn, so that the check
    below compares two computations, not one with itself."""
    t = np.asarray(temps, dtype=float)
    t = t[np.isfinite(t)]
    if t.size < 2:
        raise ValueError(f"the knot rule needs at least two finite temperatures, got {t.size}")
    return np.percentile(t, np.linspace(0, 100, N_KNOTS))


def check_knots(knots: np.ndarray, temps: np.ndarray) -> None:
    """Refuse knots that are not the rule applied to exactly these temperatures (a fit's own usable training rows):
    knots placed on more rows than the fit's own, a test row among them, are lookahead."""
    k = np.asarray(knots, dtype=float).ravel()
    expected = knot_rule(temps)
    if k.shape != expected.shape or not np.all(np.abs(k - expected) <= KNOT_TOLERANCE):
        raise LookaheadError(f"spline knots {np.round(k, 4).tolist()} are not the quantile rule on the fit's own "
                             f"{int(np.isfinite(np.asarray(temps, dtype=float)).sum())} usable training rows "
                             f"({np.round(expected, 4).tolist()})")


def bin_edge(temps: np.ndarray) -> float:
    """The intervals' bin edge (docs/plan-v3.md, section 7): the COLD_QUANTILE quantile of these temperatures, the
    noon feed's on a fit's own usable training rows, on the finite values (numpy's default, linear interpolation, as
    v2's cold threshold). Computed at each fit's clock, as the knots are; the one rule a fit, validation and the freeze
    check read."""
    t = np.asarray(temps, dtype=float).ravel()
    t = t[np.isfinite(t)]
    if t.size == 0:
        raise ValueError("no finite temperature to take the bin edge on")
    return float(np.quantile(t, COLD_QUANTILE))


def check_bin_edge(edge: float, temps: np.ndarray) -> None:
    """Refuse a bin edge that is not the rule (`bin_edge`) applied to exactly these temperatures, a fit's own usable
    training rows, to KNOT_TOLERANCE: an edge that read more rows than the fit's own, a temperature issued after its
    clock among them, is lookahead (docs/plan-v3.md, sections 4.4, 7 and 12). Pure: the freeze calls it on the edge it
    stores and the rows it takes again; a fit does not, since an edge it just computed on its rows would be compared
    with itself."""
    expected = bin_edge(temps)
    e = float(edge)
    if not (np.isfinite(e) and abs(e - expected) <= KNOT_TOLERANCE):
        raise LookaheadError(f"bin edge {e!r} C is not the 5th-percentile rule on the fit's own "
                             f"{int(np.isfinite(np.asarray(temps, dtype=float)).sum())} usable training rows "
                             f"({expected!r} C)")


def _settings(degree=None, extrapolation=None, include_bias=None) -> dict:
    """The spline's three settings: each one given, else the ruled one, read when called (never at import)."""
    return {"degree": DEGREE if degree is None else int(degree),
            "extrapolation": EXTRAPOLATION if extrapolation is None else str(extrapolation),
            "include_bias": INCLUDE_BIAS if include_bias is None else bool(include_bias)}


def _spline(knots=KNOTS, *, degree=None, extrapolation=None, include_bias=None) -> SplineTransformer:
    """The ruled transformer, or one with the settings given. n_knots is read only by a placement rule: knots given
    as values are used as they are."""
    return SplineTransformer(n_knots=N_KNOTS, knots=knots, **_settings(degree, extrapolation, include_bias))


def _place_knots(temps: np.ndarray) -> np.ndarray:
    """The base knots scikit-learn's quantile placement puts on a fit's training temperatures (the spike's
    transformer, fitted). The one place a fit's knots come from; `check_knots` then checks them against the rule."""
    sp = _spline().fit(np.asarray(temps, dtype=float).reshape(-1, 1))
    return np.array(sp.bsplines_[0].t[DEGREE:-DEGREE], dtype=float)


def gam_basis(temps: np.ndarray, knots: np.ndarray, *, degree=None, extrapolation=None,
              include_bias=None) -> np.ndarray:
    """The spline basis of these temperatures, the transformer rebuilt from the 10 stored base knots with the ruled
    degree, extrapolation and bias setting, or those given; with the ruled ones, equal to the fitted transformer's
    output. A missing temperature gives a row of NaN (never imputed)."""
    k = np.asarray(knots, dtype=float).reshape(-1, 1)
    sp = _spline(knots=k, degree=degree, extrapolation=extrapolation, include_bias=include_bias).fit(k)
    t = np.asarray(temps, dtype=float).ravel()
    ok = np.isfinite(t)
    out = np.full((len(t), sp.n_features_out_), np.nan)
    if ok.any():
        out[ok] = sp.transform(t[ok].reshape(-1, 1))
    return out


def _n_basis() -> int:
    return N_KNOTS + DEGREE - 1 - (0 if INCLUDE_BIAS else 1)


def _terms(terms=()) -> tuple[str, ...]:
    """The terms given, in issue.TERMS' order. Refused (ValueError) unless each is one of issue.TERMS, once."""
    terms = tuple(str(t) for t in terms)
    unknown = sorted(set(terms) - set(TERMS))
    if unknown or len(set(terms)) != len(terms):
        raise ValueError(f"terms {list(terms)} are not distinct terms of {list(TERMS)}")
    return tuple(t for t in TERMS if t in terms)


def term_inputs(terms, scenario: str, wind: str | None = None) -> tuple[str, ...]:
    """The raw columns the terms read (issue.py's column contract), in issue.TERMS' order: daymean_<scenario>,
    lagday_observed, wind_<wind> and temp_<scenario>."""
    wind = scenario if wind is None else wind
    reads = {"day_mean_cold": (f"daymean_{scenario}",), "lag_day_mean_cold": ("lagday_observed",),
             "wind_cold": (f"wind_{wind}", f"temp_{scenario}")}
    return tuple(dict.fromkeys(c for t in _terms(terms) for c in reads[t]))


def term_columns(f: pd.DataFrame, terms, scenario: str, wind: str | None = None,
                 definitions: dict | None = None) -> dict[str, np.ndarray]:
    """The v3 terms named (docs/plan-v3.md, section 4.1), in issue.TERMS' order, each computed from the table's raw
    columns with the base temperature of `definitions` (issue.TERM_DEFINITIONS unless given; a frozen model gives its
    stored ones, checked by `require_term_definitions`):

    - day_mean_cold: max(0, base_c - daymean_<scenario>), the day's mean from the run that feeds D;
    - lag_day_mean_cold: max(0, base_c - lagday_observed), the station's mean over D-2;
    - wind_cold: wind_<wind> (km/h) * max(0, base_c - temp_<scenario>); the wind scenario is the temperature's unless
      given (the observed-weather bound keeps the NBM wind, section 4.3).

    A missing input gives NaN (np.maximum propagates it): nothing is imputed."""
    base = float((TERM_DEFINITIONS if definitions is None else definitions)["base_c"])
    wind = scenario if wind is None else wind

    def cold(column: str) -> np.ndarray:
        return np.maximum(base - f[column].to_numpy(dtype=float), 0.0)

    make = {"day_mean_cold": lambda: cold(f"daymean_{scenario}"),
            "lag_day_mean_cold": lambda: cold("lagday_observed"),
            "wind_cold": lambda: f[f"wind_{wind}"].to_numpy(dtype=float) * cold(f"temp_{scenario}")}
    return {t: make[t]() for t in _terms(terms)}


def require_term_definitions(stored, whose: str = "a frozen model") -> dict:
    """Refuse (ValueError) term definitions that are not the ruled ones, issue.TERM_DEFINITIONS, key for key and value
    for value (numbers compared as numbers, a boolean never a number): what a term means is stored with every model
    that reads one and checked when it is built or read (docs/plan-v3.md, section 4.4). Returns them as the ruled
    mapping's values, in its key order."""
    if not isinstance(stored, dict) or set(stored) != set(TERM_DEFINITIONS):
        raise ValueError(f"{whose} stores term definitions {stored!r}, not the ruled {TERM_DEFINITIONS!r}")
    for k, ruled in TERM_DEFINITIONS.items():
        v = stored[k]
        if isinstance(v, bool) or not isinstance(v, (int, float, np.integer, np.floating)) or float(v) != float(ruled):
            raise ValueError(f"{whose} stores the term definition {k}={v!r}, not the ruled {ruled!r}")
    return dict(TERM_DEFINITIONS)


def gam_columns(*, day_types=None, n_basis: int | None = None, lags=None, terms=()) -> list[str]:
    """The GAM's column names, in `gam_design`'s order: 72 cells, 24 x 11 spline columns (hour major), the terms
    ("term:<name>", issue.TERMS' order), the lags; the cells of the day types given, in their order, n_basis spline
    columns per hour, the lags and terms given, else the ruled ones, v2's lags and no term (v2's 338 columns, in v2's
    order; v3's 341, docs/plan-v3.md section 4.1). The terms sit after the spline, so each hour's curve is where v2's
    was (`gam_partial_effects`)."""
    day_types = DAY_TYPES if day_types is None else tuple(day_types)
    n_basis = _n_basis() if n_basis is None else int(n_basis)
    cells = [f"cell:{dt}:{h:02d}" for dt in day_types for h in range(24)]
    spline = [f"spline:{h:02d}:{k:02d}" for h in range(24) for k in range(n_basis)]
    return cells + spline + [TERM_PREFIX + t for t in _terms(terms)] + list(_lags(lags))


def gam_design(f: pd.DataFrame, scenario: str, knots: np.ndarray, *, day_types=None, degree=None, extrapolation=None,
               include_bias=None, lags=None, terms=(), wind: str | None = None,
               term_definitions: dict | None = None) -> np.ndarray:
    """The GAM's columns, in the spike's order: the 72 cells (day type major, hour minor, as `design`), then the
    spline basis times each local-hour indicator (hour major, basis minor: each hour has its own curve), then the
    terms given (`term_columns`, none for v2), then the lags (v2's lag24 and lag168 unless given). A row with a
    missing temperature, term input or lag has NaN in those columns; a row with a missing day type or an hour outside
    0 to 23 (`calendar_known`) is NaN throughout, never zero cells. The day types, the spline's settings and the term
    definitions are the ruled ones unless given (the frozen model gives its stored ones)."""
    day_types = DAY_TYPES if day_types is None else tuple(day_types)
    B = gam_basis(f[f"temp_{scenario}"].to_numpy(dtype=float), knots, degree=degree, extrapolation=extrapolation,
                  include_bias=include_bias)
    hour = local_hour(f)
    cells = np.column_stack([((f["day_type"] == dt) & (hour == h)).to_numpy(float)
                             for dt in day_types for h in range(24)])
    hours = np.column_stack([(hour == h).to_numpy(float) for h in range(24)])
    tensor = (hours[:, :, None] * B[:, None, :]).reshape(len(f), -1)
    added = term_columns(f, terms, scenario, wind, term_definitions).values()
    X = np.column_stack([cells, tensor, *added, *(f[lag].to_numpy(dtype=float) for lag in _lags(lags))])
    unknown = ~calendar_known(f)
    if unknown.any():
        X[unknown] = np.nan
    return X


def gam_predict(X: np.ndarray, center, scale, coef, intercept) -> np.ndarray:
    """The one prediction path of the GAM, fitted or frozen: the scaler, then the ridge's linear predictor."""
    return ((np.asarray(X, dtype=float) - np.asarray(center, dtype=float)) / np.asarray(scale, dtype=float)) \
        @ np.asarray(coef, dtype=float) + float(intercept)


def gam_partial_effects(knots, coef, scale, temps: np.ndarray, ref_c: float = 18.0, *, day_types=None, degree=None,
                        extrapolation=None, include_bias=None) -> pd.DataFrame:
    """Each hour's temperature curve relative to its value at `ref_c`, in MW: for hour h,
    sum_k coef[h,k] / scale[h,k] * (B_k(T) - B_k(ref_c)); the scaler's centring cancels in the difference. The day
    types and the spline's settings are those of `gam_design`, the ruled ones unless given."""
    t = np.asarray(temps, dtype=float).ravel()
    spline = _settings(degree, extrapolation, include_bias)
    B = gam_basis(t, knots, **spline) - gam_basis(np.array([ref_c]), knots, **spline)
    nb = B.shape[1]
    first = len(DAY_TYPES if day_types is None else tuple(day_types)) * 24
    w = (np.asarray(coef, dtype=float) / np.asarray(scale, dtype=float))[first:first + 24 * nb].reshape(24, nb)
    effect = B @ w.T  # (temps, hours)
    return pd.DataFrame({"hour": np.repeat(np.arange(24), len(t)), "temp_c": np.tile(t, 24),
                         "effect_mw": effect.T.ravel()})


def gam_lag_coefficients(coef, scale, columns=None) -> dict:
    """The lag coefficients in MW per MW: the ridge's coefficient on the standardized lag over its scale. Given the
    columns, each lag column by its name (`is_lag`), whatever lags and terms the model has; without them, v2's two
    lags, the last two columns, and only on v2's columns (`gam_columns()`, 338): a model of another shape is refused
    (ValueError), since its last two columns need not be lag24 and lag168 (v3's are lag48 and lag168)."""
    c, s = np.asarray(coef, dtype=float), np.asarray(scale, dtype=float)
    if columns is None:
        if len(c) != len(gam_columns()):
            raise ValueError(f"lag coefficients by position read v2's {len(gam_columns())} columns; this model has "
                             f"{len(c)}: give its columns")
        return {"lag24": float(c[-2] / s[-2]), "lag168": float(c[-1] / s[-1])}
    return {str(n): float(c[i] / s[i]) for i, n in enumerate(columns) if is_lag(n)}


def gam_term_coefficients(coef, scale, columns) -> dict:
    """Each term's coefficient in MW per unit of the term (°C, or km/h times °C for wind with cold), unscaled: the
    ridge's coefficient on the standardized column over its scale (docs/plan-v3.md, section 4.1)."""
    c, s = np.asarray(coef, dtype=float), np.asarray(scale, dtype=float)
    return {str(n).removeprefix(TERM_PREFIX): float(c[i] / s[i]) for i, n in enumerate(columns)
            if str(n).startswith(TERM_PREFIX)}


def vif(X: np.ndarray, columns, which) -> dict:
    """The variance inflation factor of each column named in `which`, 1 / (1 - R^2), R^2 that of the least squares of
    the column on an intercept and every other column of X (the minimum-norm solution: the 72 cells sum to the
    intercept), on these rows, a fit's own usable training rows (docs/plan-v3.md, sections 4.1 and 13). Infinite for a
    column the others reproduce exactly; NaN for a constant column, whose R^2 is undefined. Keyed as the coefficient
    tables are: a term by its name, a lag by its column. For interpretation only: it chooses nothing."""
    X = np.asarray(X, dtype=float)
    names = [str(c) for c in columns]
    if X.ndim != 2 or X.shape[1] != len(names) or not np.isfinite(X).all():
        raise ValueError(f"a VIF needs finite rows of the {len(names)} named columns, got shape {X.shape}")
    out = {}
    for name in which:
        j = names.index(str(name))
        y = X[:, j]
        others = np.column_stack([np.ones(len(X)), np.delete(X, j, axis=1)])
        beta, *_ = np.linalg.lstsq(others, y, rcond=None)
        resid = y - others @ beta
        sst = float(((y - y.mean()) ** 2).sum())
        key = str(name).removeprefix(TERM_PREFIX)
        if sst == 0.0:
            out[key] = float("nan")
            continue
        unexplained = float(resid @ resid) / sst
        out[key] = float("inf") if unexplained <= 0.0 else 1.0 / unexplained
    return out


class GAMForecaster:
    """v2's production model on one temperature input (`scenario`), as the spike measured it. v3's models
    (docs/plan-v3.md, section 4) give the lags of their issue time, the terms (`term_columns`, the wind of `wind`),
    the required inputs that make their usable rows v3's, and `binned` (the fit stores the bin edge of its own usable
    rows); given none, it is v2's model exactly."""

    def __init__(self, scenario: str, *, lags=None, terms=(), wind: str | None = None, required_inputs=None,
                 binned: bool = False):
        self.scenario = scenario
        self.lags = _lags(lags)
        self.terms = _terms(terms)
        self.wind = scenario if wind is None else wind
        self.required_inputs = None if required_inputs is None else tuple(required_inputs)
        self.binned = bool(binned)
        self.term_definitions: dict | None = None
        self.bin_edge: float | None = None
        self.knots: np.ndarray | None = None
        self.columns: list[str] = []
        self.center: np.ndarray | None = None
        self.scale: np.ndarray | None = None
        self.coef: np.ndarray | None = None
        self.intercept: float = 0.0
        self.n_train: int = 0

    def _usable(self, f: pd.DataFrame, target: pd.Series | None = None) -> pd.Series:
        """v1's rule (target, when given, and every input of `design` present), then, with terms, every term finite,
        and, given required inputs, each of them finite (`required_known`)."""
        ok = usable_rows(design(f, self.scenario, lags=self.lags), target)
        if self.terms:
            for v in term_columns(f, self.terms, self.scenario, self.wind).values():
                ok &= np.isfinite(v)
        if self.required_inputs is not None:
            ok &= required_known(f, self.required_inputs)
        return ok

    def _design(self, rows: pd.DataFrame) -> np.ndarray:
        return gam_design(rows, self.scenario, self.knots, lags=self.lags, terms=self.terms, wind=self.wind,
                          term_definitions=self.term_definitions)

    def fit(self, f: pd.DataFrame) -> "GAMForecaster":
        """Fit on the usable rows (`_usable`; v1's rule for v2), nothing imputed. A fit on a weather forecast first
        refuses any row whose forecast was not known at its origin (`require_input_known`, on the wind's input too
        when a term reads it), before the knots are placed; binned, the bin edge is then placed on the same rows
        (`bin_edge`)."""
        require_input_known(f, self.scenario, "GAM")
        if "wind_cold" in self.terms and self.wind != self.scenario:
            require_forecast_known(f, (self.wind,), f"the GAM fit's wind on {self.wind}")
        ok = self._usable(f, f["target"])
        rows = f[ok]
        temp = f"temp_{self.scenario}"
        knots = _place_knots(rows[temp].to_numpy(dtype=float))
        check_knots(knots, f.loc[ok, temp].to_numpy(dtype=float))
        if self.binned:
            self.bin_edge = bin_edge(rows[temp].to_numpy(dtype=float))
        self.term_definitions = dict(TERM_DEFINITIONS) if self.terms else None
        self.knots = knots
        X = self._design(rows)
        columns = gam_columns(lags=self.lags, terms=self.terms)
        if X.shape[1] != len(columns):
            raise RuntimeError(f"the GAM's design has {X.shape[1]} columns, not the {len(columns)} it names")
        scaler = StandardScaler().fit(X)
        ridge = Ridge(alpha=RIDGE_ALPHA).fit(scaler.transform(X), rows["target"].to_numpy(dtype=float))
        self.columns = columns
        self.center = np.asarray(scaler.mean_, dtype=float)
        self.scale = np.asarray(scaler.scale_, dtype=float)
        self.coef = np.asarray(ridge.coef_, dtype=float)
        self.intercept = float(ridge.intercept_)
        self.n_train = int(ok.sum())
        return self

    def _fitted(self) -> None:
        if self.coef is None:
            raise RuntimeError("model not fitted")

    def predict(self, f: pd.DataFrame) -> pd.Series:
        """The forecast of every usable row; NaN where an input is missing (never imputed)."""
        self._fitted()
        ok = self._usable(f)
        out = pd.Series(np.nan, index=f.index)
        if ok.any():
            X = self._design(f[ok])
            out[ok] = gam_predict(X, self.center, self.scale, self.coef, self.intercept)
        return out

    def partial_effects(self, temps: np.ndarray, ref_c: float = 18.0) -> pd.DataFrame:
        self._fitted()
        return gam_partial_effects(self.knots, self.coef, self.scale, temps, ref_c)

    def lag_coefficients(self) -> dict:
        self._fitted()
        return gam_lag_coefficients(self.coef, self.scale, self.columns)

    def term_coefficients(self) -> dict:
        self._fitted()
        return gam_term_coefficients(self.coef, self.scale, self.columns)

    def vif(self, f: pd.DataFrame) -> dict:
        """The VIF of each term and lag (`vif`) on the fit's own usable training rows, taken again from `f`, the table
        it was fitted on; refused (ValueError) when those are not as many rows as it was fitted on."""
        self._fitted()
        ok = self._usable(f, f["target"])
        if int(ok.sum()) != self.n_train:
            raise ValueError(f"{int(ok.sum())} usable rows, not the {self.n_train} the model was fitted on")
        which = [c for c in self.columns if c.startswith(TERM_PREFIX) or is_lag(c)]
        return vif(self._design(f[ok]), self.columns, which)


# The models v2 fits: key -> (family, temperature input). The OLS keys are v1's scenario names, so `pred_persistence`
# keeps its meaning (v1 as shipped). Bounds and sensitivities (observed weather, the late forecast) are validation
# only and never choose (plan section 4.3).
MODELS = {"gam_forecast": ("gam", "forecast"), "gam_observed": ("gam", "observed"),
          "gam_forecast_late": ("gam", "forecast_late"),
          "forecast": ("ols", "forecast"), "persistence": ("ols", "persistence"), "observed": ("ols", "observed")}
PRODUCTION = "gam_forecast"
# The comparison models (plan section 4.2): they never recommend and never set a threshold.
COMPARATORS = {"family": "forecast", "shipped": "persistence"}


@dataclass(frozen=True)
class ModelSpec:
    """A model of v3's registry (docs/plan-v3.md, section 4): its family (gam or ols), its temperature input
    (`scenario`), the wind input its wind term reads (`wind`, None when it has no such term), its issue time (whose
    demand lags it reads, `lags`), its terms (a subset of issue.TERMS, in their order; least squares has none), the
    columns whose finite values make a row usable (`required_inputs`; None: its own design's rule, v2's), and its
    interval: one half-width (single, v2's) or one per temperature bin (binned, section 7)."""

    family: str
    scenario: str
    wind: str | None = None
    terms: tuple = ()
    required_inputs: tuple | None = None
    interval: str = "single"
    issue: IssueSpec = MIDNIGHT

    def __post_init__(self):
        if self.family not in ("gam", "ols"):
            raise ValueError(f"unknown family {self.family!r}")
        if self.interval not in INTERVALS:
            raise ValueError(f"interval {self.interval!r} is not one of {INTERVALS}")
        if tuple(self.terms) != _terms(self.terms):
            raise ValueError(f"terms {list(self.terms)} are not in issue.TERMS' order {list(TERMS)}")
        if self.family == "ols" and self.terms:
            raise ValueError("least squares reads no term")
        if ("wind_cold" in self.terms) != (self.wind is not None):
            raise ValueError("a wind input is given exactly when a term reads it (wind_cold)")
        object.__setattr__(self, "terms", tuple(self.terms))
        if self.required_inputs is not None:
            object.__setattr__(self, "required_inputs", tuple(self.required_inputs))

    @property
    def lags(self) -> tuple[str, ...]:
        """The issue time's demand lags: no lag of 24 h enters a noon model (docs/plan-v3.md, section 12)."""
        return self.issue.lag_columns

    @property
    def inputs(self) -> tuple[str, ...]:
        """The raw columns that decide the model's rows besides the calendar: those it reads (its temperature, the
        terms' inputs, its lags), then its required inputs not among them, in order. A frozen model or comparator
        built from them applies the same row mask (`required_known`) as the fit: v2's and v1's forms at noon list
        v3's wind, day's mean and lag day's mean though they read none of them."""
        read = (f"temp_{self.scenario}", *term_inputs(self.terms, self.scenario, self.wind), *self.lags)
        return tuple(dict.fromkeys((*read, *(self.required_inputs or ()))))


def noon_inputs(scenario: str = "forecast", wind: str = "forecast") -> tuple[str, ...]:
    """What v3 reads at noon on these inputs (issue.py's column contract): the hour's temperature, the hour's wind,
    the day's mean from the same run, the lag day's observed mean, lag48 and lag168."""
    return (f"temp_{scenario}", f"wind_{wind}", f"daymean_{scenario}", "lagday_observed", *NOON.lag_columns)


# v3's usable rows (docs/plan-v3.md, sections 4.2 and 5): every noon model of the decision rule, and each term left out
# in turn, fits and forecasts exactly the rows where v3's inputs are all finite.
V3_INPUTS = noon_inputs()


def _noon(family: str, terms=TERMS, scenario: str = "forecast", wind: str | None = "forecast",
          required_inputs=V3_INPUTS, interval: str = "binned") -> ModelSpec:
    return ModelSpec(family, scenario, wind if "wind_cold" in terms else None, tuple(terms), required_inputs,
                     interval, NOON)


# The models v3 fits (docs/plan-v3.md, section 4): v3; the comparison models, which never recommend and never set a
# threshold (section 4.2): v2's form at noon and v1's form at noon, both on v3's usable rows, and v2's form at
# midnight, v2's model exactly (MODELS["gam_forecast"]) at v2's issue time; and the bounds and sensitivities of
# section 4.3, validation only, labelled, never choosing: each term left out in turn (on v3's usable rows), the
# six-hour availability margin (forecast_late) and the observed-weather bound (the station's temperature and day's
# mean, the NBM wind kept), each on its own inputs' rows.
MODELS_V3 = {
    "v3": _noon("gam"),
    "v2_noon": _noon("gam", terms=()),
    "v1_noon": _noon("ols", terms=()),
    "v2_midnight": ModelSpec("gam", "forecast"),
    **{f"v3_no_{name}": _noon("gam", terms=tuple(t for t in TERMS if t != term), interval="single")
       for name, term in (("day_mean", "day_mean_cold"), ("lag_day", "lag_day_mean_cold"), ("wind", "wind_cold"))},
    "v3_late": _noon("gam", scenario="forecast_late", wind="forecast_late",
                     required_inputs=noon_inputs("forecast_late", "forecast_late"), interval="single"),
    "v3_observed": _noon("gam", scenario="observed", wind="forecast",
                         required_inputs=noon_inputs("observed", "forecast"), interval="single"),
}
V3_PRODUCTION = "v3"
# The comparison models by the contrast each enters (section 8.1): terms = v3 - v2's form at noon, family = v2's form
# at noon - v1's form at noon, horizon = v2's form at noon - v2's form at midnight.
V3_COMPARATORS = {"terms": "v2_noon", "family": "v1_noon", "horizon": "v2_midnight"}
NOON_KEYS = ("v3", "v2_noon", "v1_noon")  # the three noon forecasts of the common set and the window ranking


def from_spec(spec: ModelSpec) -> OLSForecaster | GAMForecaster:
    """A fresh, unfitted forecaster of a v3 model."""
    binned = spec.interval == "binned"
    if spec.family == "ols":
        return OLSForecaster(spec.scenario, lags=spec.lags, required_inputs=spec.required_inputs, binned=binned)
    return GAMForecaster(spec.scenario, lags=spec.lags, terms=spec.terms, wind=spec.wind,
                         required_inputs=spec.required_inputs, binned=binned)


def forecaster(key: str, registry: dict | None = None) -> OLSForecaster | GAMForecaster:
    """A fresh, unfitted forecaster for one key of MODELS (v2's), or of the registry given (MODELS_V3)."""
    models = MODELS if registry is None else registry
    if key not in models:
        raise ValueError(f"unknown model {key!r}; the models are {sorted(models)}")
    entry = models[key]
    if isinstance(entry, ModelSpec):
        return from_spec(entry)
    family, scenario = entry
    return GAMForecaster(scenario) if family == "gam" else OLSForecaster(scenario)
