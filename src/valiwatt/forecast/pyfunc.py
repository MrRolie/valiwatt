"""The MLflow pyfunc wrapper of the frozen model: a feature table in, forecast and interval bounds out.

Logged as MLflow "models from code": the registry stores this file, not a pickled object, so loading the model runs
this code and nothing else. The model, least squares or the GAM, travels as a JSON artifact of numbers read through
`FrozenModel.from_json`, which refuses a file that no longer matches its own hash or a GAM whose spline settings are
not the ruled ones; the GAM's basis is rebuilt from its stored knots."""

from __future__ import annotations

from pathlib import Path

import mlflow

from valiwatt.forecast.freeze import FrozenModel


class FrozenPyfunc(mlflow.pyfunc.PythonModel):
    def load_context(self, context):
        self.frozen = FrozenModel.from_json(Path(context.artifacts["frozen"]).read_text(encoding="utf-8"))

    def predict(self, context, model_input, params=None):
        return self.frozen.predict(model_input)


mlflow.models.set_model(FrozenPyfunc())
