"""The monitoring rules: what the replay's monitors compute each day and week, and the ordered decision table.

These rules are frozen in a named commit (the `rules-frozen` tag) before the 2024 replay runs; the replay refuses to
run when any file in RULE_FILES differs from that commit. The thresholds themselves are not set here: they are
quantiles of validation weeks, computed and logged by the validation run (`valiwatt validate`).

Why each weekly performance rule also counts days: the forecast reuses demand from one day and one week before, so a
single bad day of demand also spoils the forecast of the next day and of the same day a week later. Those three days
never put more than two days into any one week. The performance rules need the week out of band AND most of its days
out of band too (MIN_BREACH_DAYS = 4 of 7), in each of two consecutive weeks. One bad day alone cannot meet that: it
would need two more bad days of its own in each of the two weeks.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from valiwatt.forecast.evaluate import SEASONS
from valiwatt.paths import REPO_ROOT

# Quantiles of the validation weeks that become the thresholds (per season for performance).
QUANTILES = {"mape_high": 0.95, "bias_low": 0.025, "bias_high": 0.975, "coverage_low": 0.05, "drift_high": 0.95}
# A week with fewer scored hours than this has no performance statistics; a day with fewer is not counted as a breach day.
MIN_WEEK_HOURS = 120
MIN_DAY_HOURS = 18
CONSECUTIVE_WEEKS = 2
MIN_BREACH_DAYS = 4
# Day-level data quality: the primary station may miss at most this many of the day's hours.
MAX_SILENT_TEMPERATURE_HOURS = 6

RETRAIN = "retrain"
RECALIBRATE = "recalibrate"
INVESTIGATE = "investigate_data"

# Every file whose content can change what the replay computes or recommends.
RULE_FILES = (
    "src/valiwatt/monitor/rules.py",
    "src/valiwatt/monitor/clock.py",
    "src/valiwatt/monitor/drift.py",
    "src/valiwatt/monitor/replay.py",
    "src/valiwatt/monitor/run.py",
    "src/valiwatt/forecast/evaluate.py",
    "src/valiwatt/forecast/features.py",
    "src/valiwatt/forecast/freeze.py",
    "src/valiwatt/forecast/guards.py",
    "src/valiwatt/forecast/model.py",
    "src/valiwatt/forecast/pyfunc.py",
    "src/valiwatt/forecast/validate.py",
    "src/valiwatt/forecast/walkforward.py",
    "src/valiwatt/quality/checks.py",
    "src/valiwatt/quality/demand.py",
    "src/valiwatt/quality/weather.py",
    "config/stations.toml",
)
FREEZE_TAG = "rules-frozen"


def rules_sha256(read=lambda p: (REPO_ROOT / p).read_bytes()) -> str:
    """Hash of the rule files: every validation, frozen-model and replay run carries it."""
    h = hashlib.sha256()
    for p in RULE_FILES:
        h.update(p.encode() + b"\0" + read(p) + b"\0")
    return h.hexdigest()


def season_thresholds(thresholds: dict, season: str) -> dict:
    perf = thresholds["performance"]
    if season not in perf:
        raise KeyError(f"no validation thresholds for season {season!r}: the monitor refuses to judge it")
    return perf[season]


def performance_flags(stats: dict, t: dict) -> dict:
    """Which performance statistics of one period (a week or a day) are out of their season's band."""
    return {
        "mape_high": bool(stats["mape"] > t["mape_high"]),
        "bias_out": bool(stats["rel_bias"] < t["bias_low"] or stats["rel_bias"] > t["bias_high"]),
        "coverage_low": bool(stats["covered"] < t["coverage_low"]),
    }


def period_stats(scored: pd.DataFrame) -> dict:
    """MAPE, signed relative bias and coverage over the scored hours of a period."""
    return {"mape": float(scored["ape"].mean()), "rel_bias": float(scored["rel_bias"].mean()),
            "covered": float(scored["covered"].mean()), "hours": int(len(scored))}


def week_record(week_start: pd.Timestamp, scored: pd.DataFrame, days: list[dict], drift: dict,
                thresholds: dict) -> dict:
    """Everything the decision table reads about one week, with the numbers behind each flag.

    scored: the week's scored hours (columns day, ape, rel_bias, covered); days: the week's day-level data-quality
    records; drift: {"temperature": value, "forecast": value}."""
    season = SEASONS[(week_start + pd.Timedelta(days=3)).month]  # the season of the week's Thursday
    t = season_thresholds(thresholds, season)
    failed_days = [d for d in days if d["failed"]]
    rec = {"week_start": week_start, "season": season, "thresholds": t, "hours": int(len(scored)),
           "dq_failed": bool(failed_days), "dq_failed_days": [d["day"] for d in failed_days],
           "drift": drift, "drift_thresholds": {"temperature": thresholds["drift"]["temperature_high"],
                                                "forecast": thresholds["drift"]["forecast_high"]}}
    rec["drift_flags"] = {k: bool(np.isfinite(v) and v > rec["drift_thresholds"][k]) for k, v in drift.items()}
    rec["evaluated"] = (not rec["dq_failed"]) and len(scored) >= MIN_WEEK_HOURS
    if not rec["evaluated"]:
        rec.update(stats=None, flags=None, breach_days=None)
        return rec
    rec["stats"] = period_stats(scored)
    rec["flags"] = performance_flags(rec["stats"], t)
    breach = {k: 0 for k in rec["flags"]}
    for _, part in scored.groupby("day"):
        if len(part) < MIN_DAY_HOURS:
            continue
        for k, v in performance_flags(period_stats(part), t).items():
            breach[k] += int(v)
    rec["breach_days"] = breach
    return rec


def _perf_breach(w: dict | None, min_days: int) -> bool:
    if not w or not w["evaluated"]:
        return False
    f, b = w["flags"], w["breach_days"]
    return (f["mape_high"] and b["mape_high"] >= min_days) or (f["bias_out"] and b["bias_out"] >= min_days)


def _coverage_breach(w: dict | None, min_days: int) -> bool:
    if not w or not w["evaluated"]:
        return False
    return w["flags"]["coverage_low"] and w["breach_days"]["coverage_low"] >= min_days


def _bias_inside(w: dict | None) -> bool:
    return bool(w and w["evaluated"] and not w["flags"]["bias_out"])


def _performance_inside(w: dict | None) -> bool:
    return bool(w and w["evaluated"] and not any(w["flags"].values()))


def _drift(w: dict | None) -> bool:
    return bool(w and any(w["drift_flags"].values()))


def decide(week: dict, previous: dict | None, min_breach_days: int = MIN_BREACH_DAYS) -> dict:
    """The ordered decision table for one week. `previous` is the record of the calendar week just before, or None.

    A week whose data quality failed, or with too few hours, has no performance statistics; it breaks any streak, so
    the two weeks a rule needs are always adjacent weeks that were both evaluated. `min_breach_days=0` gives the
    two-week rule alone, reported as a sensitivity and never used to recommend."""
    if previous is not None and previous["week_start"] != week["week_start"] - pd.Timedelta(days=7):
        previous = None
    fired = []
    if week["dq_failed"]:
        fired.append({"rule": 1, "recommendation": INVESTIGATE, "why": "data_quality",
                      "evidence": {"failed_days": week["dq_failed_days"]}})
    pair = (week, previous)
    if not week["dq_failed"]:
        if all(_perf_breach(w, min_breach_days) for w in pair):
            fired.append({"rule": 2, "recommendation": RETRAIN, "why": "performance",
                          "evidence": [_perf_evidence(w) for w in pair]})
        if all(_coverage_breach(w, min_breach_days) for w in pair) and all(_bias_inside(w) for w in pair):
            fired.append({"rule": 3, "recommendation": RECALIBRATE, "why": "coverage",
                          "evidence": [_perf_evidence(w) for w in pair]})
        if all(_drift(w) for w in pair) and all(_performance_inside(w) for w in pair):
            fired.append({"rule": 4, "recommendation": INVESTIGATE, "why": "drift",
                          "evidence": [{"week_start": w["week_start"], "drift": w["drift"],
                                        "thresholds": w["drift_thresholds"]} for w in pair]})
    return {"week_start": week["week_start"], "recommendation": fired[0]["recommendation"] if fired else None,
            "rule": fired[0]["rule"] if fired else None, "fired": fired}


def _perf_evidence(w: dict) -> dict:
    return {"week_start": w["week_start"], "season": w["season"], "stats": w["stats"], "flags": w["flags"],
            "breach_days": w["breach_days"], "thresholds": w["thresholds"]}
