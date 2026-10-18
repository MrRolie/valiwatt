"""The committed report sits beside the code that produced it: its stamp must match the code and rules hashes, every
rendered file must match the hash the renderer wrote, the replay ledger must match the hash it had when the report was
rendered, and both reports must name the runs of one chain."""

import json

import pytest

from valiwatt.monitor.rules import rules_sha256
from valiwatt.paths import REPORTS
from valiwatt.report import ledger
from valiwatt.report.validation import file_hashes, make_stamp
from valiwatt.tracking import code_sha256


def check_ledger(stamp: dict) -> None:
    assert stamp.get("ledger_sha256") == ledger.digest(), (
        "the replay ledger changed since the report was rendered: run `valiwatt report`")


def test_the_committed_report_matches_the_code():
    stamp = json.loads((REPORTS / "stamp.json").read_text(encoding="utf-8"))
    assert stamp["code_sha256"] == code_sha256(), "code changed since the report was rendered: run `valiwatt all`"
    assert stamp["rules_sha256"] == rules_sha256()
    check_ledger(stamp)
    for lang in ("fr", "en"):
        text = (REPORTS / f"validation.{lang}.md").read_text(encoding="utf-8")
        assert stamp["code_sha256"] in text and stamp["rules_sha256"] in text
        assert all(run_id in text for run_id in stamp["runs"].values())
        assert stamp["runs"]["dq"] in (REPORTS / f"dq.{lang}.md").read_text(encoding="utf-8")


def test_no_rendered_file_was_edited_by_hand():
    stamp = json.loads((REPORTS / "stamp.json").read_text(encoding="utf-8"))
    assert file_hashes() == stamp["files"], "a rendered file differs from what the renderer wrote: run `valiwatt report`"


def test_a_ledger_changed_after_the_render_fails_the_stamp(tmp_path, monkeypatch):
    """The stamp the renderer writes holds the ledger's hash: an entry edited by hand after the render, or the ledger
    removed, no longer matches it. A ledger absent at render time is stamped `absent`."""
    path = tmp_path / "replay-ledger.json"
    monkeypatch.setattr(ledger, "LEDGER", path)
    entry = {"run_id": "0" * 32, "year": 2024, "freeze_tag": "rules-frozen", "freeze_commit": "e" * 40,
             "rules_sha256": "d" * 64, "code_sha256": "c" * 64, "started_utc": "2020-01-01T00:00:00.000Z",
             "ended_utc": "2020-01-01T01:00:00.000Z", "status": "complete", "mape": 0.04, "coverage": 0.9,
             "recommendations": [], "departure": False, "departure_from": []}
    path.write_text(ledger.dumps([entry]), encoding="utf-8")

    def stamp():
        return make_stamp("c" * 64, "d" * 64, "f" * 40, {"replay": "0" * 32})

    rendered = stamp()
    check_ledger(rendered)
    path.write_text(ledger.dumps([{**entry, "mape": 0.01}]), encoding="utf-8")  # the entry edited by hand
    with pytest.raises(AssertionError, match="ledger changed since the report was rendered"):
        check_ledger(rendered)
    path.unlink()
    with pytest.raises(AssertionError, match="ledger changed since the report was rendered"):
        check_ledger(rendered)
    absent = stamp()
    assert absent["ledger_sha256"] == ledger.ABSENT
    check_ledger(absent)
    path.write_text(ledger.dumps([]), encoding="utf-8")  # a ledger written after a render that had none
    with pytest.raises(AssertionError, match="ledger changed since the report was rendered"):
        check_ledger(absent)


def render_world(w, tmp_path, monkeypatch) -> list:
    """`render_validation` end to end on a fake store (tests/report_store.py): the real chain, context, figures,
    templates and checks; the data-quality render stubbed (its own tests render it); everything under tmp_path."""
    import report_store as S

    import valiwatt.monitor.rules as rules
    import valiwatt.monitor.run as monitor_run
    import valiwatt.tracking as tracking
    from valiwatt.report import validation as RV

    out = tmp_path / "report"
    out.mkdir(exist_ok=True)
    monkeypatch.setattr(RV, "OUT", out)
    monkeypatch.setattr(RV, "STAMP", out / "stamp.json")
    monkeypatch.setattr(RV, "REPO_ROOT", tmp_path)
    path = tmp_path / "replay-ledger.json"
    path.write_text(ledger.dumps(w.entries), encoding="utf-8")
    monkeypatch.setattr(ledger, "LEDGER", path)
    monkeypatch.setattr(tracking, "code_sha256", lambda: S.CODE)
    monkeypatch.setattr(rules, "rules_sha256", lambda: S.RULES)
    monkeypatch.setattr(monitor_run, "check_freeze", lambda: dict(S.FREEZE))

    def dq_render(run_id):
        assert run_id == "dq"
        (out / "dq.fr.md").write_text("dq\n", encoding="utf-8")
        return [out / "dq.fr.md"]

    monkeypatch.setattr(RV, "render", dq_render)
    return RV.render_validation()


def test_the_render_writes_its_stamp_through_make_stamp(tmp_path, monkeypatch):
    """`render_validation` writes the stamp `make_stamp` builds, ledger hash included, from the chain it followed."""
    import report_store as S

    from valiwatt.report import validation as RV

    w = S.World().install(monkeypatch)
    out = tmp_path / "report"
    written = render_world(w, tmp_path, monkeypatch)
    assert written == [out / "dq.fr.md", out / "validation.fr.md", out / "story.fr.md", out / "validation.en.md",
                       out / "story.en.md"]
    stamp = json.loads((out / "stamp.json").read_text(encoding="utf-8"))
    assert stamp["ledger_sha256"] == ledger.digest() != ledger.ABSENT
    # The chain's runs, each replay followed by the diagnostic run sections 7 and 8 cite (provenance).
    runs = {"dq": "dq", "validation": "val", "frozen_2023": "f2023", "frozen_2024": "f2024", "pseudo_replay": "pseudo",
            "diag_pseudo_replay": "diag-pseudo", "replay": S.REPLAY, "diag_replay": f"diag-{S.REPLAY}",
            "breaks": "breaks"}
    assert stamp == {"code_sha256": S.CODE, "rules_sha256": S.RULES, "freeze_commit": S.FREEZE["freeze_commit"],
                     "runs": runs, "files": RV.file_hashes(), "ledger_sha256": ledger.digest(),
                     "standing_2025": None}
    assert {"dq.fr.md", "validation.fr.md", "validation.en.md", "story.fr.md", "story.en.md"} <= set(stamp["files"])
    assert all(k.startswith("figures/validation/") for k in set(stamp["files"]) - {
        "dq.fr.md", "validation.fr.md", "validation.en.md", "story.fr.md", "story.en.md"})
    check_ledger(stamp)


def test_the_stamp_carries_the_standing_of_2025(tmp_path, monkeypatch):
    """null before a 2025 start; then the test of record's run ID, whether it finished, and the standing its gate
    logged: the record's, never a later rerun's."""
    import report_store as S
    import report_world as W

    from valiwatt.report import validation as RV

    def stamped(w, name):
        render_world(w, tmp_path / name, monkeypatch)
        return json.loads((tmp_path / name / "report" / "stamp.json").read_text(encoding="utf-8"))["standing_2025"]

    (tmp_path / "a").mkdir()
    assert stamped(S.World().install(monkeypatch), "a") is None
    w = S.World().install(monkeypatch)
    w.add_2025(run_id="b" * 32, standing="post_hoc", gate=W.gate("post_hoc", ["a"], W.ruling("post_hoc")))
    w.add_2025(run_id="d" * 32, standing="clean", gate=W.gate("clean"))  # a later rerun, labelled otherwise
    (tmp_path / "b").mkdir()
    assert stamped(w, "b") == {"record_run_id": "b" * 32, "finished": True, "standing": "post_hoc"}
    w = S.World().install(monkeypatch)
    w.add_2025(finished=False)
    (tmp_path / "c").mkdir()
    assert stamped(w, "c") == {"record_run_id": "b" * 32, "finished": False, "standing": "clean"}
    assert RV.standing_2025(None) is None
