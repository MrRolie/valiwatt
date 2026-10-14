"""The committed report sits beside the code that produced it: its stamp must match the code and rules hashes, every
rendered file must match the hash the renderer wrote, and both reports must name the runs of one chain."""

import json

from valiwatt.monitor.rules import rules_sha256
from valiwatt.paths import REPORTS
from valiwatt.report.validation import file_hashes
from valiwatt.tracking import code_sha256


def test_the_committed_report_matches_the_code():
    stamp = json.loads((REPORTS / "stamp.json").read_text(encoding="utf-8"))
    assert stamp["code_sha256"] == code_sha256(), "code changed since the report was rendered: run `valiwatt all`"
    assert stamp["rules_sha256"] == rules_sha256()
    for lang in ("fr", "en"):
        text = (REPORTS / f"validation.{lang}.md").read_text(encoding="utf-8")
        assert stamp["code_sha256"] in text and stamp["rules_sha256"] in text
        assert all(run_id in text for run_id in stamp["runs"].values())
        assert stamp["runs"]["dq"] in (REPORTS / f"dq.{lang}.md").read_text(encoding="utf-8")


def test_no_rendered_file_was_edited_by_hand():
    stamp = json.loads((REPORTS / "stamp.json").read_text(encoding="utf-8"))
    assert file_hashes() == stamp["files"], "a rendered file differs from what the renderer wrote: run `valiwatt report`"
