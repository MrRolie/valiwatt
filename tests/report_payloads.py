"""v2's run payloads for the report's tests, and a context as `validation.context` builds it from them.

The payloads have v2's shapes: validation.json with every model, the contrasts, verdicts and labels (computed by the
decision rule itself on a synthetic out-of-sample frame, so the report's checks against the frame hold); the 2023
rehearsal and the 2024 replay with lane D's v2 keys; each replay's diagnostics (diagnostics.json); a data-quality
run with the forecast's checks; a 2025 data-quality run with revised records. Most values come from `Distinct`, so
each lands in its own place. The 2025 starts are built with report_world's helpers and walked by `ledger.of_record`,
exactly as the report walks them.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import report_world as W

from valiwatt.breaks import BREAKS, GROUPS
from valiwatt.forecast import evaluate
from valiwatt.report import ledger
from valiwatt.report import validation as RV
from valiwatt.report.lineage import RecordedReplay

LANGS = ("fr", "en")
P_YEAR, R_YEAR, H_YEAR = 2023, 2024, 2025
SEASONS = ("winter", "spring", "summer", "fall")
KEYS = ("gam_forecast", "gam_observed", "gam_forecast_late", "forecast", "persistence", "observed")
PROD, FAM, SHIP = "gam_forecast", "forecast", "persistence"
PHI = (5 ** 0.5 - 1) / 2


class Distinct:
    """Values that never repeat: the k-th is lo + (hi - lo) * frac(k * phi), rounded to 6 decimals, and a value
    already given is skipped."""

    def __init__(self):
        self.k, self.seen = 0, set()

    def __call__(self, lo=0.01, hi=0.09):
        while True:
            self.k += 1
            x = round(lo + (hi - lo) * ((self.k * PHI) % 1), 6)
            if x not in self.seen:
                self.seen.add(x)
                return x


def ci(g, lo, hi):
    m = g(lo, hi)
    return {"mean": m, "ci95_low": round(m - g(0.002, 0.01), 6), "ci95_high": round(m + g(0.002, 0.01), 6)}


def group_rows(g, keys, lo=0.02, hi=0.08):
    rows = []
    for k in keys:
        a, b = g(lo, hi), g(-0.03, 0.03)
        rows.append({"key": str(k), "ape": a, "ape_low": round(a - g(0.001, 0.005), 6),
                     "ape_high": round(a + g(0.001, 0.005), 6), "rel_bias": b,
                     "rel_bias_low": round(b - g(0.001, 0.005), 6), "rel_bias_high": round(b + g(0.001, 0.005), 6),
                     "hours": int(g(1000, 9000))})
    return rows


def cov_rows(g, keys):
    rows = []
    for k in keys:
        m = g(0.84, 0.95)
        rows.append({"key": str(k), "covered": m, "covered_low": round(m - g(0.01, 0.03), 6),
                     "covered_high": round(m + g(0.01, 0.03), 6)})
    return rows


def coverage_block(g):
    o = ci(g, 0.88, 0.92)
    return {"overall": o, "season": cov_rows(g, SEASONS), "year": cov_rows(g, range(2021, 2024)),
            "hour_group": cov_rows(g, ("night", "morning", "day", "evening", "late")),
            "decile": cov_rows(g, range(1, 11)), "mean_half_width_pct": g(7, 12)}


def thresholds(g, first, last, excluded):
    return {"performance": {s: {"mape_high": g(0.04, 0.09), "bias_low": g(-0.05, -0.01), "bias_high": g(0.02, 0.06),
                                "coverage_low": g(0.55, 0.85), "weeks": int(g(30, 40))} for s in SEASONS},
            "drift": {"temperature_high": g(1.0, 1.5), "forecast_high": g(0.8, 1.2), "reference_half_window_weeks": 2},
            "quantiles": {"mape_high": 0.95, "bias_low": 0.025, "bias_high": 0.975, "coverage_low": 0.05,
                          "drift_high": 0.95},
            "first_year": first, "last_year": last, "week_days": 7, "min_week_hours": 120,
            "excluded_weeks": [{"week_start": f"{last}-0{i + 1}-0{i + 2}", "reason": "failed_day"}
                               for i in range(excluded)]}


def week(g, start, flags=(), broad=()):
    keys = ("mape_high", "bias_high", "bias_low", "coverage_low")
    return {"week_start": start, "evaluated": True,
            "stats": {"mape": g(0.03, 0.09), "rel_bias": g(-0.04, 0.04), "covered": g(0.6, 0.95), "hours": 168},
            "flags": {k: k in flags for k in keys}, "broad": {k: k in broad for k in keys},
            "trimmed": {k: g(0.01, 0.06) for k in keys},
            "thresholds": {k: g(0.02, 0.07) for k in keys},
            "drift": {"temperature": g(0.1, 0.9), "forecast": g(0.1, 0.9)},
            "drift_thresholds": {"temperature": 1.2, "forecast": 1.0}}


def power(g, year, offset):
    starts = [f"{year}-01-0{1 + offset}", f"{year}-03-0{4 + offset}"]
    bias = [{"start": s, "season": "winter", "bias": b, "weeks": None if (b == 0.02 and i == 0) else int(g(1, 8))}
            for i, s in enumerate(starts) for b in (-0.04, -0.02, 0.02, 0.04)]
    half = [{"start": s, "season": "winter", "half_width": f, "weeks": int(g(1, 8)) if f == 0.5 or i else None}
            for i, s in enumerate(starts) for f in (0.9, 0.7, 0.5)]
    return {"bias": bias, "half_width": half}


def sensitivity(g, year, base):
    """One year's measures: the design (lengths, factors, shifts, noise) is the code's, every count its own."""
    rows = []
    for days in (1, 3, 5, 7):
        for factor in (0.7, 0.85, 1.15, 1.3):
            starts = base + len(rows)
            retrain = 0 if days == 1 else int(g(1, 60))
            rows.append({"days": days, "factor": factor, "starts": starts,
                         "retrain": retrain, "retrain_clean": min(retrain, int(g(0, 9))),
                         "recalibrate": 0 if days == 1 else int(g(0, 5)),
                         "kept_from_retrain_by_echo_exclusion": int(g(0, 9))})
    seasons = [f"{year}-01-0{2 if year == P_YEAR else 1}", f"{year}-03-06", f"{year}-06-05", f"{year}-09-04"]
    shifts = []
    for i, s in enumerate(seasons):
        for f in (-0.1, -0.05, 0.05, 0.1):
            up_missed = f == 0.1 and i < (3 if year == P_YEAR else 1)
            down_missed = f == -0.1 and i == 0 and year == P_YEAR
            shifts.append({"start": s, "season": SEASONS[i], "shift": f,
                           "weeks": None if up_missed or down_missed else int(g(1, 10)),
                           "weekly_bias": [0.001 * (i + 1), -0.001 * (i + 1)]})
    echo = [{"sd": sd, "runs": 2 * base + 100 * i, "retrain_with": base // 2 + 10 * i,
             "retrain_without": base // 2 + 10 * i + 7 + i, "suppressed": 7 + i, "delayed": 3 + i,
             "weeks_delayed": 5 + i}
            for i, sd in enumerate((0.06, 0.10, 0.15))]
    trials = [{"start": seasons[0], "season": "winter", "sd": 0.06, "seed": 0, "weeks_with": 3, "weeks_without": 2}]
    # The measures' own shape (monitor/sensitivity.py `measure`), the keys the report does not read included.
    return {"episodes": {"rows": rows, "first_start": f"{year}-01-0{3 if year == P_YEAR else 8}",
                         "jobs_with_alarms": []},
            "level_shifts": shifts, "echo_exclusion_cost": {"rows": echo, "horizon_weeks": 8, "runs": trials}}


REUSE_OFFSETS = (5.0, -5.0)


def reuse_offset(days: list[str], offset: float, changed: int, rule4: bool) -> dict:
    """One offset of a constructible placement, as monitor/sensitivity.py `_reuse` writes it: the corrupted run fed
    both days, each affected week's statistics (its two most extreme days set aside) against its untouched days' mean,
    none beyond it."""
    first = pd.Timestamp(days[0])
    week = (first - pd.Timedelta(days=first.dayofweek)).strftime("%Y-%m-%d")
    stats = {"mape": 0.02, "rel_bias": 0.004 * offset / abs(offset), "covered": 0.9}
    return {"offset_c": offset, "days_fed": [f"{d}T00:00:00" for d in days], "shown": True,
            "weeks": [{"week_start": f"{week}T00:00:00", "evaluated": True,
                       "trimmed": {"mape_high": stats["mape"], "bias_high": stats["rel_bias"],
                                   "bias_low": stats["rel_bias"], "coverage_low": stats["covered"]},
                       "untouched_mean": {k: v + 0.01 for k, v in stats.items()}, "untouched_days": 5,
                       "beyond_untouched": []}],
            "decisions_changed": [{"week_start": f"{week}T00:00:00", "was": "", "now": "investigate"}] * changed,
            "rule4_fired": rule4, "beyond_untouched": 0}


def diagnostics(g, year, base):
    """A replay's diagnostics.json (its diagnostic run's results, lane D): the measures and the run-reuse measure, in
    monitor/sensitivity.py `forecast_run_reuse`'s shape (summer across two weeks not constructible)."""
    placements = []
    for i, s in enumerate(SEASONS):
        for j, where in enumerate(("within_week", "across_weeks")):
            p = {"season": s, "placement": where}
            changed = int(g(0, 3))
            if s == "summer" and where == "across_weeks":
                placements.append(p | {"constructible": False, "reason": "no_feeding_run_proven_to_cover_the_next_day"})
                continue
            days = [f"{year}-{1 + 3 * i:02d}-0{3 + j}", f"{year}-{1 + 3 * i:02d}-0{4 + j}"]
            placements.append(p | {"days": [f"{d}T00:00:00" for d in days],
                                   "runtime": f"{days[0]}T03:00:00+00:00", "constructible": True,
                                   "offsets": [reuse_offset(days, o, changed, where == "within_week" and i == 0)
                                               for o in REUSE_OFFSETS]})
    made = [p for p in placements if p["constructible"]]
    reuse = {"label": "sensitivity_never_chooses", "placements": placements, "offsets_c": list(REUSE_OFFSETS),
             "constructible": len(made), "beyond_untouched": sum(o["beyond_untouched"] for p in made
                                                                   for o in p["offsets"]),
             "longest_lead_h": 71.0, "reuse_lead_h": 75}  # the longest lead as the data-quality runs state it
    return {"sensitivity": sensitivity(g, year, base), "sensitivity_seconds": g(100, 900), "reuse_sensitivity": reuse,
            "run_id": f"{base:032x}"}


def frame(first: str, last: str, seed: int = 0, gap=None) -> pd.DataFrame:
    """Out-of-sample hours (four a day) whose forecasts the decision rule can compare: the target rises in the cold,
    v2's error is the smallest, v1 as shipped's the largest. The busiest hour has no production forecast (no covering
    run), so it shows its reason."""
    rng = np.random.default_rng(seed)
    days = pd.date_range(first, last, freq="D")
    idx = pd.DatetimeIndex([d + pd.Timedelta(hours=6 + h) for d in days for h in (0, 6, 12, 18)], tz="UTC")
    day = pd.DatetimeIndex([d for d in days for _ in range(4)])
    doy = day.dayofyear.to_numpy()
    temp = 6 - 16 * np.cos(2 * np.pi * doy / 365.25) + rng.normal(0, 3, len(idx))
    target = 21_000 + 320 * (18 - temp) + rng.normal(0, 300, len(idx))
    out = pd.DataFrame({"target": target, "day": day, "hour": np.tile([0, 6, 12, 18], len(days)),
                        "fold": day.strftime("%Y-%m"), "temp_forecast": temp}, index=idx)
    sd = {PROD: 0.025, FAM: 0.032, SHIP: 0.041, "observed": 0.028}
    for k, s in sd.items():
        out[f"pred_{k}"] = target * (1 + rng.normal(0, s, len(idx)))
    out["baseline"] = target * (1 + rng.normal(0, 0.06, len(idx)))
    spring = (day.year == 2020) & day.month.isin([3, 4, 5, 6])
    out.loc[spring, f"pred_{PROD}"] *= 0.97  # the 2020 spring shift, seen by the production model
    for k in (PROD, FAM, SHIP):
        out[f"why_{k}"] = ""
    top = out["target"].idxmax()
    out.loc[top, [f"pred_{PROD}", f"pred_{FAM}"]] = np.nan
    out.loc[top, [f"why_{PROD}", f"why_{FAM}"]] = "no_covering_run"
    out.loc[top, "temp_forecast"] = np.nan
    return out


_CACHE: dict = {}


def oos_frame() -> pd.DataFrame:
    if "oos" not in _CACHE:
        _CACHE["oos"] = frame("2020-01-01", "2023-12-31", seed=1)
    return _CACHE["oos"].copy()


def year_contrasts(year: int, seed: int, label: str) -> dict:
    key = ("contrasts", year, seed)
    if key not in _CACHE:
        f = frame(f"{year}-01-01", f"{year}-12-31", seed=seed)
        cold = float(np.nanquantile(oos_frame()["temp_forecast"], 0.05))
        _CACHE[key] = evaluate.contrasts(f, cold, n_boot=200)
    c = _CACHE[key]
    return c | {"label": label}


def paired(lo: float, hi: float, hours: int = 8000) -> dict:
    mid = (lo + hi) / 2
    return {"difference": mid, "ci95_low": lo, "ci95_high": hi, "hours": hours,
            "separated": bool(lo > 0 or hi < 0), "state": evaluate.state({"ci95_low": lo, "ci95_high": hi})}


VERDICT_STATE = {"better": (-0.01, -0.004), "better_cold_untested": (-0.01, -0.004),
                 "not_shown_better": (-0.004, 0.002), "worse": (0.002, 0.008)}


def contrasts_for(verdict: tuple[str, str], label: str) -> dict:
    """A 2025 contrasts block (lane D's shape) whose states give `verdict` by the decision rule."""
    s, f = (paired(*VERDICT_STATE[x]) for x in verdict)
    testable = "better_cold_untested" not in verdict
    cold = {"threshold_c": -14.5, "hours": 300 if testable else 80, "weeks": 8 if testable else 2,
            "testable": testable, "shipped": paired(-0.02, -0.001, 300), "family": paired(-0.02, -0.001, 300)}
    c = {"hours": 8000, "weeks": 52, "shipped": s, "family": f, "input": paired(s["difference"] - f["difference"] - 0.001,
                                                                                   s["difference"] - f["difference"] + 0.001),
         "cold": cold, "peak": year_contrasts(H_YEAR, 9, label)["peak"], "additive_check": 0.0}
    assert tuple(evaluate.verdict(c).values()) == tuple(verdict), (verdict, evaluate.verdict(c))
    return c | {"label": label}


def replay(g, year, base, decisions, near, label):
    weeks = [week(g, f"{year}-02-05", ("mape_high",), ()), week(g, f"{year}-02-12", ("mape_high",), ()),
             week(g, f"{year}-04-01", ("mape_high",), ()), week(g, f"{year}-04-08", ("bias_high",), ())]
    pair = weeks[:2] if near == "trim" else weeks[2:]
    recs = {}
    for d in decisions:
        if d["recommendation"]:
            recs[d["recommendation"]] = recs.get(d["recommendation"], 0) + 1
    c = year_contrasts(year, base, label)
    return {
        "year": year, "summary": {
            "mape": ci(g, 0.03, 0.06), "bias": ci(g, -0.01, 0.01), "coverage": ci(g, 0.85, 0.93),
            "days": 366 if year == R_YEAR else 365, "hours_issued": int(g(8700, 8760)),
            "hours_scored": int(g(8600, 8700)), "days_failed": int(g(1, 5)), "weeks_evaluated": 51, "weeks": 53,
            "recommendations": recs,
            "flag_weeks": {k: base // 100 + i for i, k in enumerate(("mape_high", "bias_high", "bias_low",
                                                                      "coverage_low"))},
            "retrain_under_two_week_rule_alone": [{"week_start": pair[1]["week_start"],
                                                   "weeks": [{k: w[k] for k in ("week_start", "stats", "flags",
                                                                                "trimmed")} for w in pair]}]},
        "weeks": weeks, "decisions": decisions,
        "days": [{"day": f"{year}-01-01T00:00:00", "not_predicted": {f"{year}-01-01T06:00": "lag24, temp_forecast"}},
                 {"day": f"{year}-12-31T00:00:00", "not_predicted": {}}],
        "model": {"sha256": f"{base:064x}", "lag_coefficients": {"lag24": g(0.4, 0.6), "lag168": g(0.1, 0.3)}},
        "power": power(g, year, base % 3),
        "evidently_checks": [{"week_start": f"{year}-03-04", "name": "temperature"}],
        "freeze_tag": W.FREEZE_TAG if year == R_YEAR else "", "frozen_sha256": W.FROZEN,
        "comparators": {k: {"mape": ci(g, 0.03, 0.06), "coverage": ci(g, 0.85, 0.93), "hours": int(g(8000, 8700))}
                        for k in (FAM, SHIP)},
        "contrasts": c, "verdict": evaluate.verdict(c) | {"label": label},
        # monitor/run.py `forecast_error` itself, on the hourly frame the replay's run holds (`hourly`), so the report's
        # weekly table, derived from that frame, counts the hours the logged aggregates count.
        "forecast_error": forecast_error(hourly(year)),
    }


def hourly(year: int) -> pd.DataFrame:
    """A replay's frames/hourly.parquet, the columns the report reads from it: every hour of `year` (hour ending, UTC),
    its local standard day, the issued forecast temperature and the ECCC observation, some hours without one."""
    from valiwatt.config import LST_OFFSET

    idx = pd.date_range(f"{year}-01-01T06:00Z", f"{year + 1}-01-01T05:00Z", freq="h", name="ts_utc")
    rng = np.random.default_rng(year)
    observed = rng.normal(5.0, 10.0, len(idx))
    forecast = observed + rng.normal(0.2, 1.5, len(idx))
    observed[::97] = np.nan
    day = (idx - pd.Timedelta(hours=1) + LST_OFFSET).tz_localize(None).normalize()
    return pd.DataFrame({"day": day, "temp_forecast": forecast, "temp_observed": observed}, index=idx)


def forecast_error(frame: pd.DataFrame) -> dict:
    from valiwatt.monitor.run import forecast_error as logged

    return logged(frame)


_WEEKLY: dict = {}


def weekly_error() -> dict:
    """What `validation.context` derives from the two replays' hourly frames."""
    if not _WEEKLY:
        _WEEKLY.update({k: RV.weekly_forecast_error(hourly(y), forecast_error(hourly(y)), y)
                        for k, y in (("pseudo", P_YEAR), ("replay", R_YEAR))})
    return {k: [dict(x) for x in rows] for k, rows in _WEEKLY.items()}


V1_PLAIN = {"run_id": "9" * 32, "year": 2024, "freeze_tag": "rules-frozen", "freeze_commit": "e" * 40,
            "rules_sha256": "d" * 64, "code_sha256": "c" * 64, "started_utc": "2026-10-14T22:48:56.547Z",
            "ended_utc": "2026-10-14T22:48:58.424Z", "status": "complete", "mape": 0.0423, "coverage": 0.8943,
            "recommendations": [], "departure": False, "departure_from": []}
V1_DEPARTED = {**V1_PLAIN, "run_id": "8" * 32, "freeze_commit": "f" * 40, "started_utc": "2026-10-15T01:45:51.192Z",
               "ended_utc": "2026-10-15T01:45:53.101Z", "mape": 0.04231, "coverage": 0.8941, "departure": True,
               "departure_from": ["9" * 32]}


def curves() -> list[dict]:
    rows = []
    for h in range(24):
        for t in range(-35, 36, 5):
            rows.append({"hour": h, "temp_c": float(t), "effect_mw": float((18 - t) * (40 + h)),
                         "effect_mw_nolag": float((18 - t) * (120 + 2 * h)), "in_training_range": -28 <= t <= 30})
    return rows


def nbm_checks(g, at_or_above=(), unfed=(("2021-02-03", "no_covering_run"),), longest=None):
    longest = longest or {str(y): 71.0 if y >= 2021 else 72.0 for y in range(2019, 2025)}
    return {
        "nbm_bounds": {"name": "nbm_bounds", "kind": "gate", "passed": True,
                       "metrics": {"low_c": -50.0, "high_c": 45.0, "violations": 0, "points": int(g(9e5, 1e6)),
                                   "blank_points": 3, "min_c": -38.9, "max_c": 36.1}, "evidence": []},
        "nbm_coverage": {"name": "nbm_coverage", "kind": "gate", "passed": True,
                         "metrics": {"share": g(0.98, 0.999), "threshold": 0.98, "days": 2192,
                                     "fed_days": 2190}, "evidence": []},
        "nbm_runs": {"name": "nbm_runs", "kind": "finding", "passed": True,
                     "metrics": {"identical_duplicates": 12, "runs": 50_000, "points": 900_000,
                                 "run_hour_by_year": {"2019": {"0": 365}, "2020": {"0": 200, "1": 166}},
                                 "rank_counts": {"0": 2180, "1": 10}, "lead_counts": {"5.0": 600, "4.0": 1590},
                                 "days_not_fed": len(unfed), "reasons": {"no_covering_run": len(unfed)},
                                 "longest_lead_h": longest, "reuse_lead_h": 75,
                                 "leads_at_or_above_reuse_bound": list(at_or_above),
                                 "days_fed_by_the_previous_days_run": 4,
                                 "error_vs_observed": {}, "error_last_utc": "2024-01-01T05:00:00+00:00"},
                     "evidence": [{"day": f"{d}T00:00:00", "reason": r} for d, r in unfed]
                     + [{"reused_day": "2022-03-04T00:00:00", "runtime": "2022-03-03T01:00:00+00:00"}]},
    }


def payloads(seed: int = 0):
    """v, dq, p, r, b, frozen, oos, diag: the runs a v2 report reads, v2-shaped."""
    g = Distinct()
    for _ in range(seed):
        g()
    oos = oos_frame()
    P = {}
    for name in (*KEYS, "baseline", "panel_persistence"):
        o = ci(g, 0.025, 0.08)
        P[name] = {"overall": {**o, "hours": int(g(30000, 36000)), "bias": {"mean": g(-0.01, 0.01)}},
                   "season": group_rows(g, SEASONS), "year": group_rows(g, range(2020, 2024)),
                   "hour": group_rows(g, range(24)), "decile": group_rows(g, range(1, 11))}
    Cv = {name: coverage_block(g) for name in ("relative", "relative_by_season", "absolute", "observed_relative")}
    for k in (PROD, SHIP):
        Cv[k] = {form: coverage_block(g) for form in ("relative", "absolute", "relative_by_season")}
    for k in ("gam_observed", "observed"):
        Cv[k] = {"relative": coverage_block(g)}

    def case(separated):
        return {"months": [3, 4, 5, 6], "year": 2020, "evaluable": True, "bias_shift_year": ci(g, -0.03, -0.01),
                "bias_other_years": ci(g, -0.01, 0.01), "separated": separated,
                "monthly": [{"key": f"2020-0{m}", "ape": g(0.03, 0.06), "rel_bias": g(-0.03, 0.0)} for m in (3, 4, 5, 6)]}

    peak = {"hours": [{"utc": f"2023-02-0{d}T23:00:00+00:00", "target": g(41000, 42000), "temp_c": g(-28, -24),
                       "temp_forecast": g(-29, -23), **{f"pred_{k}": g(31000, 40000) for k in KEYS},
                       "lower": g(28000, 29000), "upper": g(33000, 34000), "covered": False} for d in (3, 4)],
            "covered": 0, "evaluated": 2, "busiest_in_named_month": True, "named_month": "2023-02"}
    peak["hours"][1][f"pred_{FAM}"] = None  # a comparison model without a forecast for one of the hours
    # The first row's hour, forecast temperature and v2 forecast are those the soundness run breaks down (the
    # soundness world's busiest hour, report/soundness.py `fold_breakdown`): the report refuses a breakdown that is
    # not of the cold-peak table's first row.
    import replay_world as RW

    bd = RW.soundness_results()["breakdown"]
    peak["hours"][0] |= {"utc": bd["utc"], "temp_forecast": bd["temp_c"], f"pred_{PROD}": bd["forecast"]}
    cold_c = float(np.nanquantile(oos["temp_forecast"], 0.05))
    c = evaluate.contrasts(oos, cold_c, n_boot=200) if "vc" not in _CACHE else _CACHE["vc"]
    _CACHE["vc"] = c
    v = {
        "folds": {"first": "2020-01", "last": "2023-12", "count": 48, "embargo_days": 7, "train_rows_last_fold": 30000},
        "conformal": {"alpha": 0.1, "calibration_folds": 12, "first_fold": "2021-01"},
        "config": {"base_c": 18.0, "n_boot": 1000, "temperature_source": "primary station",
                   "hour_blocks": {"night": [0, 5], "morning": [6, 9], "day": [10, 15], "evening": [16, 20],
                                   "late": [21, 23]},
                   "cold_c": cold_c, "n_knots": 10, "degree": 3, "ridge_alpha": 1.0, "availability_lag_h": 2.0,
                   "sensitivity_lag_h": 6.0, "models": {k: ["gam" if k.startswith("gam") else "ols", k] for k in KEYS},
                   "production": PROD},
        "performance": P, "coverage": Cv,
        "compare": {k: (lambda x: {**x, "difference": x["mean"], "separated": True})(ci(g, -0.04, -0.01))
                    for k in ("persistence_vs_baseline", "observed_vs_persistence", "panel_vs_primary")},
        "cases": {"shift_2020": case(False), "shift_2020_persistence": case(True), "shift_2020_observed": case(True),
                  "peak": peak},
        "thresholds": {str(P_YEAR): thresholds(g, 2021, 2022, 1), str(R_YEAR): thresholds(g, 2021, 2023, 4)},
        "production": PROD,
        "contrasts": c | {"label": "in_sample_for_choice"},
        "verdict": evaluate.verdict(c) | {"label": "in_sample_for_choice"},
        "common_hours": {"hours": c["hours"], "oos_hours": len(oos), "no_target": 0,
                         PROD: {"no_covering_run": 1}, FAM: {"no_covering_run": 1}, SHIP: {}},
        "forecast_input": {"days": 1826, "fed_days": 1825, "days_not_fed": [{"day": "2021-02-03T00:00:00",
                                                                             "reason": "no_covering_run"}],
                           "reasons": {"no_covering_run": 1}, "rank_counts": {"0": 1800, "1": 25},
                           "lead_counts": {"4.0": 1200, "5.0": 625},
                           "run_hour_by_year": {"2019": {"0": 365}, "2020": {"0": 180, "1": 186}},
                           "error_vs_observed": {"2019": {s: {"mean_c": g(-1, 1), "mae_c": g(1, 3),
                                                              "hours": int(g(2000, 2200))} for s in SEASONS}},
                           "availability_lag_h": 2.0},
        "extrapolation": [{"fold": "2020-01", "train_min_c": -27.0, "train_max_c": 31.0, "hours": 2900,
                           "hours_below": 3, "hours_above": 0, "mape_outside": 0.05, "outside": []},
                          {"fold": "2023-02", "train_min_c": -29.0, "train_max_c": 32.0, "hours": 2600,
                           "hours_below": 7, "hours_above": 0, "mape_outside": 0.08, "outside": []}],
        "sensitivity": {"availability_6h": {"difference": 0.0012, "ci95_low": 0.0007, "ci95_high": 0.0018,
                                            "hours": 33000, "separated": True}},
    }

    def coefficients():
        rows = [{"feature": f"cell:weekday:{h:02d}", "frozen": g(10000, 20000), "mw_per_unit": g(1, 9)} for h in range(3)]
        rows += [{"feature": f"spline:{h:02d}:{k:02d}", "frozen": g(5, 60), "mw_per_unit": g(1, 9)}
                 for h in range(2) for k in range(2)]
        rows += [{"feature": "lag24", "frozen": g(0.5, 0.7), "mw_per_unit": g(0.4, 0.6)},
                 {"feature": "lag168", "frozen": g(0.1, 0.3), "mw_per_unit": g(0.1, 0.3)}]
        return rows

    frozen = {}
    for year in (P_YEAR, R_YEAR):
        frozen[year] = {"run_id": f"{year:032x}",
                        "params": {"train_last_day": f"{year - 1}-12-24", "q": str(g(0.08, 0.1)),
                                   "calibration_first_fold": f"{year - 1}-01", "calibration_last_fold": f"{year - 1}-12",
                                   "calibration_scores": str(int(g(8000, 8700)))},
                        "tags": {"coefficient_sha256": f"{year * 7:064x}"}, "coefficients": coefficients()}
    p_decisions = [{"week_start": f"{P_YEAR}-05-01", "recommendation": "retrain", "rule": 2,
                    "fired": [{"rule": 2, "evidence": [{"week_start": f"{P_YEAR}-04-24",
                                                        "stats": {"mape": 0.071, "rel_bias": 0.031, "covered": 0.71}}]}]}]
    r_decisions = [{"week_start": f"{R_YEAR}-11-04T00:00:00", "recommendation": "retrain", "rule": 2,
                    "fired": [{"rule": 2, "evidence": [{"week_start": f"{R_YEAR}-10-28",
                                                        "stats": {"mape": 0.0623, "rel_bias": 0.0124,
                                                                  "covered": 0.664}}]}]},
                   {"week_start": f"{R_YEAR}-12-09T00:00:00", "recommendation": "investigate_data", "rule": 1,
                    "fired": [{"rule": 1, "evidence": {"failed_days": [f"{R_YEAR}-12-10"]}}]},
                   {"week_start": f"{R_YEAR}-12-16T00:00:00", "recommendation": None, "rule": None, "fired": []}]
    p = replay(g, P_YEAR, 301, p_decisions, "different", "rehearsal")
    p["refitted"] = {"hours": int(g(8000, 8700)),
                     "mape": {"frozen": {"mean": g(0.03, 0.06)}, "refitted": {"mean": g(0.03, 0.06)}},
                     "bias": {"frozen": {"mean": g(-0.01, 0.01)}, "refitted": {"mean": g(-0.01, 0.01)}},
                     "coverage": {"frozen": {"mean": g(0.85, 0.93)}, "refitted": {"mean": g(0.85, 0.93)}},
                     "flag_weeks": {k: {f: int(g(0, 9)) for f in ("mape_high", "bias_high", "bias_low", "coverage_low")}
                                    for k in ("frozen", "refitted")},
                     "recommendations": {"frozen": {"retrain": 1}, "refitted": {"retrain": 2}},
                     "frozen_minus_refitted_ape": {**ci(g, -0.003, 0.003), "difference": g(-0.003, 0.003),
                                                   "separated": False}}
    p["weather_identity"] = {"hours": 8760, "max_abs_diff_c": 0.0, "days": 365, "run_or_reason_differences": 0}
    r = replay(g, R_YEAR, 402, r_decisions, "trim", "seen_by_developers")
    r["v1_reconciliation"] = {"ledger_run_id": V1_DEPARTED["run_id"], "ledger_mape": V1_DEPARTED["mape"],
                              "mape": V1_DEPARTED["mape"] * (1 + 3e-10), "mape_rel_diff": 3e-10,
                              "ledger_coverage": V1_DEPARTED["coverage"], "coverage": 0.8952}
    dq = {"run_id": "d" * 32,
          "sources": {"eccc": [{"centre": "Montréal", "primary": True, "first_retrieved_utc": "2026-10-13T10:00:00Z"},
                               {"centre": "Québec", "primary": False, "first_retrieved_utc": "2026-10-12T10:00:00Z"}],
                      "hydroquebec": {"retrieved_utc": "2026-10-11T09:00:00Z"},
                      # quality/run.py's `sources.nbm`: the fetch manifest of the window's years.
                      "nbm": {"station": "CYUL", "model": "NBS", "years": {
                          str(y): {"retrieved_utc": f"2026-10-1{5 + (y == 2024)}T16:3{y - 2019}:00Z", "rows": 30000 + y}
                          for y in range(2019, 2025)}}},
          "checks": {"demand_window": {"kind": "gate"}, "demand_bounds": {"kind": "gate"},
                     "demand_misplaced": {"kind": "repair", "evidence": [
                         {"moved_to_utc": "2022-01-01T05:00:00+00:00", "duplicated_utc": "2023-01-01T05:00:00+00:00"}]},
                     **nbm_checks(g)}}
    b = {"breaks": [{"group": x.group, "test": x.test, "fr": x.fr, "en": x.en, "control": x.control,
                     "outcome": "passed"} for x in BREAKS], "groups": {k: list(v) for k, v in GROUPS.items()}}
    diag = {"pseudo": diagnostics(g, P_YEAR, 501), "replay": diagnostics(g, R_YEAR, 602)}
    return v, dq, p, r, b, frozen, oos, diag


SOUND_RUN = "50" * 16  # the soundness run (report/soundness.py)
SOUND_YEARS = {"2021": "2023", "2022": "2024"}  # the synthetic world's two freezes, named as the chain's


def _relabel(x):
    """The synthetic world's freeze years renamed as the chain's: keys, the values of `year` and those of `years`."""
    def year(v):
        return int(SOUND_YEARS.get(str(v), v))

    if isinstance(x, dict):
        return {SOUND_YEARS.get(k, k): (year(v) if k == "year" and v is not None else
                                        [year(y) for y in v] if k == "years" else _relabel(v)) for k, v in x.items()}
    if isinstance(x, list):
        return [_relabel(v) for v in x]
    return x


def soundness(run_id: str = SOUND_RUN, shas: dict | None = None) -> dict:
    """The soundness run's results.json: report/soundness.py `measure` run on replay_world's soundness world (its
    numbers the measures' own), its two freezes named 2023 and 2024 as the chain's, their hashes `shas` (by year) or
    those of the payloads' frozen runs."""
    import copy

    import replay_world as RW

    out = _relabel(copy.deepcopy(RW.soundness_results()))
    shas = shas or {2023: f"{2023 * 7:064x}", 2024: f"{2024 * 7:064x}"}
    for year, sha in shas.items():
        out["freezes"][str(year)]["sha256"] = sha
    return out | {"run_id": run_id}


def dq_2025(g=None, revised=3, at_or_above=(), unfed=(("2025-02-03", "no_covering_run"),)):
    """The results of the through-2025 data-quality run the test of record read, and its revisions artifact."""
    g = g or Distinct()
    longest = {str(y): 71.0 for y in range(2019, 2026)}
    for y in at_or_above:
        longest[str(y)] = 76.0
    # As quality/run.py revised_records writes them; the first, a label that held two records and now holds one.
    rows = pd.DataFrame([{"utc": (pd.Timestamp("2024-01-02T10:00:00+00:00") + pd.Timedelta(hours=7 * i)).isoformat(),
                          "kind": ("changed", "added", "removed")[i % 3],
                          "old_mw": "" if i % 3 == 1 else f"2{i}000.0" + (";21000.0" if i == 0 else ""),
                          "new_mw": "" if i % 3 == 2 else f"2{i}100.0"}
                         for i in range(revised)], columns=["utc", "kind", "old_mw", "new_mw"])
    kinds = {k: int((rows["kind"] == k).sum()) for k in ("added", "removed", "changed")}
    results = {"run_id": "5" * 32, "checks": nbm_checks(g, at_or_above, unfed, longest),
               "revisions": {"hydroquebec": {"artifact": "revisions/hydroquebec.csv", "records": revised,
                                             "by_kind": kinds, "baseline": {}}}}
    return results, rows


# --- the 2025 starts -----------------------------------------------------------------------------------------------

FIRST_2024 = W.v2_entry("a" * 32, 2024, W.utc(0))


def record(run_id: str, started: str, verdict=("better", "better"), standing: str = "clean",
           evaluation: str | None = None, recorded: bool = False, gate: dict | None = None, diag_finished=True,
           departure_from=(), predecessors=(), coverage=(0.884, 0.901, 0.917)) -> dict:
    """A finished 2025 replay as the report shows it (`validation.record_view`): its entry's fields and, from its run,
    its results and its gate's record. coverage: the production model's coverage, its 95 % interval's bounds around
    its mean (low, mean, high)."""
    e = W.v2_entry(run_id, H_YEAR, started, evaluation=evaluation, standing=standing, verdict=verdict,
                   departure_from=departure_from, coverage=coverage[1])
    label = e["evaluation"]
    g = gate if gate is not None else W.gate(standing, ["a"] if standing == "post_hoc" else [],
                                             W.ruling("post_hoc") if standing == "post_hoc" else None)
    out = {**e, "recorded": recorded, "finished": True, "predecessors": list(predecessors)}
    if recorded:
        return out | {"results": None, "gate": None, "diag": None, "diag_finished": None}
    summary = {"mape": {"mean": e["mape"], "ci95_low": e["mape"] - 0.002, "ci95_high": e["mape"] + 0.002},
               "coverage": {"mean": coverage[1], "ci95_low": coverage[0], "ci95_high": coverage[2]}}
    return out | {"results": {"contrasts": contrasts_for(verdict, label), "verdict": dict(zip(("shipped", "family"),
                                                                                           verdict)) | {"label": label},
                              "summary": summary},
                  "gate": g, "diag": {**diagnostics(Distinct(), H_YEAR, 703), "run_id": "4" * 32} if diag_finished else None, "diag_finished": diag_finished,
                  "git_commit": "1" * 40}


def held(starts: list[dict], records: dict, findings=None, entries=None) -> dict:
    """The 2025 view (`validation.assemble_held`) of `starts`, walked by `ledger.of_record` over `entries` (by
    default FIRST_2024 and the records' entries), each finished start shown by its record."""
    if entries is None:
        entries = [FIRST_2024] + [{k: x[k] for k in ledger.V2_FIELDS} for x in records.values()]
    walk = ledger.of_record(entries, starts)
    rec = walk["record"]
    if rec["finished"]:
        shown = records[rec["run_id"]]
    else:
        shown = {"run_id": rec["run_id"], "started_utc": rec["started_utc"], "finished": False, "recorded": False,
                 "stage": rec["stage"], "error": rec["error"], "standing": rec["standing"], "gate": rec["gate"],
                 "verdict": None, "results": None, "holds_results": rec["holds_results"], "predecessors": []}
    reruns = [records[e["run_id"]] for e in walk["reruns"]]
    return RV.assemble_held(walk, shown, reruns, findings)


def findings(**kw) -> dict:
    results, rows = dq_2025(**kw)
    return RV.held_out_findings(results, rows if len(rows) else None)


def clean_record(verdict=("better", "better"), **kw):
    """One clean 2025 start that finished: the test of record."""
    r = record("b" * 32, W.utc(10), verdict, **kw)
    return held([W.start(r["run_id"], r["started_utc"], True, r["gate"])], {r["run_id"]: r}, findings())


_BASE: dict = {}


def context(held_view=None, rec24=None, rerun24=None, departures=(), dep_entries=(), diag=None, first24="rerun",
            **override) -> dict:
    """A context as `validation.context` builds it, from the payloads above through the real `derive`, the real
    2025 view (`assemble_held`) and the real v1 view. first24: v2's first 2024 replay as `validation.first_2024`
    names it from the ledger, beside the chain's own replay: "rerun" (FIRST_2024, and the chain's replay a rerun with
    the same printed results), "same" (the chain's replay is the first), or a view given whole."""
    v, dq, p, r, b, frozen, oos, d = payloads()
    diag = d if diag is None else diag
    key = tuple(sorted((k, x is None) for k, x in diag.items()))
    if key not in _BASE:
        _BASE[key] = RV.derive(v, dq, p, r, b, frozen, oos, diag)
    derived = _BASE[key]
    status = held_view["status"] if held_view else RV.status_of(None)
    runs = {k: {"id": f"{i + 1:032x}", "when": f"2026-10-14 0{i}:00 UTC"} for i, k in enumerate(
        ("dq", "validation", "frozen_2023", "frozen_2024", "pseudo_replay", "diag_pseudo_replay", "replay",
         "diag_replay", "breaks"))}
    runs["soundness"] = {"id": SOUND_RUN, "when": "2026-10-14 09:00 UTC"}
    for k, x in (("diag_pseudo_replay", diag.get("pseudo")), ("diag_replay", diag.get("replay"))):
        if x is None:  # a diagnostic run that did not finish is in no provenance
            runs.pop(k)
    if rec24 is not None:
        runs["replay_2024_record"] = {"id": rec24["run_id"], "when": "2026-11-01 00:00 UTC"}
    if held_view is not None:
        runs["replay_2025"] = {"id": held_view["record"]["run_id"], "when": "2026-11-11 00:00 UTC"}
    ctx = {"v": v, "dq": dq, "p": p, "r": r, "r_source": "chain" if rec24 is None else ("record" if rec24["results"] is not None else "rerun"),
           "r_id": runs["replay"]["id"], "b": b, "frozen": frozen, **derived, "diag": diag, "curves": curves(),
           "sound": _sound_view(),
           "departures": [dict(x) for x in departures], "departure_record": None, "dep_entries": list(dep_entries),
           "v1": RV.v1_view([RecordedReplay(V1_PLAIN), RecordedReplay(V1_DEPARTED)], r),
           "weekly_error": weekly_error(),
           "rec24": rec24, "rerun24": rerun24, "first24": first24_view(first24, runs["replay"]["id"]),
           "held": held_view, "status": status,
           "recommendation": held_view["recommendation"] if held_view else None,
           "store_only": bool((rec24 or {}).get("recorded")) or bool(held_view and held_view["recorded"]),
           "runs": runs, "code_sha": "c" * 64, "rules_sha": "d" * 64,
           "freeze": {"freeze_commit": W.F0, "freeze_tag": W.FREEZE_TAG}, "commit": "a" * 40, "dirty": False}
    ctx.update(override)
    RV.check_held_diagnostics(ctx["held"], ctx["sens"])
    return ctx


def first24_view(kind, chain_id: str):
    """v2's first 2024 replay, through the real `first_2024` on a ledger holding FIRST_2024 and the chain's own
    replay's entry (the same printed results, or, for "same", the chain's replay itself the first)."""
    if not isinstance(kind, str):
        return kind
    chain = {**FIRST_2024, "run_id": chain_id, "started_utc": W.utc(1) if kind == "rerun" else W.utc(0)}
    entries = [chain] if kind == "same" else [FIRST_2024, chain]
    return RV.first_2024(entries, W.FREEZE_TAG, chain)


def _sound_view() -> dict:
    """The soundness view `validation.context` builds from the soundness run (report/soundness.py `view`)."""
    from valiwatt.report import soundness as SN

    return SN.view(soundness(), SOUND_RUN)


FIG = {k: f"figures/validation/{k}.png" for k in (
    "validation_soundness_slopes",
    "validation_monthly_mape", "validation_coverage_by_season", "validation_peak_hours", "validation_partial_effects",
    "pseudo_replay_2023_weekly", "replay_2024_weekly", "replay_2024_drift")}


def attribution(lang: str) -> list[str]:
    from valiwatt import attribution as A

    return A.lines(lang, retrieved="2026-10-13")


def render(ctx: dict, lang: str, doc: str = "validation", check: bool = True) -> str:
    """A page rendered as `render_validation` renders it, and checked as it checks it."""
    from valiwatt.report.render import environment

    env = environment(lang)
    links = {}
    if doc == "story":
        links = RV.anchors(render(ctx, lang, "validation", check=False))
    text = env.get_template(f"{doc}.{lang}.md.j2").render(**ctx, fig=FIG, lang=lang, say=RV.SAY[lang],
                                                          attribution=attribution(lang), anchors=links)
    if check:
        RV.check_rendered(text, lang, ctx, doc, RV.status_sentence(env, ctx, lang))
    return text


# --- the 2025 states a report can be rendered in -----------------------------------------------------------------

def stopped_then_record(verdict=("better", "better"), outcome="clean") -> dict:
    """A first start that stopped, then the start the owner's ruling naming it let run: the test of record."""
    s0 = W.start("c" * 32, W.utc(9), finished=False, gate_record=W.gate("clean"), stage="replayed",
                 error="MemoryError")
    g = W.gate(outcome, ["f"], W.ruling(outcome, start=s0["started_utc"]))
    r = record("b" * 32, W.utc(10), verdict, standing=outcome, gate=g)
    return held([s0, W.start(r["run_id"], r["started_utc"], True, g)], {r["run_id"]: r}, findings())


def unfinished_record() -> dict:
    s0 = W.start("b" * 32, W.utc(10), finished=False, gate_record=W.gate("clean"), stage="inputs_read",
                 error="KeyboardInterrupt")
    return held([s0], {}, findings())


def record_and_reruns() -> dict:
    """A clean test of record, a later start that did not finish, and a later rerun that finished with other
    verdicts."""
    r = record("b" * 32, W.utc(10), ("better", "better"))
    x = W.start("e" * 32, W.utc(11), finished=False, gate_record=W.gate("clean"), stage="opened", error="OSError")
    again = record("d" * 32, W.utc(12), ("better", "not_shown_better"), evaluation="seen_by_developers")
    starts = [W.start(r["run_id"], r["started_utc"], True, r["gate"]), x,
              W.start(again["run_id"], again["started_utc"], True, again["gate"])]
    return held(starts, {r["run_id"]: r, again["run_id"]: again}, findings())


def recorded_record() -> dict:
    """The test of record read from the ledger: the store lacks it."""
    r = record("b" * 32, W.utc(10), ("better", "better"), recorded=True)
    e = {k: r[k] for k in ledger.V2_FIELDS}
    return held([W.from_entry(e)], {r["run_id"]: r}, None)


def rec24_view(recorded: bool = False) -> dict:
    """v2's first 2024 replay, the 2024 replay of record, as `view_2024` shows it."""
    out = {**FIRST_2024, "recorded": recorded, "predecessors": []}
    if recorded:
        return out | {"results": None, "gate": None, "diag": None, "diag_finished": None}
    return out | {"results": {}, "gate": None, "diag": None, "diag_finished": False, "git_commit": "1" * 40}


def rerun24_view() -> dict:
    """The chain's own 2024 replay beside the 2024 replay of record, a rerun (`view_2024`)."""
    e = W.v2_entry(f"{6:032x}", 2024, W.utc(1), mape=0.0335)
    return {**e, "recorded": False, "results": {}, "gate": None, "diag": None, "diag_finished": False,
            "git_commit": "1" * 40, "differences": RV.differences(e, FIRST_2024)}


STATES = {
    "none": lambda: {},
    "confirmed": lambda: {"held_view": clean_record()},
    "input_only": lambda: {"held_view": clean_record(("better", "not_shown_better"))},
    "not_confirmed": lambda: {"held_view": clean_record(("worse", "better"))},
    "cold_untested": lambda: {"held_view": clean_record(("better_cold_untested", "better_cold_untested"))},
    "stopped_ruled": lambda: {"held_view": stopped_then_record()},
    "post_hoc": lambda: {"held_view": stopped_then_record(outcome="post_hoc")},
    "unfinished": lambda: {"held_view": unfinished_record()},
    "reruns": lambda: {"held_view": record_and_reruns()},
    "recorded": lambda: {"held_view": recorded_record(), "rec24": rec24_view(True), "rerun24": rerun24_view()},
}


def state(name: str) -> dict:
    """The context of a report in one of STATES; once a 2025 start exists, with v2's first 2024 replay as the 2024
    replay of record."""
    kw = STATES[name]()
    if kw.get("held_view") is not None and "rec24" not in kw:
        kw["rec24"] = rec24_view()
    return context(**kw)
