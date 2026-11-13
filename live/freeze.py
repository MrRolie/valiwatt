"""The live freeze (docs/plan-live.md, the owner's ruling of 2026-11-13): v2's production model refitted on data
through 2025 under v2's rules, its symmetric half-width (official), and the experimental rule (c) with its per-bin
centring, both calibrated on the 12 walk-forward folds of 2025 at the 2026 fit clock.

Reads data through 2025 only (the dq run checked with through = 2025); never reads data/live/capture.
`uv run python -m live.freeze` prints what it would freeze; `--write` writes data/live/frozen/ and live/FREEZE.json."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from valiwatt.config import data_last
from valiwatt.forecast import model
from valiwatt.forecast import walkforward as wf
from valiwatt.forecast.evaluate import ALPHA, calibration_rows, conformal_quantile
from valiwatt.forecast.features import AsOf
from valiwatt.forecast.freeze import FrozenModel, FrozenModelError, calibration_folds, fit_clock, require_echo_premise
from valiwatt.forecast.guards import LookaheadError, require_embargo, require_known_at_origin
from valiwatt.forecast.model import MODELS, PRODUCTION, design, usable_rows
from valiwatt.forecast.validate import DQ_EXPERIMENT, design_days, forecast_inputs, temperature_sources
from valiwatt.monitor.run import load_raw
from valiwatt.paths import INTERIM, REPO_ROOT
from valiwatt.quality import run as qrun
from valiwatt.tracking import latest_run

LIVE_YEAR = 2026
THROUGH = 2025
LAST = data_last(THROUGH)
# Rule (c), pre-declared (docs/plan-live.md): bins on each hour's forecast temperature, coldest first; a group with
# fewer than MIN_N calibration hours merges into its warmer neighbour, a warm leftover into the group before it.
EDGES = (-np.inf, -20.0, -11.0, 0.0, 10.0, np.inf)
LABELS = ("<-20", "-20..-11", "-11..0", "0..10", ">10")
MIN_N = 200
FROZEN_DIR = REPO_ROOT / "data" / "live" / "frozen"
RECORD = REPO_ROOT / "live" / "FREEZE.json"


def groups_of(counts: dict) -> dict:
    """Bin label -> group label, by the pre-declared merge rule."""
    mapping, acc, n = {}, [], 0
    for lab in LABELS:
        acc.append(lab)
        n += counts.get(lab, 0)
        if n >= MIN_N:
            for x in acc:
                mapping[x] = "+".join(acc)
            acc, n = [], 0
    if acc:
        if not mapping:
            raise FrozenModelError("fewer than MIN_N calibration hours in all bins together")
        prev = mapping[LABELS[LABELS.index(acc[0]) - 1]]
        merged = prev + "+" + "+".join(acc)
        for x in list(mapping):
            if mapping[x] == prev:
                mapping[x] = merged
        for x in acc:
            mapping[x] = merged
    return mapping


def bin_of(temp, edges=EDGES, labels=LABELS) -> pd.Series:
    """Each hour's bin label, left-closed; "nan" where the temperature is missing."""
    edges = [(-np.inf if i == 0 else np.inf) if v is None else float(v) for i, v in enumerate(edges)]
    return pd.cut(pd.Series(temp, dtype=float), edges, labels=list(labels), right=False).astype(str)


def experimental_rule(cal: pd.DataFrame, official_sha256: str) -> dict:
    """Rule (c) and its centring from the calibration rows (columns target, pred, temp)."""
    bad = ~(np.isfinite(cal["target"]) & np.isfinite(cal["pred"]) & (cal["pred"] > 0) & np.isfinite(cal["temp"]))
    if bad.any():
        raise FrozenModelError(f"{int(bad.sum())} calibration hours lack a finite target, a positive finite forecast "
                               "or a forecast temperature")
    e = (cal["target"] - cal["pred"]) / cal["pred"]
    b = bin_of(cal["temp"].to_numpy())
    b.index = cal.index
    if (b == "nan").any():
        raise FrozenModelError(f"{int((b == 'nan').sum())} calibration hours have no forecast temperature")
    mapping = groups_of(b.value_counts().to_dict())
    g = b.map(mapping)
    groups = {}
    for lab in dict.fromkeys(mapping.values()):
        x = e[g == lab].to_numpy(dtype=float)
        groups[lab] = {"n": int(len(x)), "lo": -conformal_quantile(-x, ALPHA / 2), "hi": conformal_quantile(x, ALPHA / 2),
                       "median": float(np.median(x))}
        if not all(np.isfinite(v) for v in groups[lab].values()):
            raise FrozenModelError(f"group {lab}: too few hours for the conformal quantiles")
    rule = {"rule": "c_mondrian_asym_centred", "alpha": ALPHA, "edges_c": [None if not np.isfinite(v) else v for v in EDGES],
            "labels": list(LABELS), "min_n": MIN_N, "bin_to_group": mapping, "groups": groups,
            "relative_to": "official forecast", "official_sha256": official_sha256}
    rule["sha256"] = hashlib.sha256(json.dumps(rule, sort_keys=True).encode()).hexdigest()
    return rule


def build():
    dq = latest_run(DQ_EXPERIMENT, "tags.kind = 'dq' and tags.gates = 'passed' and attributes.status = 'FINISHED' "
                    f"and params.through = '{THROUGH}'")
    raw_demand, _ = load_raw(dq)
    if pd.Timestamp(raw_demand["ts_utc"].max()) > LAST:
        raise LookaheadError(f"demand records after {LAST}: the live freeze reads data through {THROUGH} only")
    weather = pd.read_parquet(INTERIM / "weather.parquet")
    weather = weather[weather.index <= LAST]
    runs = qrun.load_runs(dq, years=range(2019, THROUGH + 1))
    asof = AsOf(raw_demand, last=LAST)
    temp = temperature_sources(weather)["primary"]
    forecasts = forecast_inputs(runs, design_days(LAST), last=LAST)
    f = asof.issued(temp, forecasts)
    train_at = lambda clock: asof.table(clock, temp, forecasts)  # noqa: E731

    # Calibration: v2's walk-forward on the 12 folds before the live year, scored as the replay would by its fit clock.
    folds = calibration_folds(LIVE_YEAR)
    oos = wf.run(f, wf.folds(folds[0], folds[-1]), train_at=train_at, models=[PRODUCTION])
    col = f"pred_{PRODUCTION}"
    cal = calibration_rows(oos, fit_clock(LIVE_YEAR)).dropna(subset=[col, "target"])
    cal = pd.DataFrame({"target": cal["target"], "pred": cal[col], "temp": f.loc[cal.index, "temp_forecast"]})
    q = conformal_quantile(((cal["target"] - cal["pred"]).abs() / cal["pred"]).to_numpy(), ALPHA)
    if not (np.isfinite(q) and 0 < q < 1):
        raise FrozenModelError(f"official half-width {q} is not a finite fraction")

    # The model: as fit_frozen fits it, on the table as of the live year's fit clock (7-day embargo).
    family, scenario = MODELS[PRODUCTION]
    fold = wf.folds(f"{LIVE_YEAR}-01", f"{LIVE_YEAR}-01")[0]
    train, _ = wf.split(train_at(fold.fit_clock), fold)
    require_embargo(train, fold.test_first_day, wf.EMBARGO_DAYS)
    require_known_at_origin(train)
    m = model.forecaster(PRODUCTION).fit(train)
    frozen = FrozenModel(columns=m.columns, beta=m.coef, knots=m.knots, center=m.center, scale=m.scale,
                         intercept=m.intercept, q=q, replay_year=LIVE_YEAR,
                         train_last_day=str(fold.train_last_day.date()), n_train=m.n_train, calibration_folds=folds,
                         calibration_scores=len(cal), family=family, scenario=scenario)
    ok = usable_rows(design(train, scenario), train["target"])
    model.check_knots(np.asarray(frozen.knots), train.loc[ok, f"temp_{scenario}"].to_numpy(dtype=float))
    require_echo_premise(frozen)
    rule = experimental_rule(cal, frozen.sha256())
    record = {"live_year": LIVE_YEAR, "through": THROUGH, "dq_run_id": dq.info.run_id,
              "hq_sha256": dq.data.params.get("hq_sha256"), "nbm_manifest_sha256": dq.data.params.get("nbm_manifest_sha256"),
              "official": {"sha256": frozen.sha256(), "q": q, "n_train": int(m.n_train),
                           "train_last_day": frozen.train_last_day, "calibration_folds": list(folds),
                           "calibration_hours": len(cal), "lag_coefficients": frozen.lag_coefficients()},
              "experimental": {"sha256": rule["sha256"], "groups": rule["groups"], "bin_to_group": rule["bin_to_group"]}}
    return frozen, rule, record


def main(argv) -> int:
    frozen, rule, record = build()
    print(json.dumps(record, indent=2, default=float))
    if "--write" in argv:
        FROZEN_DIR.mkdir(parents=True, exist_ok=True)
        (FROZEN_DIR / "official.json").write_text(frozen.to_json())
        (FROZEN_DIR / "experimental.json").write_text(json.dumps(rule, indent=2))
        RECORD.write_text(json.dumps(record, indent=2, default=float) + "\n")
        print("written:", FROZEN_DIR, RECORD)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
