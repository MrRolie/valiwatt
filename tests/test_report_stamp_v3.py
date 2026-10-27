"""v3's committed report sits beside the code that produced it (docs/plan-v3.md, section 10.3), as v2's does
(tests/test_report_stamp.py, unchanged): `docs/report/v3/stamp.json` holds the code and rules hashes, the lineage, the
freeze record, the runs, the hash of every rendered file under docs/report/v3/ and of `docs/replay-ledger-v3.json`;
`validation_v3.stamp_problems` says each way a committed v3 report no longer matches it. Before step 5 of section 17
no v3 report is committed, so the committed check holds nothing to compare; the synthetic renders below show it fails
on a stale or edited report, and v2's stamp never reads v3's home."""

from __future__ import annotations

import hashlib
import json

import pytest

import records_v3_world as rw
import report_v3_world as RW
from valiwatt.paths import REPO_ROOT
from valiwatt.report import validation_v3 as R3


def test_the_committed_v3_report_matches_the_code():
    """docs/report/v3/, when it holds anything, has its stamp, and the stamp matches the code, the rules, every page,
    ledger-v3 and the freeze record as committed (stamp_problems); with no v3 report rendered yet there is nothing to
    match, and no stamp may stand alone."""
    out = REPO_ROOT / "docs" / "report" / "v3"
    assert R3.stamp_problems(REPO_ROOT) == [], "the v3 report is stale: run `valiwatt report`"
    if not out.exists():
        assert not (REPO_ROOT / "docs" / "report" / "v3" / "stamp.json").exists()
    else:
        assert (out / "stamp.json").exists() and (out / "validation.fr.md").exists() \
            and (out / "validation.en.md").exists()


def test_a_stale_or_edited_v3_report_fails_its_stamp(tmp_path, monkeypatch):
    """On a report rendered from the runs of record, stamp_problems is empty; then, one at a time and each undone:
    the code changed (a test file added), the rules changed (a rule file edited), one byte of a page changed, a page
    removed, ledger-v3 edited, and the stamp naming another freeze record each give their problem."""
    chain = RW.copy("of_record", tmp_path, monkeypatch)
    RW.render(chain)
    assert R3.stamp_problems() == []
    out = chain.repo / "docs" / "report" / "v3"

    extra = chain.repo / "tests" / "test_extra.py"
    extra.write_text("x = 1\n", encoding="utf-8")
    assert R3.stamp_problems() == ["the code changed since the v3 report was rendered: run `valiwatt report`"]
    extra.unlink()
    assert R3.stamp_problems() == []

    rule = chain.repo / rw.RULE_FILE
    original = rule.read_bytes()
    rule.write_text("MIN = 5\n", encoding="utf-8")
    assert "the rules changed since the v3 report was rendered: run `valiwatt report`" in R3.stamp_problems()
    rule.write_bytes(original)
    assert R3.stamp_problems() == []

    page = out / "validation.fr.md"
    text = page.read_bytes()
    page.write_bytes(text[:-1] + bytes([text[-1] ^ 1]))
    assert R3.stamp_problems() == ["a file under docs/report/v3/ differs from what the renderer wrote: run "
                                   "`valiwatt report`"]
    page.unlink()
    assert "validation.fr.md does not name the code, rules and runs of its stamp" in R3.stamp_problems()
    page.write_bytes(text)
    assert R3.stamp_problems() == []

    ledger = chain.repo / "docs" / "replay-ledger-v3.json"
    before = ledger.read_bytes()
    ledger.write_bytes(before.replace(b'"coverage": 0.', b'"coverage": 0.0', 1))
    assert R3.stamp_problems() == ["docs/replay-ledger-v3.json changed since the v3 report was rendered: run "
                                   "`valiwatt report`"]
    ledger.write_bytes(before)
    assert R3.stamp_problems() == []

    stamp_path = out / "stamp.json"
    stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    stamp_path.write_text(json.dumps(stamp | {"freeze_record": {"commit": "0" * 40, "sha256": "0" * 64}}),
                          encoding="utf-8")
    problems = R3.stamp_problems()
    assert len(problems) == 1 and problems[0].startswith("the v3 report was rendered from another freeze record")
    stamp_path.write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
    assert R3.stamp_problems() == []
    stamp_path.unlink()
    assert R3.stamp_problems() == ["docs/report/v3/ holds pages and no stamp: run `valiwatt report`"]


def test_v2_s_file_hashes_never_read_v3_s_home(tmp_path, monkeypatch):
    """v2's stamp (report/validation.py `file_hashes`, which v2's unchanged stamp test calls) hashes every file under
    docs/report/ but v3's own home: a v3 page added or changed leaves it as it was; a file anywhere else under
    docs/report/, a folder named like v3's inside another included, enters it."""
    from valiwatt.report import validation as RV
    from test_report_stamp import README_BEFORE

    out = tmp_path / "report"
    (out / "figures").mkdir(parents=True)
    (out / "validation.fr.md").write_text("v2\n", encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text(README_BEFORE, encoding="utf-8")
    monkeypatch.setattr(RV, "OUT", out)
    monkeypatch.setattr(RV, "STAMP", out / "stamp.json")
    monkeypatch.setattr(RV, "README", readme)
    before = RV.file_hashes()
    (out / "v3").mkdir()
    (out / "v3" / "validation.fr.md").write_text("v3\n", encoding="utf-8")
    (out / "v3" / "stamp.json").write_text("{}\n", encoding="utf-8")
    assert RV.file_hashes() == before
    (out / "figures" / "v3").mkdir()
    (out / "figures" / "v3" / "a.png").write_bytes(b"x")
    after = RV.file_hashes()
    assert after == before | {"figures/v3/a.png": hashlib.sha256(b"x").hexdigest()}


def test_the_v3_stamp_holds_what_make_stamp_v3_gives(tmp_path, monkeypatch):
    """The stamp `render_v3` writes is make_stamp_v3 of its context: the hashes of the code and rules it rendered
    under, its lineage, the freeze record, the runs, every page's hash and ledger-v3's."""
    from valiwatt.monitor.rules import rules_sha256
    from valiwatt.report import ledger_v3
    from valiwatt.tracking import code_sha256

    chain = RW.copy("of_record", tmp_path, monkeypatch)
    stamp = RW.render(chain)["stamp"]
    assert set(stamp) == {"code_sha256", "rules_sha256", "lineage", "freeze_record", "runs", "files", "ledger_sha256",
                          "standing_2025"}
    assert stamp["ledger_sha256"] == ledger_v3.digest() != "absent"
    out = chain.repo / "docs" / "report" / "v3"
    assert stamp["files"] == {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in sorted(out.iterdir()) if p.name != "stamp.json"}
    # Rendered pages leave the tree dirty; the stamp holds the code hash of the tree as rendered, as v2's does.
    assert stamp["code_sha256"] == code_sha256() and stamp["rules_sha256"] == rules_sha256()


@pytest.mark.parametrize("lineage", ["fresh_clone", "pre_freeze"])
def test_every_lineage_writes_its_stamp(lineage, tmp_path, monkeypatch):
    chain = RW.copy(lineage, tmp_path, monkeypatch)
    stamp = RW.render(chain)["stamp"]
    assert stamp["lineage"] == lineage and R3.stamp_problems() == []
    assert (stamp["freeze_record"] is None) == (lineage == "pre_freeze")


@pytest.mark.parametrize("fault", ["tag", "record", "none"])
def test_a_report_rendered_before_the_freeze_fails_its_stamp_once_the_freeze_exists(fault, tmp_path, monkeypatch):
    """A report rendered before the freeze, then `v3-rules-frozen` made, or a freeze record committed (here one the
    report cannot read, so its stamp names none and the committed one differs): the stamp fails, saying the report was
    rendered before the freeze. The control (none): nothing changed, the stamp matches."""
    from valiwatt.config import V3_FREEZE_TAG

    chain = RW.copy("pre_freeze", tmp_path, monkeypatch)
    assert RW.render(chain)["stamp"]["freeze_record"] is None and R3.stamp_problems() == []
    if fault == "none":
        return
    if fault == "tag":
        rw.tag(chain.repo, V3_FREEZE_TAG, "HEAD")
    else:
        rw.commit(chain.repo, {"docs/freeze-v3.json": "{}\n"}, "a freeze record")
    problems = R3.stamp_problems()
    assert any(p.startswith("the v3 report was rendered before the freeze") for p in problems), problems
    if fault == "record":
        assert any(p.startswith("the v3 report was rendered from another freeze record") for p in problems)


def test_a_stamp_naming_no_freeze_record_fails_once_one_is_committed(tmp_path, monkeypatch):
    """The stamp's freeze record is always compared with the committed one, none against one included: a report of
    the runs of record whose stamp names no freeze record fails; the stamp as rendered matches."""
    chain = RW.copy("of_record", tmp_path, monkeypatch)
    RW.render(chain)
    assert R3.stamp_problems() == []
    stamp_path = chain.repo / "docs" / "report" / "v3" / "stamp.json"
    stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    stamp_path.write_text(json.dumps(stamp | {"freeze_record": None}), encoding="utf-8")
    problems = R3.stamp_problems()
    assert len(problems) == 1 and problems[0].startswith("the v3 report was rendered from another freeze record")
