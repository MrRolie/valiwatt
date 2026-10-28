"""v3's validation report (docs/plan-v3.md, sections 3.1, 8.4, 8.5, 10.3 and 13; report/validation_v3.py), rendered
from the synthetic chain of tests/v3_world.py in each of its three lineages (tests/report_v3_world.py): the runs of
record, a fresh clone, and before the freeze.

The report opens with v3's status and says, before any measured number, that the forecast is issued at noon the day
before and why; every measured number carries its label, with the lineage's beside it; a held-out number comes only
from a run ledger-v3 names; a fresh clone shows the recorded runs, labels every rerun and says what did not run. Each
refusal has its injected fault and its clean control."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from functools import lru_cache

import pandas as pd
import pytest

import records_v3_world as rw
import report_v3_world as RW
from valiwatt.config import V3_FREEZE_TAG
from valiwatt.forecast import evaluate as ev
from valiwatt.report import validation_v3 as R3
from valiwatt.report.render import ReportRefused, environment

LANGS = ("fr", "en")
SECTIONS = {"fr": ("## En bref", "## Fiche du modèle", "## 1. ", "## 2. ", "## 3. ", "## 4. ", "## 5. ", "## 6. ",
                   "## 7. Provenance"),
            "en": ("## In brief", "## Model card", "## 1. ", "## 2. ", "## 3. ", "## 4. ", "## 5. ", "## 6. ",
                   "## 7. Provenance")}


def context_of(chain) -> dict:
    """The context `render_v3` renders from, on the installed copy."""
    from valiwatt.monitor.rules import rules_sha256
    from valiwatt.records_v3 import replay_starts_v3
    from valiwatt.report import ledger_v3
    from valiwatt.tracking import code_sha256

    f = R3.follow_v3()
    entries = ledger_v3.read()
    return R3.context_v3(f, entries, replay_starts_v3(2025, None, {"replays": entries}), code_sha256(), rules_sha256())


@lru_cache(maxsize=None)
def rendered(name: str) -> dict:
    """The pages, the stamp and the context of the base `name`, rendered once per process on a private copy."""
    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.mkdtemp(prefix=f"valiwatt-report-v3-rendered-{name}-"))
    with pytest.MonkeyPatch.context() as mp:
        chain = RW.copy(name, tmp, mp)
        pages = RW.render(chain)
        ctx = context_of(chain)
    shutil.rmtree(tmp, ignore_errors=True)
    return {**pages, "ctx": ctx}


def bullets(text: str, lang: str) -> list[str]:
    part = text.split(R3.SAY[lang]["brief"], 1)[1].split("\n## ", 1)[0]
    return [line for line in part.splitlines() if line.startswith("- ")]


def status_line(ctx: dict, lang: str) -> str:
    return R3.status_sentence(environment(lang), ctx, lang)


def check(text: str, lang: str, ctx: dict) -> None:
    R3.check_rendered_v3(text, lang, ctx, status_line(ctx, lang))


# ----------------------------------------------------------------------------------------------- the runs of record

def test_the_v3_report_renders_from_the_runs_of_record(tmp_path, monkeypatch):
    """`render_v3` on the runs of record: both pages and the stamp under docs/report/v3/, and nothing else written; the
    pages open with the candidate status, then the noon sentence; they hold every section, name the freeze record's
    runs, the code and the rules; the stamp is `make_stamp_v3`'s and the committed report matches it."""
    chain = RW.copy("of_record", tmp_path, monkeypatch)
    before = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=chain.repo,
                            capture_output=True, text=True, check=True).stdout
    pages = RW.render(chain)
    after = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=chain.repo,
                           capture_output=True, text=True, check=True).stdout
    new = sorted(set(after.splitlines()) - set(before.splitlines()))
    assert new == ["?? docs/report/v3/stamp.json", "?? docs/report/v3/validation.en.md",
                   "?? docs/report/v3/validation.fr.md"]
    from valiwatt.freeze_record import read_freeze_record

    record = read_freeze_record()
    named = {role: r["run_id"] for role, r in record["data"]["runs"].items()}
    stamp = pages["stamp"]
    assert stamp["lineage"] == "of_record" and stamp["runs"] == named
    assert stamp["freeze_record"] == {"commit": record["commit"], "sha256": record["sha256"]}
    assert stamp["files"] == R3.file_hashes_v3() and set(stamp["files"]) == {"validation.fr.md", "validation.en.md"}
    assert stamp["standing_2025"] is None and R3.stamp_problems() == []
    ctx = context_of(chain)
    assert ctx["status"] == "candidate" and ctx["recommendation"] == []
    for lang in LANGS:
        text = pages[lang]
        b = bullets(text, lang)
        assert b[0] == "- " + status_line(ctx, lang)
        assert b[1].startswith("- **" + {"fr": "La prévision du jour J est émise à midi la veille",
                                          "en": "The forecast for day D is issued at noon the day before"}[lang])
        at = [text.find(s) for s in SECTIONS[lang]]
        assert all(i >= 0 for i in at) and at == sorted(at), lang
        assert all(run_id in text for run_id in named.values())
        assert stamp["code_sha256"][:12] in text and stamp["rules_sha256"][:12] in text
        assert R3.unlabelled_lines(text, list(R3.labels(lang, "of_record").values())) == []
        assert R3.labels(lang, "of_record")["held_out"] not in text


OFF_HEAD = {"fr": ("qui n'est pas dans cet historique", "Le commit qui ajoute le registre n'est ni HEAD ni un de ses "
                    "ancêtres"),
            "en": ("which is not in this history", "The commit that adds the record is not HEAD or an ancestor of it")}


@pytest.mark.parametrize("case", ["off_head", "control"])
def test_a_freeze_record_off_head_s_history_is_shown_and_said_so(case, tmp_path, monkeypatch):
    """The report reads the freeze record through its anchor across the repository's refs, as the 2025 gate does
    (monitor/run_v3.py `held_out_2025_v3`, step 5): the record R kept by branch `keep`, the working branch reset to R's
    parent, a clean ruling on its code and `valiwatt evidence-v3`, then the 2025 start run and finished, ruled under (a)
    (off_head). The pages are those of the runs of record: they name R and say its commit is not in this history; the
    stamp holds R and the 2025 standing, and the report matches it. The control: the runs of record, R on HEAD's
    history, render without that sentence."""
    from replay_world import ruling
    from valiwatt.freeze_record import read_freeze_record
    from valiwatt.monitor import run_v3
    from valiwatt.report import ledger_v3

    chain = RW.copy("of_record", tmp_path, monkeypatch)
    record = chain.ids["record"]
    if case == "off_head":
        rw.git(chain.repo, "branch", "keep", record)
        rw.git(chain.repo, "reset", "--quiet", "--hard", f"{record}^")
        target = rw.git(chain.repo, "rev-parse", "HEAD")
        rw.commit(chain.repo, {"AGENTS.md": rw.agents(ruling("clean", target))}, "v3 departures")
        assert run_v3.run_evidence_v3() == 0
        assert run_v3.run_replay_v3(2025, n_boot=RW.N_BOOT) == 0
        ledger_v3.sync()
        assert subprocess.run(["git", "merge-base", "--is-ancestor", record, "HEAD"], cwd=chain.repo).returncode == 1
    pages = RW.render(chain)
    stamp = pages["stamp"]
    assert stamp["lineage"] == "of_record" and stamp["freeze_record"]["commit"] == record
    assert stamp["freeze_record"]["sha256"] == read_freeze_record()["sha256"]
    assert (stamp["standing_2025"] or {}).get("standing") == ("clean" if case == "off_head" else None)
    assert R3.stamp_problems() == []
    for lang in LANGS:
        text = pages[lang]
        assert record[:12] in text and record in text.split("## 7. Provenance", 1)[1]
        said = [phrase in text for phrase in OFF_HEAD[lang]]
        assert said == [case == "off_head"] * 2, lang


def test_the_report_holds_the_validation_s_numbers_each_labelled():
    """The contrasts of each subset, the verdicts, the bins and the frozen models' terms are the runs' own, printed in
    tables whose header carries the label of section 8.4."""
    from valiwatt.report.render import filters

    out = rendered("of_record")
    ctx = out["ctx"]
    v = ctx["v"]
    for lang in LANGS:
        text, f = out[lang], filters(lang)
        lbl = R3.labels(lang, "of_record")["in_sample"]
        unit = {"fr": " pt", "en": " pp"}[lang]
        for name in ("terms", "family", "whole"):
            for sub in ("all", "cold", "event_windows"):
                d = v["contrasts"][name][sub]
                row = [line for line in text.splitlines() if f"| {f['signed'](100 * d['difference'], 2)}{unit} |" in line]
                assert row, (lang, name, sub)
        assert f"({lbl}) | " in text
        mod = environment(lang).get_template(f"v3/sentences.{lang}.md.j2").make_module({})
        assert f"« {mod.TERMS_VERDICT[v['verdict']['terms']]} »" in text or \
            f'"{mod.TERMS_VERDICT[v["verdict"]["terms"]]}"' in text
        for t in ctx["frozen"][2025]["terms"]:
            assert f"| {f['num'](t['mw_per_unit'], 3)} |" in text and f"| {f['num'](t['vif'], 2)} |" in text
        for b in ctx["bins"]:
            assert f"| {b['fold']} | {f['num'](b['edge_c'], 1)} °C |" in text


# ------------------------------------------------------------------------------------------------------------ 2025

def test_after_a_clean_2025_start_its_numbers_come_from_its_ledger_v3_entry():
    """A clean 2025 test of record: the status is its verdicts' (evaluate.status_v3), the brief opens with it, the
    numbers are its ledger-v3 entry's, labelled held out, and a status that is not both better carries its
    recommendation."""
    from valiwatt.report.render import filters

    out = rendered("held_out")
    ctx = out["ctx"]
    held = ctx["held"]
    e = held["numbers"]["entry"]
    assert e["evaluation"] == "held_out" and e["standing"] == "clean"
    assert ctx["status"] == ev.status_v3({"finished": True, "standing": "clean", "verdict": e["verdict"]})
    assert ctx["recommendation"] == ev.recommendation_v3({"finished": True, "standing": "clean",
                                                          "verdict": e["verdict"]})
    for lang in LANGS:
        text = out[lang]
        assert bullets(text, lang)[0] == "- " + status_line(ctx, lang)
        label = R3.labels(lang, "of_record")["held_out"]
        four = R3.numbered_section(text, 4)
        assert e["run_id"] in four and label in four and filters(lang)["pct"](e["mape"], 2) in four
        for kind in ctx["recommendation"]:
            assert R3.SAY[lang]["recommendation"][kind] in text
    assert out["stamp"]["standing_2025"] == {"record_run_id": e["run_id"], "finished": True, "standing": "clean"}


def test_a_held_out_number_whose_run_is_not_in_ledger_v3_is_refused(tmp_path, monkeypatch):
    """A finished 2025 start that ledger-v3 does not name: the render refuses, and writes nothing. The control: the
    ledger naming it, the render goes through. `held_out_entry` refuses a run the entries lack, and returns its entry
    otherwise."""
    from valiwatt.report import ledger_v3

    chain = RW.copy("held_out", tmp_path, monkeypatch)
    path = ledger_v3.path()
    synced = path.read_text(encoding="utf-8")
    entries = ledger_v3.read()
    held = [e for e in entries if e["year"] == 2025]
    assert len(held) == 1
    path.write_text(ledger_v3.dumps([e for e in entries if e["year"] != 2025]), encoding="utf-8")
    with pytest.raises(ReportRefused, match="ledger"):
        R3.render_v3()
    assert not (chain.repo / "docs" / "report" / "v3").exists()
    with pytest.raises(ReportRefused, match="is not in docs/replay-ledger-v3.json"):
        R3.held_out_entry([e for e in entries if e["year"] != 2025], held[0]["run_id"])
    assert R3.held_out_entry(entries, held[0]["run_id"]) == held[0]
    path.write_text(synced, encoding="utf-8")
    assert RW.render(chain)["stamp"]["standing_2025"]["record_run_id"] == held[0]["run_id"]


def fake_2025(ctx: dict, status: str) -> dict:
    """The of-record context with a finished 2025 test of record giving `status` (evaluate.STATUSES_V3)."""
    verdicts = {"both_better": ("better", "better"), "terms_only": ("better_windows_cold_untested", "worse"),
                "family_only": ("not_shown_better", "better_cold_untested"),
                "neither": ("not_testable", "not_shown_better"), "post_hoc": ("better", "better")}
    terms, family = verdicts.get(status, ("better", "better"))
    standing = "post_hoc" if status == "post_hoc" else "clean"
    entry = {"run_id": "9" * 32, "year": 2025, "started_utc": "2027-06-01T00:00:00.000Z", "mape": 0.031,
             "coverage": 0.88, "verdict": {"terms": terms, "family": family},
             "evaluation": "post_hoc" if standing == "post_hoc" else "held_out", "standing": standing}
    record = {"run_id": entry["run_id"], "started_utc": entry["started_utc"], "finished": status != "unfinished",
              "standing": standing, "stage": "replayed"}
    held = {"record": record, "stopped": [], "unfinished": [],
            "numbers": None if status == "unfinished" else {"entry": entry, "results": None}, "reruns": []}
    tor = R3.record_of_2025(held)
    assert ev.status_v3(tor) == status
    return {**ctx, "held": held, "status": status, "recommendation": ev.recommendation_v3(tor),
            "verdict_2025": (tor or {}).get("verdict"),
            "entries": ctx["entries"] if status == "unfinished" else [*ctx["entries"], entry]}  # unfinished: no entry


@pytest.mark.parametrize("status", ["both_better", "terms_only", "family_only", "neither", "post_hoc", "unfinished"])
def test_every_status_opens_the_report_and_a_clean_2025_not_better_recommends(status):
    """Section 8.5: the page opens with the status the 2025 test of record gives, whatever it is; the held-out label
    appears only on a clean record's numbers; a clean 2025 that does not show the terms or the family better prints its
    recommendation, the family first."""
    ctx = fake_2025(rendered("of_record")["ctx"], status)
    pages = R3.render_pages_v3(ctx)
    for lang in LANGS:
        text = pages[lang]
        assert bullets(text, lang)[0] == "- " + status_line(ctx, lang)
        held = R3.labels(lang, "of_record")["held_out"]
        assert (held in text) == (status in ("both_better", "terms_only", "family_only", "neither"))
        recs = [R3.SAY[lang]["recommendation"][k] for k in ctx["recommendation"]]
        assert all(r in text for r in recs)
        if len(recs) == 2:
            assert text.index(recs[0]) < text.index(recs[1])
    assert ctx["recommendation"] == {"terms_only": ["review_family"], "family_only": ["keep_v2_noon"],
                                     "neither": ["review_family", "keep_v2_noon"]}.get(status, [])


def test_a_clean_2025_not_better_without_its_recommendation_is_refused():
    """The recommendation's words removed from a page whose 2025 recommends: refused. The control: the page as
    rendered passes."""
    ctx = fake_2025(rendered("of_record")["ctx"], "neither")
    pages = R3.render_pages_v3(ctx)
    for lang in LANGS:
        check(pages[lang], lang, ctx)
        words = R3.SAY[lang]["recommendation"]["keep_v2_noon"]
        with pytest.raises(ReportRefused, match="recommendation"):
            check(pages[lang].replace(words, "x"), lang, ctx)


def test_the_held_out_label_needs_a_clean_test_of_record():
    """A candidate page carrying the held-out label is refused; a clean record's page carries it and passes."""
    out = rendered("of_record")
    ctx = out["ctx"]
    for lang in LANGS:
        label = R3.labels(lang, "of_record")
        check(out[lang], lang, ctx)
        forged = out[lang].replace(f"({label['seen_by_developers']})", f"({label['held_out']})", 1)
        assert forged != out[lang]
        with pytest.raises(ReportRefused, match="held out without a clean test of record"):
            check(forged, lang, ctx)


# ------------------------------------------------------------------------------------------------- a fresh clone

def test_a_fresh_clone_shows_the_recorded_runs_and_labels_every_rerun():
    """Section 3.1: on a fresh clone, every computed number is labelled a rerun on a later vintage, the recorded runs
    are named from docs/freeze-v3.json, ledger-v3's entries are shown as recorded, the byte-copy and reproduction
    checks are said not run, and the exports' retrieval and modification dates are given."""
    out = rendered("fresh_clone")
    ctx = out["ctx"]
    _, ids = RW.fresh_clone_base()
    assert ctx["lineage"] == "fresh_clone" and out["stamp"]["lineage"] == "fresh_clone"
    assert out["stamp"]["runs"]["rehearsal_2024"] == ids["rehearsal"] != ids["recorded"]["rehearsal"]
    assert ctx["byte_copies"] == "not_run" and ctx["reproduction"]["status"] == "not_run"
    for lang in LANGS:
        text = out[lang]
        L = R3.labels(lang, "fresh_clone")
        rerun = R3.SAY[lang]["lineage"]["fresh_clone"]
        assert all(rerun in v for k, v in L.items() if k != "recorded")
        assert R3.unlabelled_lines(text, list(L.values())) == []
        assert R3.unlabelled_lines(text, [v for k, v in R3.labels(lang, "of_record").items()
                                          if k != "recorded"]) != []  # without the rerun's label, lines are bare
        prov = R3.numbered_section(text, 7)
        assert all(r in prov for r in ids["recorded"].values())
        assert prov.count(R3.SAY[lang]["not_run"]) >= 2
        assert RW.RETRIEVED["fresh_clone"][:10] in prov and RW.MODIFIED["fresh_clone"][:10] in prov
        assert L["recorded"] in R3.numbered_section(text, 5)
        # the model card: the rerun's hash labelled a rerun, and the recorded production model's hash beside it
        card = text.split(R3.SAY[lang]["card"], 1)[1].split("\n## ", 1)[0]
        row = next(x for x in card.splitlines() if x.startswith({"fr": "| **Modèle**", "en": "| **Model**"}[lang]))
        assert f"`{ctx['frozen'][2025]['sha'][:12]}` (_{rerun}_)" in row
        assert f"`{ctx['record']['data']['model_sha256'][:12]}` ({L['recorded']})" in row
        # the reproduction check said not run in the page's own words, never the run's text
        assert ctx["reproduction"]["why"] not in text and R3.SAY[lang]["reproduction_not_run"] in R3.numbered_section(
            text, 2)


def test_a_fresh_clone_s_rerun_never_enters_ledger_v3(tmp_path, monkeypatch):
    """Section 3.1: a fresh clone writes no ledger entry. Its rehearsal rerun, under `v3-rules-frozen` and tagged a
    fresh clone's, is skipped by ledger-v3's sync, which leaves the committed ledger as it is. The control: the same run
    not tagged a fresh clone's is entered."""
    from valiwatt import tracking
    from valiwatt.report import ledger_v3

    chain = RW.copy("fresh_clone", tmp_path, monkeypatch)
    run = tracking.client().get_run(chain.ids["rehearsal"])
    assert (run.data.tags["freeze_tag"], run.data.tags["fresh_clone"]) == (V3_FREEZE_TAG, "true")
    committed = ledger_v3.path().read_bytes()
    assert ledger_v3.from_store() == []
    ledger_v3.sync()
    assert ledger_v3.path().read_bytes() == committed
    tracking.client().set_tag(run.info.run_id, "fresh_clone", "false")
    assert [e["run_id"] for e in ledger_v3.from_store()] == [run.info.run_id]


def test_a_fresh_clone_page_must_say_what_did_not_run_and_name_the_recorded_runs():
    """A fresh clone's page without "not run", without the export's retrieval date or without a recorded run's ID is
    refused. The control: the page as rendered passes."""
    out = rendered("fresh_clone")
    ctx = out["ctx"]
    _, ids = RW.fresh_clone_base()
    for lang in LANGS:
        text = out[lang]
        check(text, lang, ctx)
        prov_at = text.rfind("\n## ")
        head, prov = text[:prov_at], text[prov_at:]
        byte_line = f"- {R3.SAY[lang]['byte_copies_not_run']}."
        assert byte_line in prov
        for forged in (head + prov.replace(R3.SAY[lang]["not_run"], "x"),
                       head + prov.replace(ctx["exports"]["hq_retrieved"], "x"),
                       head + prov.replace(ids["recorded"]["validation"], "x"),
                       # the byte-copy line gone, the words "not run" said twice elsewhere: a count would pass it
                       head + prov.replace(byte_line, f"- {R3.SAY[lang]['not_run']}, {R3.SAY[lang]['not_run']}.")):
            assert forged != text
            with pytest.raises(ReportRefused, match="fresh clone"):
                check(forged, lang, ctx)
        for key in ("byte_copies", "reproduction"):  # the chain claiming a check passed on a fresh clone
            claimed = {**ctx, key: "passed" if key == "byte_copies" else {**ctx["reproduction"], "status": "passed"}}
            with pytest.raises(ReportRefused, match="a fresh clone's chain says"):
                check(text, lang, claimed)


# ------------------------------------------------------------------------------------------------- before the freeze

def test_before_the_freeze_the_report_reads_the_latest_pre_freeze_runs_labelled_so():
    """No freeze record and no `v3-rules-frozen`: the latest runs that passed, all under `v3-pre-freeze`, each number
    labelled a run made before the freeze."""
    out = rendered("pre_freeze")
    _, ids = RW.pre_freeze_base()
    assert out["stamp"]["lineage"] == "pre_freeze" and out["stamp"]["freeze_record"] is None
    assert out["stamp"]["runs"] == {"data_quality": ids["dq"], "validation": ids["validation"],
                                    "frozen_2024": ids["frozen_2024"], "rehearsal_2024": ids["rehearsal"],
                                    "frozen_2025": ids["frozen_2025"]}
    for lang in LANGS:
        L = R3.labels(lang, "pre_freeze")
        assert R3.unlabelled_lines(out[lang], list(L.values())) == []
        assert R3.SAY[lang]["lineage"]["pre_freeze"] in L["in_sample"]
        check(out[lang], lang, out["ctx"])


def test_a_pre_freeze_number_without_the_pre_freeze_label_is_refused():
    """One line of a pre-freeze page labelled as if of record: refused. The control: the page as rendered passes."""
    out = rendered("pre_freeze")
    for lang in LANGS:
        L, plain = R3.labels(lang, "pre_freeze"), R3.labels(lang, "of_record")
        forged = out[lang].replace(L["in_sample"], plain["in_sample"], 2)  # the brief's validation line, and the card's
        with pytest.raises(ReportRefused, match="without its label"):
            check(forged, lang, out["ctx"])


@pytest.mark.parametrize("fault", ["tag_without_record", "uncommitted_record", "other_rules", "none"])
def test_a_lineage_the_report_cannot_name_is_refused(fault, tmp_path, monkeypatch):
    """`v3-rules-frozen` without a committed freeze record; a freeze record in the working tree that no commit adds;
    pre-freeze runs under other rules than the code's: each refused before anything is written. The control (none):
    the pre-freeze chain renders."""
    chain = RW.copy("pre_freeze", tmp_path, monkeypatch)
    match = {"tag_without_record": "no freeze record is committed", "uncommitted_record": "no commit adds it",
             "other_rules": "ran under other rules than the code's"}.get(fault)
    if fault == "tag_without_record":
        rw.tag(chain.repo, V3_FREEZE_TAG, "HEAD")
    elif fault == "uncommitted_record":
        (chain.repo / "docs").mkdir(exist_ok=True)
        (chain.repo / "docs" / "freeze-v3.json").write_text("{}\n", encoding="utf-8")
    elif fault == "other_rules":
        (chain.repo / rw.RULE_FILE).write_text("MIN = 5\n", encoding="utf-8")
    if match is None:
        assert RW.render(chain)["stamp"]["lineage"] == "pre_freeze"
        return
    with pytest.raises(ReportRefused, match=match):
        R3.render_v3()
    assert not (chain.repo / "docs" / "report" / "v3").exists()


@pytest.mark.parametrize("fault", ["some_runs", "other_artifacts", "other_2024_artifacts", "record_committed_twice",
                                   "none"])
def test_a_store_holding_part_of_the_record_or_other_artifacts_is_refused(fault, tmp_path, monkeypatch):
    """The store holding some of the freeze record's runs and not the others; the artifacts frozen for 2025, or for the
    2024 rehearsal, read now, other than those the record names (a threshold of the validation run changed); a freeze
    record a second commit touches (freeze_record's own check, passed through): each refused. The control (none):
    the runs of record render."""
    from valiwatt import tracking
    from valiwatt.monitor import run as mrun

    chain = RW.copy("of_record", tmp_path, monkeypatch)
    ids = chain.ids
    if fault == "some_runs":
        real = R3._get
        monkeypatch.setattr(R3, "_get", lambda c, run_id: None if run_id == ids["frozen_2025"] else real(c, run_id))
        match = "holds the freeze record's runs"
    elif fault == "other_artifacts":
        c = tracking.client()
        res = mrun._load_json_artifact(c, ids["validation"], "results/validation.json")
        res["thresholds"]["2025"]["last_year"] = 1999
        c.log_text(ids["validation"], json.dumps(res), "results/validation.json")
        match = "not those the freeze record names"
    elif fault == "other_2024_artifacts":
        c = tracking.client()
        res = mrun._load_json_artifact(c, ids["validation"], "results/validation.json")
        res["thresholds"]["2024"]["last_year"] = 1999
        c.log_text(ids["validation"], json.dumps(res), "results/validation.json")
        match = "frozen for the 2024 rehearsal, read now"
    elif fault == "record_committed_twice":
        rw.commit(chain.repo, {"docs/freeze-v3.json": "{}\n"}, "a second commit touching the freeze record")
        match = "the freeze record cannot be read as committed"
    else:
        assert RW.render(chain)["stamp"]["lineage"] == "of_record"
        return
    with pytest.raises(ReportRefused, match=match):
        R3.render_v3()
    assert not (chain.repo / "docs" / "report" / "v3").exists()


@pytest.mark.parametrize("fault", ["no_export_dates", "validation_without_ranking", "rehearsal_held_out",
                                   "held_out_status_not_held_out", "none"])
def test_a_run_without_what_the_report_prints_is_refused(fault, tmp_path, monkeypatch):
    """A data-quality run with no record of when its exports were retrieved; a validation run without its window
    ranking; a 2024 rehearsal labelled other than seen by the developers; a 2025 test of record whose verdicts give a
    held-out status, its entry not labelled held out: each refused before anything is written. The control (none): the
    clean 2025 start renders."""
    from valiwatt import tracking
    from valiwatt.monitor import run as mrun
    from valiwatt.report import ledger_v3

    chain = RW.copy("held_out", tmp_path, monkeypatch)
    c, ids = tracking.client(), chain.ids
    if fault == "no_export_dates":
        c.log_text(ids["dq24"], json.dumps({"sources": {}}), "results/dq_results.json")
        match = "holds no record of when its exports were retrieved"
    elif fault == "validation_without_ranking":
        res = mrun._load_json_artifact(c, ids["validation"], "results/validation.json")
        del res["ranking"]
        c.log_text(ids["validation"], json.dumps(res), "results/validation.json")
        match = "holds no ranking"
    elif fault == "rehearsal_held_out":
        res = mrun._load_json_artifact(c, ids["rehearsal"], "results/replay.json")
        c.log_text(ids["rehearsal"], json.dumps(res | {"evaluation": "held_out"}), "results/replay.json")
        match = "not seen by the developers"
    elif fault == "held_out_status_not_held_out":
        # ledger-v3's own check refuses such an entry in the file (ledger.LedgerError); the context refuses it as well,
        # from entries handed to it directly
        from valiwatt.monitor.rules import rules_sha256
        from valiwatt.records_v3 import replay_starts_v3
        from valiwatt.tracking import code_sha256

        entries = ledger_v3.read()
        forged = [e | {"evaluation": "seen_by_developers"} if e["year"] == 2025 else e for e in entries]
        ledger_v3.path().write_text(ledger_v3.dumps(forged), encoding="utf-8")
        with pytest.raises(Exception, match="is labelled 'seen_by_developers'"):
            R3.render_v3()
        starts = replay_starts_v3(2025, None, {"replays": entries})
        with pytest.raises(ReportRefused, match="a held-out status needs the test of record labelled held out"):
            R3.context_v3(R3.follow_v3(), forged, starts, code_sha256(), rules_sha256())
        R3.context_v3(R3.follow_v3(), entries, starts, code_sha256(), rules_sha256())
        assert not (chain.repo / "docs" / "report" / "v3").exists()
        return
    else:
        assert RW.render(chain)["stamp"]["standing_2025"]["standing"] == "clean"
        return
    with pytest.raises(ReportRefused, match=match):
        R3.render_v3()
    assert not (chain.repo / "docs" / "report" / "v3").exists()


@pytest.mark.parametrize("fault", ["no_data_quality_run", "no_validation_run", "coefficients_without_a_term", "none"])
def test_a_chain_the_report_cannot_print_is_refused(fault, tmp_path, monkeypatch):
    """On a fresh clone (the lineage that reads the latest chain): no data-quality run that passed (deleted); a
    data-quality run with no complete validation run on it (deleted); the coefficient table of the models frozen for
    2025 missing one of the model's terms: each refused before anything is written. The control (none): the fresh
    clone renders."""
    from valiwatt import tracking
    from valiwatt.forecast import model

    chain = RW.copy("fresh_clone", tmp_path, monkeypatch)
    c, ids = tracking.client(), chain.ids
    if fault == "no_data_quality_run":
        c.delete_run(ids["dq"])
        match = "holds no data-quality run of the 2019-2024 window that passed"
    elif fault == "no_validation_run":
        c.delete_run(ids["validation"])
        match = "holds no complete validation run"
    elif fault == "coefficients_without_a_term":
        coef = R3._frame(c, ids["frozen_2025"], "coefficients.csv")
        term = coef.index[coef["feature"].astype(str).str.startswith(model.TERM_PREFIX)][0]
        path = tmp_path / "coefficients.csv"
        coef.drop(index=term).to_csv(path, index=False)
        c.log_artifact(ids["frozen_2025"], str(path))
        match = "coefficient table does not hold its model's terms"
    else:
        assert RW.render(chain)["stamp"]["lineage"] == "fresh_clone"
        return
    with pytest.raises(ReportRefused, match=match):
        R3.render_v3()
    assert not (chain.repo / "docs" / "report" / "v3").exists()


# ---------------------------------------------------------------------------------------------------- the page checks

def test_a_page_not_opening_with_its_status_and_the_noon_sentence_is_refused():
    """The brief's first two bullets swapped (the status no longer first); the noon sentence moved after the
    validation's numbers; the noon sentence removed: each refused. The control: the page as rendered passes."""
    out = rendered("of_record")
    ctx = out["ctx"]
    for lang in LANGS:
        text = out[lang]
        check(text, lang, ctx)
        b = bullets(text, lang)
        swapped = text.replace(b[0] + "\n" + b[1], b[1] + "\n" + b[0])
        with pytest.raises(ReportRefused, match="does not open with its status"):
            check(swapped, lang, ctx)
        later = text.replace(b[1] + "\n" + b[2], b[2] + "\n" + b[1])
        assert later != text
        with pytest.raises(ReportRefused, match="noon the day before"):
            check(later, lang, ctx)
        key = re.search(r"\*\*(.+?)\*\*", b[1]).group(1)
        with pytest.raises(ReportRefused, match="noon the day before"):
            check(text.replace(key, "x"), lang, ctx)


def test_a_number_without_its_label_is_refused():
    """A table header's label removed (every row below it then bare), or a bullet's: refused. The control passes."""
    out = rendered("of_record")
    ctx = out["ctx"]
    for lang in LANGS:
        text = out[lang]
        L = R3.labels(lang, "of_record")
        header = {"fr": f"| Modèle ({L['in_sample']}) | EAMP |", "en": f"| Model ({L['in_sample']}) | MAPE |"}[lang]
        assert text.count(header) == 1
        with pytest.raises(ReportRefused, match="without its label"):
            check(text.replace(header, header.replace(f" ({L['in_sample']})", "")), lang, ctx)
        line = next(x for x in bullets(text, lang) if L["seen_by_developers"] in x)
        with pytest.raises(ReportRefused, match="without its label"):
            check(text.replace(line, line.replace(f" ({L['seen_by_developers']})", "")), lang, ctx)


def test_a_template_that_drops_a_label_is_refused(tmp_path, monkeypatch):
    """From a copy of the templates whose model table drops its label, `render_pages_v3` refuses before returning any
    page. The control: the copy unchanged renders."""
    from valiwatt.report import render

    ctx = rendered("of_record")["ctx"]
    copy = tmp_path / "templates"
    shutil.copytree(render.TEMPLATES, copy)
    monkeypatch.setattr(render, "TEMPLATES", copy)
    assert set(R3.render_pages_v3(ctx)) == set(LANGS)
    for lang, head in (("fr", "| Modèle ({{ L.in_sample }}) | EAMP |"), ("en", "| Model ({{ L.in_sample }}) | MAPE |")):
        page = copy / "v3" / f"validation.{lang}.md.j2"
        source = page.read_text(encoding="utf-8")
        assert source.count(head) == 1
        page.write_text(source.replace(head, head.replace(" ({{ L.in_sample }})", "")), encoding="utf-8")
        with pytest.raises(ReportRefused, match="without its label"):
            R3.render_pages_v3(ctx)
        page.write_text(source, encoding="utf-8")


# ----------------------------------------------------------------------------------------------- the constants

def test_the_design_constants_are_the_code_s():
    """What the pages state of the design, read from the code that applies it (docs/plan-v3.md, sections 2, 3, 4, 7,
    8 and 17): the noon origin, D−1 17:00 UTC, 12:00 local standard time; the lags; the terms' base and the lag day's
    rule; the wind bounds and the knot conversion; the subsets' minimums; the rate's windows and events; the
    availability margins; the reproduction tolerance."""
    from valiwatt.forecast.issue import KNOTS_TO_KMH, LAG_DAY_MIN_HOURS, NOON
    from valiwatt.forecast.validate_v3 import REPRODUCTION_TOLERANCE_MW
    from valiwatt.sources.nbm import AVAILABILITY_LAG, SENSITIVITY_LAG

    K = R3.design_v3()
    assert (K["origin_utc_h"], K["origin_local_h"], K["origin_days_before"], K["midnight_utc_h"]) == (17, 12, 1, 5)
    assert (K["origin_utc_hm"], K["origin_local_hm"], K["first_label_hm"], K["last_label_hm"]) == (
        "17:00", "12:00", "06:00", "05:00")
    assert K["lags_noon"] == list(NOON.lags_h) == [48, 168] and K["lags_midnight"] == [24, 168]
    assert (K["base_c"], K["lag_day_min_hours"], K["knots_to_kmh"]) == (18.0, LAG_DAY_MIN_HOURS, KNOTS_TO_KMH)
    assert (K["wind_min_kt"], K["wind_max_kt"], K["min_hours"], K["min_weeks"], K["rate_events"]) == (
        0.0, 100.0, ev.MIN_COLD_HOURS, ev.MIN_COLD_WEEKS, ev.RATE_EVENTS)
    assert K["windows"] == {"morning": [7, 8, 9, 10], "evening": [17, 18, 19, 20]}
    assert K["availability_lag_h"] == AVAILABILITY_LAG / pd.Timedelta(hours=1)
    assert K["sensitivity_lag_h"] == SENSITIVITY_LAG / pd.Timedelta(hours=1)
    assert K["tolerance_mw"] == REPRODUCTION_TOLERANCE_MW and K["bin_quantile"] == ev.COLD_QUANTILE
    # plan section 2: 13:00 EDT under daylight time; the notice before 15:00, 3 hours after the origin, 2 under daylight
    # time; December 24, 25, 26 and 31, January 1 and 2; events of 4 hours, at most 120 hours (30 events) a winter
    assert (K["origin_local_dst_h"], K["origin_local_dst_hm"], K["notice_local_h"]) == (13, "13:00", 15)
    assert (K["notice_lead_h"], K["notice_lead_dst_h"]) == (3, 2)
    assert (K["excluded_december"], K["excluded_january"]) == ([24, 25, 26, 31], [1, 2])
    assert (K["event_hours"], K["winter_event_hours"], K["rate_events"]) == (4, 120, 30)
    assert "n_boot" not in K  # each run's own count is printed (validation_v3.resamples)


# What a numeral may be in v3's templates beyond what v2's may (tests/test_report_constants.py ALLOWED), and why it is
# not a design constant.
ALLOWED_V3 = {
    "a template's file name": r"\bv3/\w+\.(?:fr|en)\.md\.j2\b",
    "the version name v3": r"\bv3\b",
    "the day two days before the forecast day, D−2": r"\b[DJ]−2\b",
    "the rate's citation: its edition year, its effective date, its section and the articles it cites (its peak "
    "hours, its events, its notice)":
        r"\b2026 Electricity Rates\b|Tarifs d'électricité 2026|\b2026-04-01\b|\b[Ss]ection 9\b|\bart\. 2\.(?:67|70|71)\b",
    "the winter of the rate's peak hours, December 1 to March 31 (calendar facts)":
        r"\bDecember 1 to March 31\b|\b1er décembre au 31 mars\b",
    "a formula's floor, max(0, …)": r"max\(0, ",
    "the rounding of the window ranking's k, + 0.5": r"\+ 0[.,]5\)",
}


@pytest.mark.parametrize("doc, lang", [(d, lang) for d in ("validation", "sentences") for lang in LANGS])
def test_the_v3_templates_type_no_design_constant(doc, lang):
    """tests/test_report_constants.py's scan on v3's templates, with ALLOWED_V3 beside v2's ALLOWED."""
    import test_report_constants as TC
    from valiwatt.report.render import TEMPLATES

    path = TEMPLATES / "v3" / f"{doc}.{lang}.md.j2"

    def typed(text: str) -> list[str]:
        for pattern in ALLOWED_V3.values():
            text = re.sub(pattern, "¤", text)
        return TC.typed_numerals(text)

    assert typed(TC.literal_text(path)) == []
    assert [s for s in TC.jinja_strings(path) if typed(s)] == []
    assert [e for e in TC.expressions(path) if TC.printed_numbers(e)] == []
    assert [e for e in TC.assigned(path) if TC.printed_numbers(e)] == []


# ------------------------------------------------------------------------------------- what judged a 2025 number

@pytest.mark.parametrize("fault", ["ledger_digest", "results_digest", "none"])
def test_a_2025_number_judged_by_other_artifacts_than_the_freeze_record_s_is_refused(fault, tmp_path, monkeypatch):
    """Section 10.3: the freeze record holds the one hash of every artifact that will judge 2025. A 2025 entry of
    ledger-v3 whose `frozen_sha256` is another digest, or a 2025 run in the store whose results' own `frozen_sha256`
    digests to another: refused before anything is written. The control (none): the clean 2025 start renders."""
    from valiwatt import tracking
    from valiwatt.monitor import run as mrun
    from valiwatt.report import ledger_v3

    chain = RW.copy("held_out", tmp_path, monkeypatch)
    if fault == "ledger_digest":
        entries = ledger_v3.read()
        forged = [e | {"frozen_sha256": "f" * 64} if e["year"] == 2025 else e for e in entries]
        ledger_v3.path().write_text(ledger_v3.dumps(forged), encoding="utf-8")
        match = "judged by artifacts of digest ffffffffffff in ledger-v3"
    elif fault == "results_digest":
        c = tracking.client()
        res = mrun._load_json_artifact(c, chain.ids["held_out"], "results/replay.json")
        res["frozen_sha256"]["thresholds"] = "0" * 64
        c.log_text(chain.ids["held_out"], json.dumps(res), "results/replay.json")
        match = "results were judged by other artifacts than those the freeze record names"
    else:
        assert RW.render(chain)["stamp"]["standing_2025"]["standing"] == "clean"
        return
    with pytest.raises(ReportRefused, match=match):
        R3.render_v3()
    assert not (chain.repo / "docs" / "report" / "v3").exists()


def test_a_2025_entry_without_a_committed_freeze_record_is_refused():
    """No 2025 number stands without the record of what judged it: a 2025 entry with no record is refused; a 2024 one
    needs none, and a 2025 entry carrying the record's digest passes."""
    record = {"data": {"frozen_sha256": "a" * 64}}
    e25 = {"run_id": "9" * 32, "year": 2025, "frozen_sha256": "a" * 64}
    with pytest.raises(ReportRefused, match="no freeze record is committed"):
        R3.judged_by_the_record([e25], None)
    R3.judged_by_the_record([e25 | {"year": 2024}], None)
    R3.judged_by_the_record([e25], record)


# ----------------------------------------------------------------------------------------- labels by section

def _forge(text: str, lang: str, fault: str, ctx: dict) -> str:
    """`text` with one label moved against its section (test_a_label_against_its_section_is_refused)."""
    L = R3.labels(lang, ctx["lineage"])
    sep = R3.SAY[lang]["sep"]

    def in_section(n: int, old: str, new: str, count: int = -1) -> str:
        part = R3.numbered_section(text, n)
        assert old in part, (fault, lang)
        return text.replace(part, part.replace(old, new, count))

    if fault == "rehearsal_held_out":
        return in_section(3, L["seen_by_developers"], L["held_out"])
    if fault == "validation_seen":
        header = {"fr": f"| Modèle ({L['in_sample']}) | EAMP |", "en": f"| Model ({L['in_sample']}) | MAPE |"}[lang]
        return in_section(2, header, header.replace(L["in_sample"], L["seen_by_developers"]))
    if fault == "sensitivity_without_in_sample":
        return in_section(2, f"({L['in_sample']}{sep}{L['sensitivity']})", f"({L['sensitivity']})")
    if fault == "ledger_row_held_out":
        row = next(x for x in R3.numbered_section(text, 5).splitlines() if L["seen_by_developers"] in x)
        return in_section(5, row, row.replace(L["seen_by_developers"], L["held_out"]))
    if fault == "brief_rehearsal_held_out":
        line = next(x for x in bullets(text, lang) if L["seen_by_developers"] in x)
        return text.replace(line, line.replace(L["seen_by_developers"], L["held_out"]))
    if fault == "rerun_recorded":
        return in_section(3, L["seen_by_developers"], L["recorded"])
    if fault == "recorded_off_a_fresh_clone":  # a ledger row, where a fresh clone's page may say "as recorded"
        row = next(x for x in R3.numbered_section(text, 5).splitlines() if L["seen_by_developers"] in x)
        return in_section(5, row, row.replace(L["seen_by_developers"], R3.labels(lang, "fresh_clone")["recorded"]))
    raise AssertionError(fault)


@pytest.mark.parametrize("base, fault", [
    ("held_out", "rehearsal_held_out"), ("held_out", "validation_seen"), ("held_out", "sensitivity_without_in_sample"),
    ("held_out", "ledger_row_held_out"), ("held_out", "brief_rehearsal_held_out"),
    ("fresh_clone", "rerun_recorded"), ("held_out", "recorded_off_a_fresh_clone"),
    ("held_out", "none"), ("fresh_clone", "none"), ("pre_freeze", "none")])
def test_a_label_against_its_section_is_refused(base, fault):
    """Section 8.4, each number labelled as its year gives it: on a page whose 2025 test of record is clean, the 2024
    rehearsal's section relabelled held out; a validation table labelled seen by the developers; §2.6's sensitivities
    without the in-sample label beside theirs; a ledger-v3 row other than the test of record's labelled held out; the
    brief's rehearsal line labelled held out; on a fresh clone, a rerun's number labelled only "as recorded"; and "as
    recorded" on a ledger row of a page of the runs of record: each refused. The controls
    (none): the pages as rendered pass."""
    out = rendered(base)
    ctx = out["ctx"]
    for lang in LANGS:
        text = out[lang]
        check(text, lang, ctx)
        if fault == "none":
            assert R3.mislabelled_lines(text, lang, ctx) == []
            continue
        forged = _forge(text, lang, fault, ctx)
        assert forged != text
        with pytest.raises(ReportRefused, match="against its section|without its label"):
            check(forged, lang, ctx)


# -------------------------------------------------------------------------------------------- the resamples

@pytest.mark.parametrize("fault", ["validation", "rehearsal", "held_out", "unrecorded", "none"])
def test_a_run_of_record_with_other_resamples_is_refused(fault, tmp_path, monkeypatch):
    """Section 8.1: every interval of the decision rule takes the plan's resamples (evaluate.N_BOOT, set to the chain's
    in the test world). A run of record that took another count (the validation, the rehearsal, the 2025 test of
    record), or a replay that does not record its count: refused before anything is written. The control (none):
    the clean 2025 start renders, and each page prints each run's own count."""
    from valiwatt import tracking
    from valiwatt.monitor import run as mrun

    chain = RW.copy("held_out", tmp_path, monkeypatch)
    c, ids = tracking.client(), chain.ids
    run_id, path = {"validation": (ids["validation"], "results/validation.json"),
                    "rehearsal": (ids["rehearsal"], "results/replay.json"),
                    "held_out": (ids["held_out"], "results/replay.json"),
                    "unrecorded": (ids["rehearsal"], "results/replay.json")}.get(fault, (None, None))
    if fault == "none":
        pages = RW.render(chain)
        for lang in LANGS:
            word = {"fr": "rééchantillonnages", "en": "resamples"}[lang]
            assert pages[lang].count(f"{RW.N_BOOT} {word}") == 3  # the validation, the rehearsal, the 2025 run
        return
    res = mrun._load_json_artifact(c, run_id, path)
    if fault == "validation":
        res["config"]["n_boot"] = RW.N_BOOT + 1
    elif fault == "unrecorded":
        del res["n_boot"]
    else:
        res["n_boot"] = RW.N_BOOT + 1
    c.log_text(run_id, json.dumps(res), path)
    match = "does not record how many resamples" if fault == "unrecorded" else \
        f"took {RW.N_BOOT + 1} resamples for its bootstrap intervals, not the plan's {RW.N_BOOT}"
    with pytest.raises(ReportRefused, match=match):
        R3.render_v3()
    assert not (chain.repo / "docs" / "report" / "v3").exists()


def test_a_run_made_before_the_freeze_prints_its_own_resamples(tmp_path, monkeypatch):
    """Before the freeze no run is of record: a validation that took another count than the plan's renders, and its
    page prints the count the run took, never the plan's."""
    from valiwatt import tracking
    from valiwatt.monitor import run as mrun

    chain = RW.copy("pre_freeze", tmp_path, monkeypatch)
    c = tracking.client()
    res = mrun._load_json_artifact(c, chain.ids["validation"], "results/validation.json")
    res["config"]["n_boot"] = 37
    c.log_text(chain.ids["validation"], json.dumps(res), "results/validation.json")
    pages = RW.render(chain)
    assert "37 rééchantillonnages" in pages["fr"] and "37 resamples" in pages["en"]


# ------------------------------------------------------------------------------------ one cold_c, keyed reasons

@pytest.mark.parametrize("fault", ["two_cold_c", "none"])
def test_a_validation_run_with_two_cold_c_is_refused(fault, tmp_path, monkeypatch):
    """Section 6: one validation run has one cold_c. A validation whose contrasts cut the cold tail elsewhere than its
    config's cold_c: refused before anything is written. The control (none): the runs of record render, every
    cold tail of the page at the one cold_c."""
    from valiwatt import tracking
    from valiwatt.monitor import run as mrun
    from valiwatt.report.render import filters

    chain = RW.copy("of_record", tmp_path, monkeypatch)
    c, ids = tracking.client(), chain.ids
    res = mrun._load_json_artifact(c, ids["validation"], "results/validation.json")
    cold_c = res["config"]["cold_c"]
    assert res["contrasts"]["cold_c"] == cold_c
    if fault == "none":
        pages = RW.render(chain)
        for lang in LANGS:
            said = re.findall(r"([−-]?\d+[.,]\d) °C, \d", pages[lang])
            assert said and set(said) == {filters(lang)["num"](cold_c, 1)}, said
        return
    res["contrasts"]["cold_c"] = cold_c - 1
    c.log_text(ids["validation"], json.dumps(res), "results/validation.json")
    with pytest.raises(ReportRefused, match="a validation run has one cold_c"):
        R3.render_v3()
    assert not (chain.repo / "docs" / "report" / "v3").exists()


@pytest.mark.parametrize("fault", ["unknown_day_reason", "unknown_window_reason", "none"])
def test_a_reason_the_pages_cannot_phrase_is_refused(fault, monkeypatch):
    """A run's reasons are printed by their keys, one phrase per language, never as the run's text: a day without a
    forecast whose reason has no phrase, or a window not eligible for the ranking whose reason has none, is refused.
    The control (none): the context as rendered passes, and the French page prints no English reason."""
    import copy

    ctx = copy.deepcopy(rendered("of_record")["ctx"])
    if fault == "unknown_day_reason":
        ctx["v"]["forecast_input"]["noon"]["days_not_fed"][0]["reason"] = "a reason no template phrases"
        for lang in LANGS:  # each language's page refuses on its own
            monkeypatch.setattr(R3, "LANGS", (lang,))
            with pytest.raises(ReportRefused, match=f"the v3 {lang} page meets something it cannot phrase"):
                R3.render_pages_v3(ctx)
    elif fault == "unknown_window_reason":
        parts = next(iter(ctx["v"]["ranking"]["years"].values()))
        parts[0]["ineligible"] = [{"day": "2020-02-12", "window": "morning", "reason": "a reason no one phrases"}]
        with pytest.raises(ReportRefused, match="cannot phrase"):
            R3.keyed_ineligible(parts)
    else:
        pages = R3.render_pages_v3(ctx)
        for reason in ("no forecast from", "of its 4 hours", "no_covering_run", "lag_day_temperature_missing"):
            assert reason not in pages["fr"] and reason not in pages["en"]


# ----------------------------------------------------------------------------------- what the pages list and show

def test_the_ineligible_windows_and_the_days_without_the_lag_day_s_mean_are_listed():
    """Section 8.3 lists the windows not eligible with their reason; section 5 the days without the lag day's mean.
    Each part of the validation's ranking names every reason with its count and its days, and §2.9 names every day
    without the lag day's mean."""
    out = rendered("of_record")
    ctx = out["ctx"]
    fi = ctx["v"]["forecast_input"]["noon"]
    assert fi["lag_day_missing_days"]
    for lang in LANGS:
        two = R3.numbered_section(out[lang], 2)
        ranking = two.split("### 2.8", 1)[1].split("### 2.9", 1)[0]
        noon = two.split("### 2.9", 1)[1]
        for parts in ctx["v"]["ranking"]["years"].values():
            for p in parts:
                assert sum(g["count"] for g in p["ineligible_by_reason"]) == len(p["ineligible"])
                for g in p["ineligible_by_reason"]:
                    assert all(r["first"] in ranking for r in g["ranges"])
        assert any(p["ineligible"] for parts in ctx["v"]["ranking"]["years"].values() for p in parts)
        assert all(r["first"] in noon and r["last"] in noon for r in ctx["lag_day_missing"])
        assert {d for r in ctx["lag_day_missing"] for d in (r["first"], r["last"])} <= set(fi["lag_day_missing_days"])


def test_sections_3_and_4_show_what_plan_8_3_reports_beside_the_verdicts():
    """Section 8.3, reported beside the verdicts: the subsets (and which cannot be tested), the horizon contrast, the
    coverage in each bin, each model's MAPE and the busiest hours, for the 2024 rehearsal and for 2025's test of record
    alike, each labelled as its year is; a subset that cannot be tested is marked so on its rows."""
    from valiwatt.report.render import filters

    out = rendered("held_out")
    ctx = out["ctx"]
    rs, hr = ctx["rehearsal"]["results"], ctx["held"]["numbers"]["results"]
    for lang in LANGS:
        L, f = R3.labels(lang, "of_record"), filters(lang)
        heads = {"fr": ("Les sous-ensembles (", "| Horizon : ", "| Classe de v3 (", "| Modèle (", "| Heure de fin, UTC ("),
                 "en": ("The subsets (", "| Horizon: ", "| v3's bin (", "| Model (", "| Hour ending, UTC (")}[lang]
        untestable = {"fr": "(sous-ensemble non testable)", "en": "(subset not testable)"}[lang]
        for n, res, label in ((3, rs, L["seen_by_developers"]), (4, hr, L["held_out"])):
            part = R3.numbered_section(out[lang], n)
            assert all(part.count(h) == 1 for h in heads), (lang, n)
            assert all(label in next(x for x in part.splitlines() if x.startswith(h)) for h in heads), (lang, n)
            for h in res["judgement"]["contrasts"]["peak"]:
                assert f"| {f['utc'](h['utc'])} | {f['num'](h['target'])} MW |" in part
            assert f"| {f['num'](res['coverage_by_bin']['cold']['hours'])} |" in part
            subsets = res["judgement"]["contrasts"]["subsets"]
            if not (subsets["cold"]["testable"] and subsets["event_windows"]["testable"]):
                assert untestable in part


def test_the_horizon_s_all_row_names_the_horizon_s_own_hours():
    """The horizon's "all" contrast is computed on its own hours (the target, v2's form at noon and at midnight;
    forecast/evaluate.py contrasts_v3), not on the common hours of the noon contrasts: every horizon table (the
    validation's, the rehearsal's, 2025's test of record) labels that row so, in both languages, with the hours the
    run counted, and never "all common hours"; the noon contrasts' "all" row keeps the common hours' label."""
    from valiwatt.report.render import filters

    out = rendered("held_out")
    ctx = out["ctx"]
    horizons = {2: ctx["v"]["contrasts"]["horizon"],
                3: ctx["rehearsal"]["results"]["judgement"]["contrasts"]["horizon"],
                4: ctx["held"]["numbers"]["results"]["judgement"]["contrasts"]["horizon"]}
    common = {"fr": "toutes les heures communes", "en": "all common hours"}
    for lang in LANGS:
        f, own = filters(lang), R3.SAY[lang]["horizon_all"]
        assert common[lang] not in own
        for n, h in horizons.items():
            lines = R3.numbered_section(out[lang], n).splitlines()
            at = next(i for i, x in enumerate(lines) if x.startswith(("| Horizon: ", "| Horizon : ")))
            assert lines[at + 2].startswith(f"| {own} | {f['num'](h['subsets']['all']['hours'])} |"), (lang, n)
            assert not any(x.startswith(f"| {common[lang]} |") for x in lines[at:at + 8]), (lang, n)
        assert f"| {common[lang]} |" in R3.numbered_section(out[lang], 2)


@pytest.mark.parametrize("fault", ["data_quality_not_a_fresh_clone", "none"])
def test_a_fresh_clone_lineage_needs_a_data_quality_run_labelled_so(fault, tmp_path, monkeypatch):
    """Section 3.1: a store without the runs of record reports a rerun on a later vintage, whose data-quality run is
    labelled a fresh clone's (its byte copies and reproduction not run). Its data-quality run not labelled so (a second
    checkout on the machine of record, say): refused before anything is written. The control (none): the fresh clone
    renders."""
    from valiwatt import tracking

    chain = RW.copy("fresh_clone", tmp_path, monkeypatch)
    if fault == "none":
        assert RW.render(chain)["stamp"]["lineage"] == "fresh_clone"
        return
    tracking.client().set_tag(chain.ids["dq"], "fresh_clone", "false")
    with pytest.raises(ReportRefused, match="is not labelled a fresh clone's"):
        R3.render_v3()
    assert not (chain.repo / "docs" / "report" / "v3").exists()


def test_every_reason_the_code_can_give_has_a_phrase_in_both_languages():
    """The pages print a reason only by its key (`why`, strict): every key the code can write has a phrase in both
    sentence templates. The keys: each input a v3 model reads (validate_v3.why_noon names a missing input by its
    column, the lag day's by LAG_DAY_REASON), the replay's noon reasons (replay.NOON_REASONS), the forecast record's
    reasons (features.REASONS), v2's form at midnight's inputs (lag24, lag168, temp_forecast), "calendar" and "no
    forecast"."""
    from valiwatt.forecast.features import REASONS
    from valiwatt.forecast.issue import LAG_DAY_REASON
    from valiwatt.forecast.model import MODELS_V3
    from valiwatt.monitor.replay import NOON_REASONS

    keys = ({c for s in MODELS_V3.values() for c in s.inputs} | set(NOON_REASONS) | set(REASONS)
            | {LAG_DAY_REASON, "lag24", "lag168", "temp_forecast", "calendar", "no forecast"})
    for lang in LANGS:
        phrased = environment(lang).get_template(f"v3/sentences.{lang}.md.j2").make_module({}).REASON
        assert keys - set(phrased) == set(), lang


def test_the_validation_and_the_replay_draw_the_same_number_of_resamples():
    """The resample count has two homes, validation's and the replay's; the report refuses a run of record whose count
    is not validation's, so the two must move together."""
    from valiwatt.forecast import evaluate
    from valiwatt.monitor import run as mrun
    assert mrun.N_BOOT == evaluate.N_BOOT
