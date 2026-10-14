"""Render the validation report (French first, English derived) into docs/report/, from the chain of tracked runs.

The report is committed (owner's ruling). It carries a stamp (docs/report/stamp.json): the run IDs it was rendered
from, the code hash, the rules hash and the hash of every rendered file. `tests/test_report_stamp.py` fails when the
committed code no longer matches the stamp, or a rendered file no longer matches its hash, so a report can never sit
beside code that did not produce it, nor be edited by hand. The data-quality report is rendered from the chain's own
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
from valiwatt.report.lineage import Chain, follow
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


def _boot_means(d: pd.DataFrame, value: str, n_boot: int, seed: int) -> np.ndarray:
    """The draws behind `evaluate.mean_with_ci`: whole weeks resampled with the same weights and seed."""
    g = d.groupby("week")[value].agg(["sum", "count"])
    W = _weights(len(g), n_boot, seed)
    return (W @ g["sum"].to_numpy()) / (W @ g["count"].to_numpy())


def shift_difference(oos: pd.DataFrame, pred_col: str, n_boot: int) -> dict:
    """Spring of SHIFT_YEAR minus the same months of the other validation years: the difference of the mean signed
    bias, with a 95 % interval from the code's week-block bootstrap, each side resampled on its own weeks (the two
    sides share no hour, so no paired difference exists). Computed at render time, after the results."""
    from valiwatt.forecast.validate import SHIFT_MONTHS, SHIFT_YEAR

    e = errors(oos, pred_col)
    months = pd.to_datetime(e["day"]).dt.month.isin(SHIFT_MONTHS)
    a, b = e[months & (e["year"] == SHIFT_YEAR)], e[months & (e["year"] != SHIFT_YEAR)]
    if a.empty or b.empty:
        raise ReportRefused(f"the validation frame holds no spring {SHIFT_YEAR} hours or no other spring")
    diff = _boot_means(a, "rel_bias", n_boot, 0) - _boot_means(b, "rel_bias", n_boot, 1)
    lo, hi = np.quantile(diff, [0.025, 0.975])
    return {"difference": float(a["rel_bias"].mean() - b["rel_bias"].mean()), "ci95_low": float(lo),
            "ci95_high": float(hi), "separated": bool(printed(lo, 2) > 0 or printed(hi, 2) < 0)}


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
    """The largest level shift measured on the rehearsal year: rises missed and falls caught within the horizon."""
    rows = p["sensitivity"]["level_shifts"]
    up = [x for x in rows if abs(x["shift"] - size) < 1e-9]
    down = [x for x in rows if abs(x["shift"] + size) < 1e-9]
    return {"size": size, "up_n": len(up), "up_missed": sum(x["weeks"] is None for x in up),
            "down_n": len(down), "down_caught": sum(x["weeks"] is not None for x in down),
            "months": [pd.Timestamp(x["start"]).month for x in up]}


def derive(v: dict, dq: dict, p: dict, r: dict, b: dict, frozen: dict, oos: pd.DataFrame) -> dict:
    """Everything the templates read that is computed from the runs rather than printed as stored. Pure: no store."""
    from valiwatt.breaks import SYNTHETIC_WORLD
    from valiwatt.config import LOCAL_TZ

    for row in b["breaks"]:
        if "control" not in row:
            raise ReportRefused("the breaks run does not say which breaks are controls; run `valiwatt breaks` again")
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
                  "decile": coverage_sides(C["relative"]["decile"], nominal),
                  "overall": coverage_sides([{"key": "all", "covered_low": C["relative"]["overall"]["ci95_low"],
                                              "covered_high": C["relative"]["overall"]["ci95_high"]}], nominal)},
        "limits": {"replay": detection_limits(r["power"]), "pseudo": detection_limits(p["power"])},
        "near": {k: near_misses(x["summary"]["retrain_under_two_week_rule_alone"], x["weeks"], x["decisions"])
                 for k, x in (("replay", r), ("pseudo", p))},
        "shift_diff": {"persistence": shift_difference(oos, "pred_persistence", v["config"]["n_boot"]),
                       "observed": shift_difference(oos, "pred_observed", v["config"]["n_boot"])},
        "drift_years": drift_reference_years(th24["first_year"], th24["last_year"], r["year"], v["folds"]["first"]),
        "level": level_shift(p, K["shift"]),
        "peak_above": sum(h["upper"] is not None and h["target"] > h["upper"] for h in peak),
        "peak_in_month": bool(peak) and all(
            pd.Timestamp(h["utc"]).tz_convert(LOCAL_TZ).strftime("%Y-%m") == v["cases"]["peak"]["named_month"]
            for h in peak),
        "world": SYNTHETIC_WORLD,
    }


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
    return {
        "v": v, "dq": dq, "p": p, "r": r, "b": b, "frozen": frozen,
        **derive(v, dq, p, r, b, frozen, oos),
        "departures": [{"id": run.info.run_id, "freeze_commit": run.data.tags.get("freeze_commit", ""),
                        "summary": _json(c, run, "results/replay.json")["summary"]} for run in chain.departed],
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
    stamp = {"code_sha256": code_sha, "rules_sha256": rules_sha, "freeze_commit": chain.freeze["freeze_commit"],
             "runs": {k: x["id"] for k, x in ctx["runs"].items()}, "files": file_hashes()}
    STAMP.write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
    print("rendered " + ", ".join(p.relative_to(REPO_ROOT).as_posix() for p in written)
          + f" from replay run {chain.replay.info.run_id}")
    return written
