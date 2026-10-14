"""The report's run chain is refused at its first broken link: a run that did not finish or pass, a run made from
other code, a frozen model for another year, a replay under another freeze commit, a validation built on another
data-quality run. The runs are fakes in a fake store; one clean chain passes, and each break spoils one tag of it."""

from types import SimpleNamespace

import pytest

from valiwatt import tracking
from valiwatt.report import lineage

CODE, RULES, FREEZE = "c" * 64, "r" * 64, {"freeze_tag": "rules-frozen", "freeze_commit": "f" * 40}


def fake_run(run_id, run_status="FINISHED", **tags):
    return SimpleNamespace(info=SimpleNamespace(run_id=run_id, status=run_status), data=SimpleNamespace(tags=tags))


def clean_chain() -> dict:
    """Every run of the chain by its run ID, each tagged as `follow` expects."""
    made = {"code_sha256": CODE, "rules_sha256": RULES, "status": "complete"}
    on_dq = made | {"dq_run_id": "dq"}
    return {
        "dq": fake_run("dq", gates="passed", code_sha256=CODE),
        "dq2": fake_run("dq2", gates="passed", code_sha256=CODE),  # another passed data-quality run
        "val": fake_run("val", **on_dq),
        "f2024": fake_run("f2024", **on_dq, replay_year="2024", validation_run_id="val", coefficient_sha256="k24"),
        "f2023": fake_run("f2023", **on_dq, replay_year="2023", validation_run_id="val", coefficient_sha256="k23"),
        "replay": fake_run("replay", **on_dq, kind="replay", validation_run_id="val", frozen_run_id="f2024",
                           coefficient_sha256="k24", freeze_commit=FREEZE["freeze_commit"]),
        "pseudo": fake_run("pseudo", **on_dq, kind="pseudo_replay", validation_run_id="val", frozen_run_id="f2023",
                           coefficient_sha256="k23"),
        "breaks": fake_run("breaks", **made, kind="breaks"),
    }


def serve(monkeypatch, runs: dict) -> None:
    """A fake store: the latest run of each kind, and any run by its ID."""
    def latest_run(experiment, filter_string=""):
        for kind in ("pseudo_replay", "breaks", "replay"):
            if f"'{kind}'" in filter_string:
                return runs["pseudo" if kind == "pseudo_replay" else kind]
        raise LookupError(filter_string)

    monkeypatch.setattr(tracking, "latest_run", latest_run)
    monkeypatch.setattr(tracking, "client", lambda: SimpleNamespace(get_run=lambda run_id: runs[run_id]))


def test_a_clean_chain_is_followed(monkeypatch):
    serve(monkeypatch, clean_chain())
    chain = lineage.follow(CODE, RULES, FREEZE)
    assert {k: r.info.run_id for k, r in chain.runs().items()} == {
        "dq": "dq", "validation": "val", "frozen_2023": "f2023", "frozen_2024": "f2024", "pseudo_replay": "pseudo",
        "replay": "replay", "breaks": "breaks"}


# Each break: the run to spoil, the tag to set on it, and the words the refusal must carry. A new link is one row.
BROKEN_LINKS = {
    "breaks_failed": ("breaks", "status", "failed", "breaks run breaks is not marked complete"),
    "other_code": ("pseudo", "code_sha256", "0" * 64, "pseudo_replay run pseudo: code_sha256"),
    "frozen_for_another_year": ("f2024", "replay_year", "2023", "frozen-model 2024 run f2024: replay_year"),
    "replay_under_another_freeze": ("replay", "freeze_commit", "e" * 40, "replay run replay: freeze_commit"),
    # The data-quality run is the one the validation names, so the mismatch shows on the runs built before it.
    "validation_on_another_dq": ("val", "dq_run_id", "dq2", "dq_run_id is 'dq', expected 'dq2'"),
}


@pytest.mark.parametrize("case", list(BROKEN_LINKS))
def test_a_broken_link_in_the_run_chain_is_refused(case, monkeypatch):
    run, tag, value, words = BROKEN_LINKS[case]
    runs = clean_chain()
    runs[run].data.tags[tag] = value
    serve(monkeypatch, runs)
    with pytest.raises(lineage.ChainBroken, match=words):
        lineage.follow(CODE, RULES, FREEZE)


def test_a_departure_replay_brings_the_replays_it_departs_from_and_names_them(monkeypatch):
    """A replay run after a recorded departure must carry the earlier replays into the chain, so the report can label
    it post hoc beside them; one that names none is refused."""
    runs = clean_chain()
    serve(monkeypatch, runs)
    assert lineage.follow(CODE, RULES, FREEZE).departed == []
    runs["earlier"] = fake_run("earlier", kind="replay", status="complete", freeze_commit="e" * 40)
    runs["replay"].data.tags |= {"departure": "true", "departure_from": "earlier"}
    assert [r.info.run_id for r in lineage.follow(CODE, RULES, FREEZE).departed] == ["earlier"]
    runs["earlier"].data.tags["status"] = "failed"  # an earlier replay that never completed cannot stand beside it
    with pytest.raises(lineage.ChainBroken, match="departed replay"):
        lineage.follow(CODE, RULES, FREEZE)
    runs["earlier"].data.tags["status"] = "complete"
    runs["replay"].data.tags["departure_from"] = ""
    with pytest.raises(lineage.ChainBroken, match="names no earlier replay"):
        lineage.follow(CODE, RULES, FREEZE)
