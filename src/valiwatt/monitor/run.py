"""`valiwatt replay --year Y`: replay a year with its frozen model and log every daily and weekly judgement.

- Y = 2023 is the pseudo-replay: design years only (records after the end of 2023 are cut before the replay's feeds
  are built), thresholds from validation weeks up to 2022, and a side-by-side with the monthly-refitted 2023 folds.
- Y = 2024 is the replay proper. It refuses to run unless the rule files are exactly those of the `rules-frozen` tag,
  the validation and frozen-model runs it reads were made with those same rules, and no earlier 2024 replay ran
  under another freeze commit (a moved tag is a departure, run only with `--departure` once it is recorded).
Both refuse raw files that differ from the ones the data-quality run checked.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from valiwatt.config import DEMAND_LAST, DESIGN_LAST, stations
from valiwatt.forecast.evaluate import SEASONS, errors, mean_with_ci, paired_difference
from valiwatt.forecast.freeze import load_frozen
from valiwatt.forecast.validate import LineageError, load_inputs, load_validation, validation_results
from valiwatt.monitor.drift import evidently_snapshot, normed_wasserstein, seasonal_reference
from valiwatt.monitor.replay import MonitorActedError, ReplayResult, replay
from valiwatt.monitor.rules import (
    FREEZE_TAG,
    MIN_WEEK_HOURS,
    RETRAIN,
    decide,
    rule_files,
    rules_sha256,
    two_week_rule_alone,
    week_record,
)
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
    """The rule files must equal the tagged freeze, names and bytes, before the 2024 replay may run."""
    commit = _git("rev-list", "-n", "1", FREEZE_TAG).stdout.strip()
    if not commit:
        raise FreezeError(f"no `{FREEZE_TAG}` tag: freeze the monitoring rules in a named commit first")
    tagged = rules_sha256(commit)
    if tagged != rules_sha256():
        then, now = set(rule_files(commit)), set(rule_files())
        changed = sorted((then ^ now) | {p for p in then & now if _git("diff", "--quiet", commit, "--", p).returncode})
        raise FreezeError(f"rule files differ from `{FREEZE_TAG}` ({commit[:12]}): {changed}")
    return {"freeze_tag": FREEZE_TAG, "freeze_commit": commit, "rules_sha256": tagged}


def load_raw(dq):
    """The raw records, refused unless they are the files the data-quality run `dq` checked."""
    from valiwatt.quality.run import _verify_raw, eccc_manifests_sha256
    from valiwatt.sources import eccc, hydroquebec

    hq, stations_manifests = _verify_raw()
    if hq["sha256"] != dq.data.params.get("hq_sha256"):
        raise LineageError(f"the Hydro-Québec file differs from the one data-quality run {dq.info.run_id} checked")
    if eccc_manifests_sha256(stations_manifests) != dq.data.params.get("eccc_manifests_sha256"):
        raise LineageError(f"the ECCC files differ from the ones data-quality run {dq.info.run_id} checked")
    primary = next(s for s in stations() if s.primary)
    return hydroquebec.load_raw(), eccc.load_station(primary)


def check_lineage(year: int, val, frozen_run, freeze: dict) -> None:
    """The frozen model must come from this validation run, under these rules, for this year."""
    if frozen_run.data.tags.get("validation_run_id") != val.info.run_id:
        raise LineageError(f"frozen model run {frozen_run.info.run_id} was built on another validation run; "
                           f"run `valiwatt freeze --year {year}` again")
    if frozen_run.data.tags.get("rules_sha256") != freeze["rules_sha256"]:
        raise LineageError(f"frozen model run {frozen_run.info.run_id} was made under other rules")
    if frozen_run.data.tags.get("replay_year") != str(year):
        raise LineageError(f"frozen model run {frozen_run.info.run_id} was frozen for another year")


DEPARTURES_HEADING = "## Departures from the frozen monitoring rules"


def recorded_departures(agents: str | None = None) -> str:
    text = agents if agents is not None else (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    if DEPARTURES_HEADING not in text:
        return ""
    section = text.split(DEPARTURES_HEADING, 1)[1]
    return section.split("\n## ", 1)[0]


def check_tag_not_moved(freeze: dict, earlier: dict[str, list[str]], departure: bool,
                        recorded: str | None = None) -> list[str]:
    """A 2024 replay already ran under another freeze commit: the tag moved, which is a departure. It runs only with
    `departure` AND the new freeze commit named in AGENTS.md's departures section. Returns the earlier run IDs."""
    moved = {c: ids for c, ids in earlier.items() if c != freeze["freeze_commit"]}
    if not moved:
        return []
    if not departure:
        raise FreezeError(f"earlier 2024 replays ran under freeze commits {[c[:12] or '(none)' for c in moved]}, not "
                          f"{freeze['freeze_commit'][:12]}: moving the tag is a departure; record it in AGENTS.md and "
                          "rerun with --departure")
    section = recorded_departures() if recorded is None else recorded
    if freeze["freeze_commit"][:12] not in section:
        raise FreezeError(f"--departure given, but AGENTS.md's departures section does not name commit "
                          f"{freeze['freeze_commit'][:12]}")
    return sorted(i for ids in moved.values() for i in ids)


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
        "retrain_under_two_week_rule_alone": retrain_under_two_week_rule_alone(r.weeks),
    }


def retrain_under_two_week_rule_alone(weeks: list[dict]) -> list[dict]:
    """Sensitivity, never acted on: the weeks where the plan's first wording of the retrain rule (weekly flags only,
    any mix, two weeks running) would have said retrain, with both weeks' statistics."""
    out = []
    for i, w in enumerate(weeks):
        prev = weeks[i - 1] if i else None
        if two_week_rule_alone(w, prev):
            out.append({"week_start": w["week_start"],
                        "weeks": [{"week_start": x["week_start"], "stats": x["stats"], "flags": x["flags"],
                                   "trimmed": x["trimmed"]} for x in (prev, w)]})
    return out


POWER_BIASES = (-0.08, -0.06, -0.04, -0.02, 0.02, 0.04, 0.06, 0.08)
POWER_HORIZON_WEEKS = 8


def weekly_decisions(hourly: pd.DataFrame, r: ReplayResult, thresholds: dict) -> tuple[list[dict], list[dict]]:
    """Week records and decisions recomputed from an hourly frame, with the replay's own days and drift values."""
    weeks, decisions = [], []
    for w0 in r.weeks:
        ws = w0["week_start"]
        part = hourly[(hourly["day"] >= ws) & (hourly["day"] < ws + pd.Timedelta(days=7))]
        days = [d for d in r.days if ws <= d["day"] < ws + pd.Timedelta(days=7)]
        w = week_record(ws, part.dropna(subset=["ape", "covered"]), days, w0["drift"], thresholds)
        decisions.append(decide(w, weeks[-1] if weeks else None, weeks[-2] if len(weeks) > 1 else None))
        weeks.append(w)
    return weeks, decisions


def detection_power(r: ReplayResult, thresholds: dict, biases=POWER_BIASES,
                    horizon: int = POWER_HORIZON_WEEKS) -> list[dict]:
    """How fast rule 2 catches a sustained shift: from the first full week of each season, demand runs a fraction b
    above the forecast (forecast and interval divided by 1 + b), and the weekly rules are run again on the replay's
    hours. Reported: the weeks until *retrain*, or None within `horizon` weeks. Never acted on; it measures the
    rules' power on this year's errors."""
    starts = []
    for month in (1, 4, 7, 10):
        cands = [w["week_start"] for w in r.weeks if w["evaluated"] and w["week_start"].month == month]
        if cands:
            starts.append(min(cands))
    out = []
    for start in starts:
        for b in biases:
            h = r.hourly.copy()
            late = (h["day"] >= start).to_numpy()
            for col in ("forecast", "lower", "upper"):
                h.loc[late, col] = h.loc[late, col] / (1 + b)
            ok = h["target"].notna() & h["forecast"].notna()
            h["ape"] = ((h["target"] - h["forecast"]).abs() / h["target"]).where(ok)
            h["rel_bias"] = ((h["target"] - h["forecast"]) / h["target"]).where(ok)
            h["covered"] = ((h["target"] >= h["lower"]) & (h["target"] <= h["upper"])).astype(float).where(ok)
            _, decisions = weekly_decisions(h, r, thresholds)
            hit = [d["week_start"] for d in decisions if d["recommendation"] == RETRAIN
                   and start <= d["week_start"] < start + pd.Timedelta(weeks=horizon)]
            out.append({"start": start, "season": SEASONS[(start + pd.Timedelta(days=3)).month], "bias": b,
                        "weeks_to_retrain": int((hit[0] - start).days // 7 + 1) if hit else None})
    return out


def _flag_weeks(weeks: list[dict]) -> dict:
    out = {"mape_high": 0, "bias_high": 0, "bias_low": 0, "coverage_low": 0, "temperature_drift": 0,
           "forecast_drift": 0}
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
        wd = [days[d] for d in sorted(days) if ws <= d < ws + pd.Timedelta(days=7)]
        if len(wd) == 7:
            drift = {"temperature": by_week[ws]["drift"]["temperature"],
                     "forecast": normed_wasserstein(fc.to_numpy(), seasonal_reference(reference["forecast"],
                                                                                      reference["day"], ws),
                                                    MIN_WEEK_HOURS)}
        else:  # a partial week: quiet, as in the replay
            drift = {"temperature": float("nan"), "forecast": float("nan")}
        w = week_record(ws, part, wd, drift, thresholds)
        decisions.append(decide(w, weeks[-1] if weeks else None, weeks[-2] if len(weeks) > 1 else None))
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
        "refitted_retrain_under_two_week_rule_alone": retrain_under_two_week_rule_alone(weeks),
    }


def earlier_freeze_commits(year: int, client=None) -> dict[str, list[str]]:
    """The freeze commit of every earlier replay of `year`, deleted runs included, with their run IDs."""
    from mlflow.entities import ViewType

    from valiwatt import tracking

    c = client or tracking.client()
    exp = c.get_experiment_by_name(EXPERIMENT)
    if exp is None:
        return {}
    out: dict[str, list[str]] = {}
    for r in c.search_runs([exp.experiment_id], f"tags.kind = 'replay' and tags.year = '{year}'",
                           run_view_type=ViewType.ALL, max_results=10_000):
        out.setdefault(r.data.tags.get("freeze_commit", ""), []).append(r.info.run_id)
    return out


def check_unchanged(registered_sha: str, replayed_sha: str, run_sha: str) -> None:
    """After the replay, the registered model must still be the one the replay used and its run recorded."""
    if not (registered_sha == replayed_sha == run_sha):
        raise MonitorActedError("the registered frozen model changed during the replay")


def run_replay(year: int, n_boot: int = N_BOOT, departure: bool = False) -> int:
    import mlflow

    from valiwatt.tracking import client, start_run

    if year not in (PSEUDO_YEAR, REPLAY_YEAR):
        raise ValueError(f"replay years are {PSEUDO_YEAR} (pseudo-replay) and {REPLAY_YEAR}")
    departure_from: list[str] = []
    if year == REPLAY_YEAR:
        freeze = check_freeze()
        departure_from = check_tag_not_moved(freeze, earlier_freeze_commits(year), departure)
    else:
        freeze = {"freeze_tag": "", "freeze_commit": "", "rules_sha256": rules_sha256()}
    dq, _, _ = load_inputs()
    val, oos = load_validation(dq)
    thresholds = validation_results(val)["thresholds"][str(year)]
    frozen, frozen_run = load_frozen(year)
    check_lineage(year, val, frozen_run, freeze)
    raw_demand, raw_temp = load_raw(dq)
    data_end = DESIGN_LAST if year == PSEUDO_YEAR else DEMAND_LAST
    reference = oos.loc[oos["day"] < pd.Timestamp(f"{year}-01-01"), ["day"]].assign(
        forecast=oos["pred_persistence"])
    r = replay(raw_demand, raw_temp, frozen, thresholds, reference, f"{year}-01-01", f"{year}-12-31", data_end)

    again, _ = load_frozen(year)
    check_unchanged(again.sha256(), r.model_sha256, frozen_run.data.tags["coefficient_sha256"])

    results = {"year": year, "kind": "pseudo_replay" if year == PSEUDO_YEAR else "replay",
               "data_end": data_end, "clock_end": r.clock_end, "summary": summarize(r, n_boot),
               "days": r.days, "weeks": r.weeks, "decisions": r.decisions, "thresholds": thresholds,
               "model": {"sha256": r.model_sha256, "q": frozen.q, "train_last_day": frozen.train_last_day},
               "power": detection_power(r, thresholds), "departure_from": departure_from, **freeze}
    if year == PSEUDO_YEAR:
        client()
        intervals = pd.read_parquet(mlflow.artifacts.download_artifacts(
            run_id=val.info.run_id, artifact_path="frames/intervals.parquet"))
        results["refitted"] = compare_refitted(r, oos, intervals, thresholds, reference, year, n_boot)

    with start_run(EXPERIMENT, results["kind"], tags={
        "kind": results["kind"], "year": str(year), "validation_run_id": val.info.run_id,
        "frozen_run_id": frozen_run.info.run_id, "dq_run_id": dq.info.run_id,
        "coefficient_sha256": r.model_sha256, "rules_sha256": freeze["rules_sha256"],
        "freeze_commit": freeze["freeze_commit"], "departure": str(bool(departure_from)).lower(),
        "departure_from": ",".join(departure_from),
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
