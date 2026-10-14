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
