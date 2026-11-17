"""Line 3 design input: v2's walk-forward on 2020-2025 (monthly refits, 7-day embargo), production model only, data through 2025.
Writes out/oos.parquet. Calls valiwatt.forecast; copies nothing. No 2026 data exists on disk or is read."""
import sys
from pathlib import Path

import pandas as pd

from valiwatt.config import data_last
from valiwatt.forecast import walkforward as wf
from valiwatt.forecast.features import AsOf
from valiwatt.forecast.model import PRODUCTION
from valiwatt.forecast.validate import DQ_EXPERIMENT, design_days, forecast_inputs, temperature_sources
from valiwatt.monitor.run import load_raw
from valiwatt.paths import INTERIM
from valiwatt.quality import run as qrun
from valiwatt.tracking import latest_run

OUT = Path(__file__).resolve().parents[1] / "data" / "analysis" / "live-updates" / "out"  # live/updates.OOS reads it here
OUT.mkdir(exist_ok=True)
LAST = data_last(2025)

dq = latest_run(DQ_EXPERIMENT, "tags.kind = 'dq' and tags.gates = 'passed' and attributes.status = 'FINISHED' "
                "and params.through = '2025'")
print("dq run", dq.info.run_id, "through", dq.data.params.get("through"))
raw_demand, _ = load_raw(dq)
weather = pd.read_parquet(INTERIM / "weather.parquet")
assert pd.Timestamp(weather.index.max()) <= LAST + pd.Timedelta(days=1), weather.index.max()
runs = qrun.load_runs(dq, years=range(2019, 2026))
asof = AsOf(raw_demand, last=LAST)
temp = temperature_sources(weather)["primary"]
forecasts = forecast_inputs(runs, design_days(LAST), last=LAST)
f = asof.issued(temp, forecasts)
fl = wf.folds("2020-01", "2025-12")
oos = wf.run(f, fl, train_at=lambda clock: asof.table(clock, temp, forecasts), models=[PRODUCTION])
oos["temp_forecast"] = f.loc[oos.index, "temp_forecast"]
oos.to_parquet(OUT / "oos.parquet")
print(oos.groupby(oos["day"].dt.year).size(), oos.columns.tolist())
