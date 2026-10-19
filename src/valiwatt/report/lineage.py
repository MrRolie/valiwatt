"""The run chain behind the validation report, followed from the 2024 replay of its freeze and refused at the first
broken link.

replay 2024 -> frozen model 2024 (and its comparison models) -> validation -> data quality
rehearsal 2023 -> frozen model 2023 -> the same validation
soundness (v2, added after the freeze, deciding nothing) -> frozen models 2023 and 2024, the same validation
breaks run
Every run of the chain must be finished and complete, made from the code that renders the report, under the rules
that render it. The 2024 replay is the latest finished one whose run tag `freeze_tag` is the freeze's (a replay
without one is v1's, `rules-frozen`). Under v2 its frozen run's comparison models must have the hashes the replay
logged it was judged by (`frozen_sha256`).

A replay run after a recorded departure brings the earlier replays it departs from, so the report can label it post
hoc and show them beside it. The departures section of the replay's freeze in `AGENTS.md` is read at render time too:
when a departure entry of it names a commit, the replay must be a departure, and a departure replay must have its
freeze commit recorded there (v1: anywhere in its section; v2: the record complete by the replay gate's own rule,
monitor/run.py `departure_record`, every move named from its freeze commit to the next with every rule or dependency
commit between them, the replay's freeze commit one a move ended at, and every earlier freeze commit one a move
started or ended at). v2's section also holds the owner's rulings on 2025, which name commits and are no departures. A
store without the earlier replays (a fresh clone) then shows them from the replay ledger, or refuses the report
instead of dropping the post hoc label. Under v2 a departure's predecessor may be a start that never finished: read
from the store, shown as unfinished, never as a result.

The replay ledger (`docs/replay-ledger.json`, report/ledger.py) stands in for a replay the store does not hold: the
report shows it from its entry, labelled as recorded in the ledger and not re-run here. A replay that both the store
and the ledger hold must agree with its entry, or the chain is refused. v1's history is read from its ledger entries
only: the plain 2024 replay and the latest departure.

Once a 2025 replay has started (docs/plan-v2.md, section 13), two records are added, each read from its own run by
ID, or from its ledger entry when the store lacks it, its code and rules hashes stated and never linked to the
render's: the 2024 replay of record, v2's first 2024 replay (the earliest v2 2024 ledger entry), whose artifacts
judge 2025; and the 2025 test of record (`ledger.of_record`) with every rerun, whose frozen artifacts' hashes must
equal the 2024 record's, their data-quality run read by their own `dq_run_id` and checked as a passing through-2025
run. Every start passed over before the test of record is listed as stopped, never as a result.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field

from valiwatt.paths import REPO_ROOT

# A commit as `check_tag_not_moved` looks for it: at least its 12-character prefix. No English or French word is 12 hex letters long.
COMMIT = re.compile(r"\b[0-9a-f]{12,40}\b")
SHORT_COMMIT = re.compile(r"\b(?=[0-9a-f]*[0-9])[0-9a-f]{7,11}\b")  # a commit named too briefly to be checked
DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})\b")
V1_TAG = "rules-frozen"


class ChainBroken(RuntimeError):
    pass


@dataclass(frozen=True)
class RecordedReplay:
    """A replay the store does not hold, known from its ledger entry alone."""
    entry: dict


@dataclass(frozen=True)
class Unfinished:
    """A replay start that did not finish (a departure's predecessor): read from the store, shown as unfinished,
    never as a result."""
    run: object

    @property
    def run_id(self) -> str:
        return self.run.info.run_id


@dataclass(frozen=True)
class Absent:
    """A departure's predecessor that never finished, named by a record read from the ledger: it is in no ledger, so
    it is held only in the run store of the machine that ran it."""
    run_id: str


@dataclass
class Held:
    """The 2025 starts as the report shows them (plan section 11.3.5): the test of record's start, its run (from the
    store, a RecordedReplay from the ledger, or None when the store lacks a record that did not finish), every start
    passed over before it, every start that did not finish, every rerun (run or RecordedReplay), and the data-quality
    run each record read from the store ran on, by run ID."""
    record: dict
    run: object | None
    stopped: list
    unfinished: list
    reruns: list
    dq: dict = field(default_factory=dict)

    @property
    def finished(self) -> bool:
        return bool(self.record["finished"])


@dataclass
class Chain:
    replay: object
    pseudo: object
    frozen: dict
    validation: object
    dq: object
    breaks: object
    freeze: dict
    departed: list = field(default_factory=list)
    v1_history: list = field(default_factory=list)
    record_2024: object | None = None
    held: Held | None = None
    departures: list = field(default_factory=list)
    predecessors: dict = field(default_factory=dict)
    soundness: object | None = None

    def own(self) -> dict:
        """The runs the report computes from: one chain, from the code and under the rules that render it (v2's
        soundness run, added after the freeze, among them)."""
        out = {"dq": self.dq, "validation": self.validation, "frozen_2023": self.frozen[2023],
               "frozen_2024": self.frozen[2024], "pseudo_replay": self.pseudo, "replay": self.replay,
               "breaks": self.breaks}
        if self.soundness is not None:
            out["soundness"] = self.soundness
        return out

    def runs(self) -> dict:
        """The chain's own runs, then, once a 2025 replay has started, the two records (a run, or a RecordedReplay
        from the ledger)."""
        out = self.own()
        if self.record_2024 is not None:
            out["replay_2024_record"] = self.record_2024
        if self.held is not None and self.held.run is not None:
            out["replay_2025"] = self.held.run
        return out


def run_id_of(run) -> str:
    return run.entry["run_id"] if isinstance(run, RecordedReplay) else run.info.run_id


def _complete(run, what: str) -> None:
    if run.info.status != "FINISHED":
        raise ChainBroken(f"{what} run {run.info.run_id} is {run.info.status}")
    if what == "data-quality":
        if run.data.tags.get("gates") != "passed":
            raise ChainBroken(f"data-quality run {run.info.run_id} did not pass its gates")
    elif run.data.tags.get("status") != "complete":
        raise ChainBroken(f"{what} run {run.info.run_id} is not marked complete")


def _link(run, tag: str, expected: str, what: str) -> None:
    got = run.data.tags.get(tag)
    if got != expected:
        raise ChainBroken(f"{what} run {run.info.run_id}: {tag} is {got!r}, expected {expected!r}")


def resolve(prefix: str) -> str | None:
    """The full commit a prefix names in this repository's history, or None."""
    out = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{prefix}^{{commit}}"], cwd=REPO_ROOT,
                         capture_output=True, text=True, check=False)
    return out.stdout.strip() or None


def is_ancestor(a: str, b: str) -> bool:
    """Whether commit `a` is `b` or one of its ancestors."""
    return subprocess.run(["git", "merge-base", "--is-ancestor", a, b], cwd=REPO_ROOT, capture_output=True,
                          check=False).returncode == 0


def changes(a: str, b: str) -> dict[str, list[str]]:
    """Each commit of a..b in this repository's history with the paths it touched (monitor/run.py `_changes`)."""
    from valiwatt.monitor.run import _changes

    return _changes(a, b, root=REPO_ROOT)


def departure_entries(recorded: str) -> list[dict]:
    """v2's departure entries as the report reads them, each checked by the replay gate's one rule (monitor/run.py
    `departure_record`, on this repository's history): its head and date, the commits it names (their first 12
    characters), and the commits its move started and ended at. An incomplete record is refused."""
    from valiwatt.monitor.run import departure_record

    moves, problems = departure_record(recorded, resolve=resolve, is_ancestor=is_ancestor, changes=changes)
    if problems:
        raise ChainBroken("AGENTS.md's v2 departures section is not a complete record of the tag's moves: "
                          + "; ".join(problems))
    out = []
    for m in moves:
        date = DATE.match(m["head"])
        out.append({"head": m["head"], "kind": "departure", "date": date.group(1) if date else None,
                    "text": m["text"], "named": [c[:12] for c in m["named"]], "from": m["from"], "to": m["to"]})
    return out


def check_recorded(replay, recorded: str, tag: str = V1_TAG) -> list[dict]:
    """The replay agrees with the departures section of its freeze in AGENTS.md: a recorded departure needs a
    departure replay, and a departure replay needs its freeze commit recorded. A section that cannot be found cannot
    be checked. Returns the section's departure entries (v2; v1's section is read as one record: none)."""
    if not recorded.strip():
        raise ChainBroken("AGENTS.md has no departures section under its heading, so the replay cannot be checked "
                          "against it")
    short = SHORT_COMMIT.findall(recorded)
    if short:
        raise ChainBroken(f"AGENTS.md's departures section names {short} by fewer than 12 characters; the freeze commit "
                          "is checked on its first 12, so name it by at least 12")
    departure = replay.data.tags.get("departure") == "true"
    commit = replay.data.tags.get("freeze_commit", "")
    if tag == V1_TAG:
        named = COMMIT.findall(recorded)
        entries = []
    else:
        entries = departure_entries(recorded)
        named = [c for e in entries for c in e["named"]]
    if named and not departure:
        raise ChainBroken(f"AGENTS.md records a departure (commits {[c[:12] for c in named]}), but replay "
                          f"{replay.info.run_id} is not a departure: the earlier replays it departs from must be in "
                          "mlruns/ or docs/replay-ledger.json; run `valiwatt all --departure`")
    if tag == V1_TAG:
        if departure and (not commit or commit[:12] not in recorded):
            raise ChainBroken(f"replay {replay.info.run_id} is a departure, but AGENTS.md's departures section does "
                              f"not name its freeze commit {commit[:12] or '(none)'}")
        return []
    if departure and not any(commit and e["to"] == commit for e in entries):
        raise ChainBroken(f"replay {replay.info.run_id} is a departure, but no departure entry of AGENTS.md's "
                          f"{tag} section moved the tag to its freeze commit {commit[:12] or '(none)'}")
    return entries


def _stored(c, run_id: str):
    """The run, or None when the store does not hold it."""
    from mlflow.exceptions import MlflowException

    try:
        return c.get_run(run_id)
    except MlflowException as e:
        if e.error_code == "RESOURCE_DOES_NOT_EXIST":
            return None
        raise


def check_ledger(c, run, entries: list[dict]) -> None:
    """A replay the store and the ledger both hold must agree with its entry."""
    from valiwatt.report import ledger

    e = ledger.lookup(run.info.run_id, entries)
    if e is None:
        return
    try:
        stored = ledger.entry(run, ledger.results_of(c, run))
    except ledger.LedgerError as err:
        raise ChainBroken(str(err)) from err
    diff = ledger.disagreement(stored, e)
    if diff:
        raise ChainBroken(f"replay {run.info.run_id} disagrees with its ledger entry on {diff}")


def _tag_of(run) -> str:
    return run.data.tags.get("freeze_tag") or V1_TAG


def _latest_replay(tag: str, year: int):
    from valiwatt.monitor.run import EXPERIMENT as MON_EXP
    from valiwatt.tracking import latest_run

    where = f"tags.kind = 'replay' and tags.year = '{year}' and attributes.status = 'FINISHED'"
    if tag != V1_TAG:  # v1's replays carry no freeze tag; v2's carry theirs
        where += f" and tags.freeze_tag = '{tag}'"
    try:
        replay = latest_run(MON_EXP, where)
    except LookupError as e:
        raise ChainBroken(f"no finished {year} replay under `{tag}`: {e}") from e
    if _tag_of(replay) != tag:
        raise ChainBroken(f"the latest finished {year} replay {replay.info.run_id} ran under `{_tag_of(replay)}`, "
                          f"not `{tag}`")
    return replay


def _departed_v1(c, replay, entries: list[dict]) -> list:
    """v1's departed replays, exactly as v1 read them: each finished and complete, from the store or the ledger."""
    from valiwatt.report import ledger

    departed = []
    ids = [x for x in replay.data.tags.get("departure_from", "").split(",") if x]
    if not ids:
        raise ChainBroken(f"replay {replay.info.run_id} is a departure but names no earlier replay")
    for x in ids:
        run = _stored(c, x)
        if run is None:
            e = ledger.lookup(x, entries)
            if e is None:
                raise ChainBroken(f"departed replay {x} is neither in the store nor in the replay ledger")
            if e["status"] != "complete":
                raise ChainBroken(f"departed replay {x} is recorded in the ledger as {e['status']!r}, "
                                  "not complete")
            departed.append(RecordedReplay(e))
            continue
        _complete(run, "departed replay")
        _link(run, "kind", "replay", "departed replay")
        check_ledger(c, run, entries)
        departed.append(run)
    return departed


def _predecessors(c, ids: list[str], entries: list[dict], tag: str, recorded_only: bool) -> list:
    """v2's predecessors of a departure: each a finished replay (from the store, or from the ledger), or a start that
    did not finish (from the store, shown as unfinished). One in neither the store nor the ledger is refused, unless
    the record naming it was itself read from the ledger: a start that never finished is in no ledger, so it is named
    as held only in the run store of the machine that ran it."""
    from valiwatt.report import ledger

    out = []
    for x in ids:
        run = _stored(c, x)
        if run is None:
            e = ledger.lookup(x, entries)
            if e is not None:
                if e["status"] != "complete":
                    raise ChainBroken(f"departed replay {x} is recorded in the ledger as {e['status']!r}, "
                                      "not complete")
                out.append(RecordedReplay(e))
            elif recorded_only:
                out.append(Absent(x))
            else:
                raise ChainBroken(f"departed replay {x} is neither in the store nor in the replay ledger")
            continue
        _link(run, "kind", "replay", "departed replay")
        if _tag_of(run) != tag:
            raise ChainBroken(f"departed replay {x} ran under `{_tag_of(run)}`, not `{tag}`: a freeze departs "
                              "only from its own replays")
        if run.info.status == "FINISHED" and run.data.tags.get("status") == "complete":
            check_ledger(c, run, entries)
            out.append(run)
        else:
            out.append(Unfinished(run))
    return out


def _freeze_commit_of(p) -> str:
    if isinstance(p, RecordedReplay):
        return p.entry["freeze_commit"]
    if isinstance(p, Unfinished):
        return p.run.data.tags.get("freeze_commit", "")
    if isinstance(p, Absent):
        return ""
    return p.data.tags.get("freeze_commit", "")


def check_named(predecessors: list, departures: list[dict], what: str) -> None:
    """Every earlier freeze commit a departure's predecessors ran under must be one a departure entry of the section
    moved the tag from or to (successive departures each have theirs), as the replay gate requires."""
    freezes = {c[:12] for e in departures for c in (e["from"], e["to"])}
    for p in predecessors:
        commit = _freeze_commit_of(p)
        if commit and commit[:12] not in freezes:
            raise ChainBroken(f"{what} departs from a replay under freeze commit {commit[:12]}, which no departure "
                              "entry of AGENTS.md names as a commit the tag moved from or to")


def _departure_ids(run) -> list[str]:
    if isinstance(run, RecordedReplay):
        return list(run.entry["departure_from"])
    return [x for x in run.data.tags.get("departure_from", "").split(",") if x]


def _record(c, run_id: str, entries: list[dict], year: int, tag: str, what: str):
    """A record read from its own run by ID, finished and complete, of `year` under `tag`, agreeing with its ledger
    entry; or, when the store lacks it, its ledger entry."""
    from valiwatt.report import ledger

    run = _stored(c, run_id)
    if run is None:
        e = ledger.lookup(run_id, entries)
        if e is None:
            raise ChainBroken(f"{what} {run_id} is neither in the store nor in the replay ledger")
        return RecordedReplay(e)
    _complete(run, what)
    _link(run, "kind", "replay", what)
    _link(run, "year", str(year), what)
    if _tag_of(run) != tag:
        raise ChainBroken(f"{what} {run_id} ran under `{_tag_of(run)}`, not `{tag}`")
    check_ledger(c, run, entries)
    return run


def _frozen_digest_of(c, run, entries: list[dict]) -> str:
    from valiwatt.monitor.run import frozen_digest
    from valiwatt.report import ledger

    if isinstance(run, RecordedReplay):
        return run.entry["frozen_sha256"]
    results = ledger.results_of(c, run)
    if not isinstance(results.get("frozen_sha256"), dict):
        raise ChainBroken(f"replay {run.info.run_id} logged no frozen_sha256 of what it was judged by")
    return frozen_digest(results["frozen_sha256"])


def _gate_record(c, run) -> dict | None:
    import json
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        try:
            p = Path(c.download_artifacts(run.info.run_id, "results/held_out_gate.json", tmp))
        except Exception:  # noqa: BLE001 - a run without the record
            return None
        return json.loads(p.read_text(encoding="utf-8"))


def _through_2025(c, run_id: str, what: str):
    from valiwatt.config import HELD_OUT_YEAR, data_last

    dq = _stored(c, run_id) if run_id else None
    if dq is None:
        raise ChainBroken(f"{what}: its data-quality run {run_id or '(none)'} is not in the store")
    _complete(dq, "data-quality")
    params = dq.data.params
    if str(params.get("through")) != str(HELD_OUT_YEAR) or params.get("window_last_utc") != \
            data_last(HELD_OUT_YEAR).isoformat():
        raise ChainBroken(f"{what}: its data-quality run {run_id} does not reach the end of {HELD_OUT_YEAR}")
    return dq


def _held(c, entries: list[dict], starts: list[dict], record_2024, tag: str, digest_2024: str, departures: list,
          predecessors: dict) -> Held:
    """The 2025 starts as the report shows them: the test of record (`ledger.of_record`), the starts it passed over,
    the reruns; each record read from its own run (or its ledger entry), its frozen artifacts' hashes those of the
    2024 record, its data-quality run a passing through-2025 run read by its own ID."""
    from valiwatt.config import HELD_OUT_YEAR
    from valiwatt.monitor.run import frozen_digest
    from valiwatt.report import ledger
    from valiwatt.report.render import ReportRefused

    try:
        walk = ledger.of_record(entries, starts, HELD_OUT_YEAR, tag)
    except ReportRefused as e:
        raise ChainBroken(str(e)) from e
    rec = walk["record"]
    dq: dict = {}

    def judged(run, what):
        if frozen_hashes(run) != digest_2024:
            raise ChainBroken(f"{what} {run_id_of(run)} was judged by frozen artifacts whose hashes differ from the "
                              f"2024 replay of record's ({run_id_of(record_2024)}): 2025 is judged by exactly the "
                              "artifacts frozen for v2's first 2024 replay")

    def frozen_hashes(run):
        return _frozen_digest_of(c, run, entries)

    def follow_record(run, what):
        judged(run, what)
        if isinstance(run, RecordedReplay):
            predecessors[run_id_of(run)] = _predecessors(c, _departure_ids(run), entries, tag, True)
            return
        if run.data.tags.get("standing", "") == "" or _gate_record(c, run) is None:
            raise ChainBroken(f"{what} {run.info.run_id} finished, but its run lacks the standing its gate logged "
                              "when it opened, or the gate's record (results/held_out_gate.json)")
        dq[run.info.run_id] = _through_2025(c, run.data.tags.get("dq_run_id", ""), f"{what} {run.info.run_id}")
        if run.data.tags.get("departure") == "true":
            predecessors[run.info.run_id] = _predecessors(c, _departure_ids(run), entries, tag, False)
            check_named(predecessors[run.info.run_id], departures, f"{what} {run.info.run_id}")

    if rec["finished"]:
        run = _record(c, rec["run_id"], entries, HELD_OUT_YEAR, tag, f"the {HELD_OUT_YEAR} test of record")
        follow_record(run, f"the {HELD_OUT_YEAR} test of record")
    else:
        run = _stored(c, rec["run_id"])  # listed as unfinished: never a result
        if run is not None:
            g = _gate_record(c, run)
            if g is not None and isinstance(g.get("frozen_sha256"), dict) and \
                    frozen_digest(g["frozen_sha256"]) != digest_2024:
                raise ChainBroken(f"the unfinished {HELD_OUT_YEAR} test of record {rec['run_id']}: its gate's "
                                  "record names frozen artifacts whose hashes differ from the 2024 replay of record's")
            if run.data.tags.get("dq_run_id"):
                dq[rec["run_id"]] = _through_2025(c, run.data.tags["dq_run_id"],
                                                  f"the unfinished {HELD_OUT_YEAR} test of record {rec['run_id']}")
    reruns = []
    for e in walk["reruns"]:
        r = _record(c, e["run_id"], entries, HELD_OUT_YEAR, tag, f"the {HELD_OUT_YEAR} rerun")
        follow_record(r, f"the {HELD_OUT_YEAR} rerun")
        reruns.append(r)
    return Held(rec, run, walk["stopped"], walk["unfinished"], reruns, dq)


def v1_history(entries: list[dict]) -> list:
    """v1's 2024 replays as the v2 report shows them, from v1's ledger entries only (`rules-frozen`): the plain
    replay and the latest departure, each a RecordedReplay."""
    v1 = [e for e in entries if e["freeze_tag"] == V1_TAG]
    plain = [e for e in v1 if not e["departure"]]
    departed = [e for e in v1 if e["departure"]]
    if not plain:
        raise ChainBroken("the replay ledger holds no plain v1 2024 replay: v1's 2024 numbers come from its ledger "
                          "entries only")
    return [RecordedReplay(plain[0])] + ([RecordedReplay(departed[-1])] if departed else [])


def follow(code_sha: str, rules_sha: str, freeze: dict, *, recorded: str | None = None,
           entries: list[dict] | None = None, starts: list[dict] | None = None) -> Chain:
    """The chain of `freeze["freeze_tag"]`. `recorded` is that freeze's departures section of AGENTS.md; None reads it
    (monitor.run.recorded_departures). `entries` is the replay ledger; None reads docs/replay-ledger.json. `starts`
    is the 2025 starts (`ledger.starts`, the store's and the ledger's); None reads them, for a freeze that replays
    2025."""
    from valiwatt.breaks import EXPERIMENT as BREAKS_EXP
    from valiwatt.config import HELD_OUT_YEAR
    from valiwatt.monitor.run import EXPERIMENT as MON_EXP
    from valiwatt.monitor.run import recorded_departures
    from valiwatt.report import ledger
    from valiwatt.tracking import client, latest_run

    tag = freeze.get("freeze_tag") or V1_TAG
    v2 = ledger.is_v2(tag)
    c = client()
    replay = _latest_replay(tag, 2024)
    _complete(replay, "replay")
    if entries is None:
        try:
            entries = ledger.read()
        except ledger.LedgerError as e:
            raise ChainBroken(f"the replay ledger cannot be read: {e}") from e
    check_ledger(c, replay, entries)
    section = recorded_departures(tag=tag) if recorded is None else recorded
    departures = check_recorded(replay, section, tag)
    departed, predecessors = [], {}
    if replay.data.tags.get("departure") == "true":
        if not v2:
            departed = _departed_v1(c, replay, entries)
        else:
            ids = _departure_ids(replay)
            if not ids:
                raise ChainBroken(f"replay {replay.info.run_id} is a departure but names no earlier replay")
            departed = _predecessors(c, ids, entries, tag, False)
            check_named(departed, departures, f"replay {replay.info.run_id}")
            predecessors[replay.info.run_id] = departed
    val_id = replay.data.tags.get("validation_run_id", "")
    try:
        pseudo = latest_run(MON_EXP, "tags.kind = 'pseudo_replay' and attributes.status = 'FINISHED' "
                                     f"and tags.validation_run_id = '{val_id}'")
        breaks = latest_run(BREAKS_EXP, "tags.kind = 'breaks' and attributes.status = 'FINISHED'")
    except LookupError as e:
        raise ChainBroken(str(e)) from e
    _complete(pseudo, "pseudo-replay")
    _complete(breaks, "breaks")
    validation = c.get_run(val_id)
    _complete(validation, "validation")
    dq = c.get_run(validation.data.tags.get("dq_run_id", ""))
    _complete(dq, "data-quality")
    frozen = {}
    for year, run in ((2024, replay), (2023, pseudo)):
        f = c.get_run(run.data.tags.get("frozen_run_id", ""))
        _complete(f, f"frozen-model {year}")
        _link(f, "replay_year", str(year), f"frozen-model {year}")
        _link(f, "validation_run_id", val_id, f"frozen-model {year}")
        _link(run, "coefficient_sha256", f.data.tags.get("coefficient_sha256", ""), f"replay {year}")
        if v2:
            _check_comparators(c, run, f, year)
        frozen[year] = f
    chain = Chain(replay, pseudo, frozen, validation, dq, breaks, freeze, departed,
                  soundness=_soundness(frozen, val_id) if v2 else None)
    for name, run in chain.own().items():
        if name != "dq":
            _link(run, "rules_sha256", rules_sha, name)
        if name in ("validation", "frozen_2023", "frozen_2024", "pseudo_replay", "replay", "soundness"):
            _link(run, "dq_run_id", dq.info.run_id, name)
        _link(run, "code_sha256", code_sha, name)
    _link(replay, "freeze_commit", freeze["freeze_commit"], "replay")
    chain.departures, chain.predecessors = departures, predecessors
    if v2:
        chain.v1_history = v1_history(entries)
        if HELD_OUT_YEAR in ledger.YEARS[tag]:
            held = ledger.starts(c, HELD_OUT_YEAR, tag) if starts is None else starts
            if held:
                _records(chain, c, entries, held, tag)
    return chain


def _soundness(frozen: dict, val_id: str):
    """v2's soundness run (report/soundness.py, added after the freeze, deciding nothing): the latest finished one of
    the chain's frozen 2024 run, which must name the chain's frozen 2023 run and validation run and the hashes of both
    frozen production models; refused otherwise, as every other link of the chain."""
    from valiwatt.report.soundness import EXPERIMENT, FREEZES, KIND
    from valiwatt.tracking import latest_run

    try:
        run = latest_run(EXPERIMENT, f"tags.kind = '{KIND}' and attributes.status = 'FINISHED' "
                                     f"and tags.frozen_run_id_{max(FREEZES)} = "
                                     f"'{frozen[max(FREEZES)].info.run_id}'")
    except LookupError as e:
        raise ChainBroken(f"no finished soundness run of the chain's frozen models ({e}): run `valiwatt soundness`") \
            from e
    _complete(run, "soundness")
    _link(run, "validation_run_id", val_id, "soundness")
    for year in FREEZES:
        _link(run, f"frozen_run_id_{year}", frozen[year].info.run_id, "soundness")
        _link(run, f"coefficient_sha256_{year}", frozen[year].data.tags.get("coefficient_sha256", ""), "soundness")
    return run


def _check_comparators(c, run, frozen_run, year: int) -> None:
    """The frozen run's comparison models have the hashes the replay logged it was judged by, and so has its
    production model."""
    from valiwatt.report import ledger

    try:
        results = ledger.results_of(c, run)
    except ledger.LedgerError as e:
        raise ChainBroken(str(e)) from e
    judged = results.get("frozen_sha256")
    if not isinstance(judged, dict) or not isinstance(judged.get("comparators"), dict):
        raise ChainBroken(f"replay {year} run {run.info.run_id} logged no frozen_sha256 of what it was judged by")
    if judged.get("production") != frozen_run.data.tags.get("coefficient_sha256"):
        raise ChainBroken(f"replay {year} run {run.info.run_id} was judged by another production model than frozen "
                          f"run {frozen_run.info.run_id}'s")
    tagged = {k[len("comparator_sha256."):]: v for k, v in frozen_run.data.tags.items()
              if k.startswith("comparator_sha256.")}
    if tagged != judged["comparators"]:
        diff = sorted(k for k in set(tagged) | set(judged["comparators"])
                      if tagged.get(k) != judged["comparators"].get(k))
        raise ChainBroken(f"frozen-model {year} run {frozen_run.info.run_id}: its comparison models {diff} have "
                          f"other hashes than replay {run.info.run_id} logged it was judged by")


def _records(chain: Chain, c, entries: list[dict], starts: list[dict], tag: str) -> None:
    """The two records a started 2025 adds: v2's first 2024 replay, of record, and the 2025 test of record with every
    rerun and every start it passed over."""
    first = first_replays(entries, tag)
    if not first:
        raise ChainBroken("a 2025 replay has started, but the replay ledger holds no v2 2024 entry: the 2024 replay "
                          "of record is v2's first 2024 replay, whose artifacts judge 2025")
    record_2024 = _record(c, first[0]["run_id"], entries, 2024, tag, "the 2024 replay of record")
    rid = run_id_of(record_2024)
    if isinstance(record_2024, RecordedReplay):
        chain.predecessors[rid] = _predecessors(c, _departure_ids(record_2024), entries, tag, True)
    elif record_2024.data.tags.get("departure") == "true" and rid != chain.replay.info.run_id:
        chain.predecessors[rid] = _predecessors(c, _departure_ids(record_2024), entries, tag, False)
        check_named(chain.predecessors[rid], chain.departures, f"the 2024 replay of record {rid}")
    chain.record_2024 = record_2024
    digest = first[0]["frozen_sha256"]
    chain.held = _held(c, entries, starts, record_2024, tag, digest, chain.departures, chain.predecessors)


def first_replays(entries: list[dict], tag: str) -> list[dict]:
    """The ledger's entries of `tag`'s first replayed year (2024 for v2), earliest start first: the first is v2's first
    2024 replay, whose frozen artifacts judge 2025."""
    return sorted((e for e in entries if e["freeze_tag"] == tag and e["year"] == min(ledger_years(tag))),
                  key=lambda e: (e["started_utc"], e["run_id"]))


def ledger_years(tag: str) -> tuple:
    from valiwatt.report import ledger

    return ledger.YEARS[tag]
