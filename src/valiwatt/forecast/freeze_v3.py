"""v3's frozen models (docs/plan-v3.md, sections 4.4, 11.1 and 11.2), beside v2's freeze (forecast/freeze.py), whose
file format, hash and logging they reuse.

v3 and its three comparison models (section 4.2: v2's form at noon, v1's form at noon, v2's form at midnight) are
refitted once on the design years with v2's embargo and frozen, for the 2024 rehearsal and for 2025: the noon models
at their noon fit clock (2023-12-31 17:00 and 2024-12-31 17:00 UTC), on the noon table as of that clock; v2's form at
midnight at v2's own clocks (2024-01-01 05:00 and 2025-01-01 05:00 UTC), on v2's table. The bounds and sensitivities of
section 4.3 are validation only and are not frozen.

Each noon model stores the bin edge of its own usable training rows and the interval layer calibrated against it on
the 12 validation folds before the replayed year, those already scored at its fit clock (section 7), with the cold
bin's fallback; v3 stores its terms' definitions. v2's form at midnight issues no interval. The freeze refuses a model
whose knots or bin edge are not the rule on its own training rows, taken again from the table, and a production model
whose two lag coefficients are not both positive (the echo premise, section 9.2) or whose terms are not the ruled ones
(section 4.4).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from valiwatt.config import THROUGH_DEFAULT, V3_DESIGN_LAST
from valiwatt.forecast import model
from valiwatt.forecast import validate as vd
from valiwatt.forecast import walkforward as wf
from valiwatt.forecast.evaluate import binned_half_widths, calibration_rows
from valiwatt.forecast.features import BASE_C, AsOf
from valiwatt.forecast.freeze import (
    FrozenModel,
    FrozenModelError,
    calibration_folds,
    log_frozen,
    require_echo_premise,
)
from valiwatt.forecast.guards import LookaheadError, require_design_window, require_embargo, require_known_at_origin
from valiwatt.forecast.issue import MIDNIGHT, NOON, TERMS
from valiwatt.forecast.model import MODELS_V3, V3_COMPARATORS, V3_PRODUCTION
from valiwatt.forecast.validate_v3 import (
    MIDNIGHT_KEY,
    V3_DESIGN_LAST_DAY,
    V3_DESIGN_YEARS,
    freeze_tag,
    load_validation_v3,
    own_rows,
)

MODEL_NAME_V3 = "valiwatt-demand-v3"  # v3's registered model: its replay-<year> aliases never move v2's
FROZEN_KEYS = (V3_PRODUCTION, *V3_COMPARATORS.values())  # v3, v2_noon, v1_noon, v2_midnight
REPLAY_YEARS = (2024, 2025)  # the rehearsal (section 11.1) and the models frozen for 2025 (section 11.2)
CALIBRATION_TEMPERATURE = "temp_forecast"  # the noon feed's temperature each calibration score is binned by


def fold_of(key: str, replay_year: int) -> wf.Fold:
    """The fold a frozen model of `key` is fitted as: the replayed year's January at the model's own issue time, with
    v2's embargo (noon: fit clock the eve at 17:00 UTC; v2's form at midnight: the first day's 05:00 UTC)."""
    return wf.month_folds(f"{replay_year}-01", f"{replay_year}-01", issue=MODELS_V3[key].issue)[0]


def frozen_tables_v3(raw_demand: pd.DataFrame, weather: pd.DataFrame, replay_year: int, runs: pd.DataFrame,
                     last: pd.Timestamp = V3_DESIGN_LAST) -> dict[str, pd.DataFrame]:
    """What `valiwatt freeze-v3` fits on, from the raw demand records, the weather table and the NBM runs of
    V3_DESIGN_YEARS: the noon table as of the noon models' fit clock, each day with the forecast issued for it at noon
    (validate.forecast_inputs at NOON), and v2's table as of v2's fit clock (validate.forecast_inputs at MIDNIGHT), the
    primary station's (validate.temperature_sources). last: the last record loaded."""
    primary = vd.temperature_sources(weather)["primary"]
    days = vd.design_days(last)
    noon = vd.forecast_inputs(runs, days, last=last, issue=NOON)
    midnight = vd.forecast_inputs(runs, days, last=last)
    return {"noon": AsOf(raw_demand, last=last, issue=NOON).table(fold_of(V3_PRODUCTION, replay_year).fit_clock,
                                                                  primary, noon),
            "midnight": AsOf(raw_demand, last=last).table(fold_of(MIDNIGHT_KEY, replay_year).fit_clock, primary,
                                                          midnight)}


def table_of(key: str, tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """The frozen table model `key` is fitted on: the noon table for a noon model, v2's for v2's form at midnight."""
    return tables["noon" if MODELS_V3[key].issue == NOON else "midnight"]


def require_ruled_terms(m: FrozenModel) -> None:
    """The production model reads exactly the ruled terms (issue.TERMS) with their ruled definitions (docs/plan-v3.md,
    sections 4.1 and 4.4); refused (FrozenModelError) otherwise. A frozen model already refuses definitions other
    than the ruled ones at construction; this refuses a production model missing a term, or holding none."""
    if tuple(m.terms) != TERMS:
        raise FrozenModelError(f"the production model's terms {list(m.terms)} are not the ruled ones {list(TERMS)}")
    model.require_term_definitions(m.term_definitions, "the production model")


def coefficient_table_v3(frozen: FrozenModel, fitted, train: pd.DataFrame) -> pd.DataFrame:
    """The frozen model's named coefficients; for a GAM also each in MW per unit of its column (beta / scale), and for
    its terms and lags their variance inflation factor on its own usable training rows (docs/plan-v3.md, section
    4.1: for interpretation only)."""
    coef = pd.DataFrame({"feature": list(frozen.columns), "frozen": frozen.beta})
    if frozen.family == "gam":
        coef["mw_per_unit"] = frozen.beta / np.asarray(frozen.scale)
        vifs = fitted.vif(train)
        names = [c.removeprefix(model.TERM_PREFIX) for c in frozen.columns]
        coef["vif"] = [vifs.get(n, np.nan) if (c.startswith(model.TERM_PREFIX) or model.is_lag(c)) else np.nan
                       for c, n in zip(frozen.columns, names)]
    return coef


def fit_frozen_v3(f: pd.DataFrame, oos: pd.DataFrame, replay_year: int, key: str = V3_PRODUCTION,
                  last_day: pd.Timestamp = V3_DESIGN_LAST_DAY) -> tuple[FrozenModel, pd.DataFrame]:
    """Fit the frozen model of `key` (one of FROZEN_KEYS) for `replay_year` on `f`, its frozen table (`table_of`), and
    its interval from v3's validation (`oos`: pred_<key>, the noon feed's temp_forecast, fold, day, scored_at, target).
    Returns the model and its coefficient table (`coefficient_table_v3`).

    The fit trains on the rows before its embargo, inside the design window (to `last_day`), each known at its
    origin. A noon model's interval layer (docs/plan-v3.md, section 7) calibrates on the 12 folds before the replayed
    year, the rows already scored at its fit clock (evaluate.calibration_rows), every score binned by its own noon-feed
    temperature against the model's own bin edge (evaluate.binned_half_widths, the cold bin falling back to the pooled
    half-width under 120 scores or 4 weeks, recorded); v2's form at midnight issues none (q NaN).

    Before it returns, the freeze checks on its own, on the frozen table's own usable training rows taken again from
    f (`validate_v3.own_rows`, as many as the fit's): the knots of a GAM (model.check_knots) and the bin edge of a noon
    model (model.check_bin_edge) must be the rule on them; else FrozenModelError."""
    if key not in FROZEN_KEYS:
        raise ValueError(f"v3 freezes {list(FROZEN_KEYS)}, not {key!r}: the bounds and sensitivities are validation "
                         "only (docs/plan-v3.md, section 4.3)")
    spec = MODELS_V3[key]
    fold = fold_of(key, replay_year)
    train, _ = wf.split(f, fold)
    what = f"frozen model {key} for {replay_year}"
    require_embargo(train, fold.test_first_day, wf.EMBARGO_DAYS)
    require_design_window(train, what, last_day)
    require_known_at_origin(train, what)
    m = model.forecaster(key, MODELS_V3).fit(train)
    rows = own_rows(key, train)
    if int(rows.sum()) != m.n_train:
        raise FrozenModelError(f"{what}: {int(rows.sum())} own usable rows taken again, not the {m.n_train} it was "
                               "fitted on")
    temps = train.loc[rows, f"temp_{spec.scenario}"].to_numpy(dtype=float)
    common = dict(replay_year=replay_year, train_last_day=str(fold.train_last_day.date()), n_train=m.n_train,
                  family=spec.family, scenario=spec.scenario, lags=spec.lags, terms=spec.terms, wind=spec.wind,
                  term_definitions=getattr(m, "term_definitions", None), required_inputs=spec.required_inputs)
    if spec.interval == "binned":
        folds = calibration_folds(replay_year)
        missing = sorted(set(folds) - set(oos["fold"]))
        if missing:
            raise FrozenModelError(f"v3's validation has no out-of-sample scores for folds {missing}")
        col = f"pred_{key}"
        if col not in oos or CALIBRATION_TEMPERATURE not in oos:
            raise FrozenModelError(f"v3's validation has no {col} or no {CALIBRATION_TEMPERATURE} to calibrate on")
        cal = calibration_rows(oos[oos["fold"].isin(folds)], fold.fit_clock)
        scores = ((cal["target"] - cal[col]).abs() / cal[col]).to_numpy()
        layer = binned_half_widths(scores, cal[CALIBRATION_TEMPERATURE], cal["day"], m.bin_edge)
        if not all(np.isfinite(layer[k]) for k in ("q_cold", "q_rest", "q_pooled")):
            raise FrozenModelError(f"{what}: too few calibration scores for the conformal quantile in a bin")
        try:
            model.check_bin_edge(layer["bin_edge"], temps)
        except LookaheadError as e:
            raise FrozenModelError(f"{what}: its bin edge is not the 5th-percentile rule on the frozen table's own "
                                   "usable training rows") from e
        common |= dict(q=layer["q_pooled"], interval=layer, calibration_folds=folds,
                       calibration_scores=int(np.isfinite(scores).sum()))
    else:
        common |= dict(q=float("nan"))  # v2's form at midnight: no interval (docs/plan-v3.md, section 7)

    if spec.family == "ols":
        frozen = FrozenModel(columns=m.columns, beta=m.beta, hour_blocks=model.hour_blocks(), base_c=BASE_C,
                             **common)
        return frozen, coefficient_table_v3(frozen, m, train)
    frozen = FrozenModel(columns=m.columns, beta=m.coef, knots=m.knots, center=m.center, scale=m.scale,
                         intercept=m.intercept, **common)
    try:
        model.check_knots(np.asarray(frozen.knots), temps)
    except LookaheadError as e:
        raise FrozenModelError(f"{what}: its knots are not the quantile rule on the frozen table's own usable training "
                               "rows") from e
    return frozen, coefficient_table_v3(frozen, m, train)


def interval_params(m: FrozenModel) -> dict:
    """The interval layer and the terms of a frozen model as MLflow parameters (docs/plan-v3.md, sections 4.4 and 7)."""
    out = {f"interval.{k}": v for k, v in (m.interval or {}).items()}
    if m.family == "gam":
        out |= {f"term.{k}_mw_per_unit": v for k, v in
                model.gam_term_coefficients(m.beta, m.scale, m.columns).items()}
    return out


def run_freeze_v3(replay_year: int) -> int:
    """`valiwatt freeze-v3 --year Y`: on real data, behind v3's first-run guard, in v3's own store, from v3's
    data-quality run of the 2019-2024 window whose byte copies were checked, or that is labelled a fresh clone
    (run_v3.require_design_run), the files it checked and v3's validation run built on them, whose reproduction check
    passed unless that run is a fresh clone's; v3 is checked against the echo premise and the ruled terms before
    anything is logged; v3 is registered as MODEL_NAME_V3 with the alias replay-<year>, its three comparison models
    beside it as hashed files, the run tagged v3, with its freeze tag (section 10.3), the byte-copy and reproduction
    statuses and the fresh-clone label."""
    from valiwatt.firstrun import require_first_run
    from valiwatt.monitor.rules import rules_sha256
    from valiwatt.monitor.run import load_raw
    from valiwatt.quality import run as qrun
    from valiwatt.quality import run_v3
    from valiwatt.v2store import require_own_store

    if int(replay_year) not in REPLAY_YEARS:
        raise ValueError(f"v3 freezes models for {list(REPLAY_YEARS)}, not {replay_year}")
    require_first_run()
    require_own_store()
    dq = run_v3.latest_passed()
    lineage = run_v3.require_design_run(dq)
    dq, _, weather = vd.load_inputs(dq, run_v3.canonical_paths(THROUGH_DEFAULT), command="valiwatt dq-v3")
    val, oos = load_validation_v3(dq)
    reproduction = val.data.tags.get("reproduction")
    if reproduction != "passed" and not lineage["fresh_clone"]:
        raise vd.LineageError(f"validation run {val.info.run_id}: its reproduction check is {reproduction!r}, not "
                              f"passed, and data-quality run {dq.info.run_id} is not labelled a fresh clone")
    raw_demand, _ = load_raw(dq)
    runs = qrun.load_runs(dq, years=V3_DESIGN_YEARS)
    tables = frozen_tables_v3(raw_demand, weather, replay_year, runs)
    production, coef = fit_frozen_v3(tables["noon"], oos, replay_year, key=V3_PRODUCTION)
    require_echo_premise(production)
    require_ruled_terms(production)
    comparators = {k: fit_frozen_v3(table_of(k, tables), oos, replay_year, key=k)[0] for k in V3_COMPARATORS.values()}
    spec = MODELS_V3[V3_PRODUCTION]
    cols = ["day_type", "hour", *spec.inputs]
    f = tables["noon"]
    example = f[f["day"] <= pd.Timestamp(production.train_last_day)][cols].dropna().tail(24).astype({"hour": float})
    run, version = log_frozen(production, coef, example,
                              {"version": "v3", "freeze_tag": freeze_tag(), "validation_run_id": val.info.run_id,
                               "dq_run_id": dq.info.run_id, "rules_sha256": rules_sha256(),
                               "issue": NOON.name, "comparator_issue.v2_midnight": MIDNIGHT.name,
                               "byte_copies": lineage["byte_copies"], "reproduction": reproduction,
                               run_v3.FRESH_CLONE: str(lineage["fresh_clone"]).lower()},
                              comparators=comparators, model_name=MODEL_NAME_V3,
                              params={**interval_params(production),
                                      **{f"vif.{r.feature.removeprefix(model.TERM_PREFIX)}": r.vif
                                         for r in coef.itertuples() if np.isfinite(r.vif)},
                                      **{f"{k}.{p}": v for k, m in comparators.items()
                                         for p, v in interval_params(m).items()}})
    print(f"v3 frozen models for {replay_year}: run {run.info.run_id}, {MODEL_NAME_V3} v{version} (alias "
          f"replay-{replay_year}), bin edge {production.interval['bin_edge']:.2f} C, half-widths cold "
          f"{100 * production.interval['q_cold']:.2f} % (fallback {production.interval['fallback']}), rest "
          f"{100 * production.interval['q_rest']:.2f} %, sha256 {production.sha256()[:12]}; "
          + ", ".join(f"{k} {m.sha256()[:12]}" for k, m in comparators.items()))
    return 0
