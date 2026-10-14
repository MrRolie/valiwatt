"""`valiwatt replay --year Y`: replay a year with its frozen model and log every daily and weekly judgement.

- Y = 2023 is the pseudo-replay: design years only (no 2024 record is loaded), thresholds from validation weeks up
  to 2022, and a side-by-side with the monthly-refitted 2023 folds of validation.
- Y = 2024 is the replay proper. It refuses to run unless the rule files are exactly those of the `rules-frozen` tag,
  and the validation and frozen-model runs it reads were made with those same rules.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from valiwatt.config import DEMAND_LAST, DESIGN_LAST, stations
from valiwatt.forecast.evaluate import errors, mean_with_ci, paired_difference
from valiwatt.forecast.freeze import load_frozen
from valiwatt.forecast.validate import LineageError, load_inputs, load_validation, validation_results
from valiwatt.monitor.drift import evidently_snapshot, normed_wasserstein, seasonal_reference
from valiwatt.monitor.replay import MonitorActedError, ReplayResult, replay
from valiwatt.monitor.rules import FREEZE_TAG, RULE_FILES, decide, rules_sha256, week_record
from valiwatt.paths import REPO_ROOT
from valiwatt.quality.checks import jsonable

EXPERIMENT = "valiwatt-monitoring"
PSEUDO_YEAR, REPLAY_YEAR = 2023, 2024
N_BOOT = 1000


class FreezeError(RuntimeError):
    pass


def _git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False)


def check_freeze() -> dict:
    """The rule files must equal the tagged freeze, byte for byte, before the 2024 replay may run."""
    commit = _git("rev-list", "-n", "1", FREEZE_TAG).stdout.strip()
    if not commit:
        raise FreezeError(f"no `{FREEZE_TAG}` tag: freeze the monitoring rules in a named commit first")
    tagged = rules_sha256(lambda p: subprocess.run(["git", "show", f"{commit}:{p}"], cwd=REPO_ROOT,
                                                   capture_output=True, check=True).stdout)
    if tagged != rules_sha256():
        changed = _git("diff", "--name-only", commit, "--", *RULE_FILES).stdout.split()
        raise FreezeError(f"rule files differ from `{FREEZE_TAG}` ({commit[:12]}): {changed}")
    return {"freeze_tag": FREEZE_TAG, "freeze_commit": commit, "rules_sha256": tagged}


def load_raw():
    from valiwatt.quality.run import _verify_raw
    from valiwatt.sources import eccc, hydroquebec

    _verify_raw()
    primary = next(s for s in stations() if s.primary)
    return hydroquebec.load_raw(), eccc.load_station(primary)


def summarize(r: ReplayResult, n_boot: int = N_BOOT) -> dict:
    e = errors(r.hourly.assign(fold=r.hourly["day"].dt.to_period("M").astype(str)), "forecast")
    e["covered"] = r.hourly["covered"].reindex(e.index)
    recs = [d["recommendation"] for d in r.decisions if d["recommendation"]]
    return {
        "mape": mean_with_ci(e, "ape", n_boot), "bias": mean_with_ci(e, "rel_bias", n_boot),
        "coverage": mean_with_ci(e.dropna(subset=["covered"]), "covered", n_boot),
        "hours_scored": int(len(e)), "hours_issued": int(r.hourly["forecast"].notna().sum()),
        "days": len(r.days), "days_failed": sum(d["failed"] for d in r.days),
        "weeks": len(r.weeks), "weeks_evaluated": sum(w["evaluated"] for w in r.weeks),
        "recommendations": {k: recs.count(k) for k in sorted(set(recs))},
        "flag_weeks": _flag_weeks(r.weeks),
        "two_week_rule_alone": two_week_rule_alone(r.weeks),
    }


def two_week_rule_alone(weeks: list[dict]) -> list[dict]:
    """Sensitivity: the decisions the two-week rule would give without the most-days condition (never acted on)."""
    out = []
    for i, w in enumerate(weeks):
        d = decide(w, weeks[i - 1] if i else None, min_breach_days=0)
        if d["recommendation"]:
            out.append(d)
    return out


def _flag_weeks(weeks: list[dict]) -> dict:
    out = {"mape_high": 0, "bias_out": 0, "coverage_low": 0, "temperature_drift": 0, "forecast_drift": 0}
    for w in weeks:
        if w["evaluated"]:
            for k, v in w["flags"].items():
                out[k] += int(v)
        out["temperature_drift"] += int(w["drift_flags"]["temperature"])
        out["forecast_drift"] += int(w["drift_flags"]["forecast"])
    return out


def compare_refitted(r: ReplayResult, oos: pd.DataFrame, intervals: pd.DataFrame, thresholds: dict,
                     reference: pd.DataFrame, year: int, n_boot: int = N_BOOT) -> dict:
    """The frozen model against validation's monthly-refitted folds on the same year: errors, coverage, alarms."""
    ref_year = oos[oos["fold"].str.startswith(str(year))]
    frozen = errors(r.hourly.assign(fold=r.hourly["day"].dt.to_period("M").astype(str)), "forecast")
    refit = errors(ref_year, "pred_persistence")
    refit["covered"] = intervals["covered"].reindex(refit.index)
    common = frozen.index.intersection(refit.index)
    frozen, refit = frozen.loc[common], refit.loc[common]
    frozen["covered"] = r.hourly["covered"].reindex(common)

    weeks, decisions = [], []
    days = {d["day"]: d for d in r.days}
    by_week = {w["week_start"]: w for w in r.weeks}
    for ws in by_week:
        in_week = (refit["day"] >= ws) & (refit["day"] < ws + pd.Timedelta(days=7))
        part = refit[in_week].dropna(subset=["ape", "covered"])
        fc = ref_year.loc[ref_year["day"].ge(ws) & ref_year["day"].lt(ws + pd.Timedelta(days=7)), "pred_persistence"]
        drift = {"temperature": by_week[ws]["drift"]["temperature"],
                 "forecast": normed_wasserstein(fc.to_numpy(), seasonal_reference(reference["forecast"],
                                                                                  reference["day"], ws))}
        wd = [days[d] for d in sorted(days) if ws <= d < ws + pd.Timedelta(days=7)]
        w = week_record(ws, part, wd, drift, thresholds)
        decisions.append(decide(w, weeks[-1] if weeks else None))
        weeks.append(w)
    recs = lambda ds: {k: [d["recommendation"] for d in ds].count(k)  # noqa: E731
                       for k in sorted({d["recommendation"] for d in ds if d["recommendation"]})}
    return {
        "hours": int(len(common)),
        "mape": {"frozen": mean_with_ci(frozen, "ape", n_boot), "refitted": mean_with_ci(refit, "ape", n_boot)},
        "frozen_minus_refitted_ape": paired_difference(frozen, refit, "ape", n_boot),
        "bias": {"frozen": mean_with_ci(frozen, "rel_bias", n_boot), "refitted": mean_with_ci(refit, "rel_bias", n_boot)},
        "coverage": {"frozen": mean_with_ci(frozen.dropna(subset=["covered"]), "covered", n_boot),
                     "refitted": mean_with_ci(refit.dropna(subset=["covered"]), "covered", n_boot)},
        "flag_weeks": {"frozen": _flag_weeks(r.weeks), "refitted": _flag_weeks(weeks)},
        "recommendations": {"frozen": recs(r.decisions), "refitted": recs(decisions)},
        "refitted_decisions": decisions,
        "refitted_two_week_rule_alone": two_week_rule_alone(weeks),
    }


def run_replay(year: int, n_boot: int = N_BOOT) -> int:
    import mlflow

    from valiwatt.tracking import client, start_run

    if year not in (PSEUDO_YEAR, REPLAY_YEAR):
        raise ValueError(f"replay years are {PSEUDO_YEAR} (pseudo-replay) and {REPLAY_YEAR}")
    freeze = check_freeze() if year == REPLAY_YEAR else {"freeze_tag": "", "freeze_commit": "",
                                                         "rules_sha256": rules_sha256()}
    dq, _, _ = load_inputs()
    val, oos = load_validation(dq)
    thresholds = validation_results(val)["thresholds"][str(year)]
    frozen, frozen_run = load_frozen(year)
    if frozen_run.data.tags.get("validation_run_id") != val.info.run_id:
        raise LineageError(f"frozen model run {frozen_run.info.run_id} was built on another validation run; "
                           f"run `valiwatt freeze --year {year}` again")
    if frozen_run.data.tags.get("rules_sha256") != freeze["rules_sha256"]:
        raise LineageError(f"frozen model run {frozen_run.info.run_id} was made under other rules")
    raw_demand, raw_temp = load_raw()
    data_end = DESIGN_LAST if year == PSEUDO_YEAR else DEMAND_LAST
    reference = oos.loc[oos["day"] < pd.Timestamp(f"{year}-01-01"), ["day"]].assign(
        forecast=oos["pred_persistence"])
    r = replay(raw_demand, raw_temp, frozen, thresholds, reference, f"{year}-01-01", f"{year}-12-31", data_end)

    again, _ = load_frozen(year)
    if again.sha256() != r.model_sha256 or again.sha256() != frozen_run.data.tags["coefficient_sha256"]:
        raise MonitorActedError("the registered frozen model changed during the replay")

    results = {"year": year, "kind": "pseudo_replay" if year == PSEUDO_YEAR else "replay",
               "data_end": data_end, "clock_end": r.clock_end, "summary": summarize(r, n_boot),
               "days": r.days, "weeks": r.weeks, "decisions": r.decisions, "thresholds": thresholds,
               "model": {"sha256": r.model_sha256, "q": frozen.q, "train_last_day": frozen.train_last_day}, **freeze}
    if year == PSEUDO_YEAR:
        client()
        intervals = pd.read_parquet(mlflow.artifacts.download_artifacts(
            run_id=val.info.run_id, artifact_path="frames/intervals.parquet"))
        results["refitted"] = compare_refitted(r, oos, intervals, thresholds, reference, year, n_boot)

    with start_run(EXPERIMENT, results["kind"], tags={
        "kind": results["kind"], "year": str(year), "validation_run_id": val.info.run_id,
        "frozen_run_id": frozen_run.info.run_id, "dq_run_id": dq.info.run_id,
        "coefficient_sha256": r.model_sha256, "rules_sha256": freeze["rules_sha256"],
        "freeze_commit": freeze["freeze_commit"],
    }) as run:
        s = results["summary"]
        mlflow.log_metrics({"mape": s["mape"]["mean"], "bias": s["bias"]["mean"], "coverage": s["coverage"]["mean"],
                            "days_failed": s["days_failed"], **{f"rec.{k}": v for k, v in s["recommendations"].items()}})
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            r.hourly.to_parquet(tmp / "hourly.parquet")
            mlflow.log_artifact(str(tmp / "hourly.parquet"), artifact_path="frames")
            checks = []
            for ws, sample in r.drift_samples.items():
                for name, (cur, ref) in sample.items():
                    ours = normed_wasserstein(cur, ref)
                    theirs, snap = evidently_snapshot(cur, ref, thresholds["drift"][f"{name}_high"], column=name)
                    if not np.isclose(ours, theirs, rtol=1e-6):
                        raise RuntimeError(f"drift statistic disagrees with Evidently for {name} {ws.date()}: "
                                           f"{ours} vs {theirs}")
                    path = tmp / f"evidently-{ws.date()}-{name}.html"
                    snap.save_html(str(path))
                    checks.append({"week_start": ws, "name": name, "ours": ours, "evidently": theirs})
            for p in sorted(tmp.glob("evidently-*.html")):
                mlflow.log_artifact(str(p), artifact_path="evidently")
            results["evidently_checks"] = checks
        results["run_id"] = run.info.run_id
        mlflow.log_text(json.dumps(jsonable(results), indent=2, ensure_ascii=False), "results/replay.json")
        mlflow.set_tag("status", "complete")
    rec = s["recommendations"]
    print(f"{results['kind']} {year}: run {run.info.run_id}; MAPE {100 * s['mape']['mean']:.2f} %, "
          f"coverage {100 * s['coverage']['mean']:.1f} %, failed days {s['days_failed']}, recommendations {rec or 'none'}")
    return 0
