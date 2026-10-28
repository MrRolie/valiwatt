"""`valiwatt validate-v3`: v3's walk-forward validation on 2019-2024 (docs/plan-v3.md, sections 6, 7 and 8), beside
v2's (forecast/validate.py), whose code paths it reuses and never changes.

Monthly folds from January 2020 to December 2024. Every model of section 4 (`model.MODELS_V3`) is fitted in every fold,
as the replay would see it: the noon models at their fold's noon fit clock (the eve of its first test day at 17:00
UTC) on the noon table (`features.AsOf` at `issue.NOON`), v2's form at midnight through v2's own fold loop
(`walkforward.run`, v2's clocks, v2's inputs), the two joined on the hour label only. Each noon fit stores the bin edge
of its own usable training rows (section 7), read here per fold; the intervals by temperature calibrate each fold on
the previous 12 folds already scored at its fit clock, every score binned by the edge of the fit being calibrated
(`evaluate.relative_conformal` with `bins`), with v2's single half-width beside as a sensitivity. The contrasts, the
verdicts and the window ranking are evaluate.py's v3 functions; the thresholds of each replayed year read the
validation years config.V3_THRESHOLD_YEARS gives. Every 2020-2024 result is labelled in sample for the choice of the
terms, the interval bins and the deciding subset (section 8.4).

The reproduction check (sections 12 and 17, step 2; `check_reproduction`) compares v2's form at midnight, in this
harness, with v2's recorded validation forecasts over 2020-2023, and the run refuses before it logs anything when they
differ.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd

from valiwatt.config import (
    THROUGH_DEFAULT,
    V3_DESIGN_LAST,
    V3_FIRST_RUN_TAG,
    V3_FREEZE_TAG,  # re-exported: v3's freeze (section 10.2)
    V3_PRE_FREEZE_TAG,
    V3_THRESHOLD_YEARS,
)
from valiwatt.forecast import evaluate as ev
from valiwatt.forecast import model
from valiwatt.forecast import validate as vd
from valiwatt.forecast import walkforward as wf
from valiwatt.forecast.evaluate import (
    ALPHA,
    CALIBRATION_FOLDS,
    COLD_QUANTILE,
    MIN_COLD_HOURS,
    MIN_COLD_WEEKS,
    N_BOOT,
    by_group,
    errors,
    mean_with_ci,
    relative_conformal,
)
from valiwatt.forecast.features import BASE_C, FORECAST_LAGS, AsOf
from valiwatt.forecast.guards import require_design_window, require_embargo, require_known_at_origin
from valiwatt.forecast.issue import LAG_DAY_REASON, MIDNIGHT, NOON, TERM_DEFINITIONS, TERMS, lst_day
from valiwatt.forecast.model import MODELS_V3, NOON_KEYS, V3_COMPARATORS, V3_PRODUCTION, baseline
from valiwatt.sources.nbm import AVAILABILITY_LAG, SENSITIVITY_LAG

V3_FIRST_FOLD, V3_LAST_FOLD = "2020-01", "2024-12"
# The NBM run files v3's design code opens (section 3.1): an explicit list. The 2024 file ends with the run of
# 2025-01-01 01:00 UTC, available before the end of the design window, and kept.
V3_DESIGN_YEARS = tuple(range(2019, 2025))
V3_DESIGN_LAST_DAY = lst_day(pd.DatetimeIndex([V3_DESIGN_LAST]))[0]  # 2024-12-31
KIND = "validation_v3"  # the run's kind: never v2's "validation", so no v2 step reads a v3 run
PRE_FREEZE_TAG = V3_PRE_FREEZE_TAG  # the freeze tag of every run made before v3's freeze exists (section 10.3)
IN_SAMPLE_LABEL_V3 = "in_sample_for_terms_bins_and_subset"  # section 8.4
# v2's form at midnight runs through v2's fold loop under v2's key, renamed to v3's on the joined frame.
MIDNIGHT_KEY, MIDNIGHT_SOURCE = V3_COMPARATORS["horizon"], model.PRODUCTION
NOON_MODELS = tuple(k for k, s in MODELS_V3.items() if s.issue == NOON)
BINNED = tuple(k for k in NOON_MODELS if MODELS_V3[k].interval == "binned")
# Section 4.3: each scored as a paired contrast against v3, labelled, never judged.
SENSITIVITIES = ("v3_no_day_mean", "v3_no_lag_day", "v3_no_wind", "v3_late", "v3_observed", "baseline")
SENSITIVITY_LABEL = "sensitivity_never_judged"
# The noon inputs the out-of-sample frame carries, hour by hour, for the 2024 rehearsal's identity check (section 11.1).
NOON_INPUTS = ("temp_forecast", "wind_forecast", "daymean_forecast", "available_forecast", "lagday_observed",
               "temp_observed", "daymean_observed")
# The reproduction check (sections 12 and 17, step 2): v2's validation years, and the tolerance in MW.
REPRODUCTION_DAYS = (pd.Timestamp("2020-01-01"), pd.Timestamp("2023-12-31"))
REPRODUCTION_TOLERANCE_MW = 1e-6


class ReproductionError(RuntimeError):
    """v2's form at midnight, in v3's harness, does not reproduce v2's recorded validation forecasts."""


# ---------------------------------------------------------------------------------------------------- the inputs


def validation_inputs_v3(raw_demand: pd.DataFrame, raw_temperature: pd.DataFrame, weather: pd.DataFrame,
                         runs: pd.DataFrame, last: pd.Timestamp = V3_DESIGN_LAST,
                         day_verdicts: dict | None = None) -> dict:
    """What `valiwatt validate-v3` runs `run_validation_v3` on, from the raw records, the weather table and the NBM
    runs of V3_DESIGN_YEARS (with their wind): the noon table (the forecast issued at noon for each design day, both
    margins, `validate.forecast_inputs` at NOON; the station's lag day's mean) and the midnight table (v2's inputs
    exactly, `validate.forecast_inputs` at MIDNIGHT), each as the replay issues and scores it, with its training table
    as of a clock and each margin's record of which run fed each day. day_verdicts: the day-level verdict of each
    design day at noon (quality.days.day_verdicts on the noon record and the lag day's mean) unless given. Every key
    returned is a parameter of `run_validation_v3`."""
    primary = vd.temperature_sources(weather)["primary"]
    days = vd.design_days(last)
    last_day = lst_day(pd.DatetimeIndex([last]))[0]
    noon = vd.forecast_inputs(runs, days, last=last, issue=NOON)
    midnight = vd.forecast_inputs(runs, days, last=last)
    asof_noon, asof_midnight = AsOf(raw_demand, last=last, issue=NOON), AsOf(raw_demand, last=last)
    f_noon = vd.design_features(asof_noon, primary, noon, last_day)
    if day_verdicts is None:
        from valiwatt.quality.days import day_verdicts as noon_verdicts  # the noon verdict, v3's data-quality layer

        lag_day = f_noon.groupby("day")["lagday_observed"].first()
        day_verdicts = noon_verdicts(raw_demand, raw_temperature, f_noon["day"].min(), last_day,
                                     record=noon["forecast"].record, lag_day=lag_day)
    return {"f_noon": f_noon, "f_midnight": vd.design_features(asof_midnight, primary, midnight, last_day),
            "train_noon": lambda clock: asof_noon.table(clock, primary, noon),
            "train_midnight": lambda clock: asof_midnight.table(clock, primary, midnight),
            "day_verdicts": day_verdicts,
            "forecast_record": {"noon": {s: x.record for s, x in noon.items()},
                                "midnight": {s: x.record for s, x in midnight.items()}}}


# -------------------------------------------------------------------------------------------- the noon fold loop


def own_rows(key: str, table: pd.DataFrame) -> pd.Series:
    """The usable training rows of model `key` (MODELS_V3) in `table`: its calendar known, every input it reads or
    requires finite (`ModelSpec.inputs`, model.required_known), a finite target. The rows its fit places its knots and
    its bin edge on, taken again from the table (docs/plan-v3.md, sections 4.4 and 7)."""
    spec = MODELS_V3[key]
    ok = model.required_known(table, spec.inputs) & np.isfinite(table["target"].to_numpy(dtype=float))
    return pd.Series(ok, index=table.index)


def term_values(table: pd.DataFrame, key: str = V3_PRODUCTION) -> dict[str, pd.Series]:
    """The temperature and each term (`model.term_columns`) model `key` reads on each row of `table`."""
    spec = MODELS_V3[key]
    values = {f"temp_{spec.scenario}": table[f"temp_{spec.scenario}"].astype(float)}
    values |= {t: pd.Series(v, index=table.index)
               for t, v in model.term_columns(table, spec.terms, spec.scenario, spec.wind).items()}
    return values


def run_noon(f: pd.DataFrame, fold_list: list, train_at: Callable[[pd.Timestamp], pd.DataFrame] | None = None,
             keys=NOON_MODELS) -> tuple[pd.DataFrame, dict]:
    """Out-of-sample forecasts of the noon models `keys` (MODELS_V3) in every fold, and what each fold's fits read.

    `walkforward.run`'s loop and guards, in its order, for v3's registry: the fold's clock must be the table's origin
    of its first test day (walkforward.require_fold_clock); each fold trains on the table seen at its fit clock
    (`train_at`; without it, on f) within its embargo, and its training and test rows pass the origin guard before
    anything is fitted. Every key must be issued at the folds' clock (a midnight model on noon folds is refused).

    Returns the frame (target, fold, day, scored_at, hour, day_type, baseline, pred_<key>, n_train_<key>) and, per
    fold (its test month), the fit clock, each binned model's bin edge as its fit stored it from its own usable
    training rows (section 7), and the (min, max) of v3's temperature and of each of its terms over v3's own usable
    training rows (section 6's added finding)."""
    issues = {fold.issue for fold in fold_list}
    wrong = [k for k in keys if {MODELS_V3[k].issue} != issues]
    if wrong:
        raise ValueError(f"models {wrong} are not issued at the folds' clock ({sorted(i.name for i in issues)})")
    parts, fits = [], {}
    for fold in fold_list:
        train, test = wf.split(f, fold)
        wf.require_fold_clock(test, fold)
        if train_at is not None:
            train, _ = wf.split(train_at(fold.fit_clock), fold)
        if train.empty or test.empty:
            raise ValueError(f"fold {fold.test_month} has no training or test rows")
        require_embargo(train, fold.test_first_day, wf.EMBARGO_DAYS)
        require_known_at_origin(train, f"fold {fold.test_month}, training rows")
        require_known_at_origin(test, f"fold {fold.test_month}, test rows")
        out = pd.DataFrame({"target": test["target"], "fold": str(fold.test_month), "day": test["day"],
                            "scored_at": test["scored_at"], "hour": test["hour"], "day_type": test["day_type"],
                            "baseline": baseline(test)}, index=test.index)
        record = {"fit_clock": fold.fit_clock, "edges": {}, "ranges": {}}
        for k in keys:
            m = model.forecaster(k, MODELS_V3).fit(train)
            out[f"pred_{k}"] = m.predict(test)
            out[f"n_train_{k}"] = m.n_train
            if m.binned:
                record["edges"][k] = float(m.bin_edge)
            if k == V3_PRODUCTION:
                rows = own_rows(k, train)
                if int(rows.sum()) != m.n_train:
                    raise RuntimeError(f"fold {fold.test_month}: {int(rows.sum())} own rows, not the {m.n_train} the "
                                       f"fit of {k} used")
                record["ranges"] = {c: (float(v[rows].min()), float(v[rows].max()))
                                    for c, v in term_values(train.loc[rows], k).items()}
        fits[str(fold.test_month)] = record
        parts.append(out)
    return pd.concat(parts), fits


def join_midnight(out_noon: pd.DataFrame, out_midnight: pd.DataFrame) -> pd.DataFrame:
    """The noon frame with v2's form at midnight beside it (pred_v2_midnight, n_train_v2_midnight), met on the hour
    label only (section 4.2). The two fold loops score the same hours: refused (ValueError) when their labels, days or
    targets differ (a NaN target matches only a NaN), since the contrasts would then compare two outcomes."""
    a, b = out_noon.index, out_midnight.index
    if not (a.is_unique and b.is_unique and a.sort_values().equals(b.sort_values())):
        extra, missing = b.difference(a), a.difference(b)
        raise ValueError(f"the noon and midnight folds score different hours: {len(missing)} only at noon (first "
                         f"{missing[:1].tolist()}), {len(extra)} only at midnight (first {extra[:1].tolist()})")
    m = out_midnight.reindex(a)
    for c in ("day", "target"):
        same = (out_noon[c] == m[c]) | (out_noon[c].isna() & m[c].isna())
        if not same.all():
            raise ValueError(f"the noon and midnight tables give {(~same).sum()} hours another {c} (first "
                             f"{a[~same.to_numpy()][0]})")
    out = out_noon.copy()
    out[f"pred_{MIDNIGHT_KEY}"] = m[f"pred_{MIDNIGHT_SOURCE}"].to_numpy()
    out[f"n_train_{MIDNIGHT_KEY}"] = m[f"n_train_{MIDNIGHT_SOURCE}"].to_numpy()
    return out


# ------------------------------------------------------------------------------------------- what the run reports


def why_noon(f: pd.DataFrame, out: pd.DataFrame, key: str, record: dict) -> pd.Series:
    """Why noon model `key` has no forecast for each out-of-sample hour ("" where it has one), joined by ", " without
    repeats: each input it reads or requires that is missing (`ModelSpec.inputs`), a forecast input (temp_, wind_,
    daymean_ of a forecast scenario) by its day's record reason when no run fed the day, the lag day's mean by
    LAG_DAY_REASON (section 5); "calendar" when every input is present (an unknown day type or hour)."""
    rows = f.reindex(out.index)
    labels = []
    for c in MODELS_V3[key].inputs:
        s = c.split("_", 1)[1] if c.split("_", 1)[0] in ("temp", "wind", "daymean") else None
        if s in FORECAST_LAGS:
            reason = rows["day"].map(record[s]["reason"]).fillna("")
            label = reason.where(reason != "", c)
        else:
            label = pd.Series(LAG_DAY_REASON if c == "lagday_observed" else c, index=out.index)
        labels.append(label.where(rows[c].isna(), ""))
    joined = pd.Series([", ".join(dict.fromkeys(x for x in row if x)) for row in zip(*labels)], index=out.index)
    joined = joined.where(joined != "", "calendar")
    return joined.where(out[f"pred_{key}"].isna(), "")


def sensitivity(frame: pd.DataFrame, key: str, cold_c: float, n_boot: int = N_BOOT) -> dict:
    """A bound or sensitivity of section 4.3: the paired contrast `key` minus v3 (hourly APE, week-block bootstrap),
    on the common set intersected with the hours `key` forecasts and on each subset of section 8.2 so intersected
    (evaluate's own subsets and contrast), the common hours it drops listed by reason (why_<key>). Labelled, never
    judged."""
    common = ev.common_hours(frame, [f"pred_{k}" for k in NOON_KEYS])
    has = np.isfinite(frame.loc[common, f"pred_{key}"].to_numpy(dtype=float))
    hours = common[has]
    dropped = frame.loc[common[~has], f"why_{key}"].value_counts().sort_index()
    subsets = ev._subsets(frame, hours, cold_c)
    return {"label": SENSITIVITY_LABEL, "versus": V3_PRODUCTION, "hours": int(len(hours)),
            "dropped": {str(k): int(v) for k, v in dropped.items()},
            "subsets": {k: ev._subset(frame, h, judged=k != "all") for k, h in subsets.items()},
            "contrast": {k: ev._contrast(frame, h, key, V3_PRODUCTION, n_boot, 0) if len(h) else None
                         for k, h in subsets.items()}}


def bins_table(ivs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Per binned model and fold: the edge of the fit being calibrated, the two half-widths and the pooled one, the
    cold bin's calibration count and weeks, and whether it fell back to the pooled half-width (section 7)."""
    cols = ["edge_c", "q_cold", "q_rest", "q_pooled", "cold_n", "cold_weeks", "fallback", "calibration_n"]
    return pd.concat([iv.groupby("fold", sort=True)[cols].first().reset_index().assign(model=k)
                      for k, iv in ivs.items()], ignore_index=True)[["model", "fold", *cols]]


def run_validation_v3(f_noon: pd.DataFrame, f_midnight: pd.DataFrame, *, day_verdicts: dict, forecast_record: dict,
                      train_noon: Callable[[pd.Timestamp], pd.DataFrame] | None = None,
                      train_midnight: Callable[[pd.Timestamp], pd.DataFrame] | None = None, n_boot: int = N_BOOT,
                      first_fold: str = V3_FIRST_FOLD, last_fold: str = V3_LAST_FOLD,
                      last_day: pd.Timestamp = V3_DESIGN_LAST_DAY,
                      threshold_years: dict = V3_THRESHOLD_YEARS) -> dict:
    """Pure: v3's whole validation (docs/plan-v3.md, sections 6 to 8) on given feature tables. Returns its results and
    the frames to log.

    f_noon, train_noon: the noon table and the table as of a clock (`validation_inputs_v3`); f_midnight,
    train_midnight: v2's; without the train_ callables, each fold trains on its test table, as walkforward.run does.
    day_verdicts: the day-level verdict of each design day at noon (local standard day -> its problems); the
    thresholds are calibrated only on the weeks the replay would judge, and need a verdict for every day of their
    years. forecast_record: {"noon": {scenario: record}, "midnight": {scenario: record}}, each margin's record of which
    run fed each day. threshold_years: {replayed year: (first, last) validation year} (config.V3_THRESHOLD_YEARS)."""
    if set(forecast_record) != {"noon", "midnight"} or any(set(forecast_record[c]) != set(FORECAST_LAGS)
                                                         for c in forecast_record):
        raise ValueError("v3's validation needs the forecast record of each margin at noon and at midnight "
                         "(validation_inputs_v3): without it no hour can say why a model has no forecast")
    last_day = pd.Timestamp(last_day)
    for name, f in (("noon features", f_noon), ("midnight features", f_midnight)):
        require_design_window(f, name, last_day)
        require_known_at_origin(f, name)

    # Every noon model at its noon clock; v2's form at midnight through v2's own loop, at v2's clocks.
    out_n, fits = run_noon(f_noon, wf.month_folds(first_fold, last_fold, issue=NOON), train_noon)
    out_m = wf.run(f_midnight, wf.month_folds(first_fold, last_fold), train_at=train_midnight,
                   models=(MIDNIGHT_SOURCE,))
    out = join_midnight(out_n, out_m)
    out["temp_forecast"] = f_noon["temp_forecast"].reindex(out.index)
    keys = (*NOON_MODELS, MIDNIGHT_KEY)
    err = {k: errors(out, f"pred_{k}") for k in keys} | {"baseline": errors(out, "baseline")}

    # Intervals by temperature for the noon models of the decision rule, each fold at its own edge; v2's single
    # half-width beside, as a sensitivity. v2's form at midnight gets none.
    edges = {k: {fold: r["edges"][k] for fold, r in fits.items()} for k in BINNED}
    ivs = {k: relative_conformal(out, f"pred_{k}", issue=NOON, bins=("temp_forecast", edges[k])) for k in BINNED}
    iv, iv_single = ivs[V3_PRODUCTION], relative_conformal(out, f"pred_{V3_PRODUCTION}", issue=NOON)

    decile = vd._deciles(out[f"pred_{V3_PRODUCTION}"], exact_v1=False)
    hour_group = pd.cut(out["hour"], bins=[-1, 5, 9, 15, 20, 23], labels=list(model.HOUR_BLOCKS)).astype(str)
    perf = {}
    for name, e in err.items():
        e = e.assign(decile=decile.reindex(e.index), hour_group=hour_group.reindex(e.index))
        perf[name] = {"overall": mean_with_ci(e, "ape", n_boot) | {"bias": mean_with_ci(e, "rel_bias", n_boot)},
                      **{g: by_group(e, g, n_boot=n_boot) for g in ("season", "year", "hour", "decile")}}
    coverage = {k: {"binned": vd._coverage(out, x, f"pred_{k}", decile, hour_group, n_boot, groups={"bin": x["bin"]})}
                for k, x in ivs.items()}
    coverage[V3_PRODUCTION]["single"] = vd._coverage(out, iv_single, f"pred_{V3_PRODUCTION}", decile, hour_group,
                                                     n_boot)

    # The drift monitor watches v3's own input, the noon feed's temperature (section 9.3).
    th = {str(y): vd.thresholds(err[V3_PRODUCTION], iv["covered"], f_noon["temp_forecast"], f_noon["day"],
                                out[f"pred_{V3_PRODUCTION}"], out["day"], int(a), int(b), day_verdicts)
          for y, (a, b) in sorted(threshold_years.items())}
    cold_c = vd.design_cold_c(f_noon, last_day)

    record_n, record_m = forecast_record["noon"], forecast_record["midnight"]
    frame = out[["target", "day", "temp_forecast", "baseline", *(f"pred_{k}" for k in keys)]].copy()
    frame["pred_baseline"] = frame.pop("baseline")
    for k in NOON_MODELS:
        frame[f"why_{k}"] = why_noon(f_noon, out, k, record_n)
    why_m = vd._why(f_midnight, out_m, MIDNIGHT_SOURCE, record_m)
    frame[f"why_{MIDNIGHT_KEY}"] = why_m.reindex(out.index).fillna("")
    frame["why_baseline"] = pd.Series("lag168", index=out.index).where(frame["pred_baseline"].isna(), "")
    c = ev.contrasts_v3(frame, cold_c, n_boot=n_boot)
    years = sorted({pd.Period(fold).year for fold in fits})
    ranking = {str(y): ev.rank_windows(frame, f"{y}-01-01", f"{y}-12-31") for y in years}

    lag_missing = f_noon.loc[f_noon["lagday_observed"].isna() & (f_noon["day"] <= last_day), "day"]
    extrapolation = {}
    for name, values in term_values(f_noon.reindex(out.index)).items():
        ranges = {fold: r["ranges"][name] for fold, r in fits.items()}
        unit, value = ("c", "temp_c") if name.startswith("temp_") else ("", "value")
        extrapolation[name] = vd.outside_training_range(out, values, ranges, V3_PRODUCTION, unit=unit, value=value)

    results = {
        "version": "v3",
        "production": V3_PRODUCTION,
        "folds": {"first": first_fold, "last": last_fold, "count": len(fits), "embargo_days": wf.EMBARGO_DAYS,
                  "fit_clock_noon": {fold: r["fit_clock"] for fold, r in fits.items()},
                  "train_rows_last_fold": int(out[f"n_train_{V3_PRODUCTION}"].iloc[-1])},
        "conformal": {"alpha": ALPHA, "calibration_folds": CALIBRATION_FOLDS, "first_fold": iv["fold"].min(),
                      "bin_quantile": COLD_QUANTILE, "bin_min_scores": MIN_COLD_HOURS,
                      "bin_min_weeks": MIN_COLD_WEEKS, "label": "the single half-width is a sensitivity"},
        "performance": perf,
        "coverage": coverage,
        "thresholds": th,
        "contrasts": c | {"label": IN_SAMPLE_LABEL_V3},
        "verdict": ev.verdict_v3(c) | {"label": IN_SAMPLE_LABEL_V3},
        "ranking": {"label": IN_SAMPLE_LABEL_V3, "years": ranking},
        "sensitivity": {k: sensitivity(frame, k, cold_c, n_boot) for k in SENSITIVITIES},
        "common_hours": {"hours": c["hours"], **vd._lacking(frame, keys=NOON_KEYS),
                         "horizon": vd._lacking(frame, keys=(V3_COMPARATORS["terms"], MIDNIGHT_KEY))},
        "forecast_input": {"noon": vd._forecast_input(record_n["forecast"], f_noon, last_day)
                           | {"lag_day_missing_days": [d.strftime("%Y-%m-%d")
                                                      for d in sorted(pd.DatetimeIndex(pd.unique(lag_missing)))]},
                           "midnight": vd._forecast_input(record_m["forecast"], f_midnight, last_day)},
        "extrapolation": extrapolation,
        "cases": {"shift_2020": vd.shift_case(err[V3_PRODUCTION], n_boot),
                  "peak": vd.peak_case(f_noon, out, iv, keys=(*NOON_KEYS, MIDNIGHT_KEY), forecast_temperature=True)},
        "config": {"cold_c": cold_c, "base_c": BASE_C, "term_definitions": dict(TERM_DEFINITIONS),
                   "terms": list(TERMS), "issue": {"noon": NOON.name, "midnight": MIDNIGHT.name},
                   "lags": {"noon": list(NOON.lag_columns), "midnight": list(MIDNIGHT.lag_columns)},
                   "origin_offset_h": {"noon": NOON.origin_offset / pd.Timedelta(hours=1), "midnight": 0.0},
                   "availability_lag_h": AVAILABILITY_LAG / pd.Timedelta(hours=1),
                   "sensitivity_lag_h": SENSITIVITY_LAG / pd.Timedelta(hours=1),
                   "threshold_years": {str(y): list(w) for y, w in sorted(threshold_years.items())},
                   "n_knots": model.N_KNOTS, "degree": model.DEGREE, "ridge_alpha": model.RIDGE_ALPHA,
                   "n_boot": n_boot, "temperature_source": "noon feed",
                   "models": {k: {"family": s.family, "scenario": s.scenario, "wind": s.wind, "terms": list(s.terms),
                                  "lags": list(s.lags), "interval": s.interval, "issue": s.issue.name}
                              for k, s in MODELS_V3.items()}},
    }
    inputs = f_noon.reindex(out.index)[[c for c in NOON_INPUTS if c in f_noon.columns]]
    oos = pd.concat([out, inputs.drop(columns=["temp_forecast"])], axis=1)
    for k in (*NOON_KEYS, MIDNIGHT_KEY):  # why each compared model has no forecast, hour by hour (section 5)
        oos[f"why_{k}"] = frame[f"why_{k}"]
    return {"results": results, "out": oos, "intervals": iv, "intervals_single": iv_single,
            "intervals_by_model": ivs, "bins": bins_table(ivs), "fits": fits,
            "forecast_record": {"noon": record_n["forecast"], "noon_late": record_n["forecast_late"],
                                "midnight": record_m["forecast"]}}


# ------------------------------------------------------------------------------------------ the reproduction check


def check_reproduction(oos: pd.DataFrame, recorded: pd.DataFrame, *, days=REPRODUCTION_DAYS,
                       tolerance_mw: float = REPRODUCTION_TOLERANCE_MW) -> dict:
    """The reproduction check (docs/plan-v3.md, section 17 step 2 and section 12): v2's form at midnight, in v3's
    harness, against the production forecasts of v2's recorded validation run. Pure.

    oos: v3's out-of-sample frame (`run_validation_v3`), indexed by hour-ending UTC label, with column
    pred_v2_midnight. recorded: v2's recorded validation forecasts, the read of v2's store (v2store.recorded_forecasts):
    a DataFrame with columns `ts`, the hour-ending UTC label (tz-aware, each label once), and `forecast`, v2's
    production forecast in MW (NaN where v2 had none).

    On the hours whose local standard day lies in `days` (2020-01-01 to 2023-12-31: the labels 2020-01-01 06:00 to
    2024-01-01 05:00 UTC) on both sides, an hour has a forecast when its value is finite. Refused (ReproductionError)
    when the two sets of hours with a forecast differ (an hour missing or extra, a NaN against a value), or when one
    value differs by more than `tolerance_mw` (1e-6 MW); and (ValueError) on input that breaks the contract: columns
    missing, a label without its time zone or given twice. Returns the hours compared, the largest absolute difference
    in MW and the tolerance."""
    if not {"ts", "forecast"} <= set(recorded.columns):
        raise ValueError(f"v2's recorded forecasts need columns ts and forecast, got {list(recorded.columns)}")
    col = f"pred_{MIDNIGHT_KEY}"
    if col not in oos.columns:
        raise ValueError(f"v3's out-of-sample frame has no {col}")
    ts = pd.DatetimeIndex(recorded["ts"])
    if ts.tz is None or pd.DatetimeIndex(oos.index).tz is None:
        raise ValueError("the reproduction check compares hour-ending UTC labels: a label without its time zone")
    for name, idx in (("v2's recorded forecasts", ts), ("v3's out-of-sample frame", pd.DatetimeIndex(oos.index))):
        if idx.has_duplicates:
            raise ValueError(f"{name} hold a label twice (first {idx[idx.duplicated()][0]})")
    first, last = (pd.Timestamp(d) for d in days)

    def window(values: pd.Series) -> pd.Series:
        day = lst_day(values.index)
        v = values[(day >= first) & (day <= last)].astype(float)
        return v[np.isfinite(v.to_numpy())].sort_index()

    v2 = window(pd.Series(recorded["forecast"].to_numpy(dtype=float), index=ts.tz_convert("UTC")))
    v3 = window(pd.Series(oos[col].to_numpy(dtype=float), index=pd.DatetimeIndex(oos.index).tz_convert("UTC")))
    missing, extra = v2.index.difference(v3.index), v3.index.difference(v2.index)
    if len(missing) or len(extra):
        raise ReproductionError(f"v2's form at midnight forecasts another set of {first.year}-{last.year} hours than "
                                f"v2's recorded validation: {len(missing)} missing (first {missing[:3].tolist()}), "
                                f"{len(extra)} extra (first {extra[:3].tolist()})")
    diff = (v3 - v2.reindex(v3.index)).abs()
    worst = float(diff.max()) if len(diff) else 0.0
    if not worst <= float(tolerance_mw):
        far = diff[~(diff <= float(tolerance_mw))]
        raise ReproductionError(f"v2's form at midnight differs from v2's recorded validation by more than "
                                f"{tolerance_mw} MW on {len(far)} hours (largest {worst!r} MW, first {far.index[0]})")
    return {"hours": int(len(v3)), "max_abs_mw": worst, "tolerance_mw": float(tolerance_mw),
            "first_day": first.strftime("%Y-%m-%d"), "last_day": last.strftime("%Y-%m-%d"), "status": "passed"}


# ------------------------------------------------------------------------------------------------- the run of record


def freeze_tag() -> str:
    """The freeze a v3 run is made under (section 10.3): V3_FREEZE_TAG when that tag exists in the repository, else
    PRE_FREEZE_TAG; records_v3.current_freeze, so once the tag exists a run on other rule files or dependencies than
    the tag's is refused (V3FreezeError) rather than tagged pre-freeze."""
    from valiwatt import records_v3

    return records_v3.current_freeze()["freeze_tag"]


def load_validation_v3(dq):
    """The latest complete v3 validation run built on this data-quality run and these rules, and its out-of-sample
    frame (`validate.load_validation` on v3's kind)."""
    return vd.load_validation(dq, kind=KIND)


def run_validate_v3(n_boot: int = N_BOOT) -> int:
    """`valiwatt validate-v3`: on real data, behind v3's first-run guard (section 10.2), in v3's own store, from v3's
    data-quality run of the 2019-2024 window whose byte copies were checked, or that is labelled a fresh clone
    (run_v3.require_design_run), and the files it checked; the reproduction check against the read of v2's store
    that run was checked against runs before anything is logged and refuses on failure, and is refused without that
    read; on a fresh clone it is reported as not run, so labelled. The run is tagged v3 and with its freeze tag
    (section 10.3)."""
    import mlflow

    from valiwatt.firstrun import require_first_run
    from valiwatt.monitor.rules import rules_sha256
    from valiwatt.monitor.run import load_raw  # monitor/run.py imports validate.py
    from valiwatt.quality import run as qrun
    from valiwatt.quality import run_v3
    from valiwatt.quality.checks import jsonable
    from valiwatt.tracking import start_run
    from valiwatt.v2store import recorded, recorded_forecasts, require_own_store

    require_first_run()
    require_own_store()
    dq = run_v3.latest_passed()
    lineage = run_v3.require_design_run(dq)
    read = None
    if not lineage["fresh_clone"]:
        read = recorded()
        if read is None or read["run_id"] != dq.data.params.get("v2_read_run_id"):
            raise vd.LineageError(f"data-quality run {dq.info.run_id} checked its byte copies against the read of v2's "
                                  f"store {dq.data.params.get('v2_read_run_id')!r}, and v3's store holds "
                                  f"{None if read is None else read['run_id']!r}: no reproduction check without it")
    dq, _, weather = vd.load_inputs(dq, run_v3.canonical_paths(THROUGH_DEFAULT), command="valiwatt dq-v3")
    raw_demand, raw_temp = load_raw(dq)
    runs = qrun.load_runs(dq, years=V3_DESIGN_YEARS, wind=True)
    v = run_validation_v3(n_boot=n_boot, **validation_inputs_v3(raw_demand, raw_temp, weather, runs))
    if lineage["fresh_clone"]:
        reproduction = {"status": "not_run", "fresh_clone": True,
                        "why": "a fresh clone: its data-quality run is labelled so, and no read of v2's store exists"}
    else:
        reproduction = check_reproduction(v["out"], recorded_forecasts()) | {"v2_read_run_id": read["run_id"],
                                                                             "fresh_clone": False}
    res = v["results"] | {"reproduction": reproduction}
    tags = {"kind": KIND, "version": "v3", "freeze_tag": freeze_tag(), "dq_run_id": dq.info.run_id,
            "rules_sha256": rules_sha256(), "first_run_tag": V3_FIRST_RUN_TAG, "production": V3_PRODUCTION,
            "reproduction": reproduction["status"], "v2_read_run_id": reproduction.get("v2_read_run_id", ""),
            "byte_copies": lineage["byte_copies"], run_v3.FRESH_CLONE: str(lineage["fresh_clone"]).lower(),
            **{k: dq.data.params[k] for k in ("hq_sha256", "eccc_manifests_sha256", "nbm_manifest_sha256")}}
    with start_run(vd.EXPERIMENT, "validation-v3", tags=tags) as run:
        mlflow.log_params({"first_fold": V3_FIRST_FOLD, "last_fold": V3_LAST_FOLD, "embargo_days": wf.EMBARGO_DAYS,
                           "alpha": ALPHA, "calibration_folds": CALIBRATION_FOLDS, "n_boot": n_boot,
                           "cold_c": res["config"]["cold_c"], "cold_quantile": COLD_QUANTILE,
                           "bin_quantile": COLD_QUANTILE, "bin_min_scores": MIN_COLD_HOURS,
                           "bin_min_weeks": MIN_COLD_WEEKS, "noon_fit_clock": "D-1 17:00 UTC",
                           "midnight_fit_clock": "D 05:00 UTC", "lags_noon": ",".join(NOON.lag_columns),
                           "lags_midnight": ",".join(MIDNIGHT.lag_columns),
                           **{f"term_{k}": val for k, val in TERM_DEFINITIONS.items()},
                           "design_years": ",".join(str(y) for y in V3_DESIGN_YEARS),
                           "threshold_years": ";".join(f"{y}={a}-{b}" for y, (a, b) in
                                                       sorted(V3_THRESHOLD_YEARS.items())),
                           "models": ",".join(MODELS_V3), "production": V3_PRODUCTION,
                           "comparators": ",".join(f"{k}={val}" for k, val in V3_COMPARATORS.items())})
        for replay_year, t in res["thresholds"].items():
            for season, p in t["performance"].items():
                mlflow.log_params({f"th{replay_year}_{season}_{k}": round(val, 6) for k, val in p.items()})
            mlflow.log_params({f"th{replay_year}_drift_{k}": val for k, val in t["drift"].items()})
            mlflow.log_param(f"th{replay_year}_excluded_weeks", len(t["excluded_weeks"]))
        for name, p in res["performance"].items():
            mlflow.log_metrics({f"mape.{name}": p["overall"]["mean"], f"bias.{name}": p["overall"]["bias"]["mean"]})
        for k, cov in res["coverage"].items():
            for form, cv in cov.items():
                mlflow.log_metric(f"coverage.{k}.{form}", cv["overall"]["mean"])
        for name in ("terms", "family", "whole"):
            mlflow.log_metric(f"contrast.{name}", res["contrasts"][name]["all"]["difference"])
        horizon = res["contrasts"]["horizon"]["contrast"]["all"]
        if horizon is not None:
            mlflow.log_metric("contrast.horizon", horizon["difference"])
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            v["out"].to_parquet(tmp / "oos.parquet")
            for k, x in v["intervals_by_model"].items():
                x.to_parquet(tmp / f"intervals_{k}.parquet")
            v["intervals_single"].to_parquet(tmp / "intervals_single.parquet")
            v["bins"].to_parquet(tmp / "bins.parquet")
            for name, rec in v["forecast_record"].items():
                rec.to_parquet(tmp / f"forecast_record_{name}.parquet")
            mlflow.log_artifacts(str(tmp), artifact_path="frames")
        res["run_id"] = run.info.run_id
        mlflow.log_text(json.dumps(jsonable(res), indent=2, ensure_ascii=False), "results/validation.json")
        mlflow.set_tag("status", "complete")
    o = res["performance"]
    print(f"v3 validation run {run.info.run_id} ({tags['freeze_tag']}): MAPE v3 "
          f"{100 * o[V3_PRODUCTION]['overall']['mean']:.2f}%, v2 at noon {100 * o['v2_noon']['overall']['mean']:.2f}%, "
          f"v2 at midnight {100 * o[MIDNIGHT_KEY]['overall']['mean']:.2f}%; verdicts {res['verdict']['terms']} / "
          f"{res['verdict']['family']}; reproduction {reproduction['status']} ({IN_SAMPLE_LABEL_V3})")
    return 0

