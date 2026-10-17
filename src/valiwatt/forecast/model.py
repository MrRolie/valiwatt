"""The models: a seasonal baseline, v1's least squares on named features, and v2's production model, the spline model
("the GAM"), exactly as the owner's ruling fixes it (`docs/plan-v2.md`, section 4).

The GAM is an additive model of the GAM family fitted by penalized least squares: v1's 72 hour-by-day-type cells, one
cubic spline curve of the temperature for each local hour of the day (11 basis columns times 24 hour indicators), and
the 24-hour and 168-hour demand lags, standardized, then ridge regression. The spline's knots follow each fit's own
usable training rows by a fixed rule (`knot_rule`), checked by the fit (`check_knots`); every other setting is fixed
here. Past the coldest and warmest training temperatures each curve continues as a straight line.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.preprocessing import SplineTransformer, StandardScaler

from valiwatt.forecast.guards import LookaheadError, require_forecast_known

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

LAGS = ("lag24", "lag168")


def block_of(hour: pd.Series) -> pd.Series:
    out = pd.Series(index=hour.index, dtype=object)
    for name, hours in HOUR_BLOCKS.items():
        out[hour.isin(list(hours))] = name
    return out


def design(f: pd.DataFrame, scenario: str) -> pd.DataFrame:
    """Named columns, one per coefficient. Rows with any missing input come out with NaN and are never imputed."""
    cols = {}
    for dt in DAY_TYPES:
        for h in range(24):
            cols[f"cell:{dt}:{h:02d}"] = ((f["day_type"] == dt) & (f["hour"] == h)).astype(float)
    blk = block_of(f["hour"])
    for name in HOUR_BLOCKS:
        in_block = (blk == name).astype(float)
        cols[f"heating:{name}"] = f[f"hdh_{scenario}"] * in_block
        cols[f"cooling:{name}"] = f[f"cdh_{scenario}"] * in_block
    cols["lag24"] = f["lag24"]
    cols["lag168"] = f["lag168"]
    return pd.DataFrame(cols, index=f.index)


def usable_rows(X: pd.DataFrame, y: pd.Series | None = None) -> pd.Series:
    ok = np.isfinite(X.to_numpy()).all(axis=1)
    if y is not None:
        ok &= np.isfinite(y.to_numpy())
    return pd.Series(ok, index=X.index)


def require_input_known(f: pd.DataFrame, scenario: str, family: str) -> None:
    """A fit's own check on the one input it reads: on a weather forecast (forecast, forecast_late), every row holding
    a finite temp_, hdh_ or cdh_ of it must carry its run's availability, at or before its origin, and its origin
    (guards.forecast_violations), whoever supplied the rows. The observed and persistence inputs check nothing."""
    require_forecast_known(f, (scenario,), f"the {family} fit on {scenario}")


@dataclass
class OLSForecaster:
    scenario: str
    columns: list = field(default_factory=list)
    beta: np.ndarray | None = None
    n_train: int = 0

    def fit(self, f: pd.DataFrame) -> "OLSForecaster":
        """Fit on the usable rows (target and every input of `design` present), nothing imputed. A fit on a weather
        forecast first refuses any row whose forecast was not known at its origin (`require_input_known`)."""
        require_input_known(f, self.scenario, "least squares")
        X = design(f, self.scenario)
        ok = usable_rows(X, f["target"])
        Xa, y = X[ok].to_numpy(), f.loc[ok, "target"].to_numpy()
        self.beta, *_ = np.linalg.lstsq(Xa, y, rcond=None)
        self.columns = list(X.columns)
        self.n_train = int(ok.sum())
        return self

    def predict(self, f: pd.DataFrame) -> pd.Series:
        if self.beta is None:
            raise RuntimeError("model not fitted")
        X = design(f, self.scenario)[self.columns]
        ok = usable_rows(X)
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


def _spline(knots=KNOTS) -> SplineTransformer:
    return SplineTransformer(n_knots=N_KNOTS, degree=DEGREE, knots=knots, extrapolation=EXTRAPOLATION,
                             include_bias=INCLUDE_BIAS)


def _place_knots(temps: np.ndarray) -> np.ndarray:
    """The base knots scikit-learn's quantile placement puts on a fit's training temperatures (the spike's
    transformer, fitted). The one place a fit's knots come from; `check_knots` then checks them against the rule."""
    sp = _spline().fit(np.asarray(temps, dtype=float).reshape(-1, 1))
    return np.array(sp.bsplines_[0].t[DEGREE:-DEGREE], dtype=float)


def gam_basis(temps: np.ndarray, knots: np.ndarray) -> np.ndarray:
    """The spline basis of these temperatures, the transformer rebuilt from the 10 stored base knots with the ruled
    degree, extrapolation and bias setting; equal to the fitted transformer's output. A missing temperature gives a
    row of NaN (never imputed)."""
    k = np.asarray(knots, dtype=float).reshape(-1, 1)
    sp = _spline(knots=k).fit(k)
    t = np.asarray(temps, dtype=float).ravel()
    ok = np.isfinite(t)
    out = np.full((len(t), sp.n_features_out_), np.nan)
    if ok.any():
        out[ok] = sp.transform(t[ok].reshape(-1, 1))
    return out


def _n_basis() -> int:
    return N_KNOTS + DEGREE - 1 - (0 if INCLUDE_BIAS else 1)


def gam_columns() -> list[str]:
    """The GAM's column names, in `gam_design`'s order: 72 cells, 24 x 11 spline columns (hour major), the lags."""
    cells = [f"cell:{dt}:{h:02d}" for dt in DAY_TYPES for h in range(24)]
    spline = [f"spline:{h:02d}:{k:02d}" for h in range(24) for k in range(_n_basis())]
    return cells + spline + list(LAGS)


def gam_design(f: pd.DataFrame, scenario: str, knots: np.ndarray) -> np.ndarray:
    """The GAM's columns, in the spike's order: the 72 cells (day type major, hour minor, as `design`), then the
    spline basis times each local-hour indicator (hour major, basis minor: each hour has its own curve), then lag24
    and lag168. A row with a missing temperature or lag has NaN in those columns."""
    B = gam_basis(f[f"temp_{scenario}"].to_numpy(dtype=float), knots)
    cells = np.column_stack([((f["day_type"] == dt) & (f["hour"] == h)).to_numpy(float)
                             for dt in DAY_TYPES for h in range(24)])
    hours = np.column_stack([(f["hour"] == h).to_numpy(float) for h in range(24)])
    tensor = (hours[:, :, None] * B[:, None, :]).reshape(len(f), -1)
    return np.column_stack([cells, tensor, f["lag24"].to_numpy(dtype=float), f["lag168"].to_numpy(dtype=float)])


def gam_predict(X: np.ndarray, center, scale, coef, intercept) -> np.ndarray:
    """The one prediction path of the GAM, fitted or frozen: the scaler, then the ridge's linear predictor."""
    return ((np.asarray(X, dtype=float) - np.asarray(center, dtype=float)) / np.asarray(scale, dtype=float)) \
        @ np.asarray(coef, dtype=float) + float(intercept)


def gam_partial_effects(knots, coef, scale, temps: np.ndarray, ref_c: float = 18.0) -> pd.DataFrame:
    """Each hour's temperature curve relative to its value at `ref_c`, in MW: for hour h,
    sum_k coef[h,k] / scale[h,k] * (B_k(T) - B_k(ref_c)); the scaler's centring cancels in the difference."""
    t = np.asarray(temps, dtype=float).ravel()
    B = gam_basis(t, knots) - gam_basis(np.array([ref_c]), knots)
    nb = B.shape[1]
    first = len(DAY_TYPES) * 24
    w = (np.asarray(coef, dtype=float) / np.asarray(scale, dtype=float))[first:first + 24 * nb].reshape(24, nb)
    effect = B @ w.T  # (temps, hours)
    return pd.DataFrame({"hour": np.repeat(np.arange(24), len(t)), "temp_c": np.tile(t, 24),
                         "effect_mw": effect.T.ravel()})


def gam_lag_coefficients(coef, scale) -> dict:
    """The two lag coefficients in MW per MW: the ridge's coefficient on the standardized lag over its scale."""
    c, s = np.asarray(coef, dtype=float), np.asarray(scale, dtype=float)
    return {"lag24": float(c[-2] / s[-2]), "lag168": float(c[-1] / s[-1])}


class GAMForecaster:
    """v2's production model on one temperature input (`scenario`), as the spike measured it."""

    def __init__(self, scenario: str):
        self.scenario = scenario
        self.knots: np.ndarray | None = None
        self.columns: list[str] = []
        self.center: np.ndarray | None = None
        self.scale: np.ndarray | None = None
        self.coef: np.ndarray | None = None
        self.intercept: float = 0.0
        self.n_train: int = 0

    def fit(self, f: pd.DataFrame) -> "GAMForecaster":
        """Fit on the usable rows (v1's rule: target and every input of `design` present), nothing imputed. A fit on a
        weather forecast first refuses any row whose forecast was not known at its origin (`require_input_known`),
        before the knots are placed."""
        require_input_known(f, self.scenario, "GAM")
        ok = usable_rows(design(f, self.scenario), f["target"])
        rows = f[ok]
        temp = f"temp_{self.scenario}"
        knots = _place_knots(rows[temp].to_numpy(dtype=float))
        check_knots(knots, f.loc[ok, temp].to_numpy(dtype=float))
        X = gam_design(rows, self.scenario, knots)
        columns = gam_columns()
        if X.shape[1] != len(columns):
            raise RuntimeError(f"the GAM's design has {X.shape[1]} columns, not the {len(columns)} it names")
        scaler = StandardScaler().fit(X)
        ridge = Ridge(alpha=RIDGE_ALPHA).fit(scaler.transform(X), rows["target"].to_numpy(dtype=float))
        self.knots = knots
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
        ok = usable_rows(design(f, self.scenario))
        out = pd.Series(np.nan, index=f.index)
        if ok.any():
            X = gam_design(f[ok], self.scenario, self.knots)
            out[ok] = gam_predict(X, self.center, self.scale, self.coef, self.intercept)
        return out

    def partial_effects(self, temps: np.ndarray, ref_c: float = 18.0) -> pd.DataFrame:
        self._fitted()
        return gam_partial_effects(self.knots, self.coef, self.scale, temps, ref_c)

    def lag_coefficients(self) -> dict:
        self._fitted()
        return gam_lag_coefficients(self.coef, self.scale)


# The models v2 fits: key -> (family, temperature input). The OLS keys are v1's scenario names, so `pred_persistence`
# keeps its meaning (v1 as shipped). Bounds and sensitivities (observed weather, the late forecast) are validation
# only and never choose (plan section 4.3).
MODELS = {"gam_forecast": ("gam", "forecast"), "gam_observed": ("gam", "observed"),
          "gam_forecast_late": ("gam", "forecast_late"),
          "forecast": ("ols", "forecast"), "persistence": ("ols", "persistence"), "observed": ("ols", "observed")}
PRODUCTION = "gam_forecast"
# The comparison models (plan section 4.2): they never recommend and never set a threshold.
COMPARATORS = {"family": "forecast", "shipped": "persistence"}


def forecaster(key: str) -> OLSForecaster | GAMForecaster:
    """A fresh, unfitted forecaster for one key of MODELS."""
    if key not in MODELS:
        raise ValueError(f"unknown model {key!r}; the models are {sorted(MODELS)}")
    family, scenario = MODELS[key]
    return GAMForecaster(scenario) if family == "gam" else OLSForecaster(scenario)
