"""The run chain behind the validation report, followed from the 2024 replay and refused at the first broken link.

replay 2024 -> frozen model 2024 -> validation -> data quality
pseudo-replay 2023 -> frozen model 2023 -> the same validation
breaks run
Every run must be finished and complete, made from the code that renders the report, under the rules of the
`rules-frozen` tag.
"""

from __future__ import annotations

from dataclasses import dataclass


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


def follow(code_sha: str, rules_sha: str, freeze: dict) -> Chain:
    from valiwatt.breaks import EXPERIMENT as BREAKS_EXP
    from valiwatt.monitor.run import EXPERIMENT as MON_EXP
    from valiwatt.tracking import client, latest_run

    c = client()
    try:
        replay = latest_run(MON_EXP, "tags.kind = 'replay' and attributes.status = 'FINISHED'")
    except LookupError as e:
        raise ChainBroken(f"no finished 2024 replay: {e}") from e
    _complete(replay, "replay")
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
    chain = Chain(replay, pseudo, frozen, validation, dq, breaks, freeze)
    for name, run in chain.runs().items():
        if name != "dq":
            _link(run, "rules_sha256", rules_sha, name)
        if name in ("validation", "frozen_2023", "frozen_2024", "pseudo_replay", "replay"):
            _link(run, "dq_run_id", dq.info.run_id, name)
        _link(run, "code_sha256", code_sha, name)
    _link(replay, "freeze_commit", freeze["freeze_commit"], "replay")
    return chain
