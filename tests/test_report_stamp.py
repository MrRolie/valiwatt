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


# A README as the repository holds it before a render: its title, the two markers with the placeholder between them,
# then its hand-written text.
README_TAIL = "valiwatt validates and monitors a forecast.\n\n## Run it\n\nHand-written text.\n"


def readme_with(between: str) -> str:
    from valiwatt.report.validation import GLANCE_CLOSE, GLANCE_OPEN

    return f"# valiwatt\n\n{GLANCE_OPEN}{between}{GLANCE_CLOSE}\n\n{README_TAIL}"


README_BEFORE = readme_with("\n\n_Rendered by `valiwatt report`._\n\n")


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
    readme = tmp_path / "README.md"
    if not readme.exists():
        readme.write_text(README_BEFORE, encoding="utf-8")
    monkeypatch.setattr(RV, "README", readme)
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
            "breaks": "breaks", "soundness": S.RP.SOUND_RUN}
    assert stamp == {"code_sha256": S.CODE, "rules_sha256": S.RULES, "freeze_commit": S.FREEZE["freeze_commit"],
                     "runs": runs, "files": RV.file_hashes(), "ledger_sha256": ledger.digest(),
                     "standing_2025": None}
    assert {"dq.fr.md", "validation.fr.md", "validation.en.md", "story.fr.md", "story.en.md",
            RV.GLANCE_KEY} <= set(stamp["files"])
    assert all(k.startswith("figures/validation/") for k in set(stamp["files"]) - {
        "dq.fr.md", "validation.fr.md", "validation.en.md", "story.fr.md", "story.en.md", RV.GLANCE_KEY})
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


# --- the README's "At a glance" box ---------------------------------------------------------------------------------

def test_the_glance_renders_from_the_report_s_context():
    """The box renders from the report's context through the report's own environment: the status sentence the report
    opens with, each result with its label as the report prints it, the 2024 numbers and verdicts, the detection limit
    in the model card's words, the breaks, the freeze, and the links, story first; in every 2025 state, the
    recommendation when a clean 2025 does not confirm v2."""
    import report_payloads as RP

    from valiwatt.report import validation as RV
    from valiwatt.report.render import environment, filters

    ctx = RP.context()
    text = RV.render_glance(ctx)
    env = environment("en")
    m = env.get_template("sentences.en.md.j2").make_module({"say": RV.SAY["en"]})
    pct, r, sel = filters("en")["pct"], ctx["r"], ctx["sel"]
    assert RV.status_sentence(env, ctx, "en") in text
    for lbl in (ctx["v"]["contrasts"]["label"], r["contrasts"]["label"]):
        assert f"(_{m.LABEL[lbl]}_)" in text
    prod, fam, ship = (sel["models"][k] for k in ("production", "family", "shipped"))
    assert (f"MAPE {pct(sel['mape'][prod], 2)} for v2, {pct(sel['mape'][fam], 2)} for v1's form on the same forecast, "
            f"{pct(sel['mape'][ship], 2)} for v1 as shipped") in text
    C, v = ctx["C"], ctx["v"]
    assert str(m.coverage_line(ctx["nominal"], C["relative"]["overall"]["mean"], v["conformal"]["first_fold"],
                               ctx["sides"], v["folds"]["last"], "v2's")) in text
    assert f"held demand {pct(C['relative']['overall']['mean'], 1)} of the time" in text
    assert str(m.coverage_line(ctx["nominal"], C["relative"]["overall"]["mean"], v["conformal"]["first_fold"],
                               ctx["sides"], v["folds"]["last"]))[len("Its "):] in str(m.brief_coverage(
        ctx["nominal"], C["relative"]["overall"]["mean"], v["conformal"]["first_fold"], ctx["sides"], v["folds"]["last"]))
    assert f"MAPE {pct(r['summary']['mape']['mean'], 2)}, coverage {pct(r['summary']['coverage']['mean'], 1)}" in text
    assert (f'against v1 as shipped / against v1\'s form: "{m.VERDICT[r["verdict"]["shipped"]]}" / '
            f'"{m.VERDICT[r["verdict"]["family"]]}"') in text
    assert str(m.bias_limit(ctx["limits"]["replay"], ctx["K"]["power_horizon"])) in text
    assert str(m.bias_limit(ctx["limits"]["replay"], ctx["K"]["power_horizon"])) in str(
        m.card_limits(ctx["limits"]["replay"], ctx["K"]["power_horizon"]))
    n = len([x for x in ctx["b"]["breaks"] if not x["control"]])
    q = len([x for x in ctx["b"]["breaks"] if x["control"]])
    assert f"{n} of {n} breaks passed, {q} of {q} controls stayed quiet" in text
    assert f"`{ctx['freeze']['freeze_tag']}`, commit `{ctx['freeze']['freeze_commit'][:12]}`." in text
    assert "before v2's" not in text  # the order of freeze and replay, and what backs it, is the README's own text
    assert text.index("docs/report/story.fr.md") < text.index("docs/report/story.en.md") \
        < text.index("docs/report/validation.fr.md") < text.index("docs/report/validation.en.md")
    assert all(line.startswith(">") for line in text.splitlines())
    assert "| **2025**" not in text  # no 2025 row before a 2025 start
    earlier = {"id": "1" * 32, "freeze_commit": RP.W.F0, "recorded": False, "unfinished": False, "absent": False,
               "summary": {"mape": {"mean": 0.04}, "coverage": {"mean": 0.9}}, "recommendations": []}
    c = RP.context(departures=[earlier])
    t = RV.render_glance(c)  # after a departure, the commit printed is the one the tag points to now
    assert (f"`{c['freeze']['freeze_tag']}`, now at commit `{c['freeze']['freeze_commit'][:12]}` after a departure "
            "recorded in `AGENTS.md`.") in t and "it is post hoc" in t
    for name in RP.STATES:
        c = RP.state(name)
        t = RV.render_glance(c)
        assert RV.status_sentence(env, c, "en") in t, name
        R = (c["held"] or {}).get("record")
        assert ("| **2025** (_" in t) == bool(R and R["finished"]), name  # unfinished: the status says so, once
        if R and R["finished"]:
            assert f"| **2025** (_{m.LABEL[R['evaluation']]}_) |" in t, name
        if c["recommendation"]:
            assert RV.SAY["en"]["recommendation"][c["recommendation"]["kind"]] in t, name


def test_a_glance_without_its_status_or_a_label_is_refused(tmp_path, monkeypatch):
    """A box that does not state the report's status sentence, or gives a result without its label, is refused: here
    from a copy of the templates whose box drops the validation years' label."""
    import shutil

    import report_payloads as RP

    from valiwatt.report import render
    from valiwatt.report import validation as RV

    ctx = RP.context()
    with monkeypatch.context() as m:
        m.setattr(RV, "status_sentence", lambda env, c, lang: "**Status: something else.**")
        with pytest.raises(RV.ReportRefused, match="status"):
            RV.render_glance(ctx)
    copy = tmp_path / "templates"
    shutil.copytree(render.TEMPLATES, copy)
    box = copy / "readme_glance.en.md.j2"
    source = box.read_text(encoding="utf-8")
    dropped = "| **Validation, {{ span(vy) }}** ({{ label(v.contrasts.label) }}) |"
    assert source.count(dropped) == 1
    box.write_text(source.replace(dropped, "| **Validation, {{ span(vy) }}** |"), encoding="utf-8")
    monkeypatch.setattr(render, "TEMPLATES", copy)
    with pytest.raises(RV.ReportRefused, match="without its label"):
        RV.render_glance(ctx)
    # The replay's label is printed again in the row of what the monitor can miss: a replay row without it is refused
    # all the same, and so is a finished 2025 row without its own.
    for dropped, kept, c in (("| **{{ r.year }} replay** ({{ label(r.contrasts.label) }}) |",
                              "| **{{ r.year }} replay** |", ctx),
                             ("| **{{ HY }}** ({{ label(R.evaluation) }}) |", "| **{{ HY }}** |", RP.state("confirmed"))):
        assert source.count(dropped) == 1
        box.write_text(source.replace(dropped, kept), encoding="utf-8")
        with pytest.raises(RV.ReportRefused, match="without its label"):
            RV.render_glance(c)
    box.write_text(source.replace("> | **What the monitor can miss**", "> | **What it can miss**"), encoding="utf-8")
    with pytest.raises(RV.ReportRefused, match="rows for What the monitor can miss"):
        RV.render_glance(ctx)
    box.write_text(source, encoding="utf-8")
    assert RV.render_glance(ctx) and RV.render_glance(RP.state("confirmed"))


def test_the_glance_writer_replaces_only_the_marked_region():
    from valiwatt.report import validation as RV

    out = RV.with_glance(README_BEFORE, "> new box\n")
    assert out == readme_with("\n\n> new box\n\n")
    assert out.startswith("# valiwatt\n\n" + RV.GLANCE_OPEN) and out.endswith(RV.GLANCE_CLOSE + "\n\n" + README_TAIL)
    assert RV.with_glance(out, "> new box") == out  # rendering again from the same block changes nothing
    start, end = RV.glance_span(out)
    assert out[start:end] == "\n\n> new box\n\n"
    assert RV.hand_written(out) == RV.hand_written(README_BEFORE) == readme_with("")  # the README's own words


@pytest.mark.parametrize("readme", [
    "# valiwatt\n\nno markers\n",
    readme_with("\n").replace("<!-- /at-a-glance -->", ""),
    readme_with("\n") + readme_with("\n"),
    readme_with("\n") + "\n<!-- at-a-glance: another -->\n",
    readme_with("\n") + "\n<!-- /at-a-glance -->\n",
], ids=["none", "no-close", "twice", "another-open", "two-closes"])
def test_missing_or_duplicated_glance_markers_are_refused(readme):
    from valiwatt.report import validation as RV

    with pytest.raises(RV.ReportRefused, match="at-a-glance"):
        RV.with_glance(readme, "> box")


def test_glance_markers_out_of_order_are_refused():
    from valiwatt.report import validation as RV

    readme = f"# valiwatt\n\n{RV.GLANCE_CLOSE}\n\n{RV.GLANCE_OPEN}\n"
    with pytest.raises(RV.ReportRefused, match="before its opening marker"):
        RV.with_glance(readme, "> box")


def test_the_render_writes_the_glance_and_a_hand_edit_fails_the_stamp(tmp_path, monkeypatch):
    """`render_validation` writes the box it renders from the chain's context between the README's markers, the rest of
    the README byte for byte as it was, and the stamp holds the box's hash: the check that a rendered file was not
    edited by hand passes, then fails once a figure in the box is changed by hand, or the box is removed."""
    import report_store as S

    from valiwatt.report import validation as RV

    w = S.World().install(monkeypatch)
    render_world(w, tmp_path, monkeypatch)
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    start, end = RV.glance_span(readme)
    box = readme[start:end]
    assert "**At a glance.**" in box and "_Rendered by" not in box
    assert readme[:start] == "# valiwatt\n\n" + RV.GLANCE_OPEN
    assert readme[end:] == RV.GLANCE_CLOSE + "\n\n" + README_TAIL
    stamp = json.loads((tmp_path / "report" / "stamp.json").read_text(encoding="utf-8"))
    assert RV.file_hashes() == stamp["files"]
    edited = readme[:start] + box.replace("MAPE ", "MAPE 1", 1) + readme[end:]
    (tmp_path / "README.md").write_text(edited, encoding="utf-8")
    assert RV.file_hashes() != stamp["files"]
    (tmp_path / "README.md").write_text(readme, encoding="utf-8")
    assert RV.file_hashes() == stamp["files"]
    (tmp_path / "README.md").write_text(readme[:start] + readme[end:].replace(RV.GLANCE_CLOSE, ""), encoding="utf-8")
    with pytest.raises(RV.ReportRefused, match="at-a-glance"):
        RV.file_hashes()


def test_a_readme_without_its_markers_is_refused_before_any_page_is_written(tmp_path, monkeypatch):
    import report_store as S

    from valiwatt.report import validation as RV

    w = S.World().install(monkeypatch)
    (tmp_path / "README.md").write_text("# valiwatt\n\nno markers\n", encoding="utf-8")
    with pytest.raises(RV.ReportRefused, match="at-a-glance"):
        render_world(w, tmp_path, monkeypatch)
    out = tmp_path / "report"
    assert not (out / "dq.fr.md").exists()  # the data-quality render was not called
    assert not (out / "figures").exists() and not (out / "validation.en.md").exists() and not (out / "stamp.json").exists()
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "# valiwatt\n\nno markers\n"


def test_the_committed_readme_holds_the_glance_markers_after_its_title():
    """The box sits right after the README's title line, before its first paragraph."""
    from valiwatt.paths import REPO_ROOT
    from valiwatt.report import validation as RV

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    start, end = RV.glance_span(readme)
    assert readme.startswith("# valiwatt\n\n" + RV.GLANCE_OPEN)
    assert readme[end:].startswith(RV.GLANCE_CLOSE + "\n\nvaliwatt validates")
