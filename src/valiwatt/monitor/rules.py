"""The monitoring rules: what the replay's monitors compute each day and week, and the ordered decision table.

These rules are frozen in a named commit (the `rules-frozen` tag) before the 2024 replay runs; the replay refuses to
run when any rule file differs from that commit. The thresholds themselves are not set here: they are quantiles of
validation weeks, computed and logged by the validation run (`valiwatt validate`).

A performance rule fires on the SAME failure two weeks running (MAPE high both weeks, or bias beyond the same side
of its band both weeks, or coverage low both weeks), and each of those weeks must be out of band broadly, not through
one episode: the week's statistic must stay beyond the threshold when its TRIM_DAYS = 2 most extreme days (in the
direction of the failure) are set aside. Why two: the forecast reuses demand from one day and one week before, so a
single bad day of demand also spoils the next day's forecast and the same day's a week later, and that never puts more
than two spoiled days into one week. With those two days set aside, the trimmed statistic is no further out than the
mean of the five untouched days. So one bad day can make a week broad only when the week's other days already average
beyond the threshold (the trim had been setting aside the week's own two worst days, and the bad day takes
their place): it never yields *retrain* or *recalibrate* without failing days the weeks already held.

A stretch of several bad days does more. Through the 24-hour lag a stretch of n days spoils n + 1 days, and through
the 168-hour lag n days of the following week; from three days on it can spoil more days than the trim sets aside both
in its own week and in that echo week, and both weeks can be broad on the same failure without any failing days of
their own. A stretch that crosses a Monday, putting part of itself into two calendar weeks, is the common case. Weekly
statistics cannot tell such an episode from two weeks of model failure; the pseudo-replay measures how often it yields
*retrain* on its own year (monitor/sensitivity.py).

A bad Monday-to-Sunday week comes back a week later through the 168-hour lag as an echo of the OPPOSITE sign. So a
MAPE pair whose two weeks have their bias flagged on opposite sides is an episode and its echo, not the same failure
twice, and does not count. The exclusion needs both biases flagged: when one week's bias stays inside its band (the
band is wide on that side, or the week also holds days of the stretch), the pair counts.

Trimming costs power against a slow, sustained shift. So rule 2 has a second arm: the weekly bias beyond the SAME side
of its band three weeks running (untrimmed). One bad day, or a stretch of up to a week, lies in at most two calendar
weeks; all it puts into the week after is its echo, of the opposite sign. So with every other week inside its band it
cannot put three weeks on the same side; beside a week already out of band on that side, it can.
"""

from __future__ import annotations

import hashlib
import subprocess

import numpy as np
import pandas as pd

from valiwatt.forecast.evaluate import SEASONS
from valiwatt.paths import REPO_ROOT
from valiwatt.quality.demand import BOUNDS_MW, STATUS_MASKED_DST
from valiwatt.quality.weather import BOUNDS_C

# Quantiles of the validation weeks that become the thresholds (per season for performance).
QUANTILES = {"mape_high": 0.95, "bias_low": 0.025, "bias_high": 0.975, "coverage_low": 0.05, "drift_high": 0.95}
# A week with fewer scored hours than this has no performance statistics, and fewer current values no drift
# statistic; a day with fewer scored hours does not enter the trimmed statistic.
MIN_WEEK_HOURS = 120
MIN_DAY_HOURS = 18
# A week is judged only when all its days lie in the judged period (a replayed year, or the validation years that
# calibrate a threshold set).
WEEK_DAYS = 7
CONSECUTIVE_WEEKS = 2
SUSTAINED_WEEKS = 3  # the sustained-bias arm of rule 2
TRIM_DAYS = 2
MIN_TRIMMED_DAYS = 3
# Day-level data quality: the primary station may miss at most this many of the day's hours.
MAX_SILENT_TEMPERATURE_HOURS = 6

RETRAIN = "retrain"
RECALIBRATE = "recalibrate"
INVESTIGATE = "investigate_data"
# Each performance failure: the weekly statistic, its threshold key, and which side of the threshold fails.
FAILURES = {"mape_high": ("mape", "mape_high", +1), "bias_high": ("rel_bias", "bias_high", +1),
            "bias_low": ("rel_bias", "bias_low", -1), "coverage_low": ("covered", "coverage_low", -1)}

# The rule files: every file under these roots except the ones listed after, which only render, scan or dispatch.
# A file added later under a root is a rule file too, so the set at the freeze tag and now must match exactly.
RULE_ROOTS = ("src/valiwatt", "config")
NOT_RULES = ("src/valiwatt/report/", "src/valiwatt/breaks.py", "src/valiwatt/cli.py", "src/valiwatt/safety.py",
             "src/valiwatt/licences.py", "src/valiwatt/attribution.py", "src/valiwatt/quality/figures.py")
FREEZE_TAG = "rules-frozen"


def _is_rule(path: str) -> bool:
    return path.startswith(tuple(f"{r}/" for r in RULE_ROOTS)) and not path.startswith(NOT_RULES) \
        and "__pycache__" not in path and not path.endswith(".pyc")


def rule_files(commit: str | None = None) -> list[str]:
    """The rule files in the working tree (tracked or not ignored), or in a commit."""
    if commit:
        args = ["git", "ls-tree", "-r", "--name-only", commit, "--", *RULE_ROOTS]
    else:
        args = ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", *RULE_ROOTS]
    out = subprocess.run(args, cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout.split("\n")
    return sorted(p for p in out if p and _is_rule(p) and (commit or (REPO_ROOT / p).is_file()))


def rules_sha256(commit: str | None = None) -> str:
    """Hash of the rule files' names and contents (now, or at a commit): every validation, frozen-model and replay
    run carries it."""
    def read(p):
        if commit:
            return subprocess.run(["git", "show", f"{commit}:{p}"], cwd=REPO_ROOT, capture_output=True,
                                  check=True).stdout
        return (REPO_ROOT / p).read_bytes()

    h = hashlib.sha256()
    for p in rule_files(commit):
        h.update(p.encode() + b"\0" + read(p) + b"\0")
    return h.hexdigest()


def day_problems(hours: pd.DatetimeIndex, raw: pd.DataFrame, raw_t: pd.DataFrame, status: pd.Series,
                 temperature: pd.Series) -> list[dict]:
    """Day-level data quality of one day: the problems that fail it (none: the day passes). Pure.

    hours: the day's 24 hour-ending UTC labels; raw, raw_t: the raw demand and primary-station temperature records
    stamped after the day's origin (hours[0] - 1 h) and up to its last hour; status: the canonical demand status on
    `hours`; temperature: the canonical temperature on `hours` (duplicates and out-of-bounds values blank).

    The replay calls it when it scores a day, on the canonical series rebuilt from the raw cut at that clock; the
    validation calls it on every day of the years that calibrate the thresholds (monitor/replay.py, day_verdicts), so a
    week is calibrated on exactly when the replay would judge it."""
    counts = raw[raw["ts_utc"].isin(hours)].groupby("ts_utc").size().reindex(hours, fill_value=0)
    expected = (status.reindex(hours) == STATUS_MASKED_DST).to_numpy()
    problems = []
    for t in hours[(counts.to_numpy() == 0) & ~expected]:
        problems.append({"kind": "missing_hour", "utc": t})
    for t in raw.loc[raw["mw"].isna(), "ts_utc"]:
        problems.append({"kind": "blank_value", "utc": t})
    for t in hours[(counts.to_numpy() > 1) & ~expected]:
        problems.append({"kind": "duplicate", "utc": t, "records": int(counts[t])})
    for t in raw.loc[~raw["ts_utc"].isin(hours), "ts_utc"]:
        problems.append({"kind": "off_grid_record", "utc": t})
    # Bounds are judged on every raw record, so a value inside a masked fall-back pair is still seen.
    bad = raw["mw"].notna() & ((raw["mw"] < BOUNDS_MW[0]) | (raw["mw"] > BOUNDS_MW[1]))
    for _, rec in raw[bad].iterrows():
        problems.append({"kind": "demand_out_of_bounds", "utc": rec["ts_utc"], "mw": float(rec["mw"])})
    for t, n in raw_t.groupby("ts_utc").size().items():
        if n > 1:
            problems.append({"kind": "temperature_duplicate", "utc": t, "records": int(n)})
    bad_t = raw_t["temp_c"].notna() & ((raw_t["temp_c"] < BOUNDS_C[0]) | (raw_t["temp_c"] > BOUNDS_C[1]))
    for _, rec in raw_t[bad_t].iterrows():
        problems.append({"kind": "temperature_out_of_bounds", "utc": rec["ts_utc"], "temp_c": float(rec["temp_c"])})
    silent = int(temperature.reindex(hours).isna().sum())
    if silent > MAX_SILENT_TEMPERATURE_HOURS:
        problems.append({"kind": "temperature_silent", "hours": silent, "limit": MAX_SILENT_TEMPERATURE_HOURS})
    return problems


def not_judged_because(days: int, failed_days: int, hours: int) -> list[str]:
    """Why a week gets no performance statistics (none: it is judged). The replay's weekly judgement and the
    calibration of the thresholds both read this one predicate, so the thresholds come from the weeks the replay
    would judge.

    days: the week's days inside the judged period; failed_days: how many of them failed their day-level data quality
    (day_problems); hours: the week's scored hours."""
    why = []
    if days < WEEK_DAYS:
        why.append("partial_week")
    if failed_days:
        why.append("data_quality")
    if hours < MIN_WEEK_HOURS:
        why.append("too_few_hours")
    return why


def season_thresholds(thresholds: dict, season: str) -> dict:
    perf = thresholds["performance"]
    if season not in perf:
        raise KeyError(f"no validation thresholds for season {season!r}: the monitor refuses to judge it")
    return perf[season]


def performance_flags(stats: dict, t: dict) -> dict:
    """Which failures one period's statistics show against its season's thresholds."""
    return {k: bool(side * (stats[stat] - t[key]) > 0) for k, (stat, key, side) in FAILURES.items()}


def period_stats(scored: pd.DataFrame) -> dict:
    """MAPE, signed relative bias and coverage over the scored hours of a period."""
    return {"mape": float(scored["ape"].mean()), "rel_bias": float(scored["rel_bias"].mean()),
            "covered": float(scored["covered"].mean()), "hours": int(len(scored))}


def trimmed(daily: pd.DataFrame, failure: str) -> float:
    """The mean of the daily statistic with the TRIM_DAYS most extreme days, in the failure's direction, set aside."""
    stat, _, side = FAILURES[failure]
    values = np.sort(side * daily[stat].to_numpy())
    keep = values[: len(values) - TRIM_DAYS]
    return float(side * keep.mean()) if len(keep) >= MIN_TRIMMED_DAYS else float("nan")


def week_record(week_start: pd.Timestamp, scored: pd.DataFrame, days: list[dict], drift: dict,
                thresholds: dict) -> dict:
    """Everything the decision table reads about one week, with the numbers behind each flag.

    scored: the week's scored hours (columns day, ape, rel_bias, covered); days: the day-level data-quality records
    of the week's days inside the replayed period; drift: {"temperature": value, "forecast": value} (NaN when the
    week is too short to judge). Whether the week is judged is not_judged_because's answer, the one the thresholds
    were calibrated with."""
    season = SEASONS[(week_start + pd.Timedelta(days=3)).month]  # the season of the week's Thursday
    t = season_thresholds(thresholds, season)
    failed_days = [d for d in days if d["failed"]]
    rec = {"week_start": week_start, "season": season, "thresholds": t, "hours": int(len(scored)),
           "dq_failed": bool(failed_days), "dq_failed_days": [d["day"] for d in failed_days],
           "drift": drift, "drift_thresholds": {"temperature": thresholds["drift"]["temperature_high"],
                                                "forecast": thresholds["drift"]["forecast_high"]}}
    rec["drift_flags"] = {k: bool(np.isfinite(v) and v > rec["drift_thresholds"][k]) for k, v in drift.items()}
    rec["not_judged_because"] = not_judged_because(len(days), len(failed_days), len(scored))
    rec["evaluated"] = not rec["not_judged_because"]
    if not rec["evaluated"]:
        rec.update(stats=None, flags=None, trimmed=None, broad=None)
        return rec
    rec["stats"] = period_stats(scored)
    rec["flags"] = performance_flags(rec["stats"], t)
    daily = pd.DataFrame([period_stats(part) for _, part in scored.groupby("day") if len(part) >= MIN_DAY_HOURS])
    rec["trimmed"] = {k: trimmed(daily, k) if len(daily) else float("nan") for k in FAILURES}
    rec["broad"] = {k: bool(rec["flags"][k] and np.isfinite(rec["trimmed"][k])
                            and FAILURES[k][2] * (rec["trimmed"][k] - t[FAILURES[k][1]]) > 0) for k in FAILURES}
    return rec


def _evaluated(w: dict | None) -> bool:
    return bool(w and w["evaluated"])


def _adjacent(later: dict | None, earlier: dict | None) -> dict | None:
    if later is None or earlier is None:
        return None
    return earlier if earlier["week_start"] == later["week_start"] - pd.Timedelta(days=7) else None


def _opposite_bias(a: dict, b: dict) -> bool:
    return (a["flags"]["bias_low"] and b["flags"]["bias_high"]) or (a["flags"]["bias_high"] and b["flags"]["bias_low"])


def decide(week: dict, previous: dict | None, before: dict | None = None, echo_exclusion: bool = True) -> dict:
    """The ordered decision table for one week. `previous` and `before` are the records of the two calendar weeks just
    before (None when missing). `echo_exclusion=False` exists only to measure what the exclusion changes; the replay
    never passes it.

    A week whose data quality failed, or with too few hours, has no performance statistics; it breaks any streak, so
    the weeks a rule needs are always adjacent weeks that were all evaluated."""
    previous = _adjacent(week, previous)
    before = _adjacent(previous, before)
    fired = []
    if week["dq_failed"]:
        fired.append({"rule": 1, "recommendation": INVESTIGATE, "why": "data_quality",
                      "evidence": {"failed_days": week["dq_failed_days"]}})
    pair = (week, previous)
    if not week["dq_failed"] and all(_evaluated(w) for w in pair):
        same = [k for k in ("mape_high", "bias_high", "bias_low") if all(w["broad"][k] for w in pair)]
        if echo_exclusion and "mape_high" in same and _opposite_bias(week, previous):
            same.remove("mape_high")  # an episode and its echo, not the same failure twice
        three = (week, previous, before)
        sustained = [k for k in ("bias_high", "bias_low")
                     if all(_evaluated(w) for w in three) and all(w["flags"][k] for w in three)]
        if same or sustained:
            weeks = three if sustained else pair
            fired.append({"rule": 2, "recommendation": RETRAIN, "why": same + [f"{k}_sustained" for k in sustained],
                          "evidence": [_perf_evidence(w) for w in weeks]})
        bias_inside = all(not (w["flags"]["bias_high"] or w["flags"]["bias_low"]) for w in pair)
        if all(w["broad"]["coverage_low"] for w in pair) and bias_inside:
            fired.append({"rule": 3, "recommendation": RECALIBRATE, "why": ["coverage_low"],
                          "evidence": [_perf_evidence(w) for w in pair]})
        inputs = [k for k in ("temperature", "forecast") if all(w["drift_flags"][k] for w in pair)]
        if inputs and all(not any(w["flags"].values()) for w in pair):
            fired.append({"rule": 4, "recommendation": INVESTIGATE, "why": inputs,
                          "evidence": [{"week_start": w["week_start"], "drift": w["drift"],
                                        "thresholds": w["drift_thresholds"]} for w in pair]})
    return {"week_start": week["week_start"], "recommendation": fired[0]["recommendation"] if fired else None,
            "rule": fired[0]["rule"] if fired else None, "fired": fired}


def two_week_rule_alone(week: dict, previous: dict | None) -> bool:
    """Sensitivity only, never a recommendation: the plan's first wording of the retrain rule, with weekly flags
    alone (MAPE high or bias out of band, in any mix, two weeks running), no trimming and no same-failure match."""
    if previous is None or previous["week_start"] != week["week_start"] - pd.Timedelta(days=7):
        return False
    return all(_evaluated(w) and (w["flags"]["mape_high"] or w["flags"]["bias_high"] or w["flags"]["bias_low"])
               for w in (week, previous))


def _perf_evidence(w: dict) -> dict:
    return {"week_start": w["week_start"], "season": w["season"], "stats": w["stats"], "flags": w["flags"],
            "trimmed": w["trimmed"], "broad": w["broad"], "thresholds": w["thresholds"]}
