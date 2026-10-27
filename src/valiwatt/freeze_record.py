"""v3's freeze record, `docs/freeze-v3.json` (docs/plan-v3.md, sections 10.3, 11.2 and 11.3), and the one loader of
what judges a v3 replay (`frozen_chain_v3`).

Before any 2025 record reaches the machine, one committed file names the runs of section 17 step 5: the data-quality
run (with its three raw-file hashes), the validation run, the frozen run for the 2024 rehearsal, the rehearsal and the
frozen run for 2025; for each, its code commit, that its tree was clean, its freeze tag, its end time and its rules
hash, which must equal the rules hash at the commit `v3-rules-frozen` was first pinned to (records_v3.first_pin); then
the production model frozen for 2025 and the one hash of every artifact that will judge 2025, item by item
(monitor/run.py `frozen_artifacts_sha256`, v2's function, over v3, its three comparison models, whose hashes cover
their bin edges and half-widths, the thresholds, cold_c and the forecast-drift reference).

- `write_freeze_record` writes it once, the file created exclusively: refused when the file exists, any commit of
  HEAD's history touches it or any commit of the repository's refs adds it, once a 2025 file or a
  through-2025 data-quality run is on the machine, without the tag or its pin, and for any named run that is not
  finished and complete, not under `v3-rules-frozen`, from a dirty tree, under other rules, off the history from the
  first pin to HEAD, of the wrong kind, of another lineage than the others, or whose byte copies, fresh-clone label or
  reproduction did not pass (a fresh clone never gets a freeze record). It reads only.
- `read_freeze_record` reads it as the commit that added it holds it, never the working copy: exactly one commit of
  the head's history touches the path, which is then its addition. A second touch is refused here and by the
  single-commit test; a second addition anywhere in the repository's branches, remote-tracking refs and tags, or an
  addition off the head's history, is refused as the anchor (`FreezeRecordAnchorError`, the 2025 gate's (a)).
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

import pandas as pd

from valiwatt import paths
from valiwatt.config import V3_FREEZE_RECORD, V3_FREEZE_TAG
from valiwatt.monitor.run import FreezeError

PATH = V3_FREEZE_RECORD
ROLES = ("data_quality", "validation", "frozen_2024", "rehearsal_2024", "frozen_2025")
PRED_V3 = "pred_v3"  # v3's production forecast in validation's frames/oos.parquet (model.V3_PRODUCTION)
RUN_ID = re.compile(r"[0-9a-f]{32}")
COMMIT = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")
# The first six fields of every named run, then each role's own (section 10.3, and the fields beyond its list: the end
# time (h) reads, the lineage statuses, the rehearsal's own digest).
COMMON = ("run_id", "git_commit", "git_dirty", "rules_sha256", "freeze_tag", "ended_utc")
FIELDS = {
    "data_quality": COMMON + ("through", "byte_copies", "fresh_clone", "hq_sha256", "eccc_manifests_sha256",
                              "nbm_manifest_sha256"),
    "validation": COMMON + ("dq_run_id", "reproduction", "byte_copies", "fresh_clone"),
    "frozen_2024": COMMON + ("replay_year", "validation_run_id", "model_sha256"),
    "rehearsal_2024": COMMON + ("year", "frozen_run_id", "validation_run_id", "dq_run_id", "model_sha256",
                                "frozen_sha256"),
    "frozen_2025": COMMON + ("replay_year", "validation_run_id", "model_sha256"),
}
TOP = ("freeze_tag", "first_pin", "rules_sha256", "runs", "model_sha256", "frozen_sha256", "frozen_items")
ITEMS = ("production", "comparators", "thresholds", "cold_c", "reference")


class FreezeRecordError(FreezeError):
    """The freeze record cannot be written, or cannot be read as written."""


def _git(*args, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=paths.REPO_ROOT, capture_output=True, text=text, check=False)


def _is_ancestor(a: str, b: str) -> bool:
    """a is b or an ancestor of b."""
    return bool(a) and bool(b) and _git("merge-base", "--is-ancestor", a, b).returncode == 0


# --------------------------------------------------------------------------------- what judges a v3 replay

def forecast_drift_reference_v3(validation_run, year: int) -> pd.DataFrame:
    """The forecast-drift reference of a replayed year, v3's one loader: v2's (monitor/run.py
    `forecast_drift_reference`) on the v3 validation run's frames/oos.parquet, read as a projection onto `day` and v3's
    production forecast (pred_v3), so no target is read; the rows whose day is before <year>-01-01; columns day and
    forecast."""
    import mlflow

    from valiwatt.tracking import client

    client()
    path = mlflow.artifacts.download_artifacts(run_id=validation_run.info.run_id, artifact_path="frames/oos.parquet")
    oos = pd.read_parquet(path, columns=["day", PRED_V3])
    keep = oos["day"] < pd.Timestamp(f"{year}-01-01")
    return pd.DataFrame({"day": oos.loc[keep, "day"], "forecast": oos.loc[keep, PRED_V3]})


def frozen_chain_v3(frozen_run_id: str, validation_run_id: str, year: int, client=None) -> dict:
    """The one loader of what judges a v3 replay of `year`: the 2024 rehearsal, the 2025 test and the evidence read
    their frozen artifacts through it, and the freeze record hashes them through it.

    Returns {"frozen_run", "validation_run", "model" (the production model, from the frozen run, checked against the
    hash its run recorded), "comparators" (v2_noon, v1_noon, v2_midnight, each checked against its recorded hash),
    "thresholds" (the validation run's of `year`), "cold_c", "reference" (forecast_drift_reference_v3),
    "frozen_sha256" (monitor/run.py frozen_artifacts_sha256: the items), "digest" (frozen_digest of the items)}.
    Refused (FreezeRecordError) when a run is not in the store, the frozen run is not a v3 frozen run of `year` built
    on this validation run, or the validation run is not v3's."""
    from valiwatt import tracking
    from valiwatt.forecast.freeze import FrozenModelError, load_comparators
    from valiwatt.forecast.model import V3_COMPARATORS
    from valiwatt.forecast.validate import validation_results
    from valiwatt.monitor import run as mrun

    c = client or tracking.client()
    try:
        frozen = mrun._get_run(c, frozen_run_id, "the frozen run")
        val = mrun._get_run(c, validation_run_id, "the validation run")
    except FreezeError as e:
        raise FreezeRecordError(str(e)) from e
    ft, problems = frozen.data.tags, []
    if (ft.get("kind"), ft.get("version")) != ("frozen", "v3"):
        problems.append(f"frozen run {frozen_run_id} is of kind {ft.get('kind')!r}, version {ft.get('version')!r}, "
                        "not a v3 frozen run")
    if ft.get("validation_run_id") != validation_run_id:
        problems.append(f"frozen run {frozen_run_id} was built on validation run {ft.get('validation_run_id')!r}, "
                        f"not {validation_run_id}")
    if ft.get("replay_year") != str(year):
        problems.append(f"frozen run {frozen_run_id} was frozen for {ft.get('replay_year')!r}, not {year}")
    if val.data.tags.get("kind") != "validation_v3":
        problems.append(f"validation run {validation_run_id} is of kind {val.data.tags.get('kind')!r}, not "
                        "validation_v3")
    if problems:
        raise FreezeRecordError("; ".join(problems))
    try:
        model = mrun._production_model(frozen)
        comparators = load_comparators(frozen, keys=tuple(V3_COMPARATORS.values()))
        vres = validation_results(val)
        thresholds = vres["thresholds"][str(year)]
        cold_c = float(vres["config"]["cold_c"])
    except (FreezeError, FrozenModelError, KeyError, TypeError, ValueError) as e:
        raise FreezeRecordError(f"the artifacts of frozen run {frozen_run_id} and validation run {validation_run_id} "
                                f"cannot be loaded as v3's: {e}") from e
    reference = forecast_drift_reference_v3(val, year)
    items = mrun.frozen_artifacts_sha256(model, comparators, thresholds, cold_c, reference)
    return {"frozen_run": frozen, "validation_run": val, "model": model, "comparators": comparators,
            "thresholds": thresholds, "cold_c": cold_c, "reference": reference, "frozen_sha256": items,
            "digest": mrun.frozen_digest(items)}


# ------------------------------------------------------------------------------------------------- the shape

def check_record(data: dict) -> dict:
    """The fixed shape of the freeze record, field by field and in order (section 10.3); anything else is refused
    (FreezeRecordError), so nothing but these fields is written or read. The record agrees with itself too: every
    run's rules hash is the record's, its runs name each other, and the top hashes are those of the 2025 artifacts'
    items. Returns `data`."""
    def bad(where, value):
        return FreezeRecordError(f"the freeze record's {where} is {value!r}")

    def match(where, value, pattern):
        if not isinstance(value, str) or not pattern.fullmatch(value):
            raise bad(where, value)

    if not isinstance(data, dict) or tuple(data) != TOP:
        raise FreezeRecordError(f"the freeze record must hold exactly {list(TOP)} in that order, not "
                                f"{list(data) if isinstance(data, dict) else type(data).__name__}")
    if data["freeze_tag"] != V3_FREEZE_TAG:
        raise bad("freeze_tag", data["freeze_tag"])
    match("first_pin", data["first_pin"], COMMIT)
    match("rules_sha256", data["rules_sha256"], SHA256)
    runs = data["runs"]
    if not isinstance(runs, dict) or tuple(runs) != ROLES:
        raise FreezeRecordError(f"the freeze record's runs must be exactly {list(ROLES)} in that order")
    for role in ROLES:
        r = runs[role]
        if not isinstance(r, dict) or tuple(r) != FIELDS[role]:
            raise FreezeRecordError(f"the freeze record's {role} must hold exactly {list(FIELDS[role])} in that order")
        for field, pattern in (("run_id", RUN_ID), ("git_commit", COMMIT), ("rules_sha256", SHA256),
                               ("ended_utc", UTC)):
            match(f"{role}.{field}", r[field], pattern)
        if r["git_dirty"] is not False:
            raise bad(f"{role}.git_dirty", r["git_dirty"])
        if r["freeze_tag"] != V3_FREEZE_TAG:
            raise bad(f"{role}.freeze_tag", r["freeze_tag"])
        if r["rules_sha256"] != data["rules_sha256"]:
            raise bad(f"{role}.rules_sha256", r["rules_sha256"])
        for field in ("dq_run_id", "validation_run_id", "frozen_run_id"):
            if field in r:
                match(f"{role}.{field}", r[field], RUN_ID)
        for field in ("hq_sha256", "eccc_manifests_sha256", "nbm_manifest_sha256", "model_sha256", "frozen_sha256"):
            if field in r:
                match(f"{role}.{field}", r[field], SHA256)
        for field in ("byte_copies", "reproduction"):
            if field in r and r[field] != "passed":
                raise bad(f"{role}.{field}", r[field])
        if "fresh_clone" in r and r["fresh_clone"] is not False:
            raise bad(f"{role}.fresh_clone", r["fresh_clone"])
    for role, field, want in (("data_quality", "through", 2024), ("frozen_2024", "replay_year", 2024),
                              ("rehearsal_2024", "year", 2024), ("frozen_2025", "replay_year", 2025)):
        if type(runs[role][field]) is not int or runs[role][field] != want:
            raise bad(f"{role}.{field}", runs[role][field])
    ids = {role: runs[role]["run_id"] for role in ROLES}
    for role, field, other in (("validation", "dq_run_id", "data_quality"),
                               ("frozen_2024", "validation_run_id", "validation"),
                               ("rehearsal_2024", "frozen_run_id", "frozen_2024"),
                               ("rehearsal_2024", "validation_run_id", "validation"),
                               ("rehearsal_2024", "dq_run_id", "data_quality"),
                               ("frozen_2025", "validation_run_id", "validation")):
        if runs[role][field] != ids[other]:
            raise bad(f"{role}.{field}", runs[role][field])
    if runs["rehearsal_2024"]["model_sha256"] != runs["frozen_2024"]["model_sha256"]:
        raise bad("rehearsal_2024.model_sha256", runs["rehearsal_2024"]["model_sha256"])
    items = data["frozen_items"]
    if not isinstance(items, dict) or tuple(items) != ITEMS:
        raise FreezeRecordError(f"the freeze record's frozen_items must hold exactly {list(ITEMS)} in that order")
    for field in ("production", "thresholds", "reference"):
        match(f"frozen_items.{field}", items[field], SHA256)
    comparators = items["comparators"]
    from valiwatt.forecast.model import V3_COMPARATORS

    if not isinstance(comparators, dict) or tuple(comparators) != tuple(sorted(V3_COMPARATORS.values())):
        raise bad("frozen_items.comparators", comparators)
    for k, v in comparators.items():
        match(f"frozen_items.comparators.{k}", v, SHA256)
    cold_c = items["cold_c"]
    try:
        cold_ok = isinstance(cold_c, str) and repr(float(cold_c)) == cold_c
    except ValueError:
        cold_ok = False
    if not cold_ok:
        raise bad("frozen_items.cold_c", cold_c)
    match("model_sha256", data["model_sha256"], SHA256)
    match("frozen_sha256", data["frozen_sha256"], SHA256)
    from valiwatt.monitor.run import frozen_digest

    if data["model_sha256"] != items["production"] or data["model_sha256"] != runs["frozen_2025"]["model_sha256"]:
        raise bad("model_sha256", data["model_sha256"])
    if data["frozen_sha256"] != frozen_digest(items):
        raise bad("frozen_sha256", data["frozen_sha256"])
    return data


def dumps(data: dict) -> str:
    """The record's text: its fields in the order of the fixed shape, never sorted."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


# ------------------------------------------------------------------------------------------------- the writer

def _run_or_refuse(c, run_id: str, role: str):
    from mlflow.exceptions import MlflowException

    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise FreezeRecordError(f"the {role} run is named {run_id!r}, not a run ID")
    try:
        return c.get_run(run_id)
    except MlflowException as e:
        raise FreezeRecordError(f"the {role} run {run_id} is not in v3's run store: {e}") from e


def _named_runs(c, rehearsal_run_id: str, frozen_2025: str | None = None) -> dict:
    """Every run of ROLES, derived from the rehearsal run: its tags name the data-quality, validation and frozen 2024
    runs; the frozen run for 2025 is `frozen_2025` when given (the 2025 gate's: the record's, never the latest), else
    the run of v3's registered model's alias replay-2025."""
    from mlflow.exceptions import MlflowException

    from valiwatt.forecast.freeze_v3 import MODEL_NAME_V3

    rehearsal = _run_or_refuse(c, rehearsal_run_id, "rehearsal_2024")
    t = rehearsal.data.tags
    try:
        frozen_2025 = frozen_2025 or c.get_model_version_by_alias(MODEL_NAME_V3, "replay-2025").run_id
    except MlflowException as e:
        raise FreezeRecordError(f"no model frozen for 2025 ({MODEL_NAME_V3}, alias replay-2025): run "
                                "`valiwatt freeze-v3 --year 2025` first") from e
    named = {"data_quality": t.get("dq_run_id", ""), "validation": t.get("validation_run_id", ""),
             "frozen_2024": t.get("frozen_run_id", ""), "frozen_2025": frozen_2025}
    runs = {role: _run_or_refuse(c, run_id, role) for role, run_id in named.items()}
    return {"data_quality": runs["data_quality"], "validation": runs["validation"],
            "frozen_2024": runs["frozen_2024"], "rehearsal_2024": rehearsal, "frozen_2025": runs["frozen_2025"]}


def _run_problems(role: str, run, first: str, rules: str) -> list[str]:
    """What refuses one named run: not finished and complete (the data-quality run carries no status tag: FINISHED,
    and its gates are checked with its kind), not under v3's tag (a pre-freeze run is refused here), from a dirty tree,
    under other rules than the first pin's, or from a commit that is not a descendant of the first pin and an
    ancestor of HEAD."""
    t, rid, out = run.data.tags, run.info.run_id, []
    if run.info.status != "FINISHED" or (role != "data_quality" and t.get("status") != "complete"):
        out.append(f"{role} run {rid} is {run.info.status}, status tag {t.get('status')!r}: not finished and complete")
    if t.get("freeze_tag") != V3_FREEZE_TAG:
        out.append(f"{role} run {rid} ran under the freeze tag {t.get('freeze_tag')!r}, not `{V3_FREEZE_TAG}`")
    if t.get("git_dirty") != "false":
        out.append(f"{role} run {rid} ran from a tree that was not clean (git_dirty {t.get('git_dirty')!r})")
    if t.get("rules_sha256") != rules:
        out.append(f"{role} run {rid} ran under other rules than those at the commit `{V3_FREEZE_TAG}` was first "
                   f"pinned to ({first[:12]})")
    commit = t.get("git_commit", "")
    if not (_is_ancestor(first, commit) and _is_ancestor(commit, "HEAD")):
        out.append(f"{role} run {rid} ran on commit {commit[:12] or 'none'}, which is not between the first pin "
                   f"{first[:12]} and HEAD")
    return out


def make_record(rehearsal_run_id: str, client=None, frozen_2025: str | None = None) -> dict:
    """The freeze record of the step-5 rehearsal `rehearsal_run_id` and the runs it names (section 10.3), each derived
    and checked; reads only. frozen_2025: the frozen run for 2025 (`_named_runs`); the 2025 gate derives the committed
    record again with it and requires the two equal (monitor/run_v3.py `held_out_2025_v3`). Refused (FreezeRecordError, naming each problem) in this order: `v3-rules-frozen` absent,
    not pinned, or its rules differ from the tree's; any named run not in the store; any named run unfinished, under
    another freeze tag, dirty, under other rules or off the history; a run of another kind than its role's; a lineage
    whose byte copies, fresh-clone label or reproduction did not pass; runs that name each other otherwise; and
    artifacts that do not match the hashes their runs recorded."""
    from valiwatt import records_v3, tracking
    from valiwatt.monitor import run as mrun
    from valiwatt.monitor.rules import rules_sha256

    if records_v3._commit_of(f"refs/tags/{V3_FREEZE_TAG}") is None:
        raise FreezeRecordError(f"no `{V3_FREEZE_TAG}` tag: the freeze record is written on the frozen code")
    try:
        first = records_v3.first_pin()
        records_v3.current_freeze()
    except FreezeError as e:
        raise FreezeRecordError(str(e)) from e
    rules = rules_sha256(first)
    c = client or tracking.client()
    runs = _named_runs(c, rehearsal_run_id, frozen_2025)
    tags = {role: r.data.tags for role, r in runs.items()}
    params = {role: r.data.params for role, r in runs.items()}

    problems = [p for role in ROLES for p in _run_problems(role, runs[role], first, rules)]
    if problems:
        raise FreezeRecordError("; ".join(problems))

    kinds = (("rehearsal_2024", "kind", "replay_v3"), ("rehearsal_2024", "year", "2024"),
             ("validation", "kind", "validation_v3"), ("data_quality", "kind", "dq_v3"),
             ("data_quality", "gates", "passed"), ("frozen_2024", "kind", "frozen"), ("frozen_2024", "version", "v3"),
             ("frozen_2024", "replay_year", "2024"), ("frozen_2025", "kind", "frozen"),
             ("frozen_2025", "version", "v3"), ("frozen_2025", "replay_year", "2025"))
    problems = [f"{role} run {runs[role].info.run_id}: its {k} is {tags[role].get(k)!r}, not {want!r}"
                for role, k, want in kinds if tags[role].get(k) != want]
    if params["data_quality"].get("through") != "2024":
        problems.append(f"data_quality run {runs['data_quality'].info.run_id} reads the window through "
                        f"{params['data_quality'].get('through')!r}, not 2024")
    if problems:
        raise FreezeRecordError("; ".join(problems))

    lineage = [(role, k, want) for role in ("data_quality", "validation", "frozen_2024", "frozen_2025")
               for k, want in (("byte_copies", "passed"), ("fresh_clone", "false"))]
    lineage += [(role, "reproduction", "passed") for role in ("validation", "frozen_2024", "frozen_2025")]
    problems = [f"{role} run {runs[role].info.run_id}: its {k} is {tags[role].get(k)!r}, not {want!r} (a fresh clone, "
                "or a lineage whose checks did not pass, gets no freeze record)"
                for role, k, want in lineage if tags[role].get(k) != want]
    if problems:
        raise FreezeRecordError("; ".join(problems))

    ids = {role: r.info.run_id for role, r in runs.items()}
    agree = (("frozen_2025", "validation_run_id", "validation"), ("frozen_2025", "dq_run_id", "data_quality"),
             ("validation", "dq_run_id", "data_quality"), ("frozen_2024", "validation_run_id", "validation"),
             ("frozen_2024", "dq_run_id", "data_quality"))
    problems = [f"{role} run {ids[role]} names {k} {tags[role].get(k)!r}, not the {other} run {ids[other]}"
                for role, k, other in agree if tags[role].get(k) != ids[other]]
    if problems:
        raise FreezeRecordError("; ".join(problems))

    chain_2025 = frozen_chain_v3(ids["frozen_2025"], ids["validation"], 2025, client=c)
    chain_2024 = frozen_chain_v3(ids["frozen_2024"], ids["validation"], 2024, client=c)
    try:
        replay = mrun._load_json_artifact(c, ids["rehearsal_2024"], "results/replay.json")
        logged = replay["frozen_sha256"]
        rehearsal_digest = mrun.frozen_digest(logged)
    except Exception as e:  # noqa: BLE001 - an unreadable result names nothing: the record is refused
        raise FreezeRecordError(f"the rehearsal run {ids['rehearsal_2024']}'s results/replay.json holds no "
                                f"frozen_sha256: {e}") from e
    if rehearsal_digest != chain_2024["digest"]:
        raise FreezeRecordError(f"the rehearsal run {ids['rehearsal_2024']} was judged by other artifacts than those "
                                f"of frozen run {ids['frozen_2024']}: {mrun._differences(chain_2024['frozen_sha256'], logged)}")
    if tags["rehearsal_2024"].get("coefficient_sha256") != chain_2024["model"].sha256():
        raise FreezeRecordError(f"the rehearsal run {ids['rehearsal_2024']} names another production model than frozen "
                                f"run {ids['frozen_2024']}'s")

    def common(role):
        r = runs[role]
        return {"run_id": r.info.run_id, "git_commit": tags[role]["git_commit"], "git_dirty": False,
                "rules_sha256": tags[role]["rules_sha256"], "freeze_tag": tags[role]["freeze_tag"],
                "ended_utc": mrun._utc(r.info.end_time)}

    dq = params["data_quality"]
    record_runs = {
        "data_quality": common("data_quality") | {
            "through": 2024, "byte_copies": "passed", "fresh_clone": False, "hq_sha256": dq.get("hq_sha256"),
            "eccc_manifests_sha256": dq.get("eccc_manifests_sha256"),
            "nbm_manifest_sha256": dq.get("nbm_manifest_sha256")},
        "validation": common("validation") | {"dq_run_id": ids["data_quality"], "reproduction": "passed",
                                              "byte_copies": "passed", "fresh_clone": False},
        "frozen_2024": common("frozen_2024") | {"replay_year": 2024, "validation_run_id": ids["validation"],
                                                "model_sha256": chain_2024["model"].sha256()},
        "rehearsal_2024": common("rehearsal_2024") | {
            "year": 2024, "frozen_run_id": ids["frozen_2024"], "validation_run_id": ids["validation"],
            "dq_run_id": ids["data_quality"], "model_sha256": chain_2024["model"].sha256(),
            "frozen_sha256": rehearsal_digest},
        "frozen_2025": common("frozen_2025") | {"replay_year": 2025, "validation_run_id": ids["validation"],
                                                "model_sha256": chain_2025["model"].sha256()},
    }
    return {"freeze_tag": V3_FREEZE_TAG, "first_pin": first, "rules_sha256": rules, "runs": record_runs,
            "model_sha256": chain_2025["model"].sha256(), "frozen_sha256": chain_2025["digest"],
            "frozen_items": chain_2025["frozen_sha256"]}


def _require_before_2025(c) -> None:
    """Section 10.3: the record is written before any 2025 record reaches the machine: no Hydro-Québec 2025 file or
    manifest, and no data-quality run of v3's through 2025 in the store, whatever its status."""
    from mlflow.entities import ViewType

    from valiwatt.quality import run_v3
    from valiwatt.sources import hydroquebec

    on_disk = [str(p) for p in (hydroquebec.csv_path(2025), hydroquebec.manifest_path(2025)) if Path(p).exists()]
    if on_disk:
        raise FreezeRecordError(f"2025 is on the machine ({len(on_disk)} Hydro-Québec 2025 files): the freeze record "
                                "is written before any 2025 record reaches it (docs/plan-v3.md, section 10.3)")
    exp = c.get_experiment_by_name(run_v3.EXPERIMENT)
    found = c.search_runs([exp.experiment_id], f"tags.kind = '{run_v3.KIND}' and params.through = '2025'",
                          run_view_type=ViewType.ALL, max_results=1) if exp else []
    if found:
        raise FreezeRecordError(f"the store holds a v3 data-quality run through 2025 ({found[0].info.run_id}): 2025 "
                                "reached the machine before the freeze record")


def write_freeze_record(rehearsal_run_id: str) -> Path:
    """Write `docs/freeze-v3.json` once (section 10.3): refused when the file exists, any commit of HEAD's history
    touches it or any commit of the repository's branches, remote-tracking refs and tags adds it (`adding_commits`: a
    record committed on another branch is the first one), once 2025 is on the machine, then by make_record's refusals;
    the record is checked against its fixed shape and the public-safety scan before it is written, and the file is
    created exclusively (open mode "x"), so a file another writer created meanwhile is refused, never replaced.
    Returns the path."""
    from valiwatt import tracking
    from valiwatt.safety import private_patterns, scan_text

    path = paths.REPO_ROOT / PATH
    if path.exists():
        raise FreezeRecordError(f"{PATH} exists: the freeze record is written once")
    touched = record_commits()
    if touched:
        raise FreezeRecordError(f"commits {[c[:12] for c in touched]} touch {PATH}: the freeze record is written once")
    added = adding_commits()
    if added:
        raise FreezeRecordError(f"commits {[c[:12] for c in added]} of the repository's refs add {PATH}: the freeze "
                                "record is written once")
    c = tracking.client()
    _require_before_2025(c)
    data = check_record(make_record(rehearsal_run_id, client=c))
    text = dumps(data)
    issues = scan_text(PATH, text, private_patterns(paths.REPO_ROOT))
    if issues:
        raise FreezeRecordError(f"the freeze record fails the public-safety scan, so it is not written: {issues}")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as f:
            f.write(text)
    except FileExistsError as e:
        raise FreezeRecordError(f"{PATH} was created while the record was derived: the freeze record is written "
                                "once, and another writer's file is never replaced") from e
    return path


def run_write_freeze_record(rehearsal_run_id: str) -> int:
    """`valiwatt freeze-record-v3 --rehearsal RUN_ID`: v3's first-run guard, then write_freeze_record."""
    from valiwatt.firstrun import require_first_run

    require_first_run()
    path = write_freeze_record(rehearsal_run_id)
    print(f"freeze record written: {paths.rel(path)}; commit it alone, before any 2025 record reaches the machine")
    return 0


# ------------------------------------------------------------------------------------------------- the reader

def record_commits(head: str = "HEAD") -> list[str]:
    """The commits of `head`'s history that touch PATH, oldest first, every one counted (`--full-history`)."""
    out = _git("log", "--format=%H", "--full-history", head, "--", PATH)
    if out.returncode != 0:
        raise FreezeRecordError(f"the history of {PATH} up to {head} cannot be read: {out.stderr.strip()}")
    return list(reversed(out.stdout.split()))


class FreezeRecordAnchorError(FreezeRecordError):
    """The freeze record is added by more than one commit across the repository's refs, or by one that is not an
    ancestor of the head: the record read is not the one first committed (the 2025 gate's condition (a))."""


ANCHOR_REFS = ("--branches", "--remotes", "--tags")


def adding_commits(head: str = "HEAD") -> list[str]:
    """Every commit that adds PATH, in the history of `head` and of every local branch, remote-tracking ref and tag
    the repository holds, oldest first (topological order), each once: a commit whose tree holds PATH while a parent's
    does not, or that has no parent. Read whole (`--full-history`), so a sibling history, or a merge that carries
    another side's record, never hides an addition. A history that cannot be read is refused."""
    out = _git("log", "--full-history", "--topo-order", "--reverse", "--format=%H %P", head, *ANCHOR_REFS, "--",
               PATH)
    if out.returncode != 0:
        raise FreezeRecordError(f"the history of {PATH} across the repository's refs cannot be read: "
                                f"{out.stderr.strip()}")

    def holds(commit: str) -> bool:
        return _git("cat-file", "-e", f"{commit}:{PATH}").returncode == 0

    added = []
    for line in out.stdout.splitlines():
        commit, *parents = line.split()
        if commit not in added and holds(commit) and (not parents or not all(holds(p) for p in parents)):
            added.append(commit)
    return added


def _require_anchor(head: str) -> None:
    """The record's anchor (section 10.3, written once; section 11.3 (3) (a)): at most one commit across the
    repository's refs adds PATH (`adding_commits`), and that commit is `head` or an ancestor of it. Refused
    (FreezeRecordAnchorError), naming the commits: a record committed again on a sibling history (a branch reset to
    the first record's parent, a second record written there) has two additions; a record added only off `head`'s
    history is not the code `head` runs."""
    added = adding_commits(head)
    if len(added) > 1:
        raise FreezeRecordAnchorError(f"{len(added)} commits across the repository's branches, remote-tracking refs "
                                      f"and tags add {PATH} ({[c[:12] for c in added]}): it is written once, by one "
                                      "commit, and a second record never replaces the first")
    if added and not _is_ancestor(added[0], head):
        raise FreezeRecordAnchorError(f"the one commit that adds {PATH}, {added[0][:12]}, is not {head} or an "
                                      "ancestor of it")


def read_freeze_record(head: str = "HEAD") -> dict:
    """The freeze record as the commit that added it holds it (section 10.3), never the working copy: {"commit",
    "data" (check_record of the JSON at that commit), "sha256" (of those bytes), "committer_utc" (that commit's
    committer date, UTC)}. Refused (FreezeRecordError): more than one commit of `head`'s history touches PATH (a later
    edit, or a deletion and a re-addition); then (FreezeRecordAnchorError) more than one commit across the
    repository's refs adds it, or the one that does is not an ancestor of `head` (`_require_anchor`); then no commit
    of `head`'s history touches it; its content is not the fixed shape."""
    commits = record_commits(head)
    if len(commits) > 1:
        raise FreezeRecordError(f"{len(commits)} commits touch {PATH} ({[c[:12] for c in commits]}): it is written "
                                "once, by one commit")
    _require_anchor(head)
    if not commits:
        raise FreezeRecordError(f"no commit of {head}'s history adds {PATH}: commit the freeze record first")
    (commit,) = commits  # the one commit touching the path is its addition: git has no other first touch
    raw = _git("show", f"{commit}:{PATH}", text=False)
    if raw.returncode != 0:
        raise FreezeRecordError(f"{PATH} cannot be read at {commit[:12]}")
    try:
        data = json.loads(raw.stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise FreezeRecordError(f"{PATH} at {commit[:12]} is not JSON: {e}") from e
    date = _git("show", "-s", "--format=%cI", commit).stdout.strip()
    return {"commit": commit, "data": check_record(data), "sha256": hashlib.sha256(raw.stdout).hexdigest(),
            "committer_utc": pd.Timestamp(date).tz_convert("UTC")}
