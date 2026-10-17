"""Render the validation report (French first, English derived) into docs/report/, from the chain of tracked runs.

The report is committed (owner's ruling). It carries a stamp (docs/report/stamp.json): the run IDs it was rendered
from, the code hash, the rules hash, the hash of every rendered file, the hash of the replay ledger and, once a 2025
replay has started, the test of record's standing as its gate logged it (`standing_2025`).
`tests/test_report_stamp.py` fails when the committed code no longer matches the stamp, or a rendered file or the
ledger no longer matches its hash, so a report can never sit beside code that did not produce it, nor be edited by
hand. The data-quality report is rendered from the chain's own data-quality run.

v2's report (docs/plan-v2.md, sections 8, 11 and 13) opens with the model card's status, computed by
`evaluate.status` from the 2025 test of record alone (`ledger.of_record`), never from a rerun; shows v1 and v2 side
by side, each result with its evaluation label read from the runs; and refuses to render a report that would claim
more than its runs show (`ReportRefused`): a v2 number on 2024 without its label, a "better" claim without the
held-out label or from a 2025 that is not clean, a 2025 verdict without a 2025 replay, a test of record other than
the walk's, a stopped or unfinished start not listed, a clean 2025 that does not confirm v2 without its
recommendation, revised records or a lead of 75 hours not stated, a standing other than the one the gate logged, a
record read from the ledger without its label or without the sentence on what only the store holds, v1's 2024
numbers from anything but its ledger entries, and an attribution block without the NOAA and IEM lines.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from valiwatt import attribution
from valiwatt.forecast.evaluate import _weights, errors
from valiwatt.paths import REPO_ROOT, REPORTS
from valiwatt.report import figures as F
from valiwatt.report import ledger
from valiwatt.report.lineage import Absent, Chain, RecordedReplay, Unfinished, follow, run_id_of
from valiwatt.report.render import (
    LEAD_FINDING,
    ReportRefused,
    assert_attributed,
    assert_public_safe,
    environment,
    filters,
    nbm_findings,
    render,
)

OUT = REPORTS
STAMP = OUT / "stamp.json"
LANGS = ("fr", "en")
HELD_OUT = 2025
# The statuses that state a result on held-out data (plan section 8.5); the others state none.
HELD_OUT_RESULTS = ("confirmed", "input_only", "not_confirmed")


def _json(c, run, path: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        return json.loads(Path(c.download_artifacts(run.info.run_id, path, tmp)).read_text(encoding="utf-8"))


def _frame(c, run, path: str, **csv) -> pd.DataFrame:
    """A run's frame artifact: parquet, or CSV read with `csv` (pandas.read_csv's arguments)."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(c.download_artifacts(run.info.run_id, path, tmp))
        return pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p, **csv)


def _when(run) -> str:
    if isinstance(run, RecordedReplay):
        return pd.Timestamp(run.entry["started_utc"]).strftime("%Y-%m-%d %H:%M UTC")
    return pd.Timestamp(run.info.start_time, unit="ms", tz="UTC").strftime("%Y-%m-%d %H:%M UTC")


def season_coverage(coverage: dict, nominal: float) -> list[dict]:
    """Coverage by season for the production interval (one quantile) and the per-season sensitivity, each with its
    week-block 95 % interval. For each season: whether the per-season interval contains the nominal, and whether the
    two forms' intervals are disjoint. No interval on the difference between the forms is computed, so disjoint
    intervals are all the comparison shows. Both judgements use the bounds as the tables print them (one decimal of a
    percentage), so a sentence never contradicts the numbers beside it."""
    rows = []
    for season in F.SEASON_ORDER:
        row = {"key": season}
        for form, name in (("pooled", "relative"), ("by_season", "relative_by_season")):
            found = [x for x in coverage[name]["season"] if x["key"] == season]
            if len(found) != 1:
                raise ReportRefused(f"the validation run has {len(found)} {name} coverage rows for {season}")
            x = found[0]
            row[form] = {"covered": x["covered"], "low": x["covered_low"], "high": x["covered_high"]}
        one, per = ({k: round(v, 3) for k, v in row[f].items()} for f in ("pooled", "by_season"))
        row["contains_nominal"] = bool(per["low"] <= round(nominal, 3) <= per["high"])
        row["disjoint"] = bool(one["high"] < per["low"] or per["high"] < one["low"])
        rows.append(row)
    return rows


def printed(x: float, digits: int = 1) -> float:
    """A fraction as the report prints it in percent (`pct(digits)`), so a judgement never contradicts its numbers."""
    return float(f"{100 * x:.{digits}f}")


def coverage_sides(rows: list[dict], nominal: float, low: str = "covered_low", high: str = "covered_high") -> dict:
    """Which groups are under-covered (95 % interval wholly below the nominal) and over-covered (wholly above), on the
    bounds as the tables print them (one decimal of a percentage). A group whose interval holds the nominal is in
    neither list."""
    n = printed(nominal)
    return {"under": [r["key"] for r in rows if printed(r[high]) < n],
            "over": [r["key"] for r in rows if printed(r[low]) > n]}


def hour_blocks(rows: list[dict], blocks: dict) -> list[dict]:
    """The coverage rows of the hour blocks in the order the run's configuration lists them, the order of the report's
    table; a block missing from the rows, or a row for a block the configuration lacks, is refused."""
    by_key = {r["key"]: r for r in rows}
    if sorted(by_key) != sorted(blocks) or len(by_key) != len(rows):
        raise ReportRefused(f"the validation run's hour-block coverage rows {sorted(by_key)} are not its configured "
                            f"blocks {sorted(blocks)}")
    return [by_key[k] for k in blocks]


def detection_limits(power: dict) -> dict:
    """What a replay's power table shows, for the sentences that sum it up.

    bias: for each size of the sustained gap (both signs), how many of the cases (start x sign) yielded *retrain*
    within the horizon; `every_from`, the smallest size from which every case of that size and every larger one did
    (None when even the largest size missed somewhere); the smallest size measured, with its catches by sign.
    half_width: for each narrowing factor, how many starts yielded *recalibrate*; the strongest narrowing (the smallest
    factor) apart, and the most any milder factor caught."""
    rows = power["bias"]
    sizes = sorted({round(abs(x["bias"]), 6) for x in rows})
    if not sizes:
        raise ReportRefused("the replay's power table has no sustained-gap rows")
    by_size = {m: [x for x in rows if round(abs(x["bias"]), 6) == m] for m in sizes}
    every_from = None
    for m in reversed(sizes):
        if all(x["weeks"] is not None for x in by_size[m]):
            every_from = m
        else:
            break
    small = sizes[0]

    def caught(xs):
        return {"caught": sum(x["weeks"] is not None for x in xs), "cases": len(xs)}

    sign = {"up": caught([x for x in by_size[small] if x["bias"] > 0]),
            "down": caught([x for x in by_size[small] if x["bias"] < 0])}
    hw = power["half_width"]
    factors = sorted({x["half_width"] for x in hw})
    if not factors:
        raise ReportRefused("the replay's power table has no narrowed-interval rows")
    per = {f: caught([x for x in hw if x["half_width"] == f]) for f in factors}
    milder = [per[f]["caught"] for f in factors[1:]]
    starts = list(dict.fromkeys(x["start"] for x in rows))
    return {"sizes": sizes, "every_from": every_from, "smallest": small, "smallest_by_sign": sign,
            "starts": len(starts), "months": [pd.Timestamp(s).month for s in starts],
            "strongest": factors[0], "strongest_caught": per[factors[0]],
            "mildest": factors[-1], "milder_max": max(milder) if milder else None,
            "half_width_starts": len({x["start"] for x in hw})}


def near_misses(alone: list[dict], weeks: list[dict], decisions: list[dict]) -> list[dict]:
    """The weeks where the first wording of rule 2 said *retrain*, with why the rule kept did or did not.

    kept_by: "fired" when the replay's own decision for that week is *retrain*; otherwise "different" when no failure
    is flagged in both weeks (the weeks fail on different statistics, or bias on opposite sides); "trim" when a
    failure is flagged in both weeks but, for each such failure, at least one week is not broad (with its most extreme
    days set aside, it is back inside); "echo" when MAPE is broad in both weeks and the biases are flagged on opposite
    sides. `same` lists the failures flagged in both weeks, each with both weeks' trimmed statistic and threshold."""
    by_start = {str(w["week_start"]): w for w in weeks}
    retrained = {str(d["week_start"]) for d in decisions if d["recommendation"] == "retrain"}
    out = []
    for item in alone:
        pair = []
        for x in item["weeks"]:
            w = by_start.get(str(x["week_start"]))
            if w is None or not w.get("evaluated"):
                raise ReportRefused(f"near-miss week {x['week_start']} is not among the replay's judged weeks")
            pair.append(w)
        same = [k for k in ("mape_high", "bias_high", "bias_low") if all(w["flags"][k] for w in pair)]
        opposite = any(pair[0]["flags"][a] and pair[1]["flags"][b]
                       for a, b in (("bias_low", "bias_high"), ("bias_high", "bias_low")))
        broad_both = [k for k in same if all(w["broad"][k] for w in pair)]
        if str(item["week_start"]) in retrained:
            kept_by = "fired"
        elif not same:
            kept_by = "different"
        elif broad_both == ["mape_high"] and opposite:
            kept_by = "echo"
        elif not broad_both:
            kept_by = "trim"
        else:
            raise ReportRefused(f"week {item['week_start']}: the first wording's retrain has a failure broad in both "
                                "weeks, yet the replay recorded no retrain")
        flips = item["weeks"][0]["stats"]["rel_bias"] * item["weeks"][1]["stats"]["rel_bias"] < 0
        out.append({**item, "kept_by": kept_by, "bias_flips": bool(flips),
                    "bias_flagged": any(w["flags"]["bias_high"] or w["flags"]["bias_low"] for w in pair),
                    "same": [{"failure": k, "trimmed": [w["trimmed"][k] for w in pair], "broad": [w["broad"][k] for w in pair],
                              "threshold": pair[0]["thresholds"][k], "thresholds_equal":
                                  pair[0]["thresholds"][k] == pair[1]["thresholds"][k]} for k in same]})
    return out


# The spring 2020 interval of the difference is one computation made at render time, after the results, so it can
# afford many more draws than the runs' N_BOOT: SHIFT_DIFF_BATCHES independent batches of SHIFT_DIFF_BATCH_DRAWS draws
# on each side. Each batch resamples the two sides with its own pair of seeds, all distinct, so the sides and the
# batches are independent. The batches also measure the Monte Carlo error of each bound (batch means: the spread of
# the bound across batches over the square root of their number). A bound within SHIFT_DIFF_MC_MULTIPLE of those
# errors of zero leaves the verdict undetermined at this resolution. The rule is applied to the bounds and errors as
# the verdict prints them, SHIFT_DIFF_DIGITS decimals of a point (two significant figures of the errors measured on
# the real runs, about 0.002 to 0.003 pt), so the verdict never contradicts its own printed numbers.
SHIFT_DIFF_BATCHES = 20
SHIFT_DIFF_BATCH_DRAWS = 20_000
SHIFT_DIFF_SEEDS = tuple((2 * k, 2 * k + 1) for k in range(SHIFT_DIFF_BATCHES))  # (spring 2020, the other springs)
SHIFT_DIFF_MC_MULTIPLE = 3
SHIFT_DIFF_DIGITS = 4


def _boot_means(d: pd.DataFrame, value: str, n_boot: int, seed: int) -> np.ndarray:
    """The draws behind `evaluate.mean_with_ci`: whole weeks resampled with the same weights and seed."""
    g = d.groupby("week")[value].agg(["sum", "count"])
    W = _weights(len(g), n_boot, seed)
    return (W @ g["sum"].to_numpy()) / (W @ g["count"].to_numpy())


def printed_pt(x: float, digits: int):
    """A fraction as the report prints it in points (`pp(x, digits)`, `mc_se`), exactly, as a decimal."""
    from decimal import Decimal

    return Decimal(f"{100 * x:.{digits}f}")


def shift_verdict(lo: float, hi: float, se_lo: float, se_hi: float, multiple: float = SHIFT_DIFF_MC_MULTIPLE,
                  digits: int = SHIFT_DIFF_DIGITS) -> dict:
    """Section 1's rule on an interval of the difference whose bounds carry Monte Carlo error.

    "undetermined" when a bound lies within `multiple` Monte Carlo standard errors of zero (`by` "mc"), or, farther
    out, rounds to zero as the report prints it, two decimals of a point (`by` "print"): the draws cannot tell which
    side of zero it is on, or the verdict would contradict the printed bound. Otherwise "confirmed" when the interval
    excludes zero and "not_confirmed" when it holds it. `near` is the bound nearest zero among those that leave the
    verdict undetermined.

    The Monte Carlo rule compares the bound and its error as the verdict prints them, `digits` decimals of a point,
    in exact decimal arithmetic: a bound printed at most `multiple` printed errors from zero is undetermined, one
    printed farther out is not, so no printed number can contradict the verdict."""
    from decimal import Decimal

    near = []
    for bound, x, se in (("low", lo, se_lo), ("high", hi, se_hi)):
        if abs(printed_pt(x, digits)) <= Decimal(str(multiple)) * printed_pt(se, digits):
            near.append({"bound": bound, "value": x, "se": se, "by": "mc"})
        elif printed(x, 2) == 0:
            near.append({"bound": bound, "value": x, "se": se, "by": "print"})
    if near:
        return {"verdict": "undetermined", "near": min(near, key=lambda n: abs(n["value"]))}
    return {"verdict": "confirmed" if lo > 0 or hi < 0 else "not_confirmed", "near": None}


def shift_difference(oos: pd.DataFrame, pred_col: str, seeds=SHIFT_DIFF_SEEDS,
                     batch_draws: int = SHIFT_DIFF_BATCH_DRAWS) -> dict:
    """Spring of SHIFT_YEAR minus the same months of the other validation years: the difference of the mean signed
    bias, with a 95 % interval from the code's week-block bootstrap, each side resampled on its own weeks (the two
    sides share no hour, so no paired difference exists), in independent batches (`seeds`, one pair per batch). The
    bounds are the quantiles of all the draws; their Monte Carlo standard errors come from the spread of the batches'
    own bounds. Computed at render time, after the results."""
    from valiwatt.forecast.validate import SHIFT_MONTHS, SHIFT_YEAR

    flat = [s for pair in seeds for s in pair]
    if len(seeds) < 2 or len(set(flat)) != len(flat):
        raise ReportRefused("the spring difference needs at least two batches and a seed of its own for each side of "
                            "each batch")
    e = errors(oos, pred_col)
    months = pd.to_datetime(e["day"]).dt.month.isin(SHIFT_MONTHS)
    a, b = e[months & (e["year"] == SHIFT_YEAR)], e[months & (e["year"] != SHIFT_YEAR)]
    if a.empty or b.empty:
        raise ReportRefused(f"the validation frame holds no spring {SHIFT_YEAR} hours or no other spring")
    batches = [_boot_means(a, "rel_bias", batch_draws, sa) - _boot_means(b, "rel_bias", batch_draws, sb)
               for sa, sb in seeds]
    per = np.array([np.quantile(d, [0.025, 0.975]) for d in batches])
    lo, hi = (float(x) for x in np.quantile(np.concatenate(batches), [0.025, 0.975]))
    se_lo, se_hi = (float(x) for x in per.std(axis=0, ddof=1) / np.sqrt(len(seeds)))
    return {"difference": float(a["rel_bias"].mean() - b["rel_bias"].mean()), "ci95_low": lo, "ci95_high": hi,
            "mc_se_low": se_lo, "mc_se_high": se_hi, "draws": len(seeds) * batch_draws, "batches": len(seeds),
            "batch_draws": batch_draws, "mc_multiple": SHIFT_DIFF_MC_MULTIPLE, "digits": SHIFT_DIFF_DIGITS,
            **shift_verdict(lo, hi, se_lo, se_hi, SHIFT_DIFF_MC_MULTIPLE, SHIFT_DIFF_DIGITS)}


def drift_reference_years(first_year: int, last_year: int, replay_year: int, folds_first: str) -> dict:
    """How many earlier years the drift references hold: for the validation weeks that set the alarm levels, and for
    the replayed year. The temperature reference (v2: the issued forecast, rebuilt from the runs in view) starts with
    the demand window; the forecast reference with the first validation fold."""
    from valiwatt.config import DEMAND_FIRST, LST_OFFSET

    starts = {"temperature": (DEMAND_FIRST - pd.Timedelta(hours=1) + LST_OFFSET).year,
              "forecast": pd.Period(folds_first, freq="M").year}
    return {k: {"calibration": (first_year - s, last_year - s), "replay": replay_year - s} for k, s in starts.items()}


def constants(v: dict, frozen24: dict, S: dict | None) -> dict:
    """The design constants the report states, read from the runs or from the code that made them (the report chain
    refuses runs made from other code, so the two agree). S: the 2023 rehearsal's false-alarm and blind-spot measures
    (its diagnostic run's), whose design the report states; None when they did not finish, and the constants drawn
    from them are None too."""
    from valiwatt.config import DEMAND_FIRST, DESIGN_LAST, HELD_OUT_YEAR, LST_OFFSET
    from valiwatt.forecast.evaluate import COLD_QUANTILE, MIN_COLD_HOURS, MIN_COLD_WEEKS, PEAK_HOURS, SEASONS
    from valiwatt.monitor import rules
    from valiwatt.monitor import sensitivity as sens
    from valiwatt.monitor.run import POWER_HORIZON_WEEKS
    from valiwatt.sources.nbm import MAX_RUNS_BACK

    th = v["thresholds"]
    features = [row["feature"] for row in frozen24["coefficients"]]
    season_months = {}
    for month, season in SEASONS.items():
        season_months.setdefault(season, []).append(month)
    year_of = lambda hour_ending: (hour_ending - pd.Timedelta(hours=1) + LST_OFFSET).year  # noqa: E731
    out = {
        "nominal": 1 - v["conformal"]["alpha"],
        "embargo_days": v["folds"]["embargo_days"],
        "n_boot": v["config"]["n_boot"],
        "calibration_folds": v["conformal"]["calibration_folds"],
        "calibration_scores": int(frozen24["params"]["calibration_scores"]),
        "centiles": {k: round(100 * q, 1) for k, q in th[max(th)]["quantiles"].items()},
        "drift_half_window": th[max(th)]["drift"]["reference_half_window_weeks"],
        "cells": sum(f.startswith("cell:") for f in features),
        "lags": sorted(int(f[3:]) for f in features if f.startswith("lag")),
        "design_years": (year_of(DEMAND_FIRST), year_of(DESIGN_LAST)),
        "validation_years": (int(v["folds"]["first"][:4]), int(v["folds"]["last"][:4])),
        "threshold_years": {y: (t["first_year"], t["last_year"]) for y, t in th.items()},
        "season_months": season_months,
        "trim_days": rules.TRIM_DAYS, "consecutive_weeks": rules.CONSECUTIVE_WEEKS,
        "sustained_weeks": rules.SUSTAINED_WEEKS,
        "power_horizon": POWER_HORIZON_WEEKS, "step_horizon": sens.STEP_HORIZON_WEEKS,
        "echo_margin_days": sens.ECHO_MARGIN_DAYS,
        "min_cold_hours": MIN_COLD_HOURS, "min_cold_weeks": MIN_COLD_WEEKS, "cold_quantile": COLD_QUANTILE,
        "peak_hours": PEAK_HOURS, "origin_utc": f"{-LST_OFFSET // pd.Timedelta(hours=1):02d}:00",
        "held_out_year": HELD_OUT_YEAR,
        "max_runs_back": MAX_RUNS_BACK, "lag_h": v["config"]["availability_lag_h"],
        "late_h": v["config"]["sensitivity_lag_h"],
        "episode_days": None, "mild_factors": None, "mild": None, "shift": None,
    }
    if S is not None:
        shifts = sorted({round(abs(x["shift"]), 6) for x in S["level_shifts"]})
        factors = sorted({x["factor"] for x in S["episodes"]["rows"]})
        mild = min(abs(f - 1) for f in factors)
        out |= {"episode_days": sorted({x["days"] for x in S["episodes"]["rows"]}),
                "mild_factors": [f for f in factors if abs(abs(f - 1) - mild) < 1e-9], "mild": mild,
                "shift": shifts[-1]}
    return out


def level_shift(p: dict, size: float) -> dict:
    """The largest level shift measured on a replay's year: rises missed and falls caught within the horizon. `months`
    names every start of a rise (the starts the measure tested), `missed_months` only those whose rise was missed.
    p: the replay's measures (its diagnostic run's results, under `sensitivity`)."""
    rows = p["sensitivity"]["level_shifts"]
    up = [x for x in rows if abs(x["shift"] - size) < 1e-9]
    down = [x for x in rows if abs(x["shift"] + size) < 1e-9]
    return {"size": size, "up_n": len(up), "up_missed": sum(x["weeks"] is None for x in up),
            "down_n": len(down), "down_caught": sum(x["weeks"] is not None for x in down),
            "months": [pd.Timestamp(x["start"]).month for x in up],
            "missed_months": [pd.Timestamp(x["start"]).month for x in up if x["weeks"] is None]}


SENSITIVITY_PARTS = ("episodes", "level_shifts", "echo_exclusion_cost")


def sensitivity(run: dict) -> dict:
    """A replay's false-alarm and blind-spot measures (monitor/sensitivity.py), from its diagnostic run's results (v2:
    the measures run after the replay, in a run of their own, docs/plan-v2.md section 11.3.5). A diagnostic run that
    finished and is marked complete but holds no measures (episodes, level shifts, echo-exclusion cost) is refused, so
    the report never prints one year's measures where it says two; one that did not finish is said so instead."""
    S = run.get("sensitivity")
    if not isinstance(S, dict) or any(k not in S for k in SENSITIVITY_PARTS):
        raise ReportRefused(f"the {run.get('year')} replay's diagnostics hold no false-alarm and blind-spot measures "
                            "(episodes, level shifts, echo-exclusion cost): they were made before they were "
                            "measured on its year; run the replay again")
    return S


def sensitivity_design(S: dict) -> dict:
    """What the measures were made of: episode lengths and factors, level-shift sizes, noise levels and horizon."""
    return {"episodes": sorted({(x["days"], x["factor"]) for x in S["episodes"]["rows"]}),
            "shifts": sorted({round(x["shift"], 6) for x in S["level_shifts"]}),
            "sds": sorted({x["sd"] for x in S["echo_exclusion_cost"]["rows"]}),
            "horizon": S["echo_exclusion_cost"]["horizon_weeks"]}


def same_sensitivity_design(p: dict, r: dict) -> None:
    """The report states one design for both years' measures (lengths, factors, shifts, noise), read from the
    rehearsal's: the 2024 replay's must be the same, or those sentences would be false for it."""
    a, b = sensitivity_design(sensitivity(p)), sensitivity_design(sensitivity(r))
    diff = [k for k in a if a[k] != b[k]]
    if diff:
        raise ReportRefused(f"the {p['year']} and {r['year']} false-alarm and blind-spot measures were made with "
                            f"different designs ({', '.join(diff)}); the report states one for both")


def episode_limits(S: dict, mild_factors: list) -> dict:
    """What the limits sum up of one year's measures: the episode of several days that most often yields *retrain*
    (None when none does) and its share of starts; the one that most often yields *recalibrate*, likewise; the
    largest *retrain* share for the mildest factors; the alarms one-day shocks gave (`one_day`: *retrain*, of which
    clean, and *recalibrate*, over all their cases, a start times a factor; the measures split *retrain* alone into
    clean and assisted); and the echo exclusion's cost, summed over its noise levels."""
    worst, share, mild = None, 0.0, 0.0
    recal_worst, recal_share = None, 0.0
    for row in S["episodes"]["rows"]:
        rate = row["retrain"] / row["starts"]
        if row["days"] > 1 and rate > share:
            worst, share = row, rate
        if row["days"] > 1 and row["recalibrate"] / row["starts"] > recal_share:
            recal_worst, recal_share = row, row["recalibrate"] / row["starts"]
        if row["factor"] in mild_factors and rate > mild:
            mild = rate
    ec = S["echo_exclusion_cost"]["rows"]
    one = [x for x in S["episodes"]["rows"] if x["days"] == 1]
    one_day = {"retrain": sum(x["retrain"] for x in one), "retrain_clean": sum(x["retrain_clean"] for x in one),
               "recalibrate": sum(x["recalibrate"] for x in one), "cases": sum(x["starts"] for x in one)}
    return {"worst": worst, "share": share, "mild": mild, "recal_worst": recal_worst, "recal_share": recal_share,
            "one_day": one_day, "one_day_alarms": one_day["retrain"] + one_day["recalibrate"],
            "echo": {k: sum(x[k] for x in ec) for k in ("suppressed", "runs", "delayed")}}


# --- v2: the three models, the labels, the selection's tracked values ---------------------------------------------

def _models() -> tuple[str, str, str]:
    from valiwatt.forecast.model import COMPARATORS, PRODUCTION

    return PRODUCTION, COMPARATORS["family"], COMPARATORS["shipped"]


def selection(v: dict, oos: pd.DataFrame) -> dict:
    """The selection's tracked values (plan section 0.2): on v2's common hours of 2020-2023 (the hours with a target
    and a forecast from each of the three models v2 carries), each model's mean absolute percentage error, and at the
    five busiest hours each model's signed error (actual minus forecast, over actual; positive when the forecast is
    short), or why it has no forecast. Computed from the validation run's out-of-sample frame and checked against its
    logged contrasts: the same hours, and differences equal to the logged ones."""
    from valiwatt.forecast.evaluate import common_hours

    prod, family, shipped = _models()
    keys = (prod, family, shipped)
    hours = common_hours(oos, [f"pred_{k}" for k in keys])
    if len(hours) != v["contrasts"]["hours"]:
        raise ReportRefused(f"the validation frame holds {len(hours)} common hours, its contrasts {v['contrasts']['hours']}")
    t = oos.loc[hours, "target"].astype(float)
    mape = {k: float(((t - oos.loc[hours, f"pred_{k}"].astype(float)).abs() / t).mean()) for k in keys}
    for name, a, b in (("shipped", prod, shipped), ("family", prod, family), ("input", family, shipped)):
        if not np.isclose(mape[a] - mape[b], v["contrasts"][name]["difference"], rtol=0, atol=1e-12):
            raise ReportRefused(f"the {name} contrast the validation run logged is not the difference of the models' "
                                "errors on its common hours")
    peak = []
    for h in v["contrasts"]["peak"]:
        row = {"utc": h["utc"], "target": h["target"], "temp_forecast": h["temp_forecast"], "signed": {}, "why": {}}
        for k in keys:
            pred = h.get(f"pred_{k}")
            if pred is None or not np.isfinite(pred):
                row["signed"][k] = None
                row["why"][k] = (h.get("missing") or {}).get(k, "no forecast")
            else:
                row["signed"][k] = (h["target"] - pred) / h["target"]
        peak.append(row)
    mean_signed = {k: (float(np.mean([r["signed"][k] for r in peak])) if peak and all(
        r["signed"][k] is not None for r in peak) else None) for k in keys}
    return {"hours": int(len(hours)), "weeks": v["contrasts"]["weeks"], "mape": mape, "peak": peak,
            "mean_signed": mean_signed, "models": {"production": prod, "family": family, "shipped": shipped}}


def check_labels(v: dict, p: dict, r: dict) -> None:
    """Every v2 result carries its evaluation label, read from the runs (plan section 10.5): 2020-2023 in sample for
    the choice, the 2023 rehearsal, 2024 seen by the developers. A v2 number on 2024 without its label refuses."""
    from valiwatt.config import EVALUATION, IN_SAMPLE_LABEL

    for name, block, want in (("validation", v, IN_SAMPLE_LABEL), (f"the {p.get('year')} rehearsal", p,
                                                                     EVALUATION.get(p.get("year"))),
                              (f"the {r.get('year')} replay", r, EVALUATION.get(r.get("year")))):
        for part in ("contrasts", "verdict"):
            got = (block.get(part) or {}).get("label") if isinstance(block.get(part), dict) else None
            if got != want:
                raise ReportRefused(f"{name}'s {part} carry the label {got!r}, not {want!r}: no v2 number is rendered "
                                    "without its label")


def derive(v: dict, dq: dict, p: dict, r: dict, b: dict, frozen: dict, oos: pd.DataFrame,
           diag: dict | None = None) -> dict:
    """Everything the templates read that is computed from the runs rather than printed as stored. Pure: no store.
    diag: each replay's diagnostics ({"pseudo": ..., "replay": ...}, each its diagnostic run's results or None when
    they did not finish)."""
    from valiwatt.breaks import SYNTHETIC_WORLD
    from valiwatt.config import LOCAL_TZ

    for row in b["breaks"]:
        if "control" not in row:
            raise ReportRefused("the breaks run does not say which breaks are controls; run `valiwatt breaks` again")
    diag = diag or {}
    dp, dr = diag.get("pseudo"), diag.get("replay")
    Sp = sensitivity({**dp, "year": p.get("year")}) if dp is not None else None
    Sr = sensitivity({**dr, "year": r.get("year")}) if dr is not None else None
    if Sp is not None and Sr is not None:
        same_sensitivity_design({"sensitivity": Sp, "year": p["year"]}, {"sensitivity": Sr, "year": r["year"]})
    check_labels(v, p, r)
    lag = (r.get("model") or {}).get("lag_coefficients") or {}
    if not all(isinstance(lag.get(k), (int, float)) and lag[k] > 0 for k in ("lag24", "lag168")):
        raise ReportRefused(f"the {r.get('year')} replay's lag coefficients {lag} are not both positive: section 3 "
                            "says they are, as the echo rule assumes and the freeze checks")
    nbm = nbm_findings(dq)
    for d, year in ((dp, p.get("year")), (dr, r.get("year"))):
        check_reuse_lead(d, nbm["longest"], year, "the data-quality run states")
    prod, family, shipped = _models()
    nominal = 1 - v["conformal"]["alpha"]
    C = v["coverage"][prod]
    K = constants(v, frozen[max(frozen)], Sp if Sp is not None else Sr)
    peak = v["cases"]["peak"]["hours"]
    monthly = pd.DataFrame({k: ((oos["target"] - oos[col]).abs() / oos["target"]).groupby(oos["fold"]).mean()
                            for k, col in ((prod, f"pred_{prod}"), (family, f"pred_{family}"),
                                           (shipped, f"pred_{shipped}"), ("baseline", "baseline"))})
    th24 = v["thresholds"][str(r["year"])]
    level = level_shift({"sensitivity": Sp}, K["shift"]) if Sp is not None else None
    level24 = level_shift({"sensitivity": Sr}, K["shift"]) if Sr is not None and K["shift"] is not None else None
    return {
        "K": K, "nominal": nominal, "monthly": monthly, "C": C,
        "seasons": season_coverage(C, nominal),
        "sides": {"season": coverage_sides(sorted(C["relative"]["season"],
                                                  key=lambda x: F.SEASON_ORDER.index(x["key"])), nominal),
                  "hour_group": coverage_sides(hour_blocks(C["relative"]["hour_group"], v["config"]["hour_blocks"]),
                                               nominal),
                  "decile": coverage_sides(C["relative"]["decile"], nominal),
                  "overall": coverage_sides([{"key": "all", "covered_low": C["relative"]["overall"]["ci95_low"],
                                              "covered_high": C["relative"]["overall"]["ci95_high"]}], nominal)},
        "limits": {"replay": detection_limits(r["power"]), "pseudo": detection_limits(p["power"])},
        "near": {k: near_misses(x["summary"]["retrain_under_two_week_rule_alone"], x["weeks"], x["decisions"])
                 for k, x in (("replay", r), ("pseudo", p))},
        "shift_diff": {"production": shift_difference(oos, f"pred_{prod}"),
                       "persistence": shift_difference(oos, f"pred_{shipped}"),
                       "observed": shift_difference(oos, "pred_observed")},
        "drift_years": drift_reference_years(th24["first_year"], th24["last_year"], r["year"], v["folds"]["first"]),
        "level": level, "level24": level24,
        "episode_limits": {"pseudo": episode_limits(Sp, K["mild_factors"]) if Sp is not None else None,
                           "replay": episode_limits(Sr, K["mild_factors"]) if Sr is not None else None},
        "sens": {"pseudo": Sp, "replay": Sr},
        "peak_above": sum(h["upper"] is not None and h["target"] > h["upper"] for h in peak),
        "peak_in_month": bool(peak) and all(
            pd.Timestamp(h["utc"]).tz_convert(LOCAL_TZ).strftime("%Y-%m") == v["cases"]["peak"]["named_month"]
            for h in peak),
        "world": SYNTHETIC_WORLD,
        "sel": selection(v, oos),
        "nbm": nbm,
    }


def held_out_findings(dq: dict, revisions: pd.DataFrame | None, year: int = HELD_OUT) -> dict:
    """What the validation report states from the data-quality run the 2025 test of record read (plan section 13):
    the forecast's coverage of 2025, as the share of its days fed; the days of 2025 no run fed, with their reasons;
    2025's longest lead and whether it reaches the bound of section 9.2's argument; and every 2019-2024 demand record
    the 2025 export revised (added, removed or changed), a data-quality finding that decides nothing."""
    n = nbm_findings(dq)
    days = pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D")
    unfed = [x for x in n["not_fed"] if str(x["day"])[:4] == str(year)]
    count = int((dq.get("revisions") or {}).get("hydroquebec", {}).get("records", 0))
    rows = [] if revisions is None else revisions.fillna("").to_dict("records")
    if count and len(rows) != count:
        raise ReportRefused(f"data-quality run {dq.get('run_id')} counts {count} revised records, its artifact lists "
                            f"{len(rows)}")
    return {"run_id": dq.get("run_id"), "year": year, "days": len(days), "unfed": unfed,
            "share": 1 - len(unfed) / len(days), "longest": n["longest"].get(year),
            "at_or_above": year in n["at_or_above"], "bound": n["runs"]["reuse_lead_h"],
            "revised": {"records": count, "by_kind": (dq.get("revisions") or {}).get("hydroquebec", {}).get(
                "by_kind", {}), "rows": rows}}


# --- v1's replays, v2's records -----------------------------------------------------------------------------------

def recommendations(decisions: list[dict]) -> list[dict]:
    """A replay's recommendations as the ledger lists them: the week (its first day) and the word."""
    return [{"week_start": str(d["week_start"])[:10], "recommendation": d["recommendation"]}
            for d in decisions if d["recommendation"]]


def departure(c, run) -> dict:
    """A departed replay as the report shows it: from the store, or from its ledger entry when the store lacks it. The
    ledger holds neither the model's hash nor the flagged weeks: None for an entry, so the report says nothing of them.
    v2's predecessors may also be a start that did not finish, shown as unfinished (never as a result), or one named
    by a record read from the ledger that no ledger holds, shown as held only in the original store."""
    if isinstance(run, RecordedReplay):
        return {**ledger.as_departure(run.entry), "model_sha256": None, "flag_weeks": None, "unfinished": False,
                "absent": False}
    if isinstance(run, Unfinished):
        t = run.run.data.tags
        return {"id": run.run_id, "freeze_commit": t.get("freeze_commit", ""), "recorded": False, "unfinished": True,
                "absent": False, "year": t.get("year", ""), "stage": t.get("stage", ""), "error": t.get("error", "")}
    if isinstance(run, Absent):
        return {"id": run.run_id, "freeze_commit": "", "recorded": False, "unfinished": True, "absent": True}
    results = _json(c, run, "results/replay.json")
    return {"id": run.info.run_id, "freeze_commit": run.data.tags.get("freeze_commit", ""), "recorded": False,
            "unfinished": False, "absent": False,
            "summary": results["summary"], "recommendations": recommendations(results["decisions"]),
            "model_sha256": results["model"]["sha256"], "flag_weeks": results["summary"]["flag_weeks"]}


# The one departure from v1's frozen monitoring rules (AGENTS.md, "Departures from the frozen monitoring rules"): its
# date and what it changed, each item named in one sentence by the templates (`departure_item` in sentences.*.md.j2).
DEPARTURE = {"date": "2026-10-14", "changes": ("A3", "A4", "B1", "B2", "B3", "B4", "B5", "B6")}


def departure_record(departures: list[dict]) -> dict | None:
    """v1's departure, for a v1 replay that departs from earlier ones; None for a plain replay.

    v1's sentences describe one departure, from the rules first frozen: the replays it departs from must all have run
    under one freeze commit. A v1 replay departing from replays under two (a second departure) is refused until the
    report describes that one too. v2's departures are described from their entries (`v2_departures`)."""
    if not departures:
        return None
    commits = sorted({d["freeze_commit"] for d in departures})
    if len(commits) != 1 or not commits[0]:
        raise ReportRefused(f"the 2024 replay departs from replays under freeze commits {[c[:12] for c in commits]}; "
                            "the report describes one departure, from the rules first frozen (report/validation.py "
                            "DEPARTURE): a second departure needs its own sentences")
    return {"date": DEPARTURE["date"], "changes": list(DEPARTURE["changes"]), "from_commit": commits[0]}


def v2_departures(entries: list[dict]) -> list[dict]:
    """v2's departures as the report describes them, each from its entry of AGENTS.md's v2 section
    (lineage.departure_entries): its date and its head, the commits it names, and the commit it moved the tag to."""
    return [{"date": e["date"], "head": e["head"], "named": [c[:12] for c in e["named"]], "to": e["to"][:12]}
            for e in entries]


def v1_view(history: list, r: dict) -> dict:
    """v1's 2024 replays from their ledger entries (`rules-frozen`), recorded and never re-run, and v1's model in
    v2's harness reconciled with the latest of them (plan section 11.2). The reconciliation must name that entry and
    carry its MAPE and coverage exactly: v1's 2024 numbers come from its ledger entries and nowhere else."""
    shown = []
    for run in history:
        if not isinstance(run, RecordedReplay) or run.entry["freeze_tag"] != ledger.V1_TAG:
            raise ReportRefused("v1's 2024 replays are shown from their ledger entries only")
        shown.append(ledger.as_departure(run.entry) | {"departure": run.entry["departure"]})
    rec = r.get("v1_reconciliation")
    if rec is None:
        raise ReportRefused(f"the {r.get('year')} replay holds no reconciliation of v1's model with v1's ledger")
    last = history[-1].entry
    if (rec.get("ledger_run_id"), rec.get("ledger_mape"), rec.get("ledger_coverage")) != (
            last["run_id"], last["mape"], last["coverage"]):
        raise ReportRefused(f"the reconciliation reads v1's 2024 numbers from {rec.get('ledger_run_id')!r}, not from "
                            f"v1's latest ledger entry {last['run_id']}: v1's 2024 numbers come from its ledger "
                            "entries only")
    return {"replays": shown, "reconciliation": rec}


def record_view(c, run, entries: list[dict], year: int) -> dict:
    """A record as the report shows it: its ledger entry's fields and, when read from its own run, its results and,
    for 2025, its gate's record (standing, causes, ruling) and its diagnostics."""
    if isinstance(run, RecordedReplay):
        return {**run.entry, "recorded": True, "results": None, "gate": None, "diag": None, "diag_finished": None}
    results = _json(c, run, "results/replay.json")
    e = ledger.lookup(run.info.run_id, entries) or ledger.entry(run, results)
    gate = None
    if year == HELD_OUT:
        gate = _json(c, run, "results/held_out_gate.json")
    diag = diagnostics(c, run)
    return {**e, "recorded": False, "results": results, "gate": gate, "diag": diag,
            "diag_finished": diag is not None, "git_commit": run.data.tags.get("git_commit", "")}


def diagnostics(c, run) -> dict | None:
    """A replay's diagnostics: the results of its latest diagnostic run (kind `diagnostic`, tag `replay_run_id` its
    run ID) that finished, complete; None when none did."""
    from valiwatt.monitor.run import EXPERIMENT
    from valiwatt.tracking import latest_run

    try:
        d = latest_run(EXPERIMENT, f"tags.kind = 'diagnostic' and tags.replay_run_id = '{run.info.run_id}' and "
                                   "attributes.status = 'FINISHED' and tags.status = 'complete'")
    except LookupError:
        return None
    if d.info.status != "FINISHED" or d.data.tags.get("status") != "complete" or \
            d.data.tags.get("replay_run_id") != run.info.run_id:
        return None
    return {**_json(c, d, "results/diagnostics.json"), "run_id": d.info.run_id}


def differences(a: dict, b: dict) -> list[str]:
    """What a rerun's entry changes from its record's, as the report prints them: MAPE (two decimals of a percent),
    coverage (one), the recommendations, the verdicts."""
    out = []
    if printed(a["mape"], 2) != printed(b["mape"], 2):
        out.append("mape")
    if printed(a["coverage"], 1) != printed(b["coverage"], 1):
        out.append("coverage")
    if a["recommendations"] != b["recommendations"]:
        out.append("recommendations")
    if a.get("verdict") != b.get("verdict"):
        out.append("verdict")
    return out


def status_of(record: dict | None) -> str:
    """The model card's status, `evaluate.status` of the 2025 test of record (None before any start): whether it
    finished, the standing its gate logged, its verdict. Never computed from another start."""
    from valiwatt.forecast.evaluate import status

    if record is None:
        return status(None)
    verdict = record.get("verdict") if record["finished"] else None
    return status({"finished": bool(record["finished"]), "standing": record.get("standing", ""), "verdict": verdict})


def recommendation_of(status: str, record: dict | None) -> dict | None:
    """The recommendation to the owner if a clean 2025 does not confirm v2 (plan section 8.5), in the monitor's form,
    with its evidence: *return to v1 as shipped or rule another model* when the shipped verdict is not better; *keep
    the forecast input and review the family* when only the family verdict is. None otherwise."""
    from valiwatt.forecast.evaluate import _BETTER

    if status not in ("not_confirmed", "input_only"):
        return None
    v = record["verdict"]
    withdrawn = [k for k in ("shipped", "family") if v[k] not in _BETTER]
    kind = "return_or_rule" if v["shipped"] not in _BETTER else "keep_input_review_family"
    results = record.get("results") or {}
    return {"kind": kind, "withdrawn": withdrawn, "run_id": record["run_id"], "verdict": v,
            "contrasts": results.get("contrasts")}


def held_view(c, chain: Chain, entries: list[dict], starts: list[dict]) -> dict | None:
    """The 2025 starts as the report shows them: every start the walk passed over (first), the test of record (or
    the fact that it did not finish), every other start that did not finish, every rerun with its differences, the
    findings of the data-quality run the test of record read, and whether any record is shown from the ledger. The
    test of record must be the walk's (`ledger.of_record` on the same starts); the standing is the one its gate
    logged (its run tag `standing` and its gate's record, which must agree), never computed again."""
    h = chain.held
    if h is None:
        return None
    walk = ledger.of_record(entries, starts, HELD_OUT, chain.freeze.get("freeze_tag"))
    if walk is None or walk["record"]["run_id"] != h.record["run_id"]:
        raise ReportRefused(f"the 2025 test of record shown ({h.record['run_id']}) is not the one the walk of the "
                            f"starts gives ({walk['record']['run_id'] if walk else None})")
    stopped_ids = [s["run_id"] for s in walk["stopped"]]
    if [s["run_id"] for s in h.stopped] != stopped_ids:
        raise ReportRefused("the 2025 starts passed over are not the ones the walk gives, in its order")
    if h.finished:
        rec = record_view(c, h.run, entries, HELD_OUT)
        rec["finished"] = True
        if not rec["recorded"]:
            if rec["gate"] is None or rec["gate"].get("standing") != rec["standing"]:
                raise ReportRefused(f"the 2025 test of record {rec['run_id']}: the standing its run carries "
                                    f"({rec['standing']!r}) is not the one its gate's record logged")
    else:
        run = h.run
        rec = {"run_id": h.record["run_id"], "started_utc": h.record["started_utc"], "finished": False,
               "recorded": False, "stage": h.record.get("stage", ""), "error": h.record.get("error", ""),
               "standing": h.record.get("standing", ""), "gate": h.record.get("gate"), "verdict": None,
               "results": None, "holds_results": h.record.get("holds_results", False)}
        if run is not None:
            rec["standing"] = run.data.tags.get("standing", rec["standing"])
    rec["predecessors"] = [departure(c, p) for p in chain.predecessors.get(rec["run_id"], [])]
    reruns = []
    for run in h.reruns:
        x = record_view(c, run, entries, HELD_OUT)
        x["predecessors"] = [departure(c, p) for p in chain.predecessors.get(x["run_id"], [])]
        reruns.append(x)
    findings = None
    dq_run = h.dq.get(rec["run_id"])
    if dq_run is not None:
        dq = _json(c, dq_run, "results/dq_results.json")
        revisions = None
        if int((dq.get("revisions") or {}).get("hydroquebec", {}).get("records", 0)):
            # As written: a label's values joined by ";", a blank record empty (quality/run.py revised_records).
            revisions = _frame(c, dq_run, dq["revisions"]["hydroquebec"]["artifact"], dtype=str,
                               keep_default_na=False)
        findings = held_out_findings(dq | {"run_id": dq_run.info.run_id}, revisions)
    elif not rec["recorded"] and h.run is not None:
        raise ReportRefused(f"the 2025 test of record {rec['run_id']} names no data-quality run in the store")
    return assemble_held(walk, rec, reruns, findings)


def assemble_held(walk: dict, rec: dict, reruns: list[dict], findings: dict | None) -> dict:
    """The 2025 view the templates read, from the walk of the starts (`ledger.of_record`), the test of record as the
    report shows it (finished or not; from its run or its ledger entry), each rerun and the findings of the
    data-quality run the record read. Pure. The status, the withdrawn claims and the recommendation come from the
    test of record alone; a record other than the walk's, a finished record without its verdict, or an unfinished
    one with a verdict, is refused."""
    if rec["run_id"] != walk["record"]["run_id"]:
        raise ReportRefused(f"the 2025 test of record shown ({rec['run_id']}) is not the one the walk of the starts "
                            f"gives ({walk['record']['run_id']})")
    if bool(rec["finished"]) != bool(walk["record"]["finished"]):
        raise ReportRefused(f"the 2025 test of record {rec['run_id']} is shown as finished={rec['finished']}, the "
                            "walk of the starts says otherwise")
    if rec["finished"] and not isinstance(rec.get("verdict"), dict):
        raise ReportRefused(f"the finished 2025 test of record {rec['run_id']} carries no verdict")
    if not rec["finished"] and (rec.get("verdict") is not None or rec.get("results") is not None):
        raise ReportRefused(f"the 2025 test of record {rec['run_id']} did not finish: no 2025 verdict or result is "
                            "rendered from it, nor from another start in its place")
    rec = {**rec, "ruling": (rec.get("gate") or {}).get("ruling")}
    status = status_of(rec)
    shown = {rec["run_id"]} | {s["run_id"] for s in walk["stopped"]}
    reruns = [{**x, "finished": True, "differences": differences(x, rec) if rec["finished"] else []} for x in reruns]
    if [x["run_id"] for x in reruns] != [e["run_id"] for e in walk["reruns"]]:
        raise ReportRefused("the 2025 reruns shown are not the ones the walk of the starts gives")
    return {"stopped": [dict(s) for s in walk["stopped"]], "record": rec,
            "others": [s for s in walk["unfinished"] if s["run_id"] not in shown], "reruns": reruns,
            "findings": findings, "status": status, "recommendation": recommendation_of(status, rec),
            "recorded": bool(rec["recorded"]) or any(x["recorded"] for x in reruns)}


def check_reuse_lead(diag: dict | None, longest: dict, year: int, what: str) -> None:
    """The replayed year's longest lead the diagnostics state beside the run-reuse measure is the one the data-quality
    run states for that year (`longest`, by year): the report prints one longest lead per year, never two."""
    lead = ((diag or {}).get("reuse_sensitivity") or {}).get("longest_lead_h")
    if diag is None or lead is None:
        return
    stated = longest.get(int(year)) if year is not None else None  # a results file may give the year as text
    if stated is None or round(float(lead), 1) != round(float(stated), 1):
        raise ReportRefused(f"the {year} diagnostics state a longest lead of {lead} h, {what} {stated} h: the report "
                            "states one longest lead per year")


def check_held_diagnostics(held: dict | None, sens: dict) -> None:
    """The 2025 test of record's diagnostics, when they finished, hold the measures and were made with the design the
    report states (from the 2023 rehearsal's measures, else the 2024 replay's); with neither, it cannot state that
    design and refuses. Their longest lead is the one its data-quality run states."""
    rec = (held or {}).get("record") or {}
    if not rec.get("finished") or not rec.get("diag_finished"):
        return
    findings = held.get("findings")
    if findings is not None:
        check_reuse_lead(rec["diag"], {HELD_OUT: findings.get("longest")}, HELD_OUT,
                         "the data-quality run the test of record read states")
    S = sensitivity({**rec["diag"], "year": HELD_OUT})
    ref = sens.get("pseudo") if sens.get("pseudo") is not None else sens.get("replay")
    if ref is None:
        raise ReportRefused("the 2025 test of record's diagnostics finished, but neither the 2023 rehearsal's nor the "
                            "2024 replay's did: the report states the measures' design from those")
    same_sensitivity_design({"sensitivity": ref, "year": "earlier"}, {"sensitivity": S, "year": HELD_OUT})


def view_2024(c, chain: Chain, entries: list[dict]) -> tuple[dict | None, dict | None]:
    """Once a 2025 replay has started: the 2024 replay of record (v2's first 2024 replay), from its run or its ledger
    entry, and the chain's own 2024 replay beside it when it is another, a rerun, with its differences."""
    if chain.record_2024 is None:
        return None, None
    rec = record_view(c, chain.record_2024, entries, 2024)
    rec["predecessors"] = [departure(c, p) for p in chain.predecessors.get(rec["run_id"], [])]
    if run_id_of(chain.record_2024) == chain.replay.info.run_id:
        return rec, None
    mine = record_view(c, chain.replay, entries, 2024)
    mine["differences"] = differences(mine, rec)
    return rec, mine


def context(chain: Chain, code_sha: str, rules_sha: str, entries: list[dict] | None = None,
            starts: list[dict] | None = None) -> dict:
    from valiwatt.tracking import client

    c = client()
    entries = ledger.read() if entries is None else entries
    starts = [] if starts is None else starts
    v = _json(c, chain.validation, "results/validation.json")
    dq = _json(c, chain.dq, "results/dq_results.json")
    oos = _frame(c, chain.validation, "frames/oos.parquet")
    p, r_chain = _json(c, chain.pseudo, "results/replay.json"), _json(c, chain.replay, "results/replay.json")
    b = _json(c, chain.breaks, "results/breaks.json")
    frozen = {}
    for year, run in chain.frozen.items():
        frozen[year] = {"run_id": run.info.run_id, "params": run.data.params, "tags": run.data.tags,
                        "coefficients": _frame(c, run, "coefficients.csv").to_dict("records")}
    curves = _frame(c, chain.frozen[2024], "partial_effects.csv")
    rec24, rerun24 = view_2024(c, chain, entries)
    # The report's 2024 numbers come from the 2024 replay of record once a 2025 replay has started (its own run when
    # the store holds it); before, from the chain's own 2024 replay.
    if rec24 is not None and rec24["results"] is not None:
        r, r_source, r_run = rec24["results"], "record", chain.record_2024
    else:
        r, r_source, r_run = r_chain, ("chain" if rec24 is None else "rerun"), chain.replay
    if r_run.data.tags.get("evaluation") != "seen_by_developers":
        raise ReportRefused(f"the 2024 replay {r_run.info.run_id} is tagged {r_run.data.tags.get('evaluation')!r}, "
                            "not seen_by_developers: no v2 number on 2024 is rendered without its label")
    diag = {"pseudo": diagnostics(c, chain.pseudo), "replay": diagnostics(c, r_run)}
    departures = [departure(c, run) for run in chain.departed]
    v2 = ledger.is_v2(chain.freeze.get("freeze_tag") or ledger.V1_TAG)
    held = held_view(c, chain, entries, starts)
    status = held["status"] if held else status_of(None)
    derived = derive(v, dq, p, r, b, frozen, oos, diag)
    check_held_diagnostics(held, derived["sens"])
    return {
        "v": v, "dq": dq, "p": p, "r": r, "r_source": r_source, "r_id": r_run.info.run_id, "b": b, "frozen": frozen,
        **derived,
        "diag": diag, "curves": curves.to_dict("records"),
        "departures": departures,
        "departure_record": None if v2 else departure_record(departures),
        "dep_entries": v2_departures(chain.departures) if v2 else [],
        "v1": v1_view(chain.v1_history, r),
        "rec24": rec24, "rerun24": rerun24, "held": held, "status": status,
        "recommendation": held["recommendation"] if held else None,
        "store_only": bool((rec24 or {}).get("recorded")) or bool(held and held["recorded"]),
        "runs": {k: {"id": run_id_of(run), "when": _when(run)} for k, run in chain.runs().items()},
        "code_sha": code_sha, "rules_sha": rules_sha, "freeze": chain.freeze,
        "commit": chain.replay.data.tags.get("git_commit", "none"),
        "dirty": chain.replay.data.tags.get("git_dirty") != "false",
    }


def figures(ctx: dict, lang: str, folder: Path | None = None) -> dict:
    """Each figure of the validation report in `lang`, drawn from the context into `folder` (docs/report/figures/
    validation by default), by the name the templates read."""
    folder = OUT / "figures" / "validation" if folder is None else folder
    out = {}
    v, p, r = ctx["v"], ctx["p"], ctx["r"]
    jobs = {
        "validation-monthly-mape": lambda path: F.monthly_mape(ctx["monthly"], lang, path),
        "validation-coverage-by-season": lambda path: F.coverage_by_season(
            {form: {x["key"]: (x[form]["covered"], x[form]["low"], x[form]["high"]) for x in ctx["seasons"]}
             for form in ("pooled", "by_season")}, lang, path, ctx["nominal"]),
        "validation-peak-hours": lambda path: F.peak_hours(v["cases"]["peak"]["hours"], lang, path, ctx["nominal"]),
        "validation-partial-effects": lambda path: F.partial_effects(ctx["curves"], lang, path),
        "pseudo-replay-2023-weekly": lambda path: F.replay_weekly(p["weeks"], p["decisions"], lang, path),
        "replay-2024-weekly": lambda path: F.replay_weekly(r["weeks"], r["decisions"], lang, path),
        "replay-2024-drift": lambda path: F.replay_drift(r["weeks"], lang, path),
    }
    for name, job in jobs.items():
        path = folder / f"{name}.{lang}.png"
        job(path)
        out[name.replace("-", "_")] = f"figures/validation/{path.name}"
    return out


# --- the statements the render refuses to omit --------------------------------------------------------------------

# Words the templates print and the render checks for, in each language: their one home. A rendered report without
# one its context calls for is refused (`check_rendered`).
SAY = {
    "fr": {
        "ledger_label": "Consigné dans le registre, non rejoué ici.",
        "store_only": ("Ce que seul le magasin d'exécutions contient n'est pas montré ici : le registre de la barrière "
                       "de 2025 et la décision du propriétaire qu'elle a lue, les enregistrements révisés, les "
                       "constats de qualité des données de 2025 et leurs délais, les diagnostics, les chiffres "
                       "horaires de ces rejeux et tout départ qui s'est arrêté sont dans le magasin MLflow de la "
                       "machine qui a rejoué 2025, et dans le rapport rendu là-bas."),
        "held_out": "sur des données réservées (2025)",
        "lead_75": LEAD_FINDING["fr"],
        "diag_unfinished": "Ses diagnostics n'ont pas fini : leurs mesures ne sont pas montrées, et elles ne décident jamais rien.",
        "recommendation": {"return_or_rule": "revenir à v1 tel que livré ou décider d'un autre modèle",
                           "keep_input_review_family": "garder l'entrée de prévision et revoir la famille"},
        "revised": "enregistrements de 2019 à 2024 révisés par Hydro-Québec",
        "unfinished": "n'a pas fini",
        "stopped": "arrêté",
    },
    "en": {
        "ledger_label": "Recorded in the ledger, not re-run here.",
        "store_only": ("What only the run store holds is not shown here: the 2025 gate's record and the owner's ruling "
                       "it read, the revised records, 2025's data-quality findings and leads, the diagnostics, those "
                       "replays' hourly figures and any start that stopped are in the MLflow store of the machine "
                       "that replayed 2025, and in the report rendered there."),
        "held_out": "on held-out data (2025)",
        "lead_75": LEAD_FINDING["en"],
        "diag_unfinished": "Its diagnostics did not finish: their measures are not shown, and they never decide anything.",
        "recommendation": {"return_or_rule": "return to v1 as shipped or rule another model",
                           "keep_input_review_family": "keep the forecast input and review the family"},
        "revised": "2019–2024 records revised by Hydro-Québec",
        "unfinished": "did not finish",
        "stopped": "stopped",
    },
}


def _bullets_after(text: str, heading: str) -> list[str]:
    part = text.split(heading, 1)[1] if heading in text else ""
    part = part.split("\n## ", 1)[0]
    return [line[2:] for line in part.splitlines() if line.startswith("- ")]


def numbered_section(text: str, number: int) -> str:
    """The text of a rendered page's numbered section `## <number>.`, up to the next second-level heading; "" when the
    page has no such section, so a statement checked there is missing rather than found elsewhere on the page."""
    m = re.search(rf"^## {number}\. ", text, re.M)
    if m is None:
        return ""
    rest = text[m.end():]
    nxt = re.search(r"^## ", rest, re.M)
    return text[m.start():m.end() + (nxt.start() if nxt else len(rest))]


def check_rendered(text: str, lang: str, ctx: dict, doc: str, status_line: str) -> None:
    """The statements a rendered page must carry, given its context; a page without one is refused. doc: "validation"
    or "story". status_line: the status sentence as the templates print it (`status_sentence`), which « En bref » and
    the story open with, whatever the status: the page is checked, not only the sentence."""
    say = SAY[lang]
    assert_attributed(text, lang)
    held = ctx.get("held")
    status = ctx.get("status")
    if status in HELD_OUT_RESULTS and say["held_out"] not in (status_line or ""):
        raise ReportRefused(f"the {doc} states a held-out result without the held-out label")
    if status in ("confirmed", "input_only") and (held is None or held["record"].get("evaluation") != "held_out"):
        raise ReportRefused("a better claim needs the test of record labelled held out")
    if ctx.get("recommendation") and doc == "validation":
        if say["recommendation"][ctx["recommendation"]["kind"]] not in text:
            raise ReportRefused("a clean 2025 that does not confirm v2 is rendered without its recommendation")
    # The page itself opens with the status sentence, whatever the status: a label checked in the sentence alone
    # would pass a page that prints another.
    first = (_bullets_after(text, "## En bref" if lang == "fr" else "## In brief") or [""])[0] \
        if doc == "validation" else _first_point(text)
    if not status_line or not first.startswith(status_line.strip()):
        raise ReportRefused(f"the {doc} does not open with " + ("the 2025 result" if status in HELD_OUT_RESULTS else
                                                                 "its status"))
    if doc != "validation":
        return
    if ctx.get("store_only"):
        if say["store_only"] not in text:
            raise ReportRefused("a record read from the ledger without the sentence on what only the store holds")
        recorded = [x["run_id"] for x in [ctx.get("rec24") or {}, *((held or {}).get("reruns") or []),
                                          (held or {}).get("record") or {}] if x.get("recorded")]
        if text.count(say["ledger_label"]) < len(set(recorded)):
            raise ReportRefused("a record read from the ledger without its label")
    # Each statement is checked in the section that makes it: 2025's in section 9, from the data-quality run the test
    # of record read; the chain's own years' in section 2. Found anywhere on the page, one would vouch for the other.
    if held is not None:
        nine = numbered_section(text, 9)
        order = [s["run_id"] for s in held["stopped"]] + [held["record"]["run_id"]]
        at = [nine.find(x) for x in order]
        if any(i < 0 for i in at) or at != sorted(at):
            raise ReportRefused("the 2025 starts passed over are not all listed, first, before the test of record")
        for s in held["others"]:
            if s["run_id"] not in nine:
                raise ReportRefused(f"the unfinished 2025 start {s['run_id']} is not listed")
        f = held.get("findings")
        if f is not None:
            shown, mw = filters(lang)["utc"], filters(lang)["mw"]
            lines = nine.splitlines()

            def listed(x) -> bool:
                values = f"| {mw(x['old_mw'], x['kind'] != 'added')} | {mw(x['new_mw'], x['kind'] != 'removed')} |"
                return any(line.startswith(f"| {shown(x['utc'])} |") and line.endswith(values) for line in lines)

            if f["revised"]["records"] and (say["revised"] not in nine or not all(
                    listed(x) for x in f["revised"]["rows"])):
                raise ReportRefused("the revised records the test of record's data-quality run lists are not all "
                                    "listed, each with its values")
            if f["at_or_above"] and say["lead_75"] not in nine:
                raise ReportRefused("a 2025 lead at or above the bound of plan section 9.2 is not stated in section 9")
    nbm = ctx.get("nbm") or {}
    if nbm.get("at_or_above") and say["lead_75"] not in numbered_section(text, 2):
        raise ReportRefused("a lead at or above the bound of plan section 9.2 is not stated in section 2")


def anchors(text: str) -> dict[int, str]:
    """The anchor GitHub gives each numbered section heading of a rendered page, by its number: the heading's text
    (emphasis markers dropped), lower case, punctuation dropped, spaces to hyphens. The story links with them."""
    out = {}
    for line in re.findall(r"^## (\d+\. .+)$", text, re.M):
        plain = re.sub(r"[_*`]", "", line).strip().lower()
        out[int(line.split(".", 1)[0])] = re.sub(r"[^\w\- ]", "", plain).replace(" ", "-")
    return out


def _first_point(text: str) -> str:
    """The story's first point: the first paragraph after its first second-level heading."""
    part = text.split("\n## ", 1)[1] if "\n## " in text else ""
    paras = [x for x in part.split("\n\n")[1:] if x.strip()]
    return paras[0] if paras else ""


def file_hashes() -> dict:
    """The sha256 of every rendered file under docs/report/, the stamp itself excepted."""
    return {p.relative_to(OUT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(OUT.rglob("*")) if p.is_file() and p != STAMP}


def standing_2025(held: dict | None) -> dict | None:
    """The stamp's `standing_2025`: null until a 2025 replay has started; then the test of record's run ID, whether it
    finished, and the standing its gate logged (plan section 10.5), the record's alone, never a rerun's."""
    if held is None:
        return None
    rec = held["record"]
    return {"record_run_id": rec["run_id"], "finished": bool(rec["finished"]), "standing": rec.get("standing", "")}


def make_stamp(code_sha: str, rules_sha: str, freeze_commit: str, runs: dict, standing: dict | None = None) -> dict:
    """What the committed report is checked against: the code and rules hashes, the runs, the hash of every rendered
    file, the replay ledger's hash (`valiwatt report` syncs the ledger before it renders) and the standing of 2025."""
    return {"code_sha256": code_sha, "rules_sha256": rules_sha, "freeze_commit": freeze_commit, "runs": runs,
            "files": file_hashes(), "ledger_sha256": ledger.digest(), "standing_2025": standing}


def render_validation(dq_run_id: str | None = None) -> list[Path]:
    from valiwatt.monitor.rules import rules_sha256
    from valiwatt.monitor.run import check_freeze
    from valiwatt.tracking import code_sha256

    code_sha, rules_sha = code_sha256(), rules_sha256()
    freeze = check_freeze()
    entries = ledger.read()
    tag = freeze.get("freeze_tag") or ledger.V1_TAG
    starts = ledger.starts(None, HELD_OUT, tag) if HELD_OUT in ledger.YEARS.get(tag, ()) else []
    chain = follow(code_sha, rules_sha, freeze, entries=entries, starts=starts)
    if dq_run_id and dq_run_id != chain.dq.info.run_id:
        raise ReportRefused(f"data-quality run {dq_run_id} is not the one the chain read ({chain.dq.info.run_id})")
    written = render(run_id=chain.dq.info.run_id)
    ctx = context(chain, code_sha, rules_sha, entries, starts)
    if (OUT / "figures" / "validation").exists():  # no figure from an earlier render survives into the stamp
        shutil.rmtree(OUT / "figures" / "validation")
    pages = render_pages(ctx, {lang: figures(ctx, lang) for lang in LANGS})
    for (doc, lang), text in pages.items():
        out = OUT / f"{doc}.{lang}.md"
        out.write_text(text, encoding="utf-8")
        written.append(out)
    stamp = make_stamp(code_sha, rules_sha, chain.freeze["freeze_commit"], {k: x["id"] for k, x in ctx["runs"].items()},
                       standing_2025(ctx["held"]))
    STAMP.write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
    print("rendered " + ", ".join(p.relative_to(REPO_ROOT).as_posix() for p in written)
          + f" from replay run {chain.replay.info.run_id}")
    return written


def render_pages(ctx: dict, figs: dict) -> dict:
    """The validation report and the story, in each language, rendered from one context and checked as published
    pages are (`assert_public_safe`, `check_rendered`): {(doc, lang): text}, French first. figs: each language's
    figures, by the names the templates read (`figures`). The story, read first, is rendered from the same context and
    figures, and links to the headings of the validation report just rendered."""
    retrieved = min(s["first_retrieved_utc"] for s in ctx["dq"]["sources"]["eccc"])[:10]
    pages = {}
    for lang in LANGS:
        env = environment(lang)
        status_line = status_sentence(env, ctx, lang)
        links = {}
        for doc in ("validation", "story"):
            text = env.get_template(f"{doc}.{lang}.md.j2").render(
                **ctx, fig=figs[lang], lang=lang, say=SAY[lang],
                attribution=attribution.lines(lang, retrieved=retrieved), anchors=links)
            if doc == "validation":
                links = anchors(text)
            assert_public_safe(text)
            check_rendered(text, lang, ctx, doc, status_line)
            pages[(doc, lang)] = text
    return pages


def status_sentence(env, ctx: dict, lang: str) -> str:
    """The status sentence as the templates print it (sentences.{lang}.md.j2 `status_line`)."""
    module = env.get_template(f"sentences.{lang}.md.j2").make_module({"say": SAY[lang]})
    return str(module.status_line(ctx.get("status"), (ctx.get("held") or {}).get("record"), SAY[lang],
                                  ctx["K"]["validation_years"])).strip()
