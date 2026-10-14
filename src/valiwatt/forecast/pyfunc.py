"""The MLflow pyfunc wrapper of the frozen model: a feature table in, forecast and interval bounds out."""

from __future__ import annotations

from pathlib import Path

import mlflow

from valiwatt.forecast.freeze import FrozenModel


class FrozenPyfunc(mlflow.pyfunc.PythonModel):
    def load_context(self, context):
        self.frozen = FrozenModel.from_json(Path(context.artifacts["frozen"]).read_text(encoding="utf-8"))

    def predict(self, context, model_input, params=None):
        return self.frozen.predict(model_input)
