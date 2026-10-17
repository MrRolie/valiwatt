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
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, fields

import numpy as np
import pandas as pd

from valiwatt.config import DESIGN_LAST
from valiwatt.forecast import model
from valiwatt.forecast import walkforward as wf
from valiwatt.forecast.evaluate import ALPHA, CALIBRATION_FOLDS, calibration_rows, conformal_quantile
from valiwatt.forecast.features import BASE_C
from valiwatt.forecast.guards import LookaheadError, require_design_window, require_embargo, require_known_at_origin
from valiwatt.forecast.model import COMPARATORS, HOUR_BLOCKS, MODELS, PRODUCTION, design, usable_rows

EXPERIMENT = "valiwatt-frozen-model"
MODEL_NAME = "valiwatt-demand-gam-forecast"
SCENARIO = "persistence"  # v1's model and input: fit_frozen's default key
FAMILIES = ("ols", "gam")
# The partial-effects table (plan section 4.5): each hour's curve on a grid of whole degrees from this far below the
# training range to this far above it, the part outside the range marked.
PARTIAL_EFFECTS_STEP_C, PARTIAL_EFFECTS_MARGIN_C = 1.0, 5.0


def input_columns(family: str, scenario: str) -> list[str]:
    """The feature columns a frozen model of this family reads (its MLflow input example)."""
    if family == "gam":
        return ["day_type", "hour", f"temp_{scenario}", "lag24", "lag168"]
    return ["day_type", "hour", f"hdh_{scenario}", f"cdh_{scenario}", "lag24", "lag168"]


INPUT_COLUMNS = input_columns("ols", SCENARIO)  # v1's model's input columns


class FrozenModelError(RuntimeError):
    pass


def frozen_sha256(family: str, scenario: str, columns, knots, degree, extrapolation, include_bias, center, scale,
                  beta, intercept, q) -> str:
    """The hash of what a frozen model's forecast and interval depend on: its family and input, the named columns, the
    spline's knots and its three settings, the scaler's means and scales, the coefficients and intercept, and the
    half-width. A function of the values, so the hash of any setting can be computed, a refused one included."""
    arrays = [np.ascontiguousarray(np.asarray(a, dtype=float).ravel(), dtype="<f8")
              for a in (knots, center, scale, beta)]
    head = {"family": str(family), "scenario": str(scenario), "columns": [str(c) for c in columns],
            "degree": int(degree), "extrapolation": str(extrapolation), "include_bias": bool(include_bias),
            "lengths": [int(a.size) for a in arrays]}
    h = hashlib.sha256()
    h.update(json.dumps(head, sort_keys=True).encode())
    for a in arrays:
        h.update(b"\0" + a.tobytes())
    h.update(b"\0" + repr(float(intercept)).encode() + b"\0" + repr(float(q)).encode())
    return h.hexdigest()


def _floats(values) -> tuple:
    return tuple(float(v) for v in np.asarray(values, dtype=float).ravel())


@dataclass(frozen=True)
class FrozenModel:
    """A frozen model: v1's least squares ("ols", `columns` and `beta` on `model.design`'s named columns) or the GAM
    ("gam": the 10 base knots, the scaler's means and scales, the ridge coefficients and intercept on
    `model.gam_columns`, with the spline's three settings), its half-width `q`, training end and calibration folds.

    Refused at construction (FrozenModelError): a family other than FAMILIES; a degree, extrapolation or bias setting
    other than the owner's ruling (model.DEGREE, model.EXTRAPOLATION, model.INCLUDE_BIAS), for either family; a GAM
    whose knots are not model.N_KNOTS finite increasing values; coefficients, means or scales that do not match the
    columns."""

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
        object.__setattr__(self, "degree", int(self.degree))
        object.__setattr__(self, "include_bias", bool(self.include_bias))

    def sha256(self) -> str:
        """Hash of everything the forecast and its interval depend on (`frozen_sha256`)."""
        return frozen_sha256(self.family, self.scenario, self.columns, self.knots, self.degree, self.extrapolation,
                             self.include_bias, self.center, self.scale, self.beta, self.intercept, self.q)

    def forecast(self, f: pd.DataFrame) -> pd.Series:
        """The forecast of every row whose inputs are all present; NaN otherwise (never imputed). The GAM predicts
        through model.gam_design and model.gam_predict, the path validation's fits predict through, its basis rebuilt
        from the stored knots and the ruled settings."""
        out = pd.Series(np.nan, index=f.index)
        if self.family == "gam":
            X = model.gam_design(f, self.scenario, np.asarray(self.knots))
            ok = np.isfinite(X).all(axis=1)
            if ok.any():
                out[ok] = model.gam_predict(X[ok], self.center, self.scale, self.beta, self.intercept)
            return out
        X = design(f, self.scenario)[list(self.columns)]
        ok = usable_rows(X)
        out[ok] = X[ok].to_numpy() @ self.beta
        return out

    def predict(self, f: pd.DataFrame) -> pd.DataFrame:
        forecast = self.forecast(f)
        return pd.DataFrame({"forecast": forecast, "lower": forecast * (1 - self.q),
                             "upper": forecast * (1 + self.q)}, index=f.index)

    def lag_coefficients(self) -> dict:
        """The two demand lags' coefficients in MW per MW: the GAM's ridge coefficient over its scale, the least
        squares' coefficient."""
        out = {}
        for lag in model.LAGS:
            i = self.columns.index(lag)
            out[lag] = float(self.beta[i] / self.scale[i]) if self.family == "gam" else float(self.beta[i])
        return out

    def partial_effects(self, temps) -> pd.DataFrame:
        """Each hour's temperature effect in MW relative to its value at BASE_C, at the temperatures `temps` (columns
        hour, temp_c, effect_mw; hour major): the GAM's curve (model.gam_partial_effects), or v1's degree-hour lines
        of the hour's block."""
        t = np.asarray(temps, dtype=float).ravel()
        if self.family == "gam":
            return model.gam_partial_effects(np.asarray(self.knots), self.beta, self.scale, t, ref_c=BASE_C)
        b = dict(zip(self.columns, self.beta))
        rows = []
        for h in range(24):
            block = next(name for name, hours in HOUR_BLOCKS.items() if h in hours)
            effect = b[f"heating:{block}"] * np.maximum(BASE_C - t, 0.0) + b[f"cooling:{block}"] * np.maximum(t - BASE_C, 0.0)
            rows.append(pd.DataFrame({"hour": h, "temp_c": t, "effect_mw": effect}))
        return pd.concat(rows, ignore_index=True)

    def to_json(self) -> str:
        """Every field, the spline's three settings included, and the hash."""
        d = {f.name: getattr(self, f.name) for f in fields(self)}
        d |= {"columns": list(self.columns), "beta": [float(b) for b in self.beta], "q": float(self.q),
              "calibration_folds": list(self.calibration_folds), "knots": list(self.knots),
              "center": list(self.center), "scale": list(self.scale)}
        return json.dumps(d | {"sha256": self.sha256()}, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "FrozenModel":
        """Refused (FrozenModelError): a file that no longer matches its own hash, and, at construction, a setting
        other than the ruled one. A file without the spline settings reads the ruled defaults, and its hash must still
        match."""
        d = json.loads(text)
        if not isinstance(d, dict) or "sha256" not in d:
            raise FrozenModelError("frozen model file holds no hash")
        expected = d.pop("sha256")
        try:
            m = cls(**d)
        except TypeError as e:
            raise FrozenModelError(f"frozen model file has fields other than a frozen model's: {e}") from e
        if m.sha256() != expected:
            raise FrozenModelError("frozen model file does not match its own hash")
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


def _no_lag_gam(rows: pd.DataFrame, scenario: str, knots: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The GAM without its two lags, on the same rows and knots, for interpretation only (plan section 4.5): its
    ridge coefficients and the scaler's scales, in model.gam_design's order without the last two columns."""
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler

    X = model.gam_design(rows, scenario, knots)[:, :-len(model.LAGS)]
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
        frozen = FrozenModel(columns=m.columns, beta=m.beta, **common)
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
               comparators: dict | None = None, extras: dict | None = None):
    """Log the frozen model as an MLflow run and a registered model version with the alias replay-<year>.

    comparators: {model key: FrozenModel}, each logged as comparators/<key>.json and its hash as the tag
    comparator_sha256.<key> (load_comparators); extras: {file name: DataFrame}, each logged as a CSV."""
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
                input_example=example, registered_model_name=MODEL_NAME,
                pip_requirements=[f"mlflow=={mlflow.__version__}", f"pandas=={pd.__version__}",
                                  f"numpy=={np.__version__}", f"scikit-learn=={sklearn.__version__}"])
        client().set_registered_model_alias(MODEL_NAME, f"replay-{replay_year}", info.registered_model_version)
        mlflow.set_tag("model_version", str(info.registered_model_version))
        mlflow.set_tag("status", "complete")
    return run, info.registered_model_version


def load_frozen(replay_year: int):
    """The registered production model for a replay year and its run; refused if its hash differs from the run's."""
    import mlflow

    from valiwatt.tracking import client

    client()
    py = mlflow.pyfunc.load_model(f"models:/{MODEL_NAME}@replay-{replay_year}")
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
