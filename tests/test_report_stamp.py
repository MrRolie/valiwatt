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


def test_the_render_writes_its_stamp_through_make_stamp(tmp_path, monkeypatch):
    """`render_validation` writes the stamp `make_stamp` builds, ledger hash included: the run chain, its context, the
    data-quality render and the templates are stubbed, and everything is written under tmp_path."""
    from types import SimpleNamespace

    import valiwatt.monitor.rules as rules
    import valiwatt.monitor.run as monitor_run
    import valiwatt.tracking as tracking
    from valiwatt.report import validation as RV

    out = tmp_path / "report"
    out.mkdir()
    monkeypatch.setattr(RV, "OUT", out)
    monkeypatch.setattr(RV, "STAMP", out / "stamp.json")
    monkeypatch.setattr(RV, "REPO_ROOT", tmp_path)
    path = tmp_path / "replay-ledger.json"
    monkeypatch.setattr(ledger, "LEDGER", path)
    entry = {"run_id": "0" * 32, "year": 2024, "freeze_tag": "rules-frozen", "freeze_commit": "e" * 40,
             "rules_sha256": "d" * 64, "code_sha256": "c" * 64, "started_utc": "2020-01-01T00:00:00.000Z",
             "ended_utc": "2020-01-01T01:00:00.000Z", "status": "complete", "mape": 0.04, "coverage": 0.9,
             "recommendations": [], "departure": False, "departure_from": []}
    path.write_text(ledger.dumps([entry]), encoding="utf-8")

    monkeypatch.setattr(tracking, "code_sha256", lambda: "c" * 64)
    monkeypatch.setattr(rules, "rules_sha256", lambda: "d" * 64)
    monkeypatch.setattr(monitor_run, "check_freeze", lambda: {"freeze_commit": "f" * 40})

    def run(run_id):
        return SimpleNamespace(info=SimpleNamespace(run_id=run_id))

    chain = SimpleNamespace(dq=run("a" * 32), replay=run("0" * 32), freeze={"freeze_commit": "f" * 40})
    monkeypatch.setattr(RV, "follow", lambda code, rules_sha, freeze: chain)

    def dq_render(run_id):
        assert run_id == "a" * 32
        (out / "dq.fr.md").write_text("dq\n", encoding="utf-8")
        return [out / "dq.fr.md"]

    monkeypatch.setattr(RV, "render", dq_render)
    runs = {"dq": "a" * 32, "replay": "0" * 32}
    ctx = {"dq": {"sources": {"eccc": [{"first_retrieved_utc": "2026-10-01T00:00:00Z"}]}},
           "runs": {k: {"id": v, "when": "2026-10-01"} for k, v in runs.items()}}
    monkeypatch.setattr(RV, "context", lambda chain_, code, rules_sha: ctx)
    monkeypatch.setattr(RV, "figures", lambda ctx_, lang: {})

    class Template:
        def __init__(self, name):
            self.name = name

        def render(self, **kwargs):
            return f"{self.name} {kwargs['lang']}\n"

    monkeypatch.setattr(RV, "environment", lambda lang: SimpleNamespace(get_template=Template))

    written = RV.render_validation()
    assert written == [out / "dq.fr.md", out / "validation.fr.md", out / "validation.en.md"]
    stamp = json.loads((out / "stamp.json").read_text(encoding="utf-8"))
    assert stamp["ledger_sha256"] == ledger.digest() != ledger.ABSENT
    assert stamp == {"code_sha256": "c" * 64, "rules_sha256": "d" * 64, "freeze_commit": "f" * 40, "runs": runs,
                     "files": RV.file_hashes(), "ledger_sha256": ledger.digest()}
    assert set(stamp["files"]) == {"dq.fr.md", "validation.fr.md", "validation.en.md"}
    check_ledger(stamp)
