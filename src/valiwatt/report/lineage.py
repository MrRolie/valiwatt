"""The run chain behind the validation report, followed from the 2024 replay and refused at the first broken link.

replay 2024 -> frozen model 2024 -> validation -> data quality
pseudo-replay 2023 -> frozen model 2023 -> the same validation
breaks run
Every run must be finished and complete, made from the code that renders the report, under the rules of the
`rules-frozen` tag. A replay run after a recorded departure brings the earlier replays it departs from, so the report
can label it post hoc and show them beside it. The departures section of `AGENTS.md` is read at render time too: when
it names a commit, the replay must be a departure, and a departure replay must have its freeze commit named there. A
store without the earlier replays (a fresh clone) then refuses the report instead of dropping the post hoc label.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# A commit as `check_tag_not_moved` looks for it: at least its 12-character prefix. No English or French word is 12 hex letters long.
COMMIT = re.compile(r"\b[0-9a-f]{12,40}\b")
SHORT_COMMIT = re.compile(r"\b(?=[0-9a-f]*[0-9])[0-9a-f]{7,11}\b")  # a commit named too briefly to be checked


class ChainBroken(RuntimeError):
    pass


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

    def runs(self) -> dict:
        return {"dq": self.dq, "validation": self.validation, "frozen_2023": self.frozen[2023],
                "frozen_2024": self.frozen[2024], "pseudo_replay": self.pseudo, "replay": self.replay,
                "breaks": self.breaks}


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


def check_recorded(replay, recorded: str) -> None:
    """The replay agrees with the departures section of AGENTS.md: a recorded departure needs a departure replay, and
    a departure replay needs its freeze commit recorded. A section that cannot be found cannot be checked."""
    if not recorded.strip():
        raise ChainBroken("AGENTS.md has no departures section under its heading, so the replay cannot be checked "
                          "against it")
    short = SHORT_COMMIT.findall(recorded)
    if short:
        raise ChainBroken(f"AGENTS.md's departures section names {short} by fewer than 12 characters; the freeze commit "
                          "is checked on its first 12, so name it by at least 12")
    named = COMMIT.findall(recorded)
    departure = replay.data.tags.get("departure") == "true"
    if named and not departure:
        raise ChainBroken(f"AGENTS.md records a departure (commits {[c[:12] for c in named]}), but replay "
                          f"{replay.info.run_id} is not a departure: the store must hold the earlier replays it "
                          "departs from, then run `valiwatt all --departure`")
    commit = replay.data.tags.get("freeze_commit", "")
    if departure and (not commit or commit[:12] not in recorded):
        raise ChainBroken(f"replay {replay.info.run_id} is a departure, but AGENTS.md's departures section does not "
                          f"name its freeze commit {commit[:12] or '(none)'}")


def follow(code_sha: str, rules_sha: str, freeze: dict, *, recorded: str | None = None) -> Chain:
    """`recorded` is the departures section of AGENTS.md; None reads it from the file."""
    from valiwatt.breaks import EXPERIMENT as BREAKS_EXP
    from valiwatt.monitor.run import EXPERIMENT as MON_EXP
    from valiwatt.monitor.run import recorded_departures
    from valiwatt.tracking import client, latest_run

    c = client()
    try:
        replay = latest_run(MON_EXP, "tags.kind = 'replay' and attributes.status = 'FINISHED'")
    except LookupError as e:
        raise ChainBroken(f"no finished 2024 replay: {e}") from e
    _complete(replay, "replay")
    departed = []
    if replay.data.tags.get("departure") == "true":
        ids = [x for x in replay.data.tags.get("departure_from", "").split(",") if x]
        if not ids:
            raise ChainBroken(f"replay {replay.info.run_id} is a departure but names no earlier replay")
        departed = [c.get_run(x) for x in ids]
        for run in departed:
            _complete(run, "departed replay")
            _link(run, "kind", "replay", "departed replay")
    check_recorded(replay, recorded_departures() if recorded is None else recorded)
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
        frozen[year] = f
    chain = Chain(replay, pseudo, frozen, validation, dq, breaks, freeze, departed)
    for name, run in chain.runs().items():
        if name != "dq":
            _link(run, "rules_sha256", rules_sha, name)
        if name in ("validation", "frozen_2023", "frozen_2024", "pseudo_replay", "replay"):
            _link(run, "dq_run_id", dq.info.run_id, name)
        _link(run, "code_sha256", code_sha, name)
    _link(replay, "freeze_commit", freeze["freeze_commit"], "replay")
    return chain
