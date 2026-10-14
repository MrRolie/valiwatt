"""The production model: the persistence-weather OLS refitted once on the design years, then frozen.

Two freezes use the same code. The one for the 2024 replay is fitted on 2019-2023; the one for the 2023
pseudo-replay is fitted on 2019-2022. Both keep the 7-day embargo before their first replayed day, so the
pseudo-replay rehearses exactly what the 2024 replay does. The interval half-width is the conformal quantile of the
relative out-of-sample scores of the 12 validation folds just before the replayed year: the same rule the validation
intervals used, applied to one more month.

The frozen model is logged to MLflow with the SHA-256 of its coefficients and half-width, and registered as a model
version with the alias `replay-<year>`. The replay loads it through that alias and refuses it if the hash differs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from valiwatt.forecast import walkforward as wf
from valiwatt.forecast.evaluate import ALPHA, CALIBRATION_FOLDS, conformal_quantile
from valiwatt.forecast.guards import require_design_window, require_embargo, require_known_at_origin
from valiwatt.forecast.model import OLSForecaster, design, usable_rows

EXPERIMENT = "valiwatt-frozen-model"
MODEL_NAME = "valiwatt-demand-persistence"
SCENARIO = "persistence"
INPUT_COLUMNS = ["day_type", "hour", f"hdh_{SCENARIO}", f"cdh_{SCENARIO}", "lag24", "lag168"]


class FrozenModelError(RuntimeError):
    pass


@dataclass(frozen=True)
class FrozenModel:
    columns: tuple
    beta: np.ndarray
    q: float  # interval: forecast x (1 - q, 1 + q)
    replay_year: int
    train_last_day: str
    n_train: int
    calibration_folds: tuple = field(default_factory=tuple)
    calibration_scores: int = 0

    def __post_init__(self):
        beta = np.array(self.beta, dtype=float)
        beta.flags.writeable = False
        object.__setattr__(self, "beta", beta)
        object.__setattr__(self, "columns", tuple(self.columns))
        object.__setattr__(self, "calibration_folds", tuple(self.calibration_folds))

    def sha256(self) -> str:
        """Hash of what the forecast and its interval depend on: the named coefficients and the half-width."""
        h = hashlib.sha256()
        h.update(json.dumps(list(self.columns)).encode())
        h.update(np.ascontiguousarray(self.beta, dtype="<f8").tobytes())
        h.update(repr(float(self.q)).encode())
        return h.hexdigest()

    def predict(self, f: pd.DataFrame) -> pd.DataFrame:
        X = design(f, SCENARIO)[list(self.columns)]
        ok = usable_rows(X)
        forecast = pd.Series(np.nan, index=f.index)
        forecast[ok] = X[ok].to_numpy() @ self.beta
        return pd.DataFrame({"forecast": forecast, "lower": forecast * (1 - self.q),
                             "upper": forecast * (1 + self.q)}, index=f.index)

    def to_json(self) -> str:
        return json.dumps({"columns": list(self.columns), "beta": [float(b) for b in self.beta], "q": float(self.q),
                           "replay_year": self.replay_year, "train_last_day": self.train_last_day,
                           "n_train": self.n_train, "calibration_folds": list(self.calibration_folds),
                           "calibration_scores": self.calibration_scores, "sha256": self.sha256()}, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "FrozenModel":
        d = json.loads(text)
        expected = d.pop("sha256")
        m = cls(**d)
        if m.sha256() != expected:
            raise FrozenModelError("frozen model file does not match its own hash")
        return m


def calibration_folds(replay_year: int) -> list[str]:
    """The CALIBRATION_FOLDS months just before the replayed year."""
    first = pd.Period(f"{replay_year}-01", freq="M")
    return [str(first - k) for k in range(CALIBRATION_FOLDS, 0, -1)]


def fit_frozen(f: pd.DataFrame, oos: pd.DataFrame, replay_year: int) -> tuple[FrozenModel, pd.DataFrame]:
    """Fit the frozen model and its half-width. Returns the model and a table of its coefficients beside an
    explanatory fit without the lags (for interpretation only)."""
    fold = wf.folds(f"{replay_year}-01", f"{replay_year}-01")[0]
    train, _ = wf.split(f, fold)
    require_embargo(train, fold.test_first_day, wf.EMBARGO_DAYS)
    require_design_window(train, f"frozen model for {replay_year}")
    require_known_at_origin(train)
    m = OLSForecaster(SCENARIO).fit(train)

    folds = calibration_folds(replay_year)
    missing = sorted(set(folds) - set(oos["fold"]))
    if missing:
        raise FrozenModelError(f"validation has no out-of-sample scores for folds {missing}")
    cal = oos[oos["fold"].isin(folds)]
    scores = ((cal["target"] - cal[f"pred_{SCENARIO}"]).abs() / cal[f"pred_{SCENARIO}"]).to_numpy()
    q = conformal_quantile(scores, ALPHA)
    if not np.isfinite(q):
        raise FrozenModelError("too few calibration scores for the conformal quantile")
    frozen = FrozenModel(columns=m.columns, beta=m.beta, q=q, replay_year=replay_year,
                         train_last_day=str(fold.train_last_day.date()), n_train=m.n_train,
                         calibration_folds=folds, calibration_scores=int(np.isfinite(scores).sum()))

    X = design(train, SCENARIO).drop(columns=["lag24", "lag168"])
    ok = usable_rows(X, train["target"])
    beta_nolag, *_ = np.linalg.lstsq(X[ok].to_numpy(), train.loc[ok, "target"].to_numpy(), rcond=None)
    coef = pd.DataFrame({"feature": m.columns, "frozen": m.beta})
    coef = coef.merge(pd.DataFrame({"feature": list(X.columns), "no_lag": beta_nolag}), on="feature", how="left")
    return frozen, coef


def run_freeze(replay_year: int) -> int:
    from valiwatt.forecast.validate import design_features, load_inputs, load_validation, temperature_sources
    from valiwatt.monitor.rules import rules_sha256

    dq, demand, weather = load_inputs()
    val, oos = load_validation(dq)
    f = design_features(demand, temperature_sources(weather)["primary"])
    frozen, coef = fit_frozen(f, oos, replay_year)
    # The hour is declared a float in the model signature: an hour without inputs comes through as NaN.
    example = f[f["day"] <= pd.Timestamp(frozen.train_last_day)][INPUT_COLUMNS].dropna().tail(24).astype({"hour": float})
    run, version = log_frozen(frozen, coef, example, {"validation_run_id": val.info.run_id, "dq_run_id": dq.info.run_id,
                                                      "rules_sha256": rules_sha256()})
    print(f"frozen model for {replay_year}: run {run.info.run_id}, {MODEL_NAME} v{version} "
          f"(alias replay-{replay_year}), half-width {100 * frozen.q:.2f} %, sha256 {frozen.sha256()[:12]}")
    return 0


def log_frozen(frozen: FrozenModel, coef: pd.DataFrame, example: pd.DataFrame, tags: dict):
    """Log the frozen model as an MLflow run and a registered model version with the alias replay-<year>."""
    import tempfile
    from pathlib import Path

    import mlflow

    from valiwatt.forecast import pyfunc as pyfunc_module
    from valiwatt.tracking import client, start_run

    replay_year = frozen.replay_year
    with start_run(EXPERIMENT, f"frozen-{replay_year}", tags={
        "kind": "frozen", "replay_year": str(replay_year), "coefficient_sha256": frozen.sha256(), **tags,
    }) as run:
        mlflow.log_params({"replay_year": replay_year, "train_last_day": frozen.train_last_day,
                           "n_train": frozen.n_train, "q": frozen.q, "alpha": ALPHA,
                           "calibration_first_fold": frozen.calibration_folds[0],
                           "calibration_last_fold": frozen.calibration_folds[-1],
                           "calibration_scores": frozen.calibration_scores, "embargo_days": wf.EMBARGO_DAYS})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frozen.json"
            path.write_text(frozen.to_json(), encoding="utf-8")
            coef.to_csv(Path(tmp) / "coefficients.csv", index=False)
            mlflow.log_artifact(str(Path(tmp) / "coefficients.csv"))
            info = mlflow.pyfunc.log_model(
                name="model", python_model=str(Path(pyfunc_module.__file__)), artifacts={"frozen": str(path)},
                input_example=example, registered_model_name=MODEL_NAME,
                pip_requirements=[f"mlflow=={mlflow.__version__}", f"pandas=={pd.__version__}",
                                  f"numpy=={np.__version__}"])
        client().set_registered_model_alias(MODEL_NAME, f"replay-{replay_year}", info.registered_model_version)
        mlflow.set_tag("model_version", str(info.registered_model_version))
        mlflow.set_tag("status", "complete")
    return run, info.registered_model_version


def load_frozen(replay_year: int):
    """The registered frozen model for a replay year and its run; refused if its hash differs from the run's."""
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
