"""A fake MLflow store holding a v2 chain, its runs tagged as lane D's spec and lane C's code tag them and their
artifacts the v2 payloads of report_payloads, so that `lineage.follow` and `validation.context` run end to end on it.

`World()` is the plain chain: the data-quality run (through 2024), the validation, the frozen models of 2023 and 2024
(with their comparators' hashes), the soundness run on both (report/soundness.py, its results report_payloads'
`soundness`), the 2023 rehearsal and the 2024 replay (each with its diagnostic run), the breaks;
the replay ledger holding v1's two entries. `add_2025()` adds a through-2025 data-quality run and a finished 2025 test
of record on the 2024 replay's frozen artifacts, with its gate's record and its diagnostics. Each test spoils one
thing of it. Commits are fakes: `lineage.resolve`, `lineage.is_ancestor` and `lineage.changes` read them from
`History`.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import report_payloads as RP
import report_world as W
from mlflow.exceptions import MlflowException
from mlflow.protos.databricks_pb2 import RESOURCE_DOES_NOT_EXIST

from valiwatt import tracking
from valiwatt.config import data_last
from valiwatt.monitor import run as monitor_run
from valiwatt.report import ledger, lineage

CODE, RULES, TAG = W.CODE, W.RULES, W.FREEZE_TAG
FREEZE = {"freeze_tag": TAG, "freeze_commit": W.F0}
# v2's departures section of AGENTS.md as the plan writes it before any departure.
NONE_RECORDED = ("\n\nThe rules are frozen by the `rules-frozen-v2` tag before v2's 2024 replay. Each entry is one of "
                 "two kinds.\n\nNone yet.\n")
FROZEN23 = {**W.FROZEN, "production": "7" * 64, "thresholds": "8" * 64}
START = 1_800_000_000_000  # ms: the chain's runs start from here, one hour apart
REPLAY = "a" * 32  # the chain's 2024 replay (a ledger entry needs a run ID of 32 hexadecimal characters)


def departure_entry(date: str, named: list[str], head: str | None = None) -> str:
    """A departure entry of v2's section: its bold head ends "a departure"; it names the commits given."""
    head = head or f"{date}, the owner's ruling; a departure"
    return f"- **{head}.** The tag moved from `{named[0][:12]}` to `{named[-1][:12]}`" + "".join(
        f", through `{c[:12]}`" for c in named[1:-1]) + ".\n"


def ruling_entry(date: str, outcome: str, commit: str, start: str = "") -> str:
    words = "clean" if outcome == "clean" else "post hoc"
    tail = f" It rules on the start of {start}." if start else ""
    return f"- **{date}, the owner's ruling on 2025: {words}.** It applies to commit `{commit[:12]}`.{tail}\n"


class History:
    """The fake commits, in order: each is an ancestor of every later one."""

    def __init__(self, *commits):
        self.order = list(commits)

    def resolve(self, prefix):
        hits = [c for c in self.order if c.startswith(prefix)]
        return hits[0] if hits else None

    def is_ancestor(self, a, b):
        return a in self.order and b in self.order and self.order.index(a) <= self.order.index(b)

    def changes(self, a, b):
        """The commits of a..b, each a rule change (every fake commit after the first is one)."""
        i, j = self.order.index(a), self.order.index(b)
        return {c: ["src/valiwatt/monitor/rules.py"] for c in self.order[i + 1:j + 1]}


def _plain(x):
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, np.floating):
        return None if np.isnan(x) else float(x)
    if isinstance(x, float) and np.isnan(x):
        return None
    if isinstance(x, pd.Timestamp):
        return x.isoformat()
    return x


def fake_run(run_id, run_status="FINISHED", start=None, params=None, metrics=None, **tags):
    start = START if start is None else start
    return SimpleNamespace(info=SimpleNamespace(run_id=run_id, status=run_status, start_time=start,
                                                end_time=start + 3_600_000),
                           data=SimpleNamespace(tags=dict(tags), params=dict(params or {}), metrics=dict(metrics or {})))


def matches(run, filter_string: str) -> bool:
    for cond in filter_string.split(" and "):
        cond = cond.strip()
        if not cond:
            continue
        key, value = (x.strip() for x in cond.split("=", 1))
        value = value.strip("'")
        if key == "attributes.status":
            got = run.info.status
        elif key.startswith("tags."):
            got = run.data.tags.get(key[5:])
        elif key.startswith("params."):
            got = run.data.params.get(key[7:])
        else:
            raise ValueError(f"the fake store cannot read {cond!r}")
        if got != value:
            return False
    return True


class World:
    """The fake store and the files its runs hold."""

    def __init__(self):
        v, dq, p, r, b, frozen, oos, diag = RP.payloads()
        self.payloads = {"v": v, "dq": dq, "p": p, "r": r, "b": b, "frozen": frozen, "oos": oos, "diag": diag}
        self.runs: dict = {}
        self.files: dict = {}
        self.section = NONE_RECORDED
        self.entries = [dict(RP.V1_PLAIN), dict(RP.V1_DEPARTED)]
        self.starts: list = []
        self.history = History(W.F0, W.F1, W.F2)
        self.t = START
        made = {"code_sha256": CODE, "rules_sha256": RULES, "status": "complete"}
        on_dq = made | {"dq_run_id": "dq"}
        self.add(fake_run("dq", start=self.tick(), params={"through": "2024",
                                                           "window_last_utc": data_last(2024).isoformat()},
                          gates="passed", code_sha256=CODE, kind="dq"),
                 {"results/dq_results.json": {**dq, "run_id": "dq"}})
        self.add(fake_run("val", start=self.tick(), **on_dq, kind="validation"),
                 {"results/validation.json": {**v, "run_id": "val"}, "frames/oos.parquet": oos})
        for year, fz, digest in ((2023, frozen[2023], FROZEN23), (2024, frozen[2024], W.FROZEN)):
            self.add(fake_run(f"f{year}", start=self.tick(), params=fz["params"], **on_dq, replay_year=str(year),
                              validation_run_id="val", coefficient_sha256=digest["production"],
                              **{f"comparator_sha256.{k}": h for k, h in digest["comparators"].items()}),
                     {"coefficients.csv": pd.DataFrame(fz["coefficients"]),
                      "partial_effects.csv": pd.DataFrame(RP.curves())})
        shas = {2023: FROZEN23["production"], 2024: W.FROZEN["production"]}
        self.add(fake_run(RP.SOUND_RUN, start=self.tick(), **on_dq, kind="soundness", validation_run_id="val",
                          frozen_run_id_2023="f2023", frozen_run_id_2024="f2024",
                          **{f"coefficient_sha256_{y}": h for y, h in shas.items()}),
                 {"results/soundness.json": RP.soundness(RP.SOUND_RUN, shas)})
        self.add(fake_run("pseudo", start=self.tick(), **on_dq, kind="pseudo_replay", year="2023",
                          validation_run_id="val", frozen_run_id="f2023", coefficient_sha256=FROZEN23["production"],
                          evaluation="rehearsal"),
                 {"results/replay.json": {**p, "frozen_sha256": FROZEN23}, "frames/hourly.parquet": RP.hourly(2023)})
        self.add_replay(REPLAY, r, start=self.tick())
        self.add(fake_run("breaks", start=self.tick(), **made, kind="breaks"), {"results/breaks.json": b})
        self.add_diagnostics("pseudo", diag["pseudo"])
        self.add_diagnostics(REPLAY, diag["replay"])

    def tick(self) -> int:
        self.t += 3_600_000
        return self.t

    def add(self, run, files: dict | None = None):
        self.runs[run.info.run_id] = run
        for path, content in (files or {}).items():
            self.files[(run.info.run_id, path)] = content
        return run

    def add_replay(self, run_id: str, results: dict, year: int = 2024, start: int | None = None,
                   freeze_commit: str = W.F0, evaluation: str | None = None, status: str = "FINISHED",
                   frozen: dict | None = None, **tags):
        evaluation = evaluation or {2024: "seen_by_developers", 2025: "held_out"}[year]
        run = fake_run(run_id, run_status=status, start=self.tick() if start is None else start,
                       params={"lag24_mw_per_mw": "0.4"}, code_sha256=CODE, rules_sha256=RULES, status="complete",
                       dq_run_id="dq", kind="replay", year=str(year), freeze_tag=TAG, freeze_commit=freeze_commit,
                       evaluation=evaluation, validation_run_id="val", frozen_run_id="f2024",
                       coefficient_sha256=W.MODEL, departure="false", departure_from="", git_commit="1" * 40,
                       git_dirty="false")
        run.data.tags |= tags
        body = {**results, "year": year, "freeze_tag": TAG, "freeze_commit": freeze_commit,
                "frozen_sha256": frozen or W.FROZEN}
        return self.add(run, {"results/replay.json": body, "frames/hourly.parquet": RP.hourly(year)})

    def add_diagnostics(self, replay_id: str, results: dict, status: str = "FINISHED"):
        return self.add(fake_run(f"diag-{replay_id}", run_status=status, start=self.tick(), kind="diagnostic",
                                 replay_run_id=replay_id, status="complete"),
                        {"results/diagnostics.json": results})

    def entry(self, run_id: str) -> dict:
        """The ledger entry the store's run gives (report/ledger.py `entry`)."""
        run = self.runs[run_id]
        return ledger.entry(run, _plain(self.files[(run_id, "results/replay.json")]))

    def add_2025(self, run_id: str = "b" * 32, verdict=("better", "better"), standing: str = "clean",
                 gate: dict | None = None, revised: int = 3, at_or_above=(), frozen: dict | None = None,
                 diag: bool = True, finished: bool = True, evaluation: str | None = None, **tags):
        """A through-2025 data-quality run and a 2025 replay on it; v2's first 2024 replay entered in the ledger
        first, as the sync before the start would have; the 2025 start in `starts`."""
        if not any(e["run_id"] == REPLAY for e in self.entries):
            self.entries.append(self.entry(REPLAY))
        results, rows = RP.dq_2025(revised=revised, at_or_above=at_or_above)
        dq_id = "dq25"
        if dq_id not in self.runs:
            self.add(fake_run(dq_id, start=self.tick(), params={"through": "2025",
                                                                "window_last_utc": data_last(2025).isoformat()},
                              gates="passed", code_sha256=CODE, kind="dq", status="complete"),
                     {"results/dq_results.json": {**self.payloads["dq"], **results, "run_id": dq_id},
                      "revisions/hydroquebec.csv": rows})
        label = evaluation or {"clean": "held_out", "post_hoc": "post_hoc"}[standing]
        g = gate if gate is not None else W.gate(standing, ["a"] if standing == "post_hoc" else [],
                                                 W.ruling("post_hoc") if standing == "post_hoc" else None)
        c = RP.contrasts_for(verdict, label)
        body = {**self.payloads["r"], "contrasts": c, "verdict": dict(zip(("shipped", "family"), verdict)) | {
            "label": label}}
        body.pop("v1_reconciliation", None)
        run = self.add_replay(run_id, body, year=2025, evaluation=label, standing=standing, dq_run_id=dq_id,
                              frozen=frozen, status="FINISHED" if finished else "FAILED", **tags)
        if not finished:
            run.data.tags["status"] = "started"
            run.data.tags["stage"] = "replayed"
        self.files[(run_id, "results/held_out_gate.json")] = g
        if diag and finished:
            diag = RP.diagnostics(RP.Distinct(), 2025, 703)
            diag["reuse_sensitivity"]["longest_lead_h"] = 76.0 if 2025 in at_or_above else 71.0  # its dq run's
            self.add_diagnostics(run_id, diag)
        if finished:
            self.entries.append(self.entry(run_id))
        started = ledger._utc(run.info.start_time)
        self.starts.append(W.start(run_id, started, finished, g, standing=standing if finished else "",
                                   stage="logged" if finished else "replayed"))
        return run

    # --- the store's interface ------------------------------------------------------------------------------------

    def latest_run(self, experiment, filter_string=""):
        hits = [r for r in self.runs.values() if matches(r, filter_string)]
        if not hits:
            raise LookupError(f"no run matches {filter_string!r}")
        return max(hits, key=lambda r: r.info.start_time)

    def get_run(self, run_id):
        if run_id not in self.runs:
            raise MlflowException(f"Run with id={run_id} not found", RESOURCE_DOES_NOT_EXIST)
        return self.runs[run_id]

    def download_artifacts(self, run_id, path, dst):
        if (run_id, path) not in self.files:
            raise MlflowException(f"no artifact {path} in run {run_id}", RESOURCE_DOES_NOT_EXIST)
        content = self.files[(run_id, path)]
        out = Path(dst) / path
        out.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, pd.DataFrame):
            content.to_parquet(out) if out.suffix == ".parquet" else content.to_csv(out, index=False)
        else:
            out.write_text(json.dumps(_plain(content)), encoding="utf-8")
        return str(out)

    def install(self, monkeypatch):
        client = SimpleNamespace(get_run=self.get_run, download_artifacts=self.download_artifacts)
        monkeypatch.setattr(tracking, "latest_run", self.latest_run)
        monkeypatch.setattr(tracking, "client", lambda: client)
        monkeypatch.setattr(monitor_run, "recorded_departures", lambda agents=None, tag=None: self.section)
        monkeypatch.setattr(ledger, "read", lambda path=None: list(self.entries))
        monkeypatch.setattr(lineage, "resolve", self.history.resolve)
        monkeypatch.setattr(lineage, "is_ancestor", self.history.is_ancestor)
        monkeypatch.setattr(lineage, "changes", self.history.changes)
        W.install_lane_d(monkeypatch, starts=self.starts)
        return self

    def follow(self, **kw):
        return lineage.follow(CODE, RULES, FREEZE, recorded=self.section, entries=list(self.entries),
                              starts=list(self.starts), **kw)

    def context(self, chain=None):
        from valiwatt.report import validation as RV

        chain = chain or self.follow()
        return RV.context(chain, CODE, RULES, list(self.entries), list(self.starts))
