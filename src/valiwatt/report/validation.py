"""Render the validation report (French first, English derived) into docs/report/, from the chain of tracked runs.

The report is committed (owner's ruling). It carries a stamp (docs/report/stamp.json): the run IDs it was rendered
from, the code hash, the rules hash, the hash of every rendered file and the hash of the replay ledger.
`tests/test_report_stamp.py` fails when the committed code no longer matches the stamp, or a rendered file or the
ledger no longer matches its hash, so a report can never sit beside code that did not produce it, nor be edited by
hand. The data-quality report is rendered from the chain's own
data-quality run.
"""

from __future__ import annotations

import hashlib
import json
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
from valiwatt.report.lineage import Chain, RecordedReplay, follow
from valiwatt.report.render import ReportRefused, assert_public_safe, environment, render

OUT = REPORTS
STAMP = OUT / "stamp.json"
LANGS = ("fr", "en")


def _json(c, run, path: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        return json.loads(Path(c.download_artifacts(run.info.run_id, path, tmp)).read_text(encoding="utf-8"))


def _frame(c, run, path: str) -> pd.DataFrame:
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(c.download_artifacts(run.info.run_id, path, tmp))
        return pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p)


def _when(run) -> str:
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
    the replayed year. The temperature reference starts with the demand window; the forecast reference with the first
    validation fold."""
    from valiwatt.config import DEMAND_FIRST, LST_OFFSET

    starts = {"temperature": (DEMAND_FIRST - pd.Timedelta(hours=1) + LST_OFFSET).year,
              "forecast": pd.Period(folds_first, freq="M").year}
    return {k: {"calibration": (first_year - s, last_year - s), "replay": replay_year - s} for k, s in starts.items()}


def constants(v: dict, frozen24: dict, p: dict) -> dict:
    """The design constants the report states, read from the runs or from the code that made them (the report chain
    refuses runs made from other code, so the two agree)."""
    from valiwatt.config import DEMAND_FIRST, DESIGN_LAST, LST_OFFSET
    from valiwatt.forecast.evaluate import SEASONS
    from valiwatt.monitor import rules
    from valiwatt.monitor import sensitivity as sens
    from valiwatt.monitor.run import POWER_HORIZON_WEEKS

    th = v["thresholds"]
    features = [row["feature"] for row in frozen24["coefficients"]]
    S = p["sensitivity"]
    shifts = sorted({round(abs(x["shift"]), 6) for x in S["level_shifts"]})
    factors = sorted({x["factor"] for x in S["episodes"]["rows"]})
    mild = min(abs(f - 1) for f in factors)
    season_months = {}
    for month, season in SEASONS.items():
        season_months.setdefault(season, []).append(month)
    year_of = lambda hour_ending: (hour_ending - pd.Timedelta(hours=1) + LST_OFFSET).year  # noqa: E731
    return {
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
        "episode_days": sorted({x["days"] for x in S["episodes"]["rows"]}),
        "mild_factors": [f for f in factors if abs(abs(f - 1) - mild) < 1e-9], "mild": mild,
        "shift": shifts[-1],
    }


def level_shift(p: dict, size: float) -> dict:
    """The largest level shift measured on a replay's year: rises missed and falls caught within the horizon. `months`
    names every start of a rise (the starts the measure tested), `missed_months` only those whose rise was missed."""
    rows = p["sensitivity"]["level_shifts"]
    up = [x for x in rows if abs(x["shift"] - size) < 1e-9]
    down = [x for x in rows if abs(x["shift"] + size) < 1e-9]
    return {"size": size, "up_n": len(up), "up_missed": sum(x["weeks"] is None for x in up),
            "down_n": len(down), "down_caught": sum(x["weeks"] is not None for x in down),
            "months": [pd.Timestamp(x["start"]).month for x in up],
            "missed_months": [pd.Timestamp(x["start"]).month for x in up if x["weeks"] is None]}


SENSITIVITY_PARTS = ("episodes", "level_shifts", "echo_exclusion_cost")


def sensitivity(run: dict) -> dict:
    """A replay's false-alarm and blind-spot measures (monitor/sensitivity.py). The 2023 pseudo-replay and, since the
    departure of 2026-10-14, the 2024 replay both carry them; a run without them (made before) is refused, so the
    report never prints one year's measures where it says two."""
    S = run.get("sensitivity")
    if not isinstance(S, dict) or any(k not in S for k in SENSITIVITY_PARTS):
        raise ReportRefused(f"the {run.get('year')} replay run holds no false-alarm and blind-spot measures (episodes, "
                            "level shifts, echo-exclusion cost): it was made before they were measured on its year; "
                            "run the replay again")
    return S


def sensitivity_design(S: dict) -> dict:
    """What the measures were made of: episode lengths and factors, level-shift sizes, noise levels and horizon."""
    return {"episodes": sorted({(x["days"], x["factor"]) for x in S["episodes"]["rows"]}),
            "shifts": sorted({round(x["shift"], 6) for x in S["level_shifts"]}),
            "sds": sorted({x["sd"] for x in S["echo_exclusion_cost"]["rows"]}),
            "horizon": S["echo_exclusion_cost"]["horizon_weeks"]}


def same_sensitivity_design(p: dict, r: dict) -> None:
    """The report states one design for both years' measures (lengths, factors, shifts, noise), read from the
    pseudo-replay: the 2024 replay's must be the same, or those sentences would be false for it."""
    a, b = sensitivity_design(sensitivity(p)), sensitivity_design(sensitivity(r))
    diff = [k for k in a if a[k] != b[k]]
    if diff:
        raise ReportRefused(f"the {p['year']} and {r['year']} false-alarm and blind-spot measures were made with "
                            f"different designs ({', '.join(diff)}); the report states one for both")


def episode_limits(S: dict, mild_factors: list) -> dict:
    """What sections 6 and 8 sum up of one year's measures: the episode of several days that most often yields
    *retrain* (None when none does) and its share of starts; the one that most often yields *recalibrate*, likewise;
    the largest *retrain* share for the mildest factors; the alarms one-day shocks gave (`one_day`: *retrain*, of which
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


def derive(v: dict, dq: dict, p: dict, r: dict, b: dict, frozen: dict, oos: pd.DataFrame) -> dict:
    """Everything the templates read that is computed from the runs rather than printed as stored. Pure: no store."""
    from valiwatt.breaks import SYNTHETIC_WORLD
    from valiwatt.config import LOCAL_TZ

    for row in b["breaks"]:
        if "control" not in row:
            raise ReportRefused("the breaks run does not say which breaks are controls; run `valiwatt breaks` again")
    same_sensitivity_design(p, r)
    nominal = 1 - v["conformal"]["alpha"]
    C = v["coverage"]
    K = constants(v, frozen[max(frozen)], p)
    peak = v["cases"]["peak"]["hours"]
    monthly = pd.DataFrame({k: ((oos["target"] - oos[col]).abs() / oos["target"]).groupby(oos["fold"]).mean()
                            for k, col in (("persistence", "pred_persistence"), ("observed", "pred_observed"),
                                           ("baseline", "baseline"))})
    th24 = v["thresholds"][str(r["year"])]
    return {
        "K": K, "nominal": nominal, "monthly": monthly,
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
        "shift_diff": {"persistence": shift_difference(oos, "pred_persistence"),
                       "observed": shift_difference(oos, "pred_observed")},
        "drift_years": drift_reference_years(th24["first_year"], th24["last_year"], r["year"], v["folds"]["first"]),
        "level": level_shift(p, K["shift"]), "level24": level_shift(r, K["shift"]),
        "episode_limits": {"pseudo": episode_limits(p["sensitivity"], K["mild_factors"]),
                           "replay": episode_limits(r["sensitivity"], K["mild_factors"])},
        "peak_above": sum(h["upper"] is not None and h["target"] > h["upper"] for h in peak),
        "peak_in_month": bool(peak) and all(
            pd.Timestamp(h["utc"]).tz_convert(LOCAL_TZ).strftime("%Y-%m") == v["cases"]["peak"]["named_month"]
            for h in peak),
        "world": SYNTHETIC_WORLD,
    }


def recommendations(decisions: list[dict]) -> list[dict]:
    """A replay's recommendations as the ledger lists them: the week (its first day) and the word."""
    return [{"week_start": str(d["week_start"])[:10], "recommendation": d["recommendation"]}
            for d in decisions if d["recommendation"]]


def departure(c, run) -> dict:
    """A departed replay as the report shows it: from the store, or from its ledger entry when the store lacks it. The
    ledger holds neither the model's hash nor the flagged weeks: None for an entry, so the report says nothing of them."""
    if isinstance(run, RecordedReplay):
        return {**ledger.as_departure(run.entry), "model_sha256": None, "flag_weeks": None}
    results = _json(c, run, "results/replay.json")
    return {"id": run.info.run_id, "freeze_commit": run.data.tags.get("freeze_commit", ""), "recorded": False,
            "summary": results["summary"], "recommendations": recommendations(results["decisions"]),
            "model_sha256": results["model"]["sha256"], "flag_weeks": results["summary"]["flag_weeks"]}


# The one departure from the frozen monitoring rules (AGENTS.md, "Departures from the frozen monitoring rules"): its
# date and what it changed, each item named in one sentence by the templates (`departure_item` in sentences.*.md.j2).
DEPARTURE = {"date": "2026-10-14", "changes": ("A3", "A4", "B1", "B2", "B3", "B4", "B5", "B6")}


def departure_record(departures: list[dict]) -> dict | None:
    """The departure the report describes, for a replay that departs from earlier ones; None for a plain replay.

    The report's sentences describe one departure, from the rules first frozen: the replays it departs from must all
    have run under one freeze commit. A replay departing from replays under two (a second departure) is refused until
    the report describes that one too."""
    if not departures:
        return None
    commits = sorted({d["freeze_commit"] for d in departures})
    if len(commits) != 1 or not commits[0]:
        raise ReportRefused(f"the 2024 replay departs from replays under freeze commits {[c[:12] for c in commits]}; "
                            "the report describes one departure, from the rules first frozen (report/validation.py "
                            "DEPARTURE): a second departure needs its own sentences")
    return {"date": DEPARTURE["date"], "changes": list(DEPARTURE["changes"]), "from_commit": commits[0]}


def context(chain: Chain, code_sha: str, rules_sha: str) -> dict:
    from valiwatt.tracking import client

    c = client()
    v = _json(c, chain.validation, "results/validation.json")
    dq = _json(c, chain.dq, "results/dq_results.json")
    oos = _frame(c, chain.validation, "frames/oos.parquet")
    p, r = _json(c, chain.pseudo, "results/replay.json"), _json(c, chain.replay, "results/replay.json")
    b = _json(c, chain.breaks, "results/breaks.json")
    frozen = {}
    for year, run in chain.frozen.items():
        frozen[year] = {"run_id": run.info.run_id, "params": run.data.params, "tags": run.data.tags,
                        "coefficients": _frame(c, run, "coefficients.csv").to_dict("records")}
    departures = [departure(c, run) for run in chain.departed]
    return {
        "v": v, "dq": dq, "p": p, "r": r, "b": b, "frozen": frozen,
        **derive(v, dq, p, r, b, frozen, oos),
        "departures": departures, "departure_record": departure_record(departures),
        "runs": {k: {"id": run.info.run_id, "when": _when(run)} for k, run in chain.runs().items()},
        "code_sha": code_sha, "rules_sha": rules_sha, "freeze": chain.freeze,
        "commit": chain.replay.data.tags.get("git_commit", "none"),
        "dirty": chain.replay.data.tags.get("git_dirty") != "false",
    }


def figures(ctx: dict, lang: str) -> dict:
    out = {}
    v, p, r = ctx["v"], ctx["p"], ctx["r"]
    jobs = {
        "validation-monthly-mape": lambda path: F.monthly_mape(ctx["monthly"], lang, path),
        "validation-coverage-by-season": lambda path: F.coverage_by_season(
            {form: {x["key"]: (x[form]["covered"], x[form]["low"], x[form]["high"]) for x in ctx["seasons"]}
             for form in ("pooled", "by_season")}, lang, path, ctx["nominal"]),
        "validation-peak-hours": lambda path: F.peak_hours(v["cases"]["peak"]["hours"], lang, path, ctx["nominal"]),
        "pseudo-replay-2023-weekly": lambda path: F.replay_weekly(p["weeks"], p["decisions"], lang, path),
        "replay-2024-weekly": lambda path: F.replay_weekly(r["weeks"], r["decisions"], lang, path),
        "replay-2024-drift": lambda path: F.replay_drift(r["weeks"], lang, path),
    }
    for name, job in jobs.items():
        path = OUT / "figures" / "validation" / f"{name}.{lang}.png"
        job(path)
        out[name.replace("-", "_")] = f"figures/validation/{path.name}"
    return out


def file_hashes() -> dict:
    """The sha256 of every rendered file under docs/report/, the stamp itself excepted."""
    return {p.relative_to(OUT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(OUT.rglob("*")) if p.is_file() and p != STAMP}


def make_stamp(code_sha: str, rules_sha: str, freeze_commit: str, runs: dict) -> dict:
    """What the committed report is checked against: the code and rules hashes, the runs, the hash of every rendered
    file, and the replay ledger's hash (`valiwatt report` syncs the ledger before it renders)."""
    return {"code_sha256": code_sha, "rules_sha256": rules_sha, "freeze_commit": freeze_commit, "runs": runs,
            "files": file_hashes(), "ledger_sha256": ledger.digest()}


def render_validation(dq_run_id: str | None = None) -> list[Path]:
    from valiwatt.monitor.rules import rules_sha256
    from valiwatt.monitor.run import check_freeze
    from valiwatt.tracking import code_sha256

    code_sha, rules_sha = code_sha256(), rules_sha256()
    chain = follow(code_sha, rules_sha, check_freeze())
    if dq_run_id and dq_run_id != chain.dq.info.run_id:
        raise ReportRefused(f"data-quality run {dq_run_id} is not the one the chain read ({chain.dq.info.run_id})")
    written = render(run_id=chain.dq.info.run_id)
    ctx = context(chain, code_sha, rules_sha)
    if (OUT / "figures" / "validation").exists():  # no figure from an earlier render survives into the stamp
        shutil.rmtree(OUT / "figures" / "validation")
    retrieved = min(s["first_retrieved_utc"] for s in ctx["dq"]["sources"]["eccc"])[:10]
    for lang in LANGS:
        figs = figures(ctx, lang)
        text = environment(lang).get_template(f"validation.{lang}.md.j2").render(
            **ctx, fig=figs, lang=lang, attribution=attribution.lines(lang, retrieved=retrieved))
        assert_public_safe(text)
        out = OUT / f"validation.{lang}.md"
        out.write_text(text, encoding="utf-8")
        written.append(out)
    stamp = make_stamp(code_sha, rules_sha, chain.freeze["freeze_commit"], {k: x["id"] for k, x in ctx["runs"].items()})
    STAMP.write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
    print("rendered " + ", ".join(p.relative_to(REPO_ROOT).as_posix() for p in written)
          + f" from replay run {chain.replay.info.run_id}")
    return written
