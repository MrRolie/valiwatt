"""The frozen models: the production model (v2: the GAM on the NBM forecast) and its two comparison models, refitted
once on the design years, then frozen.

Two freezes use the same code. The one for the 2024 replay is fitted on 2019-2023; the one for the 2023
pseudo-replay is fitted on 2019-2022. Both keep the 7-day embargo before their first replayed day, so the
pseudo-replay rehearses exactly what the 2024 replay does. Each model's interval half-width is the conformal quantile
of its own relative out-of-sample scores of the 12 validation folds just before the replayed year, those of the days
the replay would have scored by the fit clock (evaluate.calibration_rows: the eve of the replayed year never
calibrates it, in either freeze): the same rule the validation intervals used, applied to one more month. The fit sees
the demand as the replay would at its fit clock, the origin of the replayed year's first day, and each training day
carries the forecast issued for it (`frozen_table`).

A frozen model is a JSON file of numbers, no pickle (`FrozenModel`), whose SHA-256 covers everything its forecast
depends on. One MLflow run per replayed year holds the production model, registered as a model version with the alias
`replay-<year>`, and the two comparison models as hashed files; the replay loads them through the alias and the run,
and refuses any whose hash differs (docs/plan-v2.md, section 4.4).

A v3 model (docs/plan-v3.md, section 4.4) is the same file with more fields, each absent from a v2 model's file and
hash: its demand lags (lag48 and lag168 at noon), its terms and their definitions, the rows it forecasts (its required
inputs) and its interval layer by temperature (section 7). v3's freeze itself is forecast/freeze_v3.py.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, fields

import numpy as np
import pandas as pd

from valiwatt.config import DESIGN_LAST
from valiwatt.forecast import features, model
from valiwatt.forecast import walkforward as wf
from valiwatt.forecast.evaluate import (
    ALPHA,
    CALIBRATION_FOLDS,
    bin_of,
    calibration_rows,
    check_interval_layer,
    conformal_quantile,
)
from valiwatt.forecast.features import BASE_C
from valiwatt.forecast.guards import LookaheadError, require_design_window, require_embargo, require_known_at_origin
from valiwatt.forecast.model import COMPARATORS, MODELS, PRODUCTION, design, usable_rows

EXPERIMENT = "valiwatt-frozen-model"
MODEL_NAME = "valiwatt-demand-gam-forecast"
SCENARIO = "persistence"  # v1's model and input: fit_frozen's default key
FAMILIES = ("ols", "gam")
# The partial-effects table (plan section 4.5): each hour's curve on a grid of whole degrees from this far below the
# training range to this far above it, the part outside the range marked.
PARTIAL_EFFECTS_STEP_C, PARTIAL_EFFECTS_MARGIN_C = 1.0, 5.0


def input_columns(family: str, scenario: str) -> list[str]:
    """The feature columns a frozen model of this family reads (its MLflow input example): both families read the
    temperature itself, least squares computing its degree-hours with its stored base temperature."""
    return ["day_type", "hour", f"temp_{scenario}", "lag24", "lag168"]


# The frozen least squares' input columns on v1's input (persistence): the temperature, its degree-hours computed
# with the stored base, where v1's model read the table's hdh_persistence and cdh_persistence.
INPUT_COLUMNS = input_columns("ols", SCENARIO)


class FrozenModelError(RuntimeError):
    pass


def frozen_sha256(family: str, scenario: str, columns, knots, degree, extrapolation, include_bias, center, scale,
                  beta, intercept, q, hour_blocks=None, base_c=None, *, lags=None, terms=(), wind=None,
                  term_definitions=None, required_inputs=None, interval=None) -> str:
    """The hash of what a frozen model's forecast and interval depend on: its family and input, the named columns, the
    spline's knots and its three settings, the scaler's means and scales, the coefficients and intercept, and the
    half-width; for least squares also its hour blocks (each name and its hours, in column order) and its base
    temperature, which its degree-hours are computed with (a GAM reads neither, and its hash is as before). A
    function of the values, so the hash of any setting can be computed, a refused one included.

    v3's fields (docs/plan-v3.md, section 4.4) enter the head only when given, so a v2 model's hash is as before: its
    lags other than v2's, its terms, the wind they read, their definitions, its required inputs and its interval layer
    (the bin edge, the two half-widths and the pooled one, the cold bin's count and weeks, and its fallback)."""
    arrays = [np.ascontiguousarray(np.asarray(a, dtype=float).ravel(), dtype="<f8")
              for a in (knots, center, scale, beta)]
    head = {"family": str(family), "scenario": str(scenario), "columns": [str(c) for c in columns],
            "degree": int(degree), "extrapolation": str(extrapolation), "include_bias": bool(include_bias),
            "lengths": [int(a.size) for a in arrays]}
    if family == "ols":
        if hour_blocks is None or base_c is None:
            raise FrozenModelError("a least-squares model's hash needs its hour blocks and its base temperature")
        head |= {"hour_blocks": [[name, list(hours)] for name, hours in model.hour_blocks(hour_blocks)],
                 "base_c": repr(float(base_c))}
    v3 = {"lags": None if lags is None else [str(c) for c in lags], "terms": [str(t) for t in terms] or None,
          "wind": None if wind is None else str(wind),
          "term_definitions": None if term_definitions is None else {k: repr(float(v)) for k, v in
                                                                     dict(term_definitions).items()},
          "required_inputs": None if required_inputs is None else [str(c) for c in required_inputs],
          "interval": None if interval is None else {k: repr(v) for k, v in dict(interval).items()}}
    head |= {k: v for k, v in v3.items() if v is not None}
    h = hashlib.sha256()
    h.update(json.dumps(head, sort_keys=True).encode())
    for a in arrays:
        h.update(b"\0" + a.tobytes())
    h.update(b"\0" + repr(float(intercept)).encode() + b"\0" + repr(float(q)).encode())
    return h.hexdigest()


def _floats(values) -> tuple:
    return tuple(float(v) for v in np.asarray(values, dtype=float).ravel())


def _stored_blocks(raw) -> tuple:
    """A least-squares model's hour blocks as (name, hours) pairs, in their column order, from a mapping or from
    pairs. Refused (FrozenModelError) unless they partition the hours 0 to 23: each hour, an integer, in exactly one
    block, each block named once and holding at least one hour."""
    try:
        pairs = list(raw.items()) if isinstance(raw, dict) else [tuple(p) for p in raw]
    except TypeError as e:
        raise FrozenModelError(f"hour blocks {raw!r} are neither a mapping nor (name, hours) pairs") from e
    out = []
    for p in pairs:
        if len(p) != 2 or not isinstance(p[0], str) or not p[0] or isinstance(p[1], (str, bytes)):
            raise FrozenModelError(f"hour block {p!r} is not a name and its hours")
        hours = list(p[1])
        if not hours or any(isinstance(h, bool) or not isinstance(h, (int, np.integer)) for h in hours):
            raise FrozenModelError(f"hour block {p[0]!r} holds {hours!r}, not one or more whole hours")
        out.append((p[0], tuple(int(h) for h in hours)))
    names = [name for name, _ in out]
    every = sorted(h for _, hours in out for h in hours)
    if len(set(names)) != len(names) or every != list(range(24)):
        raise FrozenModelError(f"hour blocks {dict(out)} do not partition the hours 0 to 23, each hour in exactly one "
                               "block, each block named once")
    return tuple(out)


def _stored_base(raw) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float, np.integer, np.floating)) or not np.isfinite(raw):
        raise FrozenModelError(f"base temperature {raw!r} is not a finite number of degrees Celsius")
    return float(raw)


def _refuse_uncovered_day_types(f: pd.DataFrame, day_types: tuple, whose: str) -> None:
    """A row whose day type the stored cells do not cover would get all-zero cells: refused, never forecast."""
    stray = f["day_type"].notna() & ~f["day_type"].isin(day_types)
    if stray.any():
        raise FrozenModelError(
            f"{int(stray.sum())} rows have a day type the frozen {whose} stored cells do not cover: "
            f"{sorted(set(f.loc[stray, 'day_type'].astype(str)))}, stored {list(day_types)}")


@dataclass(frozen=True)
class FrozenModel:
    """A frozen model: v1's least squares ("ols", `columns` and `beta` on `model.design`'s named columns) or the GAM
    ("gam": the 10 base knots, the scaler's means and scales, the ridge coefficients and intercept on
    `model.gam_columns`, with the spline's three settings), its half-width `q`, training end and calibration folds.

    Refused at construction (FrozenModelError): a family other than FAMILIES; a degree, extrapolation or bias setting
    other than the owner's ruling (model.DEGREE, model.EXTRAPOLATION, model.INCLUDE_BIAS), for either family; a GAM
    whose knots are not model.N_KNOTS finite increasing values; coefficients, means or scales that do not match the
    columns; least squares' hour blocks that do not partition the hours 0 to 23, or a base temperature that is not a
    finite number; a GAM carrying either. A GAM forecasts, and draws its curves, from its stored values only
    (`gam_spec`), whatever the module's settings say once it is built; least squares likewise from its stored day
    types, hour blocks and base temperature (`ols_spec`), its degree-hours computed from the temperature with that
    base. A least-squares model built without blocks or base takes the module's (model.HOUR_BLOCKS,
    features.BASE_C) at construction, and stores and hashes them from then on.

    v3's fields (docs/plan-v3.md, section 4.4), each defaulting to v2 and then absent from the file and the hash:
    - lags: the demand lags it reads (None: v2's, model.LAGS; v2's lags given are stored as None);
    - terms, wind, term_definitions: a GAM's terms (model.term_columns), the wind input wind with cold reads (given
      exactly when that term is), and their definitions, which must be the ruled ones (issue.TERM_DEFINITIONS,
      model.require_term_definitions), given exactly when it has a term;
    - required_inputs: the columns whose finite values make a row forecast (model.required_known), so a comparison
      model at noon forecasts only v3's usable rows (section 4.2);
    - interval: the interval layer by temperature (section 7; evaluate.check_interval_layer), its pooled half-width
      being `q`. q NaN: the model issues no interval (v2's form at midnight in v3).
    Refused at construction otherwise (FrozenModelError)."""

    columns: tuple
    beta: np.ndarray
    q: float  # interval: forecast x (1 - q, 1 + q)
    replay_year: int
    train_last_day: str
    n_train: int
    calibration_folds: tuple = field(default_factory=tuple)
    calibration_scores: int = 0
    family: str = "ols"
    scenario: str = SCENARIO
    knots: tuple = ()
    center: tuple = ()
    scale: tuple = ()
    intercept: float = 0.0
    degree: int = model.DEGREE
    extrapolation: str = model.EXTRAPOLATION
    include_bias: bool = model.INCLUDE_BIAS
    hour_blocks: tuple | None = None  # least squares: (name, hours) pairs, in column order
    base_c: float | None = None  # least squares: the degree-hours' base temperature, in degrees Celsius
    lags: tuple | None = None  # v3: the demand lags (None: v2's)
    terms: tuple = ()  # v3: a GAM's terms, in issue.TERMS' order
    wind: str | None = None  # v3: the wind input of wind with cold
    term_definitions: dict | None = None  # v3: the ruled term definitions, given exactly when it has a term
    required_inputs: tuple | None = None  # v3: the columns a forecast row must hold
    interval: dict | None = None  # v3: the interval layer by temperature (evaluate.check_interval_layer)

    def __post_init__(self):
        beta = np.array(self.beta, dtype=float)
        beta.flags.writeable = False
        object.__setattr__(self, "beta", beta)
        object.__setattr__(self, "columns", tuple(self.columns))
        object.__setattr__(self, "calibration_folds", tuple(self.calibration_folds))
        for name in ("knots", "center", "scale"):
            object.__setattr__(self, name, _floats(getattr(self, name)))
        object.__setattr__(self, "intercept", float(self.intercept))
        if self.family not in FAMILIES:
            raise FrozenModelError(f"frozen model family {self.family!r} is none of {FAMILIES}")
        if isinstance(self.degree, bool) or not isinstance(self.degree, (int, np.integer)) \
                or int(self.degree) != model.DEGREE:
            raise FrozenModelError(f"spline degree {self.degree!r}: the ruled degree is {model.DEGREE}")
        if self.extrapolation != model.EXTRAPOLATION:
            raise FrozenModelError(f"spline extrapolation {self.extrapolation!r}: the ruled setting is "
                                   f"{model.EXTRAPOLATION!r}")
        if not isinstance(self.include_bias, (bool, np.bool_)) or bool(self.include_bias) != model.INCLUDE_BIAS:
            raise FrozenModelError(f"spline include_bias {self.include_bias!r}: the ruled setting is "
                                   f"{model.INCLUDE_BIAS}")
        if len(self.beta) != len(self.columns):
            raise FrozenModelError(f"{len(self.beta)} coefficients for {len(self.columns)} columns")
        if self.family == "gam":
            k = np.asarray(self.knots, dtype=float)
            if k.size != model.N_KNOTS or not np.isfinite(k).all() or not (np.diff(k) > 0).all():
                raise FrozenModelError(f"a frozen GAM needs {model.N_KNOTS} finite increasing knots, got {list(k)}")
            if not (len(self.center) == len(self.scale) == len(self.columns)):
                raise FrozenModelError(f"a frozen GAM needs one mean and one scale per column: {len(self.center)} "
                                       f"and {len(self.scale)} for {len(self.columns)} columns")
            if self.hour_blocks is not None or self.base_c is not None:
                raise FrozenModelError("a frozen GAM stores no hour blocks and no base temperature: it reads neither")
        else:
            object.__setattr__(self, "hour_blocks", _stored_blocks(
                model.HOUR_BLOCKS if self.hour_blocks is None else self.hour_blocks))
            object.__setattr__(self, "base_c", _stored_base(
                features.BASE_C if self.base_c is None else self.base_c))
        object.__setattr__(self, "degree", int(self.degree))
        object.__setattr__(self, "include_bias", bool(self.include_bias))
        self._v3_fields()

    def _v3_fields(self) -> None:
        """Normalize and check v3's fields (the class docstring); a v2 model passes with all of them at their
        defaults."""
        lags = None if self.lags is None else tuple(str(c) for c in self.lags)
        if lags is not None and (not lags or not all(model.is_lag(c) for c in lags)):
            raise FrozenModelError(f"frozen model lags {list(lags)} are not demand-lag columns")
        object.__setattr__(self, "lags", None if lags == model.LAGS else lags)
        try:
            object.__setattr__(self, "terms", model._terms(self.terms))
        except ValueError as e:
            raise FrozenModelError(str(e)) from e
        if self.terms and self.family != "gam":
            raise FrozenModelError("least squares reads no term")
        if ("wind_cold" in self.terms) != (self.wind is not None):
            raise FrozenModelError(f"a frozen model's wind input ({self.wind!r}) is given exactly when it reads wind "
                                   f"with cold (terms {list(self.terms)})")
        if bool(self.terms) != (self.term_definitions is not None):
            raise FrozenModelError(f"a frozen model stores its term definitions exactly when it has a term (terms "
                                   f"{list(self.terms)}, definitions {self.term_definitions!r})")
        if self.term_definitions is not None:
            try:
                defs = model.require_term_definitions(self.term_definitions, "the frozen model")
            except ValueError as e:
                raise FrozenModelError(str(e)) from e
            object.__setattr__(self, "term_definitions", {k: self.term_definitions[k] for k in defs})
        if self.required_inputs is not None:
            object.__setattr__(self, "required_inputs", tuple(str(c) for c in self.required_inputs))
        if self.interval is not None:
            try:
                layer = check_interval_layer(dict(self.interval))
            except ValueError as e:
                raise FrozenModelError(f"the frozen model's interval layer: {e}") from e
            if float(self.q) != layer["q_pooled"]:
                raise FrozenModelError(f"the frozen model's half-width q={self.q!r} is not its interval layer's pooled "
                                       f"one ({layer['q_pooled']!r})")
            object.__setattr__(self, "interval", layer)

    def _design_lags(self) -> dict:
        """The lags, terms and their reading of `model.design` / `model.gam_design` for this model: v2's when none."""
        out: dict = {"lags": self.lags}
        if self.family == "gam":
            out |= {"terms": self.terms, "wind": self.wind, "term_definitions": self.term_definitions}
        return out

    def sha256(self) -> str:
        """Hash of everything the forecast and its interval depend on (`frozen_sha256`)."""
        return frozen_sha256(self.family, self.scenario, self.columns, self.knots, self.degree, self.extrapolation,
                             self.include_bias, self.center, self.scale, self.beta, self.intercept, self.q,
                             hour_blocks=self.hour_blocks, base_c=self.base_c, lags=self.lags, terms=self.terms,
                             wind=self.wind, term_definitions=self.term_definitions,
                             required_inputs=self.required_inputs, interval=self.interval)

    def ols_spec(self) -> dict:
        """What a frozen least squares' design is rebuilt from, all of it stored and hashed: the day types in the order
        of its stored cell columns, its hour blocks and its base temperature (keyword arguments of model.design).
        Refused (FrozenModelError) unless the columns those day types and blocks generate (model.ols_columns) are its
        stored columns, name for name and in order."""
        day_types = tuple(dict.fromkeys(c.split(":")[1] for c in self.columns if c.startswith("cell:")))
        generated = tuple(model.ols_columns(day_types=day_types, blocks=self.hour_blocks, lags=self.lags))
        if generated != self.columns:
            i = next(i for i, (a, b) in enumerate(zip(self.columns + (None,), generated + (None,))) if a != b)
            raise FrozenModelError(
                f"the frozen least squares' {len(self.columns)} stored columns are not the {len(generated)} columns "
                f"its stored day types and hour blocks generate: column {i} is {self.columns[i:i + 1]} stored, "
                f"{generated[i:i + 1]} generated")
        return {"day_types": day_types, "blocks": self.hour_blocks, "base_c": self.base_c}

    def gam_spec(self) -> dict:
        """What a frozen GAM's design is rebuilt from, all of it stored and hashed: the day types in the order of its
        stored cell columns, and its spline's degree, extrapolation and bias setting (keyword arguments of
        model.gam_design and model.gam_partial_effects). Refused (FrozenModelError) unless the columns that design
        generates, with its stored knots, are its stored columns, name for name and in order."""
        day_types = tuple(dict.fromkeys(c.split(":")[1] for c in self.columns if c.startswith("cell:")))
        spline = {"degree": self.degree, "extrapolation": self.extrapolation, "include_bias": self.include_bias}
        knots = np.asarray(self.knots)
        n_basis = model.gam_basis(knots[:1], knots, **spline).shape[1]
        generated = tuple(model.gam_columns(day_types=day_types, n_basis=n_basis, lags=self.lags, terms=self.terms))
        if generated != self.columns:
            i = next(i for i, (a, b) in enumerate(zip(self.columns + (None,), generated + (None,))) if a != b)
            raise FrozenModelError(
                f"the frozen GAM's {len(self.columns)} stored columns are not the {len(generated)} columns its stored "
                f"knots, settings and day types generate: column {i} is {self.columns[i:i + 1]} stored, "
                f"{generated[i:i + 1]} generated")
        return {"day_types": day_types, **spline}

    def forecast(self, f: pd.DataFrame) -> pd.Series:
        """The forecast of every row whose inputs are all present; NaN otherwise (never imputed). The GAM predicts
        through model.gam_design and model.gam_predict, the path validation's fits predict through, its design rebuilt
        from the stored knots, settings and column order (`gam_spec`, which refuses columns it cannot rebuild). Least
        squares predicts through model.design, the function validation's fits predict through, given its stored day
        types, hour blocks and base temperature (`ols_spec`): its degree-hours are computed from temp_<scenario> with
        that base, never read from the table's hdh_ and cdh_ columns. For both, a row whose day type the stored cells
        do not cover is refused (FrozenModelError); a row with a missing day type or an hour outside 0 to 23 is a row
        with a missing input, NaN. A v3 model reads its stored lags and terms, and forecasts no row missing one of its
        required inputs (model.required_known)."""
        out = pd.Series(np.nan, index=f.index)
        if self.family == "gam":
            spec = self.gam_spec()
            _refuse_uncovered_day_types(f, spec["day_types"], "GAM's")
            X = model.gam_design(f, self.scenario, np.asarray(self.knots), **spec, **self._design_lags())
            ok = np.isfinite(X).all(axis=1)
            if self.required_inputs is not None:
                ok &= model.required_known(f, self.required_inputs)
            if ok.any():
                out[ok] = model.gam_predict(X[ok], self.center, self.scale, self.beta, self.intercept)
            return out
        spec = self.ols_spec()
        _refuse_uncovered_day_types(f, spec["day_types"], "least squares'")
        X = design(f, self.scenario, **spec, **self._design_lags())[list(self.columns)]
        ok = usable_rows(X)
        if self.required_inputs is not None:
            ok &= model.required_known(f, self.required_inputs)
        out[ok] = X[ok].to_numpy() @ self.beta
        return out

    def predict(self, f: pd.DataFrame) -> pd.DataFrame:
        """The forecast and its interval, forecast x (1 - q, 1 + q). With an interval layer (docs/plan-v3.md, section
        7), q is the half-width of each row's bin, its temp_<scenario> against the stored edge (evaluate.bin_of:
        cold strictly below it), with columns bin (None without a temperature) and fallback (the layer's: the cold bin
        took the pooled half-width), on every row, so every interval issued says so."""
        forecast = self.forecast(f)
        if self.interval is None:
            return pd.DataFrame({"forecast": forecast, "lower": forecast * (1 - self.q),
                                 "upper": forecast * (1 + self.q)}, index=f.index)
        layer = self.interval
        b = bin_of(f[f"temp_{self.scenario}"].to_numpy(dtype=float), layer["bin_edge"])
        q = np.where(b == "cold", layer["q_cold"], np.where(b == "rest", layer["q_rest"], np.nan))
        return pd.DataFrame({"forecast": forecast, "lower": forecast * (1 - q), "upper": forecast * (1 + q),
                             "bin": pd.Series(b, index=f.index, dtype=object), "fallback": layer["fallback"]},
                            index=f.index)

    def lag_coefficients(self) -> dict:
        """The demand lags' coefficients in MW per MW, each by its stored column (v2's lag24 and lag168, v3's at noon
        lag48 and lag168): the GAM's ridge coefficient over its scale, the least squares' coefficient."""
        out = {}
        for lag in model._lags(self.lags):
            i = self.columns.index(lag)
            out[lag] = float(self.beta[i] / self.scale[i]) if self.family == "gam" else float(self.beta[i])
        return out

    def partial_effects(self, temps) -> pd.DataFrame:
        """Each hour's temperature effect in MW relative to its value at BASE_C, at the temperatures `temps` (columns
        hour, temp_c, effect_mw; hour major): the GAM's curve (model.gam_partial_effects, from its stored values,
        `gam_spec`), or v1's degree-hour lines of the hour's stored block, about its stored base temperature
        (`ols_spec`).

        The GAM's reference temperature is this module's BASE_C, neither stored nor hashed, by design: it only says
        where each displayed curve is zero (a constant shift of each hour's curve), and the GAM's forecast and interval
        never read it, so its file and hash stay those of what it forecasts."""
        t = np.asarray(temps, dtype=float).ravel()
        if self.family == "gam":
            return model.gam_partial_effects(np.asarray(self.knots), self.beta, self.scale, t, ref_c=BASE_C,
                                             **self.gam_spec())
        spec = self.ols_spec()
        base = spec["base_c"]
        b = dict(zip(self.columns, self.beta))
        rows = []
        for h in range(24):
            block = next(name for name, hours in spec["blocks"] if h in hours)
            effect = (b[f"heating:{block}"] * np.maximum(base - t, 0.0)
                      + b[f"cooling:{block}"] * np.maximum(t - base, 0.0))
            rows.append(pd.DataFrame({"hour": h, "temp_c": t, "effect_mw": effect}))
        return pd.concat(rows, ignore_index=True)

    def to_json(self) -> str:
        """Every field, the spline's three settings included, and the hash; least squares' hour blocks as a mapping of
        each name to its hours, in column order, and its base temperature (a GAM's file holds neither, as before).
        Strict JSON: a model that issues no interval (q NaN) writes q as null, and no other value may be NaN."""
        d = {f.name: getattr(self, f.name) for f in fields(self)}
        q = None if np.isnan(self.q) else float(self.q)
        d |= {"columns": list(self.columns), "beta": [float(b) for b in self.beta], "q": q,
              "calibration_folds": list(self.calibration_folds), "knots": list(self.knots),
              "center": list(self.center), "scale": list(self.scale)}
        if self.family == "gam":
            del d["hour_blocks"], d["base_c"]
        else:
            d["hour_blocks"] = {name: list(hours) for name, hours in self.hour_blocks}
        # v3's fields: written when given, so a v2 model's file is as before.
        for name in ("lags", "terms", "wind", "term_definitions", "required_inputs", "interval"):
            if getattr(self, name) in (None, ()):
                del d[name]
            elif isinstance(d[name], tuple):
                d[name] = list(d[name])
        return json.dumps(d | {"sha256": self.sha256()}, indent=2, allow_nan=False)

    @classmethod
    def from_json(cls, text: str) -> "FrozenModel":
        """Refused (FrozenModelError): a file that no longer matches its own hash, and, at construction, a setting
        other than the ruled one. A file without the spline settings reads the ruled defaults, and its hash must still
        match. A least-squares file is checked on load as its forecast checks it (`ols_spec`): its columns must be
        those its stored day types and hour blocks generate. q null: the model issues no interval (q NaN)."""
        d = json.loads(text)
        if not isinstance(d, dict) or "sha256" not in d:
            raise FrozenModelError("frozen model file holds no hash")
        expected = d.pop("sha256")
        if "q" in d and d["q"] is None:
            d["q"] = float("nan")
        try:
            m = cls(**d)
        except TypeError as e:
            raise FrozenModelError(f"frozen model file has fields other than a frozen model's: {e}") from e
        if m.sha256() != expected:
            raise FrozenModelError("frozen model file does not match its own hash")
        if m.family == "ols":
            m.ols_spec()
        return m


def require_echo_premise(m: FrozenModel) -> None:
    """Rule 2's echo exclusion assumes both demand lags push the forecast the same way as the demand they read (plan
    sections 4.4 and 9.2): the freeze refuses a production model whose lag coefficients, in MW per MW, are not both
    positive."""
    lags = m.lag_coefficients()
    if not all(v > 0 for v in lags.values()):
        raise FrozenModelError(f"the echo premise fails: lag coefficients {lags} (MW per MW) are not both positive")


def calibration_folds(replay_year: int) -> list[str]:
    """The CALIBRATION_FOLDS months just before the replayed year."""
    first = pd.Period(f"{replay_year}-01", freq="M")
    return [str(first - k) for k in range(CALIBRATION_FOLDS, 0, -1)]


def fit_clock(replay_year: int) -> pd.Timestamp:
    """When the frozen model is fitted: the origin of the replayed year's first day, as for a validation fold."""
    return wf.folds(f"{replay_year}-01", f"{replay_year}-01")[0].fit_clock


def training_table(asof, temperature: pd.Series, replay_year: int, forecasts: dict | None = None) -> pd.DataFrame:
    """The feature table the frozen models for `replay_year` are fitted on: the demand as of their fit clock, each day
    with the forecast issued at its own origin."""
    return asof.table(fit_clock(replay_year), temperature, forecasts)


def frozen_table(raw_demand: pd.DataFrame, weather: pd.DataFrame, replay_year: int,
                 last: pd.Timestamp = DESIGN_LAST, runs: pd.DataFrame | None = None) -> pd.DataFrame:
    """What `valiwatt freeze` fits on, from the raw demand records, the weather table and the NBM runs of the design
    years: the primary station's table as of the fit clock, with the forecast validation issued for each day
    (validate.forecast_inputs, the same run rule and the same cut). last: the last record loaded."""
    from valiwatt.forecast.features import AsOf
    # validate loads the data-quality and tracking modules, which the replay that imports this one never needs.
    from valiwatt.forecast.validate import design_days, forecast_inputs, temperature_sources

    forecasts = None if runs is None else forecast_inputs(runs, design_days(last), last=last)
    return training_table(AsOf(raw_demand, last=last), temperature_sources(weather)["primary"], replay_year, forecasts)


def _no_lag_gam(rows: pd.DataFrame, scenario: str, knots: np.ndarray, **lags) -> tuple[np.ndarray, np.ndarray]:
    """The GAM without its two lags, on the same rows and knots, for interpretation only (plan section 4.5): its
    ridge coefficients and the scaler's scales, in model.gam_design's order without the last two columns. lags: a v3
    model's lags and terms (FrozenModel._design_lags; v2's when none), the lags being its last columns and its terms
    kept."""
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler

    X = model.gam_design(rows, scenario, knots, **lags)[:, :-len(model._lags(lags.get("lags")))]
    scaler = StandardScaler().fit(X)
    ridge = Ridge(alpha=model.RIDGE_ALPHA).fit(scaler.transform(X), rows["target"].to_numpy(dtype=float))
    return np.asarray(ridge.coef_, dtype=float), np.asarray(scaler.scale_, dtype=float)


def partial_effects_table(frozen: FrozenModel, nolag_coef: np.ndarray, nolag_scale: np.ndarray) -> pd.DataFrame:
    """Each hour's curve relative to its value at BASE_C (plan section 4.5), for the frozen GAM and for the GAM without
    the lags, on whole degrees from PARTIAL_EFFECTS_MARGIN_C below the training range to as far above it; the
    training range is that of the rows the knots were placed on (the first and last knot), and every point outside it,
    where each curve continues as a straight line, is marked."""
    lo, hi = frozen.knots[0], frozen.knots[-1]
    temps = np.arange(np.floor(lo) - PARTIAL_EFFECTS_MARGIN_C, np.ceil(hi) + PARTIAL_EFFECTS_MARGIN_C
                      + PARTIAL_EFFECTS_STEP_C / 2, PARTIAL_EFFECTS_STEP_C)
    pe = frozen.partial_effects(temps)
    nolag = model.gam_partial_effects(np.asarray(frozen.knots), nolag_coef, nolag_scale, temps, ref_c=BASE_C)
    pe["effect_mw_nolag"] = nolag["effect_mw"].to_numpy()
    pe["in_training_range"] = (pe["temp_c"] >= lo) & (pe["temp_c"] <= hi)
    return pe[["hour", "temp_c", "effect_mw", "effect_mw_nolag", "in_training_range"]]


def coefficient_table(frozen: FrozenModel) -> pd.DataFrame:
    """The frozen model's named coefficients; for the GAM also each in MW per unit of its column (beta / scale)."""
    coef = pd.DataFrame({"feature": list(frozen.columns), "frozen": frozen.beta})
    if frozen.family == "gam":
        coef["mw_per_unit"] = frozen.beta / np.asarray(frozen.scale)
    return coef


def fit_frozen(f: pd.DataFrame, oos: pd.DataFrame, replay_year: int,
               key: str = "persistence") -> tuple[FrozenModel, pd.DataFrame]:
    """Fit the frozen model of `key` (a key of model.MODELS; v1's model by default) and its half-width, from
    oos["pred_<key>"] on evaluate.calibration_rows. Returns the model and its extras: for least squares, v1's table of
    its coefficients beside an explanatory fit without the lags; for the GAM, its partial-effects table beside the GAM
    without the lags (fitted on the same rows, for interpretation only).

    For a GAM, before it returns, the freeze checks the stored knots on its own (plan sections 4.1 and 12): they must be
    the quantile rule on temp_<input> of the frozen table's usable training rows, taken again from f
    (model.check_knots), else FrozenModelError."""
    if key not in MODELS:
        raise ValueError(f"unknown model {key!r}; the models are {sorted(MODELS)}")
    family, scenario = MODELS[key]
    fold = wf.folds(f"{replay_year}-01", f"{replay_year}-01")[0]
    train, _ = wf.split(f, fold)
    require_embargo(train, fold.test_first_day, wf.EMBARGO_DAYS)
    require_design_window(train, f"frozen model for {replay_year}")
    require_known_at_origin(train)
    m = model.forecaster(key).fit(train)

    folds = calibration_folds(replay_year)
    missing = sorted(set(folds) - set(oos["fold"]))
    if missing:
        raise FrozenModelError(f"validation has no out-of-sample scores for folds {missing}")
    col = f"pred_{key}"
    if col not in oos:
        raise FrozenModelError(f"validation has no out-of-sample forecasts of {key} ({col})")
    cal = calibration_rows(oos[oos["fold"].isin(folds)], fit_clock(replay_year))
    scores = ((cal["target"] - cal[col]).abs() / cal[col]).to_numpy()
    q = conformal_quantile(scores, ALPHA)
    if not np.isfinite(q):
        raise FrozenModelError("too few calibration scores for the conformal quantile")
    common = dict(q=q, replay_year=replay_year, train_last_day=str(fold.train_last_day.date()), n_train=m.n_train,
                  calibration_folds=folds, calibration_scores=int(np.isfinite(scores).sum()), family=family,
                  scenario=scenario)

    if family == "ols":
        # The blocks and base the fit's design used (model.design reads the module's; features.build computed the
        # table's degree-hours with features.BASE_C), stored and hashed so that the forecast reads only the file.
        frozen = FrozenModel(columns=m.columns, beta=m.beta, hour_blocks=model.hour_blocks(), base_c=features.BASE_C,
                             **common)
        X = design(train, scenario).drop(columns=list(model.LAGS))
        ok = usable_rows(X, train["target"])
        beta_nolag, *_ = np.linalg.lstsq(X[ok].to_numpy(), train.loc[ok, "target"].to_numpy(), rcond=None)
        coef = pd.DataFrame({"feature": m.columns, "frozen": m.beta})
        coef = coef.merge(pd.DataFrame({"feature": list(X.columns), "no_lag": beta_nolag}), on="feature", how="left")
        return frozen, coef

    frozen = FrozenModel(columns=m.columns, beta=m.coef, knots=m.knots, center=m.center, scale=m.scale,
                         intercept=m.intercept, **common)
    rows, _ = wf.split(f, fold)
    ok = usable_rows(design(rows, scenario), rows["target"])
    try:
        model.check_knots(np.asarray(frozen.knots), rows.loc[ok, f"temp_{scenario}"].to_numpy(dtype=float))
    except LookaheadError as e:
        raise FrozenModelError(f"frozen model for {replay_year}: its knots are not the quantile rule on the frozen "
                               f"table's own usable training rows") from e
    nolag_coef, nolag_scale = _no_lag_gam(rows[ok], scenario, np.asarray(frozen.knots))
    return frozen, partial_effects_table(frozen, nolag_coef, nolag_scale)


def run_freeze(replay_year: int) -> int:
    from valiwatt.forecast.validate import DESIGN_YEARS, load_inputs, load_validation
    from valiwatt.monitor.rules import rules_sha256
    from valiwatt.monitor.run import load_raw  # the replay's loader; monitor/run.py imports this module
    from valiwatt.quality import run as qrun  # the one NBM reader, the files the data-quality run checked

    dq, _, weather = load_inputs()
    val, oos = load_validation(dq)
    raw_demand, _ = load_raw(dq)
    runs = qrun.load_runs(dq, years=DESIGN_YEARS)
    f = frozen_table(raw_demand, weather, replay_year, runs=runs)
    production, effects = fit_frozen(f, oos, replay_year, key=PRODUCTION)
    require_echo_premise(production)
    comparators = {key: fit_frozen(f, oos, replay_year, key=key)[0] for key in COMPARATORS.values()}
    # The hour is declared a float in the model signature: an hour without inputs comes through as NaN.
    cols = input_columns(production.family, production.scenario)
    example = f[f["day"] <= pd.Timestamp(production.train_last_day)][cols].dropna().tail(24).astype({"hour": float})
    run, version = log_frozen(production, coefficient_table(production), example,
                              {"validation_run_id": val.info.run_id, "dq_run_id": dq.info.run_id,
                               "rules_sha256": rules_sha256()},
                              comparators=comparators, extras={"partial_effects.csv": effects})
    print(f"frozen models for {replay_year}: run {run.info.run_id}, {MODEL_NAME} v{version} "
          f"(alias replay-{replay_year}), half-width {100 * production.q:.2f} %, sha256 {production.sha256()[:12]}; "
          + ", ".join(f"{k} {100 * m.q:.2f} % {m.sha256()[:12]}" for k, m in comparators.items()))
    return 0


def log_frozen(frozen: FrozenModel, coef: pd.DataFrame, example: pd.DataFrame, tags: dict,
               comparators: dict | None = None, extras: dict | None = None, *, model_name: str = MODEL_NAME,
               params: dict | None = None):
    """Log the frozen model as an MLflow run and a registered model version with the alias replay-<year>.

    comparators: {model key: FrozenModel}, each logged as comparators/<key>.json and its hash as the tag
    comparator_sha256.<key> (load_comparators); extras: {file name: DataFrame}, each logged as a CSV. model_name: the
    registered model (v2's MODEL_NAME unless given: v3's freeze registers its own, so its aliases never move v2's);
    params: more parameters to log (v3's interval layer, terms and variance inflation factors)."""
    import tempfile
    from pathlib import Path

    import mlflow
    import sklearn

    from valiwatt.forecast import pyfunc as pyfunc_module
    from valiwatt.tracking import client, start_run

    replay_year = frozen.replay_year
    comparators = comparators or {}
    with start_run(EXPERIMENT, f"frozen-{replay_year}", tags={
        "kind": "frozen", "replay_year": str(replay_year), "coefficient_sha256": frozen.sha256(),
        "family": frozen.family, "scenario": frozen.scenario,
        **{f"comparator_sha256.{k}": m.sha256() for k, m in comparators.items()}, **tags,
    }) as run:
        mlflow.log_params({"replay_year": replay_year, "train_last_day": frozen.train_last_day,
                           "n_train": frozen.n_train, "q": frozen.q, "alpha": ALPHA,
                           "calibration_first_fold": frozen.calibration_folds[0],
                           "calibration_last_fold": frozen.calibration_folds[-1],
                           "calibration_scores": frozen.calibration_scores, "embargo_days": wf.EMBARGO_DAYS})
        # The constants fixed before any v2 run (plan section 4), and the model's lags in MW per MW.
        mlflow.log_params({"family": frozen.family, "scenario": frozen.scenario, "n_knots": model.N_KNOTS,
                           "knots": model.KNOTS, "degree": frozen.degree, "extrapolation": frozen.extrapolation,
                           "include_bias": frozen.include_bias, "ridge_alpha": model.RIDGE_ALPHA,
                           **{f"{k}_mw_per_mw": v for k, v in frozen.lag_coefficients().items()},
                           "comparators": ",".join(sorted(comparators)),
                           **{f"q.{k}": m.q for k, m in comparators.items()}})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frozen.json"
            path.write_text(frozen.to_json(), encoding="utf-8")
            coef.to_csv(Path(tmp) / "coefficients.csv", index=False)
            mlflow.log_artifact(str(Path(tmp) / "coefficients.csv"))
            for name, table in (extras or {}).items():
                table.to_csv(Path(tmp) / name, index=False)
                mlflow.log_artifact(str(Path(tmp) / name))
            (Path(tmp) / "comparators").mkdir()
            for k, m in comparators.items():
                p = Path(tmp) / "comparators" / f"{k}.json"
                p.write_text(m.to_json(), encoding="utf-8")
                mlflow.log_artifact(str(p), artifact_path="comparators")
            info = mlflow.pyfunc.log_model(
                name="model", python_model=str(Path(pyfunc_module.__file__)), artifacts={"frozen": str(path)},
                input_example=example, registered_model_name=model_name,
                pip_requirements=[f"mlflow=={mlflow.__version__}", f"pandas=={pd.__version__}",
                                  f"numpy=={np.__version__}", f"scikit-learn=={sklearn.__version__}"])
        if params:
            mlflow.log_params(params)
        client().set_registered_model_alias(model_name, f"replay-{replay_year}", info.registered_model_version)
        mlflow.set_tag("model_version", str(info.registered_model_version))
        mlflow.set_tag("status", "complete")
    return run, info.registered_model_version


def load_frozen(replay_year: int, model_name: str = MODEL_NAME):
    """The registered production model for a replay year and its run; refused if its hash differs from the run's.
    model_name: v2's registered model unless given (v3's, freeze_v3.MODEL_NAME_V3)."""
    import mlflow

    from valiwatt.tracking import client

    client()
    py = mlflow.pyfunc.load_model(f"models:/{model_name}@replay-{replay_year}")
    frozen = py.unwrap_python_model().frozen
    run = client().get_run(py.metadata.run_id)
    if frozen.sha256() != run.data.tags.get("coefficient_sha256"):
        raise FrozenModelError(f"registered model for {replay_year} does not match run {run.info.run_id}")
    if frozen.replay_year != replay_year:
        raise FrozenModelError(f"alias replay-{replay_year} points to a model frozen for {frozen.replay_year}")
    return frozen, run


def load_comparators(run) -> dict[str, FrozenModel]:
    """The comparison models frozen in `run` (model.COMPARATORS), by model key: each file must match its own hash and
    the hash its run recorded (tag comparator_sha256.<key>), be the model its key names and be frozen for the run's
    year; anything else is refused (FrozenModelError)."""
    import mlflow

    from valiwatt.tracking import client

    c = client()
    prefix = "comparator_sha256."
    tagged = {k[len(prefix):]: v for k, v in run.data.tags.items() if k.startswith(prefix)}
    files = {_artifact_key(a.path) for a in c.list_artifacts(run.info.run_id, "comparators")}
    wanted = set(COMPARATORS.values())
    if set(tagged) != wanted or files != wanted:
        raise FrozenModelError(f"frozen run {run.info.run_id} records comparators {sorted(tagged)} and holds files "
                               f"{sorted(files)}; the comparison models are {sorted(wanted)}")
    out = {}
    for key in sorted(wanted):
        m = FrozenModel.from_json(mlflow.artifacts.load_text(f"runs:/{run.info.run_id}/comparators/{key}.json"))
        if m.sha256() != tagged[key]:
            raise FrozenModelError(f"comparison model {key} of run {run.info.run_id} does not match the hash its run "
                                   "recorded")
        if (m.family, m.scenario) != MODELS[key]:
            raise FrozenModelError(f"comparison model file {key} holds a {m.family} model on {m.scenario}, not "
                                   f"{MODELS[key]}")
        if str(m.replay_year) != run.data.tags.get("replay_year"):
            raise FrozenModelError(f"comparison model {key} was frozen for {m.replay_year}, its run for "
                                   f"{run.data.tags.get('replay_year')}")
        out[key] = m
    return out


def _artifact_key(path: str) -> str:
    """The model key of a comparators/<key>.json artifact path."""
    name = path.rsplit("/", 1)[-1]
    return name[:-len(".json")] if name.endswith(".json") else name
