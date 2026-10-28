"""Render v3's validation report (French first, English derived) into docs/report/v3/ (docs/plan-v3.md, sections 3.1,
8.4, 8.5, 10.3 and 13), beside v2's (report/validation.py), whose committed pages, stamp, README box and ledger it never
writes.

The report opens with v3's model card status (`evaluate.status_v3`, from v3's 2025 test of record alone) and says,
before any measured number, that the forecast is issued at noon the day before and why. It shows the contrasts (terms,
family, whole, and the horizon, reported only), the subsets, the verdicts, the window ranking, the intervals by bin
with their fallbacks, each term's coefficient with its variance inflation factor, section 4.3's sensitivities, the
reproduction check, the 2024 rehearsal, 2025, and ledger-v3's entries. Every measured number carries its label of
section 8.4 (in sample for the choices, seen by the developers, held out or post hoc; a sensitivity is never judged),
with the lineage's own beside it (`LINEAGES`). What did not run is said not to have run.

The runs it reads (`follow_v3`), one of three lineages, named on the page; anything else is refused (ReportRefused):

- **of record**: `docs/freeze-v3.json` committed (read as the commit that added it holds it) and every run it names
  in v3's store: those runs, checked against the record's hashes;
- **a fresh clone**: the record committed and none of its runs in the store (section 3.1): the recorded runs are shown
  from the record and ledger-v3; the report's numbers come from the latest v3 runs that passed in this store, each
  labelled a rerun on a later vintage, with each export's retrieval and modification dates; the byte-copy and
  reproduction checks as those runs give them (not run, on a fresh clone);
- **before the freeze**: no record and no `v3-rules-frozen` tag: the latest v3 runs that passed, all made under
  `v3-pre-freeze` and under the rules of the code rendering them, labelled so.

A tag without a committed record, a record in the working tree that no commit adds, or some of the record's runs in
the store and not the others is refused; so is a fresh clone whose data-quality run is not labelled a fresh clone's. A
held-out number is read only for a run ledger-v3 names (`held_out_entry`), and a 2025 number only when the artifacts that
judged it are those the freeze record names (`judged_by_the_record`). Each run's own resamples are printed, and a run of
record must have taken the plan's (`resamples`); every reason a run gives is printed by its key, never as its text.

The stamp (`docs/report/v3/stamp.json`, `make_stamp_v3`) holds the code and rules hashes, the lineage, the runs, the
hash of every rendered file under docs/report/v3/ and of `docs/replay-ledger-v3.json`; `stamp_problems` says how a
committed report no longer matches them (tests/test_report_stamp_v3.py).

Paths are read from paths.REPO_ROOT at call time.
"""

from __future__ import annotations

import functools
import hashlib
import json
import re
import tempfile
from pathlib import Path

import jinja2
import pandas as pd

from valiwatt import attribution, paths
from valiwatt.config import (HELD_OUT_YEAR, LOCAL_TZ, LST_OFFSET, V3_FREEZE_RECORD, V3_FREEZE_TAG, V3_LEDGER,
                             V3_PRE_FREEZE_TAG)
from valiwatt.report.render import ReportRefused, assert_attributed, assert_public_safe, environment

LANGS = ("fr", "en")
HOME = ("docs", "report", "v3")
LINEAGES = ("of_record", "fresh_clone", "pre_freeze")
REHEARSAL_YEAR = 2024
# The roles of the runs the report reads, in the freeze record's order (freeze_record.ROLES).
ROLES = ("data_quality", "validation", "frozen_2024", "rehearsal_2024", "frozen_2025")
# A measured quantity as the pages print it: a number followed by its unit (a share, a difference in percentage
# points, megawatts, degrees). Every line of the results that holds one carries a label (`unlabelled_lines`).
MEASURE = re.compile(r"\d(?:[\d ,.]*\d)?\s?(?:%|pt\b|pp\b|MW\b|°C)")

# The words the checks look for and the labels the pages print, in each language: one home.
SAY = {
    "fr": {
        "labels": {"in_sample": "dans l'échantillon du choix des termes, des classes d'intervalles et du "
                                "sous-ensemble qui décide",
                   "seen_by_developers": "vu par les développeurs", "held_out": "données réservées",
                   "post_hoc": "a posteriori", "sensitivity": "sensibilité, jamais jugée",
                   "recorded": "tel que consigné"},
        "lineage": {"of_record": "", "pre_freeze": "exécution d'avant le gel",
                    "fresh_clone": "reprise sur un millésime plus récent"},
        "sep": " ; ",
        "noon": "émise à midi la veille",
        "brief": "## En bref",
        "card": "## Fiche du modèle",
        "not_run": "non exécutée",
        "byte_copies_not_run": "Vérification des copies à l'identique : non exécutée",
        "reproduction_not_run": "Vérification de reproduction : non exécutée",
        "recommendation": {"keep_v2_noon": "garder la forme de v2 à midi",
                           "review_family": "revoir la famille"},
        # The horizon's "all" row: its own hours, not the common set's (forecast/evaluate.py contrasts_v3).
        "horizon_all": "les heures propres à l'horizon (la demande, la forme de v2 à midi et celle à minuit)",
    },
    "en": {
        "labels": {"in_sample": "in sample for the choice of the terms, the interval bins and the deciding subset",
                   "seen_by_developers": "seen by the developers", "held_out": "held-out data",
                   "post_hoc": "post hoc", "sensitivity": "sensitivity, never judged",
                   "recorded": "as recorded"},
        "lineage": {"of_record": "", "pre_freeze": "a run made before the freeze",
                    "fresh_clone": "a rerun on a later vintage"},
        "sep": "; ",
        "noon": "issued at noon the day before",
        "brief": "## In brief",
        "card": "## Model card",
        "not_run": "not run",
        "byte_copies_not_run": "The byte-copy check: not run",
        "reproduction_not_run": "The reproduction check: not run",
        "recommendation": {"keep_v2_noon": "keep v2's form at noon", "review_family": "review the family"},
        "horizon_all": "the horizon's own hours (the target, v2's form at noon and v2's form at midnight)",
    },
}
# The hour, local clock time, before which the rate's notice of a peak event is sent the day before (Rate Flex D,
# art. 2.71): the pages state how long before it the noon forecast is issued.
NOTICE_LOCAL_H = 15


def out_dir() -> Path:
    """docs/report/v3/, under paths.REPO_ROOT read at call time."""
    return paths.REPO_ROOT.joinpath(*HOME)


def stamp_file() -> Path:
    return out_dir() / "stamp.json"


# ----------------------------------------------------------------------------------------- the design constants

@functools.cache
def design_v3() -> dict:
    """The design constants v3's pages state, read from the code that applies them (the templates type none): the
    noon origin in UTC and local standard time, and v2's midnight one (forecast/issue.py `day_origin`); each issue
    time's lags; the terms' base, the lag day's labels and minimum hours, the knot conversion (`TERM_DEFINITIONS`);
    the forecast wind's upper bound (quality/weather.py); the subsets' minimums, the rate's peak windows, its fixed
    excluded days, its event length and its most events a winter (forecast/evaluate.py); the bin quantile; the
    availability margins (sources/nbm.py); the reproduction tolerance (forecast/validate_v3.py); the noon origin under
    daylight time (config.LOCAL_TZ) and its lead on the rate's notice (NOTICE_LOCAL_H); and v2's design constants
    (render.design). The resamples are each run's own (its config or results), never a constant here."""
    from valiwatt.forecast import evaluate as ev
    from valiwatt.forecast.issue import MIDNIGHT, NOON, TERM_DEFINITIONS, day_labels, day_origin
    from valiwatt.forecast.validate_v3 import REPRODUCTION_TOLERANCE_MW
    from valiwatt.quality.weather import WIND_BOUNDS_KT
    from valiwatt.report.render import design
    from valiwatt.sources.nbm import AVAILABILITY_LAG, SENSITIVITY_LAG

    day = pd.Timestamp("2024-01-02")
    noon, midnight = day_origin(day, issue=NOON), day_origin(day, issue=MIDNIGHT)
    summer = day_origin(pd.Timestamp("2024-07-02"), issue=NOON).tz_convert(LOCAL_TZ)  # under daylight time
    labels = day_labels(day)
    hours = pd.Timedelta(hours=1)
    hm = lambda t: t.strftime("%H:%M")  # noqa: E731 - the English pages print clock times so
    local_h = int((noon + LST_OFFSET).hour)
    excluded = {m: [d for mm, d in ev.EXCLUDED_FIXED if mm == m] for m in (12, 1)}
    if sum(map(len, excluded.values())) != len(ev.EXCLUDED_FIXED):
        raise ValueError("the rate's fixed excluded days fall outside December and January: the pages name them so")
    event_hours = len(ev.WINDOWS["morning"])
    return design() | {
        "first_label_utc_h": int(labels[0].hour), "last_label_utc_h": int(labels[-1].hour),
        "origin_utc_hm": hm(noon), "origin_local_hm": hm(noon + LST_OFFSET), "midnight_utc_hm": hm(midnight),
        "first_label_hm": hm(labels[0]), "last_label_hm": hm(labels[-1]),
        "wind_min_kt": float(WIND_BOUNDS_KT[0]),
        "origin_utc_h": int(noon.hour), "origin_local_h": local_h,
        "origin_days_before": int((day - noon.tz_localize(None).normalize()).days),
        "midnight_utc_h": int(midnight.hour), "lags_noon": list(NOON.lags_h), "lags_midnight": list(MIDNIGHT.lags_h),
        "base_c": float(TERM_DEFINITIONS["base_c"]), "lag_day_min_hours": int(TERM_DEFINITIONS["lag_day_min_hours"]),
        "lag_day_first_h": int(TERM_DEFINITIONS["lag_day_first_h"]),
        "lag_day_last_h": int(TERM_DEFINITIONS["lag_day_last_h"]), "knots_to_kmh": TERM_DEFINITIONS["knots_to_kmh"],
        "wind_max_kt": float(WIND_BOUNDS_KT[1]), "min_hours": ev.MIN_COLD_HOURS, "min_weeks": ev.MIN_COLD_WEEKS,
        "rate_events": ev.RATE_EVENTS,
        "windows": {k: [h + 1 for h in v] for k, v in ev.WINDOWS.items()},  # the local hour each hour ends at
        "bin_quantile": ev.COLD_QUANTILE, "availability_lag_h": AVAILABILITY_LAG / hours,
        "sensitivity_lag_h": SENSITIVITY_LAG / hours, "tolerance_mw": REPRODUCTION_TOLERANCE_MW,
        "peak_hours": ev.PEAK_HOURS, "nominal": 1 - ev.ALPHA,
        "origin_local_dst_h": int(summer.hour), "origin_local_dst_hm": hm(summer),
        "notice_local_h": NOTICE_LOCAL_H, "notice_local_hm": f"{NOTICE_LOCAL_H:02d}:00",
        "notice_lead_h": NOTICE_LOCAL_H - local_h, "notice_lead_dst_h": NOTICE_LOCAL_H - int(summer.hour),
        "excluded_december": excluded[12], "excluded_january": excluded[1],
        "event_hours": event_hours, "winter_event_hours": ev.RATE_EVENTS * event_hours,
    }


# ------------------------------------------------------------------------------------------------- the lineage

def _client(c=None):
    from valiwatt import tracking

    return c or tracking.client()


def _record() -> dict | None:
    """The freeze record as the commit that added it holds it (freeze_record.read_freeze_record, through its anchor
    across the repository's refs, as the 2025 gate reads it, so a record off HEAD's history after a start ruled under
    (a) is shown), None when no commit adds it and the working tree does not hold it either; refused when the working
    tree holds it and no commit adds it, or when it cannot be read as written."""
    from valiwatt import freeze_record

    try:
        if freeze_record.anchor() is None:
            if (paths.REPO_ROOT / V3_FREEZE_RECORD).exists():
                raise ReportRefused(f"{V3_FREEZE_RECORD} is in the working tree but no commit adds it: commit it, "
                                    "alone, before the report is rendered (docs/plan-v3.md, section 10.3)")
            return None
        return freeze_record.read_freeze_record()
    except freeze_record.FreezeRecordError as e:
        raise ReportRefused(f"the freeze record cannot be read as committed: {e}") from e


def _off_head(commit: str) -> bool:
    """The record's commit is not HEAD or an ancestor of it (a start ruled under (a), section 11.3 (3)): the page says
    so."""
    from valiwatt import freeze_record

    return not freeze_record._is_ancestor(commit, "HEAD")


def _tag_exists() -> bool:
    from valiwatt import records_v3

    return records_v3._commit_of(f"refs/tags/{V3_FREEZE_TAG}") is not None


def _get(c, run_id: str):
    """The run of `run_id` in v3's store, or None when the store lacks it."""
    from mlflow.exceptions import MlflowException

    try:
        return c.get_run(run_id)
    except MlflowException:
        return None


def _latest(c, experiment: str, filter_string: str):
    """The latest run of `experiment` matching `filter_string`, or None."""
    exp = c.get_experiment_by_name(experiment)
    if exp is None:
        return None
    runs = c.search_runs([exp.experiment_id], filter_string=filter_string, order_by=["attributes.start_time DESC"],
                         max_results=1)
    return runs[0] if runs else None


def _latest_chain(c, freeze_tag: str | None) -> dict:
    """The latest v3 runs that passed, each built on the one before (section 17's order): the data-quality run of the
    2019-2024 window whose gates passed, the complete validation run on it, the frozen runs for 2024 and 2025 built on
    that validation run and the finished 2024 rehearsal of the frozen 2024 run, all under `freeze_tag` when given.
    Returns {role: run or None}; the data-quality and validation runs are required."""
    from valiwatt.forecast import freeze
    from valiwatt.forecast import validate as vd
    from valiwatt.monitor import run as mrun
    from valiwatt.quality import run_v3 as dq_v3

    tag = f" and tags.freeze_tag = '{freeze_tag}'" if freeze_tag else ""
    dq = _latest(c, dq_v3.EXPERIMENT, dq_v3.PASSED + " and params.through = '2024'" + tag)
    if dq is None:
        raise ReportRefused("v3's store holds no data-quality run of the 2019-2024 window that passed its gates"
                            + (f" under `{freeze_tag}`" if freeze_tag else "") + ": run `valiwatt dq-v3` first")
    val = _latest(c, vd.EXPERIMENT, "tags.kind = 'validation_v3' and tags.status = 'complete' and "
                                     f"tags.dq_run_id = '{dq.info.run_id}'" + tag)
    if val is None:
        raise ReportRefused(f"v3's store holds no complete validation run on data-quality run {dq.info.run_id}: run "
                            "`valiwatt validate-v3` first")
    out = {"data_quality": dq, "validation": val}
    for year in (2024, 2025):
        out[f"frozen_{year}"] = _latest(
            c, freeze.EXPERIMENT, "tags.kind = 'frozen' and tags.version = 'v3' and tags.status = 'complete' and "
            f"tags.replay_year = '{year}' and tags.validation_run_id = '{val.info.run_id}'" + tag)
    f24 = out["frozen_2024"]
    out["rehearsal_2024"] = None if f24 is None else _latest(
        c, mrun.EXPERIMENT, "tags.kind = 'replay_v3' and tags.year = '2024' and tags.status = 'complete' and "
        f"attributes.status = 'FINISHED' and tags.frozen_run_id = '{f24.info.run_id}'" + tag)
    return {role: out[role] for role in ROLES}


def follow_v3(c=None) -> dict:
    """The runs v3's report reads, and how it read them (the module's docstring): {"lineage", "record" (the freeze
    record as committed, or None), "runs" ({role: run or None}), "chains" ({2024, 2025: freeze_record.frozen_chain_v3
    of the frozen run of that year, or None})}."""
    from valiwatt.freeze_record import FreezeRecordError, frozen_chain_v3
    from valiwatt.monitor.rules import rules_sha256

    c = _client(c)
    record = _record()
    if record is None:
        if _tag_exists():
            raise ReportRefused(f"`{V3_FREEZE_TAG}` exists and no freeze record is committed: the report reads the runs "
                                f"{V3_FREEZE_RECORD} names; write it with `valiwatt freeze-record-v3` and commit it "
                                "(docs/plan-v3.md, section 10.3)")
        lineage, runs = "pre_freeze", _latest_chain(c, V3_PRE_FREEZE_TAG)
    else:
        named = {role: record["data"]["runs"][role]["run_id"] for role in ROLES}
        found = {role: _get(c, run_id) for role, run_id in named.items()}
        held = [role for role, run in found.items() if run is not None]
        if held and len(held) != len(ROLES):
            raise ReportRefused(f"v3's store holds the freeze record's runs {held} and not the others: a store either "
                                "holds the runs of record or none of them (a fresh clone)")
        if held:
            lineage, runs = "of_record", found
        else:
            lineage, runs = "fresh_clone", _latest_chain(c, None)
            dq = runs["data_quality"]
            if dq.data.tags.get("fresh_clone") != "true":
                raise ReportRefused(f"v3's store holds none of the freeze record's runs, and its data-quality run "
                                    f"{dq.info.run_id} is not labelled a fresh clone's: a store without the runs of "
                                    "record reports only a rerun on a later vintage, whose data-quality run says so "
                                    "(docs/plan-v3.md, section 3.1)")
    if lineage != "of_record":
        rules = rules_sha256()
        stale = [f"{role} run {run.info.run_id}" for role, run in runs.items()
                 if run is not None and run.data.tags.get("rules_sha256") != rules]
        if stale:
            raise ReportRefused(f"{', '.join(stale)} ran under other rules than the code's: run the chain again before "
                                "rendering")
    chains = {}
    val = runs["validation"]
    for year in (2024, 2025):
        frozen = runs[f"frozen_{year}"]
        try:
            chains[year] = None if frozen is None else frozen_chain_v3(frozen.info.run_id, val.info.run_id, year, c)
        except FreezeRecordError as e:
            raise ReportRefused(f"the models frozen for {year}: {e}") from e
    if lineage == "of_record":
        data = record["data"]
        if chains[2025]["digest"] != data["frozen_sha256"] or chains[2025]["model"].sha256() != data["model_sha256"]:
            raise ReportRefused("the artifacts frozen for 2025, read now, are not those the freeze record names")
        if chains[2024]["digest"] != data["runs"]["rehearsal_2024"]["frozen_sha256"]:
            raise ReportRefused("the artifacts frozen for the 2024 rehearsal, read now, are not those the freeze record "
                                "names for it")
    return {"lineage": lineage, "record": record, "runs": runs, "chains": chains}


def held_out_entry(entries: list[dict], run_id: str) -> dict:
    """The ledger-v3 entry of a 2025 run whose numbers the report prints (section 10.3): a held-out number comes only
    from a run ledger-v3 names; refused otherwise."""
    found = [e for e in entries if e["run_id"] == run_id and e["year"] == HELD_OUT_YEAR]
    if len(found) != 1:
        raise ReportRefused(f"the 2025 run {run_id} is not in {V3_LEDGER}: no held-out number is rendered from a run the "
                            "ledger does not name (run `valiwatt report`, which syncs it first)")
    return found[0]


# --------------------------------------------------------------------------------------------------- the context

def _json(c, run_id: str, path: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        return json.loads(Path(c.download_artifacts(run_id, path, tmp)).read_text(encoding="utf-8"))


def _frame(c, run_id: str, path: str) -> pd.DataFrame:
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(c.download_artifacts(run_id, path, tmp))
        return pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p)


def _when(ms) -> str:
    return pd.Timestamp(ms, unit="ms", tz="UTC").strftime("%Y-%m-%d %H:%M UTC")


def _run_row(role: str, run) -> dict:
    t = run.data.tags
    return {"role": role, "id": run.info.run_id, "commit": t.get("git_commit", ""), "dirty": t.get("git_dirty") != "false",
            "rules": t.get("rules_sha256", ""), "freeze_tag": t.get("freeze_tag", ""), "started": _when(run.info.start_time)}


def _terms(c, chain: dict | None) -> list[dict]:
    """The frozen production model's terms and lags: each one's coefficient in MW per unit of its column and its
    variance inflation factor, as the freeze logged them (coefficients.csv, freeze_v3.coefficient_table_v3)."""
    from valiwatt.forecast import model

    if chain is None:
        return []
    coef = _frame(c, chain["frozen_run"].info.run_id, "coefficients.csv")
    rows = []
    for r in coef.itertuples():
        name = str(r.feature)
        if name.startswith(model.TERM_PREFIX) or model.is_lag(name):
            rows.append({"name": name.removeprefix(model.TERM_PREFIX), "term": name.startswith(model.TERM_PREFIX),
                         "mw_per_unit": float(r.mw_per_unit), "vif": float(r.vif)})
    if [r["name"] for r in rows if r["term"]] != list(chain["model"].terms):
        raise ReportRefused(f"frozen run {chain['frozen_run'].info.run_id}'s coefficient table does not hold its "
                            "model's terms")
    return rows


def _frozen(c, chain: dict | None) -> dict | None:
    if chain is None:
        return None
    m = chain["model"]
    return {"run_id": chain["frozen_run"].info.run_id, "sha": m.sha256(), "year": int(m.replay_year),
            "train_last_day": m.train_last_day, "n_train": int(m.n_train), "columns": len(m.columns),
            "interval": dict(m.interval or {}), "lags": m.lag_coefficients(), "terms": _terms(c, chain),
            "cold_c": chain["cold_c"]}


def _exports(dq_results: dict, dq_run) -> dict:
    """When each export the data-quality run read was retrieved, and the Hydro-Québec dataset's modification date: the
    dates a rerun on a later vintage states (section 3.1)."""
    src = dq_results.get("sources") or {}
    eccc, years = src.get("eccc") or [], ((src.get("nbm") or {}).get("years") or {}).values()
    hq = src.get("hydroquebec") or {}
    if not eccc or not years or not hq.get("retrieved_utc"):
        raise ReportRefused(f"data-quality run {dq_run.info.run_id} holds no record of when its exports were retrieved "
                            "(sources.hydroquebec, sources.eccc, sources.nbm): run `valiwatt dq-v3` again")
    day = lambda x: str(x)[:10]  # noqa: E731
    return {"hq_retrieved": day(hq["retrieved_utc"]), "hq_modified": day(hq.get("dataset_modified") or "") or None,
            "eccc_first": min(day(s["first_retrieved_utc"]) for s in eccc),
            "eccc_last": max(day(s["last_retrieved_utc"]) for s in eccc),
            "nbm_first": min(day(y["retrieved_utc"]) for y in years),
            "nbm_last": max(day(y["retrieved_utc"]) for y in years)}


def judged_by_the_record(entries: list[dict], record: dict | None) -> None:
    """Every 2025 entry of ledger-v3 was judged by the artifacts the freeze record names (section 10.3: the record holds
    "the one hash of every artifact that will judge 2025", and the 2025 gate checks each start against it): its
    `frozen_sha256` is the record's; refused otherwise, and refused when a 2025 entry stands with no committed record."""
    for e in entries:
        if e["year"] != HELD_OUT_YEAR:
            continue
        if record is None:
            raise ReportRefused(f"ledger-v3 holds the 2025 run {e['run_id']} and no freeze record is committed: no 2025 "
                                "number is rendered without the record of what judged it (docs/plan-v3.md, section "
                                "10.3)")
        if e["frozen_sha256"] != record["data"]["frozen_sha256"]:
            raise ReportRefused(f"the 2025 run {e['run_id']} was judged by artifacts of digest {e['frozen_sha256'][:12]} "
                                f"in ledger-v3, and the freeze record names {record['data']['frozen_sha256'][:12]}: no "
                                "2025 number is rendered from artifacts the freeze record does not name")


def _held(c, entries: list[dict], starts: list[dict], record: dict | None = None) -> dict | None:
    """2025 (section 11.3): the test of record (report/ledger.py `of_record`, on v3's tag), the starts it passed over,
    its reruns; each finished one's numbers from its ledger-v3 entry (`held_out_entry`) and, when the store holds it,
    its results/replay.json, whose artifacts must be those the freeze record names (`judged_by_the_record`, and the
    digest of the results' own `frozen_sha256`). None before any 2025 start."""
    from valiwatt.monitor.run import frozen_digest
    from valiwatt.report.ledger import of_record

    judged_by_the_record(entries, record)
    held = of_record(entries, starts, HELD_OUT_YEAR, V3_FREEZE_TAG)
    if held is None:
        return None

    def numbers(start: dict) -> dict:
        e = held_out_entry(entries, start["run_id"])
        run = _get(c, start["run_id"])
        results = _json(c, start["run_id"], "results/replay.json") if run is not None else None
        if results is not None and frozen_digest(results.get("frozen_sha256") or {}) != record["data"]["frozen_sha256"]:
            raise ReportRefused(f"the 2025 run {start['run_id']}'s results were judged by other artifacts than those the "
                                "freeze record names: no 2025 number is rendered from them")
        if results is not None:
            keyed_ineligible(results["judgement"]["ranking"])
        return {"entry": e, "results": results}

    rec = held["record"]
    out = {"record": rec, "stopped": held["stopped"], "unfinished": held["unfinished"],
           "numbers": numbers(rec) if rec["finished"] else None,
           "reruns": [numbers({"run_id": e["run_id"]}) for e in held["reruns"]]}
    return out


def extrapolation(found: dict) -> list[dict]:
    """Section 6's added finding, summed over the folds: for v3's temperature and each term (validate_v3 `extrapolation`),
    the hours v3 forecast, those whose input lay below or above its fold's training range, and the mean APE of those
    outside it that have a target (None when none has)."""
    out = []
    for name, rows in found.items():
        apes = [float(x["ape"]) for r in rows for x in r["outside"]
                if x.get("ape") is not None and x["ape"] == x["ape"]]
        out.append({"name": name, "hours": sum(int(r["hours"]) for r in rows),
                    "below": sum(int(r["hours_below"]) for r in rows), "above": sum(int(r["hours_above"]) for r in rows),
                    "mape": sum(apes) / len(apes) if apes else None})
    return out


# An ineligible window's reason, as evaluate.rank_windows writes it; the pages phrase each one by its key, never
# printing the run's text (`keyed_ineligible`).
_HOURS_IN_DATA = re.compile(r"(\d+) of its (\d+) hours in the period's data")
_NO_FORECAST_FROM = re.compile(r"no forecast from (\w+)")


def keyed_ineligible(parts: list[dict]) -> list[dict]:
    """Section 8.3: the windows that are not eligible "are listed with their reason". To each part of a window ranking
    (evaluate.rank_windows), add `ineligible_by_reason`: one group per keyed reason, in the order first met, {"kind":
    "outside" (none of its hours in the period's data: a window of the winter outside the scored months, counted),
    "partial" (some of its hours only, `present` of `of`), "no_target", or "no_forecast" (`model`), "count", "days":
    [{"day", "windows"}]}; a reason the report cannot phrase is refused. Returns the parts."""
    for p in parts:
        groups: dict[tuple, dict] = {}
        for x in p["ineligible"]:
            reason = x["reason"]
            if (m := _HOURS_IN_DATA.fullmatch(reason)) is not None:
                present, of = int(m.group(1)), int(m.group(2))
                key = ("outside",) if present == 0 else ("partial", present, of)
                group = {"kind": key[0]} | ({"present": present, "of": of} if present else {})
            elif reason == "no target":
                key, group = ("no_target",), {"kind": "no_target"}
            elif (m := _NO_FORECAST_FROM.fullmatch(reason)) is not None:
                key, group = ("no_forecast", m.group(1)), {"kind": "no_forecast", "model": m.group(1)}
            else:
                raise ReportRefused(f"winter {p['winter']}: a window not eligible for the ranking, {x['day']} "
                                    f"{x['window']}, has a reason the report cannot phrase: {reason!r}")
            g = groups.setdefault(key, group | {"count": 0, "days": []})
            g["count"] += 1
            if g["days"] and g["days"][-1]["day"] == x["day"]:
                g["days"][-1]["windows"].append(x["window"])
            else:
                g["days"].append({"day": x["day"], "windows": [x["window"]]})
        for g in groups.values():
            g["ranges"] = day_ranges(g["days"])
        p["ineligible_by_reason"] = list(groups.values())
    return parts


def day_ranges(days: list) -> list[dict]:
    """Days as the pages list them: consecutive calendar days merged into one range {"first", "last", "windows": None},
    a day whose windows are only some of the two kept alone with them ({"first" = "last", "windows": [...]}). `days`:
    dates (strings or timestamps), or {"day", "windows"} as `keyed_ineligible` groups them, in order."""
    out: list[dict] = []
    for d in days:
        day, windows = (d["day"], d["windows"]) if isinstance(d, dict) else (d, None)
        day = pd.Timestamp(day).strftime("%Y-%m-%d")
        some = windows is not None and len(set(windows)) < 2
        if not some and out and out[-1]["windows"] is None and \
                pd.Timestamp(out[-1]["last"]) + pd.Timedelta(days=1) == pd.Timestamp(day):
            out[-1]["last"] = day
        else:
            out.append({"first": day, "last": day, "windows": list(windows) if some else None})
    return out


def record_of_2025(held: dict | None) -> dict | None:
    """The argument of evaluate.status_v3 and recommendation_v3: None before any 2025 start; else whether the test of
    record finished, and when it did its standing and its verdicts, as its ledger-v3 entry holds them. Not named
    test_*: pytest would collect it."""
    if held is None:
        return None
    rec, nums = held["record"], held["numbers"]
    if not rec["finished"]:
        return {"finished": False}
    e = nums["entry"]
    return {"finished": True, "standing": e["standing"], "verdict": e["verdict"]}



def resamples(lineage: str, v: dict, val_id: str, reh: dict | None, held: dict | None) -> dict:
    """The resamples of each run whose bootstrap intervals the pages print, as the run records them (the validation's
    config.n_boot, a replay's results n_boot): {run ID: count}. Refused when a run does not record its count, or, for
    the runs of record, when it is not the plan's (section 8.1: 1,000, evaluate.N_BOOT)."""
    from valiwatt.forecast import evaluate as ev

    counts = {val_id: v["config"].get("n_boot")}
    if reh is not None:
        counts[reh["run_id"]] = reh["results"].get("n_boot")
    for nums in ([held["numbers"], *held["reruns"]] if held else []):
        if nums is not None and nums["results"] is not None:
            counts[nums["entry"]["run_id"]] = nums["results"].get("n_boot")
    for run_id, n in counts.items():
        if not isinstance(n, int) or isinstance(n, bool):
            raise ReportRefused(f"run {run_id} does not record how many resamples its bootstrap intervals took: run it "
                                "again")
        if lineage == "of_record" and n != ev.N_BOOT:
            raise ReportRefused(f"run of record {run_id} took {n} resamples for its bootstrap intervals, not the "
                                f"plan's {ev.N_BOOT} (docs/plan-v3.md, section 8.1)")
    return counts


def context_v3(f: dict, entries: list[dict], starts: list[dict], code_sha: str, rules_sha: str, c=None) -> dict:
    """Everything the pages print, from the lineage `f` (follow_v3), ledger-v3's entries and the 2025 starts
    (records_v3.replay_starts_v3)."""
    from valiwatt.forecast import evaluate as ev
    from valiwatt.forecast.validate import validation_results

    c = _client(c)
    runs = f["runs"]
    val, dq = runs["validation"], runs["data_quality"]
    v = validation_results(val)
    for key in ("contrasts", "verdict", "ranking", "coverage", "sensitivity", "folds", "conformal", "config",
                "performance", "extrapolation", "forecast_input", "common_hours"):
        if key not in v:
            raise ReportRefused(f"validation run {val.info.run_id} holds no {key}: run `valiwatt validate-v3` again")
    if v["contrasts"].get("cold_c") != v["config"].get("cold_c"):
        raise ReportRefused(f"validation run {val.info.run_id} cut its cold tail at {v['contrasts'].get('cold_c')} °C "
                            f"and records cold_c {v['config'].get('cold_c')} °C: a validation run has one cold_c "
                            "(docs/plan-v3.md, section 6)")
    for parts in v["ranking"]["years"].values():
        keyed_ineligible(parts)
    bins = _frame(c, val.info.run_id, "frames/bins.parquet")
    bins = bins[bins["model"] == "v3"].sort_values("fold")
    rehearsal = runs["rehearsal_2024"]
    reh = None if rehearsal is None else {"run_id": rehearsal.info.run_id,
                                          "results": _json(c, rehearsal.info.run_id, "results/replay.json")}
    if reh is not None and reh["results"].get("evaluation") != "seen_by_developers":
        raise ReportRefused(f"the 2024 rehearsal {reh['run_id']} is labelled {reh['results'].get('evaluation')!r}, not "
                            "seen by the developers (docs/plan-v3.md, section 11.1)")
    if reh is not None:
        keyed_ineligible(reh["results"]["judgement"]["ranking"])
    held = _held(c, entries, starts, f["record"])
    resamples(f["lineage"], v, val.info.run_id, reh, held)
    tor = record_of_2025(held)
    status = ev.status_v3(tor)
    if status in ("both_better", "terms_only", "family_only", "neither") and \
            held["numbers"]["entry"]["evaluation"] != "held_out":
        raise ReportRefused("a held-out status needs the test of record labelled held out")
    reproduction = v.get("reproduction") or {"status": val.data.tags.get("reproduction", "")}
    record = f["record"]
    return {
        "lineage": f["lineage"], "status": status, "recommendation": ev.recommendation_v3(tor),
        "verdict_2025": (tor or {}).get("verdict"),
        "Y": {"first": int(str(v["folds"]["first"])[:4]), "last": int(str(v["folds"]["last"])[:4]),
              "seen": REHEARSAL_YEAR, "held": HELD_OUT_YEAR},
        "extrapolation": extrapolation(v["extrapolation"]),
        "K": design_v3(), "v": v, "bins": bins.to_dict("records"),
        "frozen": {y: _frozen(c, ch) for y, ch in f["chains"].items()},
        "rehearsal": reh, "held": held, "entries": entries,
        "record": None if record is None else {"commit": record["commit"], "sha256": record["sha256"],
                                               "data": record["data"], "off_head": _off_head(record["commit"])},
        "runs": {role: _run_row(role, run) for role, run in runs.items() if run is not None},
        "absent": [role for role, run in runs.items() if run is None],
        "exports": _exports(_json(c, dq.info.run_id, "results/dq_results.json"), dq),
        "byte_copies": dq.data.tags.get("byte_copies", ""), "fresh_clone": dq.data.tags.get("fresh_clone") == "true",
        "reproduction": reproduction, "code_sha": code_sha, "rules_sha": rules_sha,
        "lag_day_missing": day_ranges(v["forecast_input"]["noon"].get("lag_day_missing_days") or []),
        "freeze_tag": V3_FREEZE_TAG, "pre_freeze_tag": V3_PRE_FREEZE_TAG,
        "first_pin": None if record is None else record["data"]["first_pin"],
    }


# ---------------------------------------------------------------------------------------------- the checks

def labels(lang: str, lineage: str) -> dict:
    """The labels the pages print, each in italics with the lineage's own beside it (none for the runs of record);
    "recorded", the label of a number read from ledger-v3 on a fresh clone, never takes the rerun's."""
    say = SAY[lang]
    extra = say["lineage"][lineage]
    return {k: f"_{v}{say['sep'] + extra if extra and k != 'recorded' else ''}_" for k, v in say["labels"].items()}


def printed_labels(lang: str, lineage: str) -> dict:
    """The labels a page of `lineage` may print (`labels`): "recorded" only on a fresh clone, the one lineage whose
    page shows recorded runs it does not hold."""
    return {k: v for k, v in labels(lang, lineage).items() if k != "recorded" or lineage == "fresh_clone"}


# Section 8.4's labels by the part of the page they may stand in. Section 1 (the method) prints no result. A measured
# line must carry one of its part's `need` (both, in §2.6) and none of the part's `never`.
ENTRY_LABELS = ("held_out", "post_hoc", "seen_by_developers", "recorded")
ZONES = {
    "brief": {"need": None, "never": ("sensitivity",)},
    "card": {"need": None, "never": ("held_out", "post_hoc", "sensitivity")},
    2: {"need": ("in_sample",), "never": ("held_out", "post_hoc", "seen_by_developers", "recorded")},
    "2.6": {"need": ("in_sample", "sensitivity"), "never": ("held_out", "post_hoc", "seen_by_developers", "recorded")},
    3: {"need": ("seen_by_developers",), "never": ("in_sample", "held_out", "post_hoc", "recorded", "sensitivity")},
    4: {"need": ENTRY_LABELS, "never": ("in_sample", "sensitivity")},
    5: {"need": ENTRY_LABELS, "never": ("in_sample", "sensitivity")},
    6: {"need": None, "never": ("held_out", "post_hoc", "recorded")},
    7: {"need": None, "never": ("held_out", "post_hoc", "in_sample", "seen_by_developers", "sensitivity")},
}


def mislabelled_lines(text: str, lang: str, ctx: dict) -> list[str]:
    """Every line whose label does not belong where it stands (ZONES; section 8.4): in sample in section 2 (with
    "sensitivity, never judged" beside it in §2.6), seen by the developers in section 3 (the 2024 rehearsal), the
    labels of ledger-v3's entries in sections 4 and 5; held out only on 2025's line of the brief, in section 4 and on
    the test of record's row of section 5; "recorded" only on a fresh clone (`printed_labels`). A table row is read
    with its table's header."""
    found = labels(lang, ctx["lineage"])
    allowed = printed_labels(lang, ctx["lineage"])
    held_year = str(ctx["Y"]["held"])
    record_id = (ctx["held"] or {}).get("record", {}).get("run_id")
    out, zone, header = [], None, None
    start = text.find("\n## ")
    for line in (text[start:] if start >= 0 else text).splitlines():
        if line.startswith("## "):
            m = re.match(r"## (\d+)\. ", line)
            zone = int(m.group(1)) if m else ("brief" if line == SAY[lang]["brief"] else
                                              "card" if line == SAY[lang]["card"] else None)
        elif line.startswith("### "):
            zone = "2.6" if line.startswith("### 2.6 ") else (2 if zone == "2.6" else zone)
        header = (line if header is None else header) if line.startswith("|") else None
        if zone == 1:
            continue
        carried = {k for k, lab in found.items() if lab in line or (header is not None and lab in header)}
        bad = [k for k in carried if k not in allowed]
        rule = ZONES.get(zone)
        if rule is not None:
            bad += [k for k in carried if k in rule["never"]]
            if MEASURE.search(line) and rule["need"] is not None:
                need = rule["need"]
                if (zone == "2.6" and not set(need) <= carried) or (zone != "2.6" and not carried & set(need)):
                    bad.append("missing")
        if "held_out" in carried and (
                (zone == "brief" and not line.startswith(f"- {held_year}")) or
                (zone == 5 and (record_id is None or record_id not in line)) or zone not in ("brief", 4, 5)):
            bad.append("held_out")
        if bad:
            out.append(line)
    return out


def numbered_section(text: str, number: int) -> str:
    m = re.search(rf"^## {number}\. ", text, re.M)
    if m is None:
        return ""
    rest = text[m.end():]
    nxt = re.search(r"^## ", rest, re.M)
    return text[m.start():m.end() + (nxt.start() if nxt else len(rest))]


def unlabelled_lines(text: str, printed: list[str]) -> list[str]:
    """Every line of the results (from the brief on, section 1 on the method excepted) that prints a measured number
    (MEASURE) and carries none of the labels `printed`, itself or, in a table, in the table's header."""
    start = text.find("\n## ")
    body = text[start:] if start >= 0 else text
    method = numbered_section(body, 1)
    if method:
        body = body.replace(method, "")
    out, header = [], None
    for line in body.splitlines():
        if line.startswith("|"):
            header = line if header is None else header
        else:
            header = None
        if MEASURE.search(line) and not any(p in line or (header is not None and p in header) for p in printed):
            out.append(line)
    return out


def check_rendered_v3(text: str, lang: str, ctx: dict, status_line: str) -> None:
    """The statements a rendered v3 page must carry; a page without one is refused (ReportRefused): the attribution;
    the brief opening with the status sentence; the sentence saying the forecast is issued at noon the day before
    before any measured number; every measured number labelled (`unlabelled_lines`), the lineage's label beside it on
    a fresh clone's rerun or before the freeze; the recommendation a clean 2025 makes; the held-out label only on a
    clean test of record's numbers; each label in the section its year belongs to (`mislabelled_lines`); and, on a
    fresh clone, a chain that checked neither its byte copies nor its reproduction, the provenance's two lines saying
    so, each export's retrieval date and every recorded run named."""
    say = SAY[lang]
    assert_attributed(text, lang)
    brief = text.split(say["brief"], 1)
    first = brief[1].lstrip("\n").split("\n", 1)[0] if len(brief) == 2 else ""
    if not status_line or not first.startswith("- " + status_line.strip()):
        raise ReportRefused("the v3 report does not open with its status")
    measured = MEASURE.search(text)
    noon = text.find(say["noon"])
    if noon < 0 or (measured is not None and measured.start() < noon):
        raise ReportRefused("the v3 report does not say, before any number, that the forecast is issued at noon the "
                            "day before")
    printed = list(printed_labels(lang, ctx["lineage"]).values())
    missing = unlabelled_lines(text, printed)
    if missing:
        raise ReportRefused(f"the v3 report prints a number without its label: {missing[0]!r}")
    for kind in ctx["recommendation"]:
        if say["recommendation"][kind] not in text:
            raise ReportRefused("a clean 2025 that does not show the terms or the family better is rendered without "
                                "its recommendation")
    held_label = labels(lang, ctx["lineage"])["held_out"]
    if held_label in text and not (ctx["held"] and ctx["held"]["numbers"]
                                   and ctx["held"]["numbers"]["entry"]["evaluation"] == "held_out"):
        raise ReportRefused("the v3 report labels a number held out without a clean test of record")
    wrong = mislabelled_lines(text, lang, ctx)
    if wrong:
        raise ReportRefused(f"the v3 report labels a line against its section (docs/plan-v3.md, section 8.4): "
                            f"{wrong[0]!r}")
    if ctx["lineage"] == "fresh_clone":
        if ctx["byte_copies"] == "passed" or ctx["reproduction"].get("status") == "passed":
            raise ReportRefused("a fresh clone's chain says its byte copies or its reproduction were checked: a fresh "
                                "clone has neither v2's files nor v2's store (docs/plan-v3.md, section 3.1)")
        prov = numbered_section(text, 7)
        lines = prov.splitlines()
        if f"- {say['byte_copies_not_run']}." not in lines or f"- {say['reproduction_not_run']}." not in lines \
                or ctx["exports"]["hq_retrieved"] not in prov:
            raise ReportRefused("a fresh clone's report does not say that the byte-copy and reproduction checks were "
                                "not run, or when its exports were retrieved")
        unnamed = [r["run_id"] for r in ctx["record"]["data"]["runs"].values() if r["run_id"] not in prov]
        if unnamed:
            raise ReportRefused(f"a fresh clone's report does not name the recorded run {unnamed[0]}")


# ------------------------------------------------------------------------------------------------- rendering

def status_sentence(env, ctx: dict, lang: str) -> str:
    """The status sentence as the templates print it (v3/sentences.{lang}.md.j2 `status_line`)."""
    module = env.get_template(f"v3/sentences.{lang}.md.j2").make_module({})
    return str(module.status_line(ctx["status"], ctx["verdict_2025"], ctx["Y"])).strip()


def render_pages_v3(ctx: dict) -> dict:
    """The v3 validation page in each language, French first, rendered from one context and checked as a published
    page is: {lang: text}."""
    retrieved = ctx["exports"]["eccc_first"]
    pages = {}
    for lang in LANGS:
        env = environment(lang)
        status_line = status_sentence(env, ctx, lang)
        try:
            text = env.get_template(f"v3/validation.{lang}.md.j2").render(
                **ctx, lang=lang, L=labels(lang, ctx["lineage"]), say=SAY[lang], status_line=status_line,
                attribution=attribution.lines(lang, retrieved=retrieved))
        except jinja2.UndefinedError as e:  # a key the templates do not phrase (a reason, a state): never printed raw
            raise ReportRefused(f"the v3 {lang} page meets something it cannot phrase: {e}") from e
        assert_public_safe(text)
        check_rendered_v3(text, lang, ctx, status_line)
        pages[lang] = text
    return pages


def file_hashes_v3(root: Path | None = None) -> dict:
    """The sha256 of every file under docs/report/v3/ (under `root`, paths.REPO_ROOT by default), the stamp excepted."""
    out = (root or paths.REPO_ROOT).joinpath(*HOME)
    if not out.exists():
        return {}
    return {p.relative_to(out).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(out.rglob("*")) if p.is_file() and p.name != "stamp.json"}


def make_stamp_v3(ctx: dict) -> dict:
    """What the committed v3 report is checked against (section 10.3): the code and rules hashes, the lineage, the
    freeze record's commit and hash, the runs, the hash of every rendered file under docs/report/v3/ and of
    docs/replay-ledger-v3.json, and the 2025 test of record's standing."""
    from valiwatt.report import ledger_v3

    held = ctx["held"]
    standing = None if held is None else {"record_run_id": held["record"]["run_id"],
                                          "finished": bool(held["record"]["finished"]),
                                          "standing": held["record"].get("standing", "")}
    rec = ctx["record"]
    return {"code_sha256": ctx["code_sha"], "rules_sha256": ctx["rules_sha"], "lineage": ctx["lineage"],
            "freeze_record": None if rec is None else {"commit": rec["commit"], "sha256": rec["sha256"]},
            "runs": {role: r["id"] for role, r in ctx["runs"].items()}, "files": file_hashes_v3(),
            "ledger_sha256": ledger_v3.digest(), "standing_2025": standing}


def stamp_problems(root: Path | None = None) -> list[str]:
    """How the committed v3 report no longer matches its stamp (tests/test_report_stamp_v3.py), [] when it does or when
    there is no v3 report at all: a page without a stamp, a stamp of other code or rules than the tree's, a file
    changed, added or removed since the render, ledger-v3 changed since, a page not naming the code, rules and runs it
    was rendered from, a freeze record other than the one committed (none against none included), or a report
    rendered before the freeze once the record or `v3-rules-frozen` exists."""
    from valiwatt.monitor.rules import rules_sha256
    from valiwatt.report import ledger_v3
    from valiwatt.tracking import code_sha256

    root = root or paths.REPO_ROOT
    out = root.joinpath(*HOME)
    stamp_path = out / "stamp.json"
    if not out.exists() or not any(out.iterdir()):
        return []
    if not stamp_path.exists():
        return ["docs/report/v3/ holds pages and no stamp: run `valiwatt report`"]
    stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    problems = []
    if stamp.get("code_sha256") != code_sha256():
        problems.append("the code changed since the v3 report was rendered: run `valiwatt report`")
    if stamp.get("rules_sha256") != rules_sha256():
        problems.append("the rules changed since the v3 report was rendered: run `valiwatt report`")
    if stamp.get("files") != file_hashes_v3(root):
        problems.append("a file under docs/report/v3/ differs from what the renderer wrote: run `valiwatt report`")
    if stamp.get("ledger_sha256") != ledger_v3.digest(root / V3_LEDGER):
        problems.append("docs/replay-ledger-v3.json changed since the v3 report was rendered: run `valiwatt report`")
    for lang in LANGS:
        page = out / f"validation.{lang}.md"
        text = page.read_text(encoding="utf-8") if page.exists() else ""
        named = [stamp.get("code_sha256", "")[:12], stamp.get("rules_sha256", "")[:12],
                 *(stamp.get("runs") or {}).values()]
        if not text or not all(x and x in text for x in named):
            problems.append(f"validation.{lang}.md does not name the code, rules and runs of its stamp")
    from valiwatt import freeze_record

    rec = stamp.get("freeze_record")
    try:
        committed = _record()
    except ReportRefused as e:
        problems.append(f"the v3 report was rendered from another freeze record than the one committed ({e})")
    else:
        now = None if committed is None else {"commit": committed["commit"], "sha256": committed["sha256"]}
        if now != rec:
            problems.append("the v3 report was rendered from another freeze record than the one committed ("
                            + ("no freeze record is committed" if now is None else
                               f"committed: {now['commit'][:12]}") + "): run `valiwatt report`")
    try:
        frozen = freeze_record.anchor() is not None or _tag_exists()
    except freeze_record.FreezeRecordError:
        frozen = True
    if stamp.get("lineage") == "pre_freeze" and frozen:
        problems.append(f"the v3 report was rendered before the freeze, and `{V3_FREEZE_TAG}` or a committed "
                        f"{V3_FREEZE_RECORD} now exists: run `valiwatt report`")
    return problems


def render_v3(c=None) -> list[Path]:
    """`valiwatt report` on the v3 branch: the lineage (`follow_v3`), ledger-v3 and the 2025 starts read, the context
    built and both pages rendered and checked before anything is written; then docs/report/v3/validation.{fr,en}.md
    and the stamp, and nothing else (v2's pages, stamp, README box and ledger are never written)."""
    from valiwatt.monitor.rules import rules_sha256
    from valiwatt.records_v3 import replay_starts_v3
    from valiwatt.report import ledger_v3
    from valiwatt.tracking import code_sha256

    c = _client(c)
    code_sha, rules_sha = code_sha256(), rules_sha256()
    f = follow_v3(c)
    entries = ledger_v3.read()
    starts = replay_starts_v3(HELD_OUT_YEAR, c, {"replays": entries})
    ctx = context_v3(f, entries, starts, code_sha, rules_sha, c)
    pages = render_pages_v3(ctx)  # every page checked before any is written
    out = out_dir()
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for lang, text in pages.items():
        p = out / f"validation.{lang}.md"
        p.write_text(text, encoding="utf-8")
        written.append(p)
    stamp_file().write_text(json.dumps(make_stamp_v3(ctx), indent=2) + "\n", encoding="utf-8")
    print("rendered " + ", ".join(paths.rel(p) for p in written) + f" ({f['lineage']}) and {paths.rel(stamp_file())}")
    return written
