"""The v2 validation report, rendered from synthetic contexts (tests/report_payloads.py) through the real `derive`, the
real 2025 view and the report's own templates and checks: every state and status renders a true sentence in both
languages, and each claim the render must refuse is refused."""

import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import report_payloads as RP
import report_store as S
import report_world as W

from valiwatt import attribution
from valiwatt.paths import REPO_ROOT
from valiwatt.report import figures as F
from valiwatt.report import ledger
from valiwatt.report import validation as RV
from valiwatt.report.lineage import RecordedReplay
from valiwatt.report.render import LEAD_FINDING, ReportRefused, environment, fmt_num

LANGS = RP.LANGS
P_YEAR, R_YEAR, H_YEAR = RP.P_YEAR, RP.R_YEAR, RP.H_YEAR
FIG = RP.FIG
SAY = RV.SAY

# A v2 predecessor of a departure, from the ledger, as `validation.departure` shows it.
EARLIER = {"id": "1" * 32, "freeze_commit": W.F0, "recorded": True, "unfinished": False, "absent": False,
           "recommendations": [{"week_start": "2024-06-17", "recommendation": "recalibrate"}],
           "summary": {"mape": {"mean": 0.04231}, "coverage": {"mean": 0.8943}, "recommendations": {"recalibrate": 1}},
           "model_sha256": None, "flag_weeks": None}
NOT_IN_LEDGER = {"model_sha256": None, "flag_weeks": None}


def payloads(seed=0):
    return RP.payloads(seed)


def oos_frame():
    return RP.oos_frame()


def context(departures=(), **override) -> dict:
    return RP.context(departures=departures, **override)


def render(ctx: dict, lang: str, check: bool = False) -> str:
    return RP.render(ctx, lang, check=check)


def section(text: str, number: int) -> str:
    start = re.search(rf"^## {number}\. ", text, re.M).start()
    nxt = re.search(r"^## \d+\. ", text[start + 4:], re.M)
    return text[start:start + 4 + nxt.start()] if nxt else text[start:]


def card_row(text: str, label: str) -> str:
    return next(line for line in text.splitlines() if line.startswith(f"| **{label}"))


def pct(x, d, lang):
    return fmt_num(100 * x, d, lang) + ("\u00a0%" if lang == "fr" else "%")


def brief(text: str, lang: str) -> list[str]:
    return RV._bullets_after(text, "## En bref" if lang == "fr" else "## In brief")


# --- every state, both languages ---------------------------------------------------------------------------------

@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("state", list(RP.STATES))
def test_every_state_renders_a_whole_report_in_both_languages(state, lang):
    text = RP.render(RP.state(state), lang)  # with the render's own checks
    assert "None" not in text and "{{" not in text and "{%" not in text and "nan" not in text.lower().split()
    for n in range(1, 14):
        assert re.search(rf"^## {n}\. ", text, re.M), n
    assert "\n- **" not in text.split("## En bref" if lang == "fr" else "## In brief")[0]


@pytest.mark.parametrize("lang", LANGS)
def test_the_pending_2025_state_renders_in_both_languages(lang):
    ctx = RP.state("none")
    text = RP.render(ctx, lang)
    nine = section(text, 9)
    assert nine.splitlines()[2].startswith("**En attente.**" if lang == "fr" else "**Pending.**")
    assert ctx["status"] == "candidate" and ctx["held"] is None
    assert brief(text, lang)[0].startswith("**Statut : candidat.**" if lang == "fr" else "**Status: candidate.**")
    assert card_row(text, "2025").endswith(("En attente (section 9). |" if lang == "fr" else "Pending (section 9). |"))
    assert SAY[lang]["held_out"] not in text  # no held-out claim before 2025 ran


SCOPE = {  # each status's sentence, and the words that name its scope
    "fr": {"candidate": ("candidat", "en attente"), "confirmed": ("v1 tel que livré et que la forme de v1", "réservées"),
           "input_only": ("le GAM n'est pas montré meilleur", "réservées"),
           "not_confirmed": ("pas montré avec une erreur horaire plus faible", "réservées"),
           "post_hoc": ("a posteriori", "ce n'est pas un test sur données réservées"),
           "unfinished": ("n'a pas fini", "aucune affirmation")},
    "en": {"candidate": ("candidate", "pending"), "confirmed": ("v1 as shipped and than v1's form", "held-out"),
           "input_only": ("the GAM not shown better", "held-out"),
           "not_confirmed": ("not shown to have lower hourly error", "held-out"),
           "post_hoc": ("post hoc", "not a held-out test"),
           "unfinished": ("did not finish", "no claim")},
}
STATUS_STATE = {"candidate": "none", "confirmed": "confirmed", "input_only": "input_only",
                "not_confirmed": "not_confirmed", "post_hoc": "post_hoc", "unfinished": "unfinished"}


@pytest.mark.parametrize("lang", LANGS)
def test_each_status_names_its_scope_in_both_languages(lang):
    seen = set()
    for status, state in STATUS_STATE.items():
        ctx = RP.state(state)
        assert ctx["status"] == status
        line = RV.status_sentence(environment(lang), ctx, lang)
        assert all(w in line for w in SCOPE[lang][status]), (status, line)
        assert (SAY[lang]["held_out"] in line) == (status in ("confirmed", "input_only", "not_confirmed"))
        text = RP.render(ctx, lang)
        assert brief(text, lang)[0].startswith(line)
        # the page is checked, not only the sentence: a page that does not open with it refuses, whatever the status,
        # and the refusal names a 2025 result only for a status that states one
        opens = "the 2025 result" if status in ("confirmed", "input_only", "not_confirmed") else "its status"
        for doc, page in (("validation", text), ("story", RP.render(ctx, lang, doc="story"))):
            with pytest.raises(ReportRefused, match=f"does not open with {opens}$"):
                RV.check_rendered(page.replace(line, ""), lang, ctx, doc, line)
        seen.add(line)
    assert len(seen) == len(STATUS_STATE)
    untested = RV.status_sentence(environment(lang), RP.state("cold_untested"), lang)
    assert ("la queue froide de 2025 n'était pas testable" if lang == "fr" else
            "2025's cold tail was not testable") in untested  # better on average, said with its cold-tail clause


@pytest.mark.parametrize("lang", LANGS)
def test_the_model_card_names_the_model_2025_is_judged_by(lang):
    """The card's hash is this chain's frozen model's, whose date, interval and curves it shows; once a 2025 replay
    has started, it names too the frozen model of the 2024 replay of record, which judges 2025, and whether it is the
    same. Before any start, it names only the chain's."""
    words = {"fr": ("jugée par le modèle figé du rejeu de 2024 de référence", "le même", "un autre"),
             "en": ("judged by the frozen model of the 2024 replay of record", "the same", "another")}[lang]
    label = "Modèle" if lang == "fr" else "Model"
    ctx = RP.context()
    own = ctx["frozen"][R_YEAR]["tags"]["coefficient_sha256"]
    row = card_row(RP.render(ctx, lang), label)
    assert f"`{own[:12]}`" in row and words[0] not in row
    for judged, said in ((own, words[1]), ("9" * 64, words[2])):
        ctx = RP.context(held_view=RP.clean_record(), rec24={**RP.rec24_view(), "model_sha256": judged})
        row = card_row(RP.render(ctx, lang), label)
        assert f"`{own[:12]}`" in row and f"{words[0]}" in row and f"`{judged[:12]}`, {said}" in row, row


# --- the labels and the held-out claim ------------------------------------------------------------------------------

def test_a_2024_v2_number_without_its_label_is_refused():
    for part in ("contrasts", "verdict"):
        v, dq, p, r, b, frozen, oos, diag = payloads()
        del r[part]["label"]
        with pytest.raises(ReportRefused, match=f"the 2024 replay's {part} carry the label None"):
            RV.derive(v, dq, p, r, b, frozen, oos, diag)
        v, dq, p, r, b, frozen, oos, diag = payloads()
        r[part]["label"] = "held_out"
        with pytest.raises(ReportRefused, match="not 'seen_by_developers'"):
            RV.derive(v, dq, p, r, b, frozen, oos, diag)
    v, dq, p, r, b, frozen, oos, diag = payloads()
    v["contrasts"]["label"] = "seen_by_developers"
    with pytest.raises(ReportRefused, match="validation's contrasts"):
        RV.derive(v, dq, p, r, b, frozen, oos, diag)


@pytest.mark.parametrize("lang", LANGS)
def test_every_labelled_table_prints_its_label(lang):
    text = RP.render(RP.state("confirmed"), lang)
    for n, label in ((3, "in_sample_for_choice"), (7, "rehearsal"), (8, "seen_by_developers"), (9, "held_out")):
        words = environment(lang).get_template(f"sentences.{lang}.md.j2").module.LABEL[label]
        tables = [x for x in section(text, n).split("\n\n") if x.startswith("| Comparaison" if lang == "fr" else
                                                                             "| Comparison")]
        assert tables and all(words in t.splitlines()[0] for t in tables), (n, label)


@pytest.mark.parametrize("lang", LANGS)
def test_a_better_claim_without_the_held_out_label_is_refused(lang):
    ctx = RP.state("confirmed")
    env = environment(lang)
    line = RV.status_sentence(env, ctx, lang)
    assert SAY[lang]["held_out"] in line
    text = RP.render(ctx, lang)
    bare = line.replace(SAY[lang]["held_out"], "")
    with pytest.raises(ReportRefused, match="without the held-out label"):
        RV.check_rendered(text.replace(line, bare), lang, ctx, "validation", bare)
    with pytest.raises(ReportRefused, match="does not open with the 2025 result"):  # the page, not the sentence
        RV.check_rendered(text.replace(line, bare), lang, ctx, "validation", line)


@pytest.mark.parametrize("lang", LANGS)
def test_a_better_claim_from_a_2025_that_is_not_clean_is_refused(lang):
    post = RP.state("post_hoc")
    assert post["status"] == "post_hoc" and post["held"]["record"]["verdict"]["shipped"] == "better"
    text = RP.render(post, lang)
    assert SAY[lang]["held_out"] not in brief(text, lang)[0]
    forced = {**post, "status": "confirmed"}
    line = RV.status_sentence(environment(lang), forced, lang)
    with pytest.raises(ReportRefused, match="labelled held out"):
        RV.check_rendered(RP.render(forced, lang, check=False), lang, forced, "validation", line)


@pytest.mark.parametrize("lang", LANGS)
def test_a_2025_verdict_without_a_2025_replay_is_refused(lang):
    ctx = {**RP.state("none"), "status": "confirmed"}
    line = RV.status_sentence(environment(lang), RP.state("confirmed"), lang)
    with pytest.raises(ReportRefused, match="labelled held out"):
        RV.check_rendered(RP.render(RP.state("none"), lang, check=False), lang, ctx, "validation", line)
    with pytest.raises(ReportRefused, match="carries no verdict"):
        RV.assemble_held(*_walk_of(RP.record("b" * 32, W.utc(10)), verdict=None))


def _walk_of(rec, verdict="keep"):
    starts = [W.start(rec["run_id"], rec["started_utc"], True, rec["gate"])]
    walk = ledger.of_record([RP.FIRST_2024, {k: rec[k] for k in ledger.V2_FIELDS}], starts)
    shown = dict(rec) if verdict == "keep" else {**rec, "verdict": verdict}
    return walk, shown, [], RP.findings()


# --- v1's 2024 numbers ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("lang", LANGS)
def test_v1_2024_numbers_come_from_its_ledger_entries(lang):
    ctx = RP.state("none")
    text = RP.render(ctx, lang)
    eight = section(text, 8)
    for e in (RP.V1_PLAIN, RP.V1_DEPARTED):
        row = next(x for x in eight.splitlines() if f"`{e['run_id']}`" in x and x.startswith("| v1"))
        assert pct(e["mape"], 2, lang) in row and pct(e["coverage"], 1, lang) in row
    r = ctx["r"]
    with pytest.raises(ReportRefused, match="from their ledger entries only"):
        RV.v1_view([RecordedReplay(RP.V1_PLAIN), object()], r)
    for key, value in (("ledger_run_id", "7" * 32), ("ledger_mape", 0.05), ("ledger_coverage", 0.5)):
        bad = {**r, "v1_reconciliation": {**r["v1_reconciliation"], key: value}}
        with pytest.raises(ReportRefused, match="v1's 2024 numbers come from its ledger entries only"):
            RV.v1_view([RecordedReplay(RP.V1_PLAIN), RecordedReplay(RP.V1_DEPARTED)], bad)
    with pytest.raises(ReportRefused, match="no reconciliation"):
        RV.v1_view([RecordedReplay(RP.V1_PLAIN)], {k: x for k, x in r.items() if k != "v1_reconciliation"})


# --- the 2025 starts ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("lang", LANGS)
def test_an_unfinished_2025_start_not_listed_is_refused(lang):
    ctx = RP.state("reruns")
    other = ctx["held"]["others"][0]["run_id"]
    text = RP.render(ctx, lang)
    assert other in section(text, 9)
    cut = "\n".join(x for x in text.splitlines() if other not in x)
    line = RV.status_sentence(environment(lang), ctx, lang)
    with pytest.raises(ReportRefused, match=f"unfinished 2025 start {other} is not listed"):
        RV.check_rendered(cut, lang, ctx, "validation", line)
    listing = next(x for x in text.splitlines() if other in x)
    moved = cut.replace("\n## 11.", "\n" + listing + "\n\n## 11.")  # listed, but in section 10, not 2025's
    with pytest.raises(ReportRefused, match=f"unfinished 2025 start {other} is not listed"):
        RV.check_rendered(moved, lang, ctx, "validation", line)


@pytest.mark.parametrize("lang", LANGS)
def test_a_later_passing_rerun_never_overrides_the_test_of_record(lang):
    first = RP.record("b" * 32, W.utc(10), ("worse", "better"))
    again = RP.record("d" * 32, W.utc(12), ("better", "better"), evaluation="seen_by_developers")
    starts = [W.start(first["run_id"], first["started_utc"], True, first["gate"]),
              W.start(again["run_id"], again["started_utc"], True, again["gate"])]
    held = RP.held(starts, {first["run_id"]: first, again["run_id"]: again}, RP.findings())
    assert held["record"]["run_id"] == first["run_id"] and held["status"] == "not_confirmed"
    assert held["recommendation"]["run_id"] == first["run_id"] and held["recommendation"]["withdrawn"] == ["shipped"]
    assert [x["run_id"] for x in held["reruns"]] == [again["run_id"]] and held["reruns"][0]["differences"] == ["verdict"]
    text = RP.render(RP.context(held_view=held, rec24=RP.rec24_view()), lang)
    assert brief(text, lang)[0].startswith("**Statut : pas montré" if lang == "fr" else "**Status: not shown")
    assert again["run_id"] in section(text, 9) and SAY[lang]["recommendation"]["return_or_rule"] in text
    walk = ledger.of_record([RP.FIRST_2024] + [{k: x[k] for k in ledger.V2_FIELDS} for x in (first, again)], starts)
    with pytest.raises(ReportRefused, match="reruns shown are not the ones the walk"):
        RV.assemble_held(walk, first, [], RP.findings())


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("state", ["not_confirmed", "input_only"])
def test_a_clean_2025_not_confirmed_without_its_recommendation_is_refused(state, lang):
    ctx = RP.state(state)
    rec = ctx["recommendation"]
    assert rec is not None and rec["kind"] == ("return_or_rule" if state == "not_confirmed" else
                                               "keep_input_review_family")
    env = environment(lang)
    line = RV.status_sentence(env, ctx, lang)
    text = RP.render(ctx, lang)
    words = SAY[lang]["recommendation"][rec["kind"]]
    with pytest.raises(ReportRefused, match="without its recommendation"):
        RV.check_rendered(text.replace(words, "..."), lang, ctx, "validation", line)
    head, rest = text.split("## En bref" if lang == "fr" else "## In brief", 1)
    bullets = rest.split("\n## ", 1)
    lines = bullets[0].strip().splitlines()
    swapped = head + ("## En bref" if lang == "fr" else "## In brief") + "\n\n" + "\n".join(lines[1:] + lines[:1]) + \
        "\n\n## " + bullets[1]
    with pytest.raises(ReportRefused, match="does not open with the 2025 result"):
        RV.check_rendered(swapped, lang, ctx, "validation", line)
    story = RP.render(ctx, lang, doc="story")
    first = RV._first_point(story)
    assert first.startswith(line)
    with pytest.raises(ReportRefused, match="does not open with the 2025 result"):
        RV.check_rendered(story.replace(first, first[len(line):].lstrip()), lang, ctx, "story", line)


@pytest.mark.parametrize("lang", LANGS)
def test_an_unfinished_test_of_record_renders_no_2025_claim(lang):
    ctx = RP.state("unfinished")
    assert ctx["status"] == "unfinished" and ctx["recommendation"] is None
    text = RP.render(ctx, lang)
    assert brief(text, lang)[0].startswith(SCOPE_LINE[lang]["unfinished"])
    nine = section(text, 9)
    assert ("aucune affirmation sur 2025" if lang == "fr" else "no claim on 2025") in nine
    assert not any(w in nine for w in ("| Comparaison", "| Comparison"))
    s0 = W.start("b" * 32, W.utc(10), finished=False, gate_record=W.gate("clean"), stage="inputs_read")
    walk = ledger.of_record([RP.FIRST_2024], [s0])
    for bad in ({"verdict": {"shipped": "better", "family": "better"}}, {"results": {"contrasts": {}}}):
        shown = {"run_id": s0["run_id"], "started_utc": s0["started_utc"], "finished": False, "recorded": False,
                 "stage": "inputs_read", "error": "", "standing": "", "gate": s0["gate"], "verdict": None,
                 "results": None, "holds_results": False, "predecessors": [], **bad}
        with pytest.raises(ReportRefused, match="did not finish: no 2025 verdict or result"):
            RV.assemble_held(walk, shown, [], None)
    # another start's verdict in its place: a finished later start offered as the record
    later = RP.record("d" * 32, W.utc(11))
    with pytest.raises(ReportRefused, match="is not the one the walk"):
        RV.assemble_held(walk, later, [], None)
    # a stopped start, then a ruled start that has not finished: the second is the record, unfinished
    s1 = W.start("d" * 32, W.utc(11), finished=False,
                 gate_record=W.gate("clean", ["f"], W.ruling("clean", start=s0["started_utc"])), stage="replayed")
    s0b = {**s0, "stage": "replayed", "error": "MemoryError"}
    walk = ledger.of_record([RP.FIRST_2024], [s0b, s1])
    assert walk["record"]["run_id"] == s1["run_id"] and [s["run_id"] for s in walk["stopped"]] == [s0["run_id"]]


SCOPE_LINE = {"fr": {"unfinished": "**Statut : le test de référence de 2025 n'a pas fini"},
              "en": {"unfinished": "**Status: the 2025 test of record did not finish"}}


@pytest.mark.parametrize("lang", LANGS)
def test_a_stopped_start_followed_by_a_ruled_test_of_record_renders(lang):
    ctx = RP.state("stopped_ruled")
    held = ctx["held"]
    stopped, rec = held["stopped"][0], held["record"]
    text = RP.render(ctx, lang)
    nine = section(text, 9)
    assert nine.index(stopped["run_id"]) < nine.index(rec["run_id"])
    line = next(x for x in nine.splitlines() if stopped["run_id"] in x)
    assert ("« le rejeu »" if lang == "fr" else '"the replay"') in line and "`MemoryError`" in line
    assert stopped["ruling"]["head"] in line
    assert rec["ruling"]["start"] == stopped["started_utc"] and stopped["started_utc"] in nine
    assert ctx["status"] == "confirmed"
    env = environment(lang)
    status = RV.status_sentence(env, ctx, lang)
    cut = "\n".join(x for x in text.splitlines() if stopped["run_id"] not in x)
    with pytest.raises(ReportRefused, match="passed over are not all listed"):
        RV.check_rendered(cut, lang, ctx, "validation", status)
    moved = text.replace(line + "\n", "").replace("## 10.", line + "\n\n## 10.")
    with pytest.raises(ReportRefused, match="passed over are not all listed"):
        RV.check_rendered(moved, lang, ctx, "validation", status)


@pytest.mark.parametrize("lang", LANGS)
def test_a_2025_lead_of_75_hours_in_the_record_s_data_quality_run_must_be_stated(lang):
    held = RP.clean_record()
    diag = held["record"]["diag"]
    diag = {**diag, "reuse_sensitivity": {**diag["reuse_sensitivity"], "longest_lead_h": 76.0}}  # its dq run's
    held = {**held, "record": {**held["record"], "diag": diag}, "findings": RP.findings(at_or_above=(H_YEAR,))}
    ctx = RP.context(held_view=held, rec24=RP.rec24_view())
    assert not ctx["nbm"]["at_or_above"]  # the chain's through-2024 run lists none
    text = RP.render(ctx, lang)
    nine = section(text, 9)
    assert LEAD_FINDING[lang] in nine and ("section 18" in nine)
    assert LEAD_FINDING[lang] not in section(text, 2)
    with pytest.raises(ReportRefused, match="2025 lead at or above"):
        RV.check_rendered(text.replace(LEAD_FINDING[lang], "x"), lang, ctx, "validation",
                          RV.status_sentence(environment(lang), ctx, lang))
    results, rows = RP.dq_2025(at_or_above=(H_YEAR,))
    f = RV.held_out_findings(results, rows)
    assert f["at_or_above"] and f["longest"] == 76.0
    chain_dq = ctx["dq"]
    assert not RV.held_out_findings({**chain_dq, "revisions": {}}, None)["at_or_above"]
    # Both: a 2022 lead in the chain's run (section 2) and 2025's in the record's (section 9). Each is checked in its
    # own section: either statement dropped refuses, though the other still carries the same words.
    dq = RP.payloads()[1]
    dq["checks"] = {**dq["checks"], **RP.nbm_checks(RP.Distinct(), at_or_above=(2022,),
                                                     longest={str(y): 76.0 if y == 2022 else 71.0
                                                              for y in range(2019, 2025)})}
    both = RP.context(dq=dq, nbm=RV.nbm_findings(dq), held_view=held, rec24=RP.rec24_view())
    text = RP.render(both, lang)
    two, nine = section(text, 2), section(text, 9)
    assert LEAD_FINDING[lang] in two and LEAD_FINDING[lang] in nine
    line = RV.status_sentence(environment(lang), both, lang)
    with pytest.raises(ReportRefused, match="2025 lead at or above .* in section 9"):
        RV.check_rendered(text.replace(nine, nine.replace(LEAD_FINDING[lang], "x")), lang, both, "validation", line)
    with pytest.raises(ReportRefused, match="a lead at or above .* in section 2"):
        RV.check_rendered(text.replace(two, two.replace(LEAD_FINDING[lang], "x")), lang, both, "validation", line)


def test_2025_s_days_not_fed_are_2025_s_alone():
    """The days of 2025 no run fed, and 2025's share of days fed, are read from 2025's days alone: a day of 2024 the
    through-2025 run also lists is no day of 2025."""
    results, rows = RP.dq_2025(unfed=(("2024-12-30", "no_covering_run"), ("2025-02-03", "no_covering_run")))
    f = RV.held_out_findings(results, rows)
    assert [str(d["day"])[:10] for d in f["unfed"]] == ["2025-02-03"]
    assert (f["days"], f["share"]) == (365, 1 - 1 / 365)


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("standing", ["clean", "post_hoc"])
def test_the_revised_records_are_listed_whatever_the_standing(standing, lang):
    """Each revised record of 2019-2024 is listed in section 9, with its values: a label that held two records shows
    both, each formatted (never one number read from the joined text), a label without a record shows none. A page
    that drops a record, prints another value for it, or lists it outside section 9 is refused."""
    held = RP.stopped_then_record(outcome=standing)
    ctx = RP.context(held_view=held, rec24=RP.rec24_view())
    text = RP.render(ctx, lang)
    nine = section(text, 9)
    rows = held["findings"]["revised"]["rows"]
    assert rows and SAY[lang]["revised"] in nine
    utc = environment(lang).filters["utc"]
    for x in rows:
        assert f"| {utc(x['utc'])} |" in nine
    both = {"fr": "| 20\u202f000,0 ; 21\u202f000,0 | 20\u202f100,0 |", "en": "| 20,000.0; 21,000.0 | 20,100.0 |"}[lang]
    assert rows[0]["old_mw"] == "20000.0;21000.0" and both in nine  # two records, then one
    assert {"fr": "| ajouté | — | 21\u202f100,0 |", "en": "| added | — | 21,100.0 |"}[lang] in nine
    status = RV.status_sentence(environment(lang), ctx, lang)
    other = text.replace(both, both.replace("21", "22", 1))
    with pytest.raises(ReportRefused, match="each with its values"):  # the record listed, another value printed
        RV.check_rendered(other, lang, ctx, "validation", status)
    cut = "\n".join(line for line in text.splitlines() if utc(rows[0]["utc"]) not in line)
    with pytest.raises(ReportRefused, match="revised records"):
        RV.check_rendered(cut, lang, ctx, "validation", status)
    row = next(line for line in text.splitlines() if utc(rows[0]["utc"]) in line)
    moved = cut.replace("\n## 11.", "\n" + row + "\n\n## 11.")  # listed, but in section 10, not 2025's
    with pytest.raises(ReportRefused, match="revised records"):
        RV.check_rendered(moved, lang, ctx, "validation", status)
    with pytest.raises(ReportRefused, match="counts 3 revised records, its artifact lists 2"):
        r, frame = RP.dq_2025()
        RV.held_out_findings(r, frame.iloc[:2])


OUTCOMES_ROW = {"fr": "| SR 11-7, analyse des résultats", "en": "| SR 11-7, outcomes analysis"}


@pytest.mark.parametrize("lang", LANGS)
def test_the_standards_table_says_held_out_only_of_a_clean_finished_test_of_record(lang):
    """Section 12's back-testing row calls 2025 held-out data done only when its test of record finished clean,
    labelled held out (from its run or its ledger entry); a post-hoc test of record is said to be no back-test on
    held-out data, never "held-out data: done, standing post hoc"; before any start, pending; while the test of
    record has not finished, no claim."""
    words = {"fr": ("données réservées : fait", "ce n'est pas un contrôle a posteriori sur des données réservées",
                    "données réservées : en attente", "a commencé et n'a pas fini"),
             "en": ("held-out data: done", "it is not a back-test on held-out data", "held-out data: pending",
                    "started and did not finish")}[lang]
    for name, want in (("confirmed", 0), ("recorded", 0), ("post_hoc", 1), ("none", 2), ("unfinished", 3)):
        twelve = section(RP.render(RP.state(name), lang), 12)
        (row,) = [line for line in twelve.splitlines() if line.startswith(OUTCOMES_ROW[lang])]
        assert [i for i, w in enumerate(words) if w in row] == [want], name


@pytest.mark.parametrize("lang", LANGS)
def test_the_2025_coverage_is_given_with_its_interval_and_whether_it_holds_the_nominal(lang):
    """Plan section 8.3: beside the 2025 verdicts, the production model's coverage, its bootstrap interval and whether
    that interval holds the 90 % nominal, judged on the bounds as printed: an interval wholly below says it does not
    (break); one around the nominal says it does (control), and so does a lower bound above 90 % that prints as
    90.0 %."""
    pct = environment(lang).filters["pct"]
    n = pct(0.9, 0)
    said = {"fr": (f"qui contient le nominal de {n}", f"qui ne contient pas le nominal de {n}"),
            "en": (f"which holds the {n} nominal", f"which does not hold the {n} nominal")}[lang]
    for cov, want in (((0.884, 0.901, 0.917), 0), ((0.801, 0.84, 0.88), 1), ((0.90004, 0.92, 0.95), 0)):
        held = RP.clean_record(coverage=cov)
        nine = section(RP.render(RP.context(held_view=held, rec24=RP.rec24_view()), lang), 9)
        assert all(pct(x, 1) in nine for x in cov), cov
        assert [i for i, w in enumerate(said) if w in nine] == [want], cov


# --- the leads, the diagnostics, the peak hours, the figure ----------------------------------------------------------

@pytest.mark.parametrize("lang", LANGS)
def test_a_lead_of_75_hours_is_stated_against_the_two_day_argument(lang):
    v, dq, p, r, b, frozen, oos, diag = payloads()
    dq["checks"] = {**dq["checks"], **RP.nbm_checks(RP.Distinct(), at_or_above=(2022,),
                                                     longest={str(y): 76.0 if y == 2022 else 71.0
                                                              for y in range(2019, 2025)})}
    ctx = RP.context(dq=dq, nbm=RV.nbm_findings(dq))
    text = RP.render(ctx, lang)
    two = section(text, 2)
    assert LEAD_FINDING[lang] in two and "section 18" in two and "2022" in two
    with pytest.raises(ReportRefused, match="lead at or above"):
        RV.check_rendered(text.replace(LEAD_FINDING[lang], "x"), lang, ctx, "validation",
                          RV.status_sentence(environment(lang), ctx, lang))
    quiet = section(RP.render(RP.context(), lang), 2)
    assert LEAD_FINDING[lang] not in quiet and ("Aucune année ne l'atteint." if lang == "fr" else
                                                "No year reaches it.") in quiet


@pytest.mark.parametrize("lang", LANGS)
def test_section_2_s_leads_stop_before_the_held_out_year(lang):
    """A through-2024 data-quality run lists a lead for 2025 (the 2024 request ends one hour into 2025, so two runs of
    2025-01-01 come with it; the real run's leads: 2019 to 2025). Section 2 states 2019 to 2024 only: a 2025 lead,
    even one at the bound, is neither listed there nor stated as the finding; 2025's findings are the test of
    record's."""
    v, dq, p, r, b, frozen, oos, diag = payloads()
    longest = {"2019": 66.0, "2020": 68.0, "2021": 70.0, "2022": 71.0, "2023": 71.0, "2024": 71.0, "2025": 76.0}
    dq["checks"] = {**dq["checks"], **RP.nbm_checks(RP.Distinct(), at_or_above=(2025,), longest=longest)}
    derived = RV.derive(v, dq, p, r, b, frozen, oos, diag)
    ctx = RP.context(dq=dq, nbm=derived["nbm"])
    text = RP.render(ctx, lang)  # check_rendered passes
    two = section(text, 2)
    assert "2024, 71 h" in two and "2019, 66 h" in two
    assert "2025, 7" not in two and LEAD_FINDING[lang] not in two
    assert ("Aucune année ne l'atteint." if lang == "fr" else "No year reaches it.") in two


@pytest.mark.parametrize("lang", LANGS)
def test_diagnostics_that_did_not_finish_are_said_so(lang):
    d = payloads()[7]
    ctx = RP.context(diag={"pseudo": d["pseudo"], "replay": None})
    text = RP.render(ctx, lang)
    eight = section(text, 8)
    assert SAY[lang]["diag_unfinished"] in eight and ("| Durée" if lang == "fr" else "| Length") not in eight
    assert ("| Durée" if lang == "fr" else "| Length") in section(text, 7)
    held = RP.clean_record(diag_finished=False)
    text = RP.render(RP.context(held_view=held, rec24=RP.rec24_view()), lang)
    assert SAY[lang]["diag_unfinished"] in section(text, 9)


@pytest.mark.parametrize("lang", LANGS)
def test_a_peak_hour_without_a_forecast_shows_its_reason(lang):
    text = RP.render(RP.context(), lang)
    reason = ("pas de prévision : aucune des 12 prévisions disponibles les plus fraîches ne couvre le jour"
              if lang == "fr" else "no forecast: none of the 12 freshest available forecasts covers the day")
    three = section(text, 3)
    row = next(x for x in three.splitlines() if reason in x)
    assert row.count(reason) == 2  # v2 and v1's form on the forecast; v1 as shipped has its own
    assert "—" in row.split(" | ")[2]  # its temperature forecast is missing too
    four = section(text, 4)
    assert ("— : pas de prévision pour cette heure." if lang == "fr" else "—: no forecast for that hour.") in four


def test_the_partial_effects_figure_marks_the_extrapolated_range(monkeypatch, tmp_path):
    figs = []
    monkeypatch.setattr(F, "_save", lambda fig, path: figs.append(fig))
    rows = RP.curves()
    F.partial_effects(rows, "fr", tmp_path / "x.png", RP.payloads()[0]["config"]["hour_blocks"])
    (fig,) = figs
    axes = fig.axes
    assert len(axes) == 2
    d = pd.DataFrame(rows)
    for ax in axes:
        solid = [ln for ln in ax.get_lines() if ln.get_linestyle() == "-"]
        dashed = [ln for ln in ax.get_lines() if ln.get_linestyle() == "--"]
        assert solid and dashed
        inside = d[d["in_training_range"]]["temp_c"]
        for ln in solid:
            assert inside.min() <= min(ln.get_xdata()) and max(ln.get_xdata()) <= inside.max()
        for ln in dashed:  # each extrapolated run reaches out of the range and joins the curve at its edge
            x = np.asarray(ln.get_xdata())
            assert (x < inside.min()).any() or (x > inside.max()).any()
    labels = [t.get_text() for t in fig.legends[0].get_texts()]
    assert F.LABELS["fr"]["extrapolated"] in labels
    seg = F._segments(np.array([1.0, 2, 3, 4, 5]), np.array([1.0, 2, 3, 4, 5]), np.array([False, True, True, False,
                                                                                        False]))
    assert [(list(x), s) for x, _, s in seg] == [([1.0, 2.0], False), ([2.0, 3.0], True), ([3.0, 4.0, 5.0], False)]


# --- attribution --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("lang", LANGS)
def test_the_attribution_block_carries_noaa_and_iem(lang):
    """Each report refuses a page without the NOAA and IEM line, or without the widened no-endorsement line."""
    from valiwatt.report import render as rr

    ctx = RP.context()
    text = RP.render(ctx, lang)
    assert attribution.NBM[lang] in text and attribution.NO_ENDORSEMENT[lang] in text
    line = RV.status_sentence(environment(lang), ctx, lang)
    for name in ("NBM", "NO_ENDORSEMENT"):
        words = getattr(attribution, name)[lang]
        with pytest.raises(ReportRefused, match=rf"attribution lines \['{name}'\]"):
            RV.check_rendered(text.replace(words, ""), lang, ctx, "validation", line)
        kept = [x for x in attribution.lines(lang, retrieved="2026-10-13") if x != words]
        with pytest.raises(ReportRefused, match=rf"attribution lines \['{name}'\]"):
            rr.check_dq("\n".join(kept), lang, {"shown_at_or_above": []})
    rr.check_dq("\n".join(attribution.lines(lang, retrieved="2026-10-13")), lang, {"shown_at_or_above": []})  # control
    lines = attribution.lines(lang, retrieved="2026-10-13")
    assert lines.index(attribution.NBM[lang]) == lines.index(attribution.ECCC[lang]) + 2  # after ECCC and its reference
    assert lines.index(attribution.NBM[lang]) + 1 == lines.index(attribution.NO_ENDORSEMENT[lang])


def ruled_wording() -> dict:
    """The four strings of AGENTS.md's ruling bullet ("The NOAA and IEM attribution"), its home: the French between
    « », the English between straight quotes, in the order NBM fr, NBM en, no-endorsement fr, no-endorsement en."""
    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    start = agents.index("- **The NOAA and IEM attribution**")
    bullet = agents[start:agents.index("\n- **", start + 1)]
    flat = " ".join(bullet.split())
    fr = re.findall(r"« (.+?) »", flat)
    en = re.findall(r'"(.+?)"', flat)
    assert len(fr) == 2 and len(en) == 2, (fr, en)
    return {"NBM": {"fr": fr[0], "en": en[0]}, "NO_ENDORSEMENT": {"fr": fr[1], "en": en[1]}}


def test_the_attribution_lines_are_the_owner_s_ruled_wording():
    """Two copies with an alarm: AGENTS.md holds the owner's approved wording; attribution.py must say it word for
    word (whitespace aside, since AGENTS.md wraps its lines)."""
    ruled = ruled_wording()
    for name in ("NBM", "NO_ENDORSEMENT"):
        for lang in LANGS:
            code = " ".join(getattr(attribution, name)[lang].split())
            assert code == ruled[name][lang], (name, lang)
    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "src/valiwatt/attribution.py" in agents[agents.index("- **The NOAA and IEM attribution**"):][:2500]


def test_the_ruled_wording_test_reads_agents_md_not_the_code(monkeypatch):
    """The check compares with AGENTS.md: one word changed in attribution.py fails it."""
    monkeypatch.setitem(attribution.NBM, "en", attribution.NBM["en"].replace("modified", "adjusted"))
    with pytest.raises(AssertionError):
        test_the_attribution_lines_are_the_owner_s_ruled_wording()


# --- the measures: refused when absent or made differently ---------------------------------------------------------

def test_a_2024_replay_without_its_sensitivity_measures_is_refused():
    """The 2024 replay's diagnostic run finished, complete, but holds no measures (or lacks a part): refused, so the
    report never prints one year's measures where it says two. A diagnostic run that did not finish is said so."""
    v, dq, p, r, b, frozen, oos, diag = payloads()
    with pytest.raises(ReportRefused, match="no false-alarm and blind-spot measures"):
        RV.derive(v, dq, p, r, b, frozen, oos, {**diag, "replay": {"run_id": "x"}})
    broken = {**diag["replay"], "sensitivity": {k: x for k, x in diag["replay"]["sensitivity"].items()
                                                 if k != "echo_exclusion_cost"}}
    with pytest.raises(ReportRefused, match="no false-alarm and blind-spot measures"):
        RV.derive(v, dq, p, r, b, frozen, oos, {**diag, "replay": broken})
    assert RV.derive(v, dq, p, r, b, frozen, oos, {**diag, "replay": None})["sens"]["replay"] is None


def test_2024_measures_made_with_another_design_are_refused():
    import copy

    for change in ("episodes", "shifts", "sds", "horizon"):
        v, dq, p, r, b, frozen, oos, diag = payloads()
        diag = copy.deepcopy(diag)
        S = diag["replay"]["sensitivity"]
        if change == "episodes":
            S["episodes"]["rows"][-1]["days"] = 9
        elif change == "shifts":
            S["level_shifts"][0]["shift"] = -0.2
        elif change == "sds":
            S["echo_exclusion_cost"]["rows"][0]["sd"] = 0.2
        else:
            S["echo_exclusion_cost"]["horizon_weeks"] = 6
        with pytest.raises(ReportRefused, match=f"different designs \\({change}\\)"):
            RV.derive(v, dq, p, r, b, frozen, oos, diag)


def test_2025_measures_made_with_another_design_are_refused():
    import copy

    ctx = RP.state("confirmed")
    held = copy.deepcopy(ctx["held"])
    held["record"]["diag"]["sensitivity"]["echo_exclusion_cost"]["horizon_weeks"] = 6
    with pytest.raises(ReportRefused, match="different designs \\(horizon\\)"):
        RV.check_held_diagnostics(held, ctx["sens"])
    with pytest.raises(ReportRefused, match="neither the 2023 rehearsal's nor the 2024 replay's"):
        RV.check_held_diagnostics(ctx["held"], {"pseudo": None, "replay": None})


def test_a_replay_departing_from_replays_under_two_freeze_commits_is_refused():
    v1 = {**EARLIER, "freeze_commit": "e" * 40}
    assert RV.departure_record([]) is None
    one = RV.departure_record([v1, {**v1, "id": "2" * 32}])
    assert one == {"date": "2026-10-14", "changes": list(RV.DEPARTURE["changes"]), "from_commit": "e" * 40}
    with pytest.raises(ReportRefused, match="describes one departure"):
        RV.departure_record([v1, {**v1, "id": "2" * 32, "freeze_commit": "9" * 40}])
    with pytest.raises(ReportRefused, match="describes one departure"):
        RV.departure_record([{**v1, "freeze_commit": ""}])


# --- what derive reads ----------------------------------------------------------------------------------------------

def test_derive_reads_each_forecast_from_its_own_column():
    """The spring differences and the monthly errors come from each model's own column."""
    oos = oos_frame()
    d = RP.context()
    for key, col in (("production", "pred_gam_forecast"), ("persistence", "pred_persistence"),
                     ("observed", "pred_observed")):
        e = (oos["target"] - oos[col]) / oos["target"]
        spring = pd.to_datetime(oos["day"]).dt.month.isin([3, 4, 5, 6])
        year = pd.to_datetime(oos["day"]).dt.year
        want = e[spring & (year == 2020)].mean() - e[spring & (year != 2020)].mean()
        assert d["shift_diff"][key]["difference"] == pytest.approx(want), key
    for key, col in (("gam_forecast", "pred_gam_forecast"), ("forecast", "pred_forecast"),
                     ("persistence", "pred_persistence"), ("baseline", "baseline")):
        mape = ((oos["target"] - oos[col]).abs() / oos["target"]).groupby(oos["fold"]).mean()
        assert d["monthly"][key].to_dict() == pytest.approx(mape.to_dict())


def test_the_selection_s_values_are_checked_against_the_validation_run():
    v, dq, p, r, b, frozen, oos, diag = payloads()
    sel = RV.selection(v, oos)
    assert sel["hours"] == v["contrasts"]["hours"]
    assert sel["peak"][0]["signed"]["gam_forecast"] is None and sel["peak"][0]["why"]["gam_forecast"] == \
        "no_covering_run"
    with pytest.raises(ReportRefused, match="common hours"):
        RV.selection({**v, "contrasts": {**v["contrasts"], "hours": v["contrasts"]["hours"] + 1}}, oos)
    bad = {**v, "contrasts": {**v["contrasts"], "shipped": {**v["contrasts"]["shipped"], "difference": 0.5}}}
    with pytest.raises(ReportRefused, match="shipped contrast"):
        RV.selection(bad, oos)


@pytest.mark.parametrize("lang", LANGS)
def test_each_spring_verdict_is_read_from_its_own_forecast(lang):
    ctx = RP.context()
    text = RP.render(ctx, lang)
    spring = next(p for p in section(text, 4).split("\n\n") if p.startswith("**Printemps" if lang == "fr" else
                                                                             "**Spring"))
    m = environment(lang).get_template(f"sentences.{lang}.md.j2").module
    for key in ("production", "persistence", "observed"):
        assert str(m.diff_verdict(ctx["shift_diff"][key])) in spring
    gam, rest = spring.split("Pour v1 tel que livré" if lang == "fr" else "For v1 as shipped")
    shipped, observed = rest.split("Avec la météo observée" if lang == "fr" else "With the observed weather")
    for key, part in (("production", gam), ("persistence", shipped), ("observed", observed)):
        assert str(m.diff_verdict(ctx["shift_diff"][key])) in part, key


# --- the limits and the sentences of several years ------------------------------------------------------------------

@pytest.mark.parametrize("lang", LANGS)
def test_the_in_brief_misses_put_each_year_in_its_place(lang):
    ctx = RP.context()
    text = RP.render(ctx, lang)
    line = brief(text, lang)[-1]
    lv, lv24 = ctx["level"], ctx["level24"]
    assert (f"sur {P_YEAR}, une hausse soutenue" if lang == "fr" else f"on {P_YEAR}, a sustained") in line
    assert (f"à {lv['up_n'] - lv['up_missed']} des {lv['up_n']} départs, et sur {R_YEAR} (vu par les développeurs), à "
            f"{lv24['up_n'] - lv24['up_missed']} des {lv24['up_n']}" if lang == "fr" else
            f"at {lv['up_n'] - lv['up_missed']} of {lv['up_n']} starts, and on {R_YEAR} (seen by the developers), at "
            f"{lv24['up_n'] - lv24['up_missed']} of {lv24['up_n']}") in line
    unfinished = RP.render(RP.context(diag={"pseudo": None, "replay": payloads()[7]["replay"]}), lang)
    assert (f"Les mesures de fausses alertes et d'angles morts de {P_YEAR} n'ont pas fini" if lang == "fr" else
            f"The {P_YEAR} false-alarm and blind-spot measures did not finish") in brief(unfinished, lang)[-1]


@pytest.mark.parametrize("lang", LANGS)
def test_the_limits_give_each_years_measures(lang):
    ctx = RP.context()
    ten = section(RP.render(ctx, lang), 10)
    E23, E24 = ctx["episode_limits"]["pseudo"], ctx["episode_limits"]["replay"]
    episodes = next(x for x in ten.splitlines() if x.startswith("- **" + ("Épisodes" if lang == "fr" else "Episodes")))
    one, two = episodes.split((" ; sur " if lang == "fr" else "; on ") + str(R_YEAR))
    assert pct(E23["share"], 1, lang) in one and pct(E24["share"], 1, lang) in two
    echo = next(x for x in ten.splitlines() if x.startswith("- **" + ("Exclusion" if lang == "fr" else "The echo")))
    for E, year in ((E23, P_YEAR), (E24, R_YEAR)):
        e = {k: fmt_num(x, 0, lang) for k, x in E["echo"].items()}
        assert (f"sur {year} dans {e['suppressed']} essais sur {e['runs']} et le retarde dans {e['delayed']}"
                if lang == "fr" else
                f"on {year} in {e['suppressed']} trials out of {e['runs']} and delays it in {e['delayed']}") in echo
    label = ("(mesuré sur 2023, l'année de répétition, et sur 2024, vue par les développeurs)" if lang == "fr" else
             "(measured on 2023, the rehearsal year, and on 2024, seen by the developers)")
    assert ten.count(label) == 3
    gone = section(RP.render(RP.context(diag={"pseudo": None, "replay": None}), lang), 10)
    assert ("Les diagnostics de 2023 et de 2024 n'ont pas fini" if lang == "fr" else
            "The diagnostics of 2023 and of 2024 did not finish") in gone and label not in gone


@pytest.mark.parametrize("lang", LANGS)
def test_the_synthetic_level_shift_is_set_against_both_years(lang):
    ctx = RP.context()
    s11 = section(RP.render(ctx, lang), 11)
    lv24 = ctx["level24"]
    assert (f"Sur les vraies heures de {P_YEAR}, une hausse" if lang == "fr" else
            f"On the real {P_YEAR} hours, a 10% rise") in s11
    assert (f"Sur les vraies heures de {R_YEAR} (vu par les développeurs), la même hausse n'a pas donné « réentraîner » "
            f"en 10 semaines à {lv24['up_missed']} des {lv24['up_n']} départs" if lang == "fr" else
            f"On the real {R_YEAR} hours (seen by the developers), the same rise did not yield \"retrain\" within 10 "
            f"weeks at {lv24['up_missed']} of {lv24['up_n']} starts") in s11


def test_episode_limits_read_each_years_rows():
    rows = [{"days": 1, "factor": 0.85, "starts": 100, "retrain": 2, "retrain_clean": 1, "recalibrate": 1},
            {"days": 3, "factor": 1.3, "starts": 100, "retrain": 30, "retrain_clean": 4, "recalibrate": 0},
            {"days": 5, "factor": 0.85, "starts": 50, "retrain": 10, "retrain_clean": 0, "recalibrate": 0}]
    S = {"episodes": {"rows": rows}, "echo_exclusion_cost": {"rows": [
        {"suppressed": 2, "runs": 60, "delayed": 1}, {"suppressed": 3, "runs": 60, "delayed": 4}]}}
    E = RV.episode_limits(S, [0.85, 1.15])
    assert E["worst"] is rows[1] and E["share"] == 0.3 and E["mild"] == 0.2 and E["one_day_alarms"] == 3
    assert E["one_day"] == {"retrain": 2, "retrain_clean": 1, "recalibrate": 1, "cases": 100}
    assert E["recal_worst"] is None and E["recal_share"] == 0.0
    assert E["echo"] == {"suppressed": 5, "runs": 120, "delayed": 5}


@pytest.mark.parametrize("lang", LANGS)
def test_every_limit_branch_renders_a_true_clause(lang):
    m = environment(lang).get_template(f"sentences.{lang}.md.j2").module
    quiet = {"recal_worst": None, "recal_share": 0.0}
    loud = {"recal_worst": {"days": 7, "factor": 1.15}, "recal_share": 0.0174}
    worst = {"worst": {"days": 5, "factor": 1.3}, "share": 0.123, "mild": 0.021}
    retrain = ("sur 2024, « réentraîner » jusqu'à 12,3 % des départs (5 jours à × 1,30) et au plus 2,1 % pour "
               "un épisode de ±15 %, " if lang == "fr" else
               "on 2024, \"retrain\" at up to 12.3% of starts (5 days at × 1.30) and at most 2.1% for an episode of "
               "±15%, ")
    recal = ("« recalibrer » jusqu'à 1,7 % des départs (7 jours à × 1,15)" if lang == "fr" else
             "\"recalibrate\" at up to 1.7% of starts (7 days at × 1.15)")
    no_recal = "aucun « recalibrer »" if lang == "fr" else "no \"recalibrate\""
    assert str(m.episode_limit({**worst, **quiet}, 2024, 0.15)) == retrain + no_recal
    assert str(m.episode_limit({**worst, **loud}, 2024, 0.15)) == retrain + recal
    absorbs = "Les retards de la demande absorbent" if lang == "fr" else "The demand lags absorb"
    for missed, caught in ((0, 4), (1, 1), (2, 1), (2, 3)):
        level = {"size": 0.1, "up_n": 4, "up_missed": missed, "down_n": 4, "down_caught": caught,
                 "months": [1, 3, 6, 9], "missed_months": [1, 3, 6][:missed]}
        text = str(m.level_limit(level, 2024, 10))
        assert text.startswith("Sur 2024, " if lang == "fr" else "On 2024, ") and text.endswith(".")
        qlevel = {"size": 0.1, "up_n": 4, "up_missed": 0, "down_n": 4, "down_caught": 4, "months": [1, 3],
                  "missed_months": []}
        for pair in (((2023, level), (2024, qlevel)), ((2023, qlevel), (2024, level))):
            why = str(m.level_why(list(pair)))
            assert why.startswith(absorbs) == (missed > 0)
            assert why == "" or why.endswith(".")
        note = str(m.level_shift_note_replay(level, 2024, 10))
        assert note.endswith("(section 10).")


@pytest.mark.parametrize("lang", LANGS)
def test_a_one_day_alarm_names_the_year_it_came_from(lang):
    ctx = RP.context()
    one = {"retrain": 3, "retrain_clean": 0, "recalibrate": 14, "cases": 1400}
    limits = {**ctx["episode_limits"], "replay": {**ctx["episode_limits"]["replay"], "one_day_alarms": 17,
                                                  "one_day": one}}
    quiet = RP.render(ctx, lang)
    assert (f"Aucun choc d'un seul jour n'a donné d'alerte sur {R_YEAR}" if lang == "fr" else
            f"No one-day shock gave an alarm on {R_YEAR}") in section(quiet, 8)
    loud = section(RP.render({**ctx, "episode_limits": limits}, lang), 8)
    assert (f"**Alertes des chocs d'un seul jour** : sur {R_YEAR}, 3 « réentraîner » (dont 0 sans aide) et 14 "
            "« recalibrer », sur 1 400 cas (départs × facteurs)." if lang == "fr" else
            f"**Alarms from one-day shocks**: on {R_YEAR}, 3 \"retrain\" (0 of them clean) and 14 \"recalibrate\", out "
            "of 1,400 cases (starts × factors).") in loud


SET_ASIDE = {
    "fr": {(0, 0): "Pour ces raisons, 0 semaine de 2021–2023 a été écartée (0 de 2021–2022 pour le rejeu de 2023).",
           (1, 0): "Pour ces raisons, 1 semaine de 2021–2023 a été écartée (0 de 2021–2022 pour le rejeu de 2023) ; "
                   "l'exécution de validation consigne chacune avec sa raison.",
           (0, 2): "Pour ces raisons, 0 semaine de 2021–2023 a été écartée (2 de 2021–2022 pour le rejeu de 2023) ; "
                   "l'exécution de validation consigne chacune avec sa raison.",
           (1, 2): "Pour ces raisons, 1 semaine de 2021–2023 a été écartée (2 de 2021–2022 pour le rejeu de 2023) ; "
                   "l'exécution de validation consigne chacune avec sa raison.",
           (4, 4): "Pour ces raisons, 4 semaines de 2021–2023 ont été écartées (4 de 2021–2022 pour le rejeu de 2023) ; "
                   "l'exécution de validation consigne chacune avec sa raison."},
    "en": {(0, 0): "On these grounds 0 weeks of 2021–2023 were set aside (0 of 2021–2022 for the 2023 replay).",
           (1, 0): "On these grounds 1 week of 2021–2023 was set aside (0 of 2021–2022 for the 2023 replay); the "
                   "validation run lists each with its reason.",
           (0, 2): "On these grounds 0 weeks of 2021–2023 were set aside (2 of 2021–2022 for the 2023 replay); the "
                   "validation run lists each with its reason.",
           (1, 2): "On these grounds 1 week of 2021–2023 was set aside (2 of 2021–2022 for the 2023 replay); the "
                   "validation run lists each with its reason.",
           (4, 4): "On these grounds 4 weeks of 2021–2023 were set aside (4 of 2021–2022 for the 2023 replay); the "
                   "validation run lists each with its reason."},
}


@pytest.mark.parametrize("lang", LANGS)
def test_the_set_aside_sentence_has_a_true_branch_for_every_count(lang):
    m = environment(lang).get_template(f"sentences.{lang}.md.j2").module
    for (n24, n23), want in SET_ASIDE[lang].items():
        assert str(m.set_aside(n24, n23, (2021, 2023), (2021, 2022), 2023)) == want


@pytest.mark.parametrize("lang", LANGS)
def test_section_6_reads_the_weeks_set_aside_from_each_threshold_set(lang):
    m = environment(lang).get_template(f"sentences.{lang}.md.j2").module
    for (n24, n23), want in SET_ASIDE[lang].items():
        assert str(m.set_aside(n24, n23, (2021, 2023), (2021, 2022), 2023)) == want
    s6 = section(RP.render(RP.context(), lang), 6)
    assert SET_ASIDE[lang][(4, 4)].replace("(4 de", "(1 de").replace("(4 of", "(1 of") in s6


# --- the interval's calibration as the code makes it -----------------------------------------------------------------

INTERVAL = {"fr": ("des n erreurs hors échantillon des 12 mois précédents déjà notées quand le modèle du mois est ajusté",
                   "sur les erreurs des 12 mois qui précèdent l'année rejouée, déjà notées quand il est ajusté",
                   "déjà notées quand le modèle figé est ajusté"),
            "en": ("of the n out-of-sample errors of the 12 months before, already scored when that month's model is "
                   "fitted", "over the errors of the 12 months before the replayed year, already scored when it is "
                   "fitted", "already scored when the frozen model is fitted")}


@pytest.mark.parametrize("lang", LANGS)
def test_the_interval_sentences_say_only_scored_errors_calibrate(lang):
    text = RP.render(RP.context(), lang)
    method, frozen, card = INTERVAL[lang]
    s1 = section(text, 1)
    assert method in s1 and frozen in s1
    assert card in card_row(text, "Intervalle" if lang == "fr" else "Interval")


# --- the data-quality report's repair paragraph and section 3.2 -----------------------------------------------------

MISPLACED = {"duplicated_utc": "2023-01-01T05:00:00+00:00", "moved_to_utc": "2022-01-01T05:00:00+00:00",
             "moved_mw": 18000.5, "kept_mw": 21000.25, "cost_mw": 300.0, "swapped_cost_mw": 9000.0, "cost_ratio": 30.0,
             "runner_up": None, "destinations": 1, "destination_ratio": None}


@pytest.mark.parametrize("lang", LANGS)
def test_the_repair_paragraph_says_no_usable_hour_is_out_of_bounds(lang):
    text = str(environment(lang).get_template(f"repair.{lang}.md.j2").module.misplaced(MISPLACED, 2.0, 4321.0,
                                                                                         65432.0))
    assert ("dans les bornes physiques (4\u202f321 à 65\u202f432 MW)" if lang == "fr" else
            "inside the physical bounds (4,321 to 65,432 MW)") in text
    assert text.rstrip().endswith(
        "Enfin, aucune heure dont la valeur sort de ces bornes n'est rendue utilisable, quelle que soit la voie (le "
        "contrôle sur toute la fenêtre ou la série que le rejeu reconstruit à son horloge) : une dernière passe la "
        "marque hors bornes." if lang == "fr" else
        "Finally, no hour whose value lies outside those bounds is returned as usable, whatever the path (the check "
        "over the whole window or the series the replay rebuilds at its clock): a last pass marks it out of bounds.")


@pytest.mark.parametrize("lang", LANGS)
def test_section_3_2_states_the_move_rule_and_the_bounds_pass_when_nothing_was_misplaced(lang):
    """Section 3.2 of the data-quality template, rendered as written with no misplaced record: the rule and the bounds
    pass are still stated, with the run's numbers; with one record, the event paragraph carries them once."""
    env = environment(lang)
    src = env.loader.get_source(env, f"dq.{lang}.md.j2")[0]
    head = src.splitlines()[0]
    assert head.startswith("{% from 'repair.") and "move_rule" in head and "bounds_pass" in head
    block = head + "\n" + src[src.index("### 3.2"):src.index("### 3.3")]
    c = {"demand_misplaced": {"evidence": [], "metrics": {"min_ratio": 2.5}},
         "demand_bounds": {"metrics": {"low_mw": 4321.0, "high_mw": 65432.0}}}
    text = env.from_string(block).render(c=c)
    m = env.get_template(f"repair.{lang}.md.j2").module
    rule, bounds = str(m.move_rule(2.5, 4321.0, 65432.0)), str(m.bounds_pass())
    assert rule in text and bounds in text and text.index(rule) < text.index(bounds)
    assert ("Aucun enregistrement déplacé dans la fenêtre. Une heure qui porte deux valeurs" if lang == "fr" else
            "No misplaced record in the window. An hour that carries two values") in text
    assert fmt_num(2.5, 1, lang) in rule and fmt_num(4321.0, 0, lang) in rule and fmt_num(65432.0, 0, lang) in rule
    one = env.from_string(block).render(c={**c, "demand_misplaced": {**c["demand_misplaced"], "evidence": [MISPLACED]}})
    assert one.count(rule) == 1 and one.count(bounds) == 1 and "Aucun enregistrement" not in one \
        and "No misplaced" not in one


def test_agents_md_records_the_departure_the_report_describes():
    """One list, two homes: v1's departure items in DEPARTURE, recorded in AGENTS.md's v1 section. Once that section
    names a commit, its entry carries DEPARTURE's date and exactly its items, in order; before that, it lists none."""
    from test_report_lineage import v1_departures

    from valiwatt.report.lineage import COMMIT

    found = v1_departures()
    items = re.findall(r"^\s*- ([A-Z][0-9]+):", found, re.M)
    if COMMIT.findall(found):
        assert RV.DEPARTURE["date"] in found
        assert tuple(items) == RV.DEPARTURE["changes"]
    else:
        assert items == []


# --- departures under v2 ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("lang", LANGS)
def test_a_v2_departure_is_labelled_post_hoc_with_its_predecessors(lang):
    unfinished = {"id": "3" * 32, "freeze_commit": W.F1, "recorded": False, "unfinished": True, "absent": False,
                  "stage": "replayed", "error": "MemoryError"}
    entry = {"date": "2026-11-20", "head": "2026-11-20, the owner's ruling; a departure", "named": [W.F0[:12],
                                                                                              W.F1[:12]],
             "to": W.F1[:12]}
    ctx = RP.context(departures=[EARLIER, unfinished], dep_entries=[entry])
    text = RP.render(ctx, lang)
    eight = section(text, 8)
    assert ("**A posteriori.**" if lang == "fr" else "**Post hoc.**") in eight
    assert entry["head"] in eight and f"`{W.F1[:12]}`" in eight
    assert EARLIER["id"] in eight and unfinished["id"] in eight and "`MemoryError`" in eight
    assert SAY[lang]["unfinished"] in eight
    assert text.count("**" + SAY[lang]["ledger_label"] + "**") == 1  # the recorded predecessor's block
    assert ("après une dérogation aux règles gelées de v2" if lang == "fr" else
            "after a departure from v2's frozen rules") in text.split("\n", 3)[2]
    plain = RP.render(RP.context(), lang)
    assert ("**A posteriori.**" if lang == "fr" else "**Post hoc.**") not in plain


def test_a_departed_replay_from_the_store_brings_its_recommendations(monkeypatch):
    class Run:
        class info:
            run_id = "3" * 32

        class data:
            tags = {"freeze_commit": "e" * 40}

    flags = {"mape_high": 5, "bias_high": 1, "bias_low": 2, "coverage_low": 6}
    results = {"summary": {"mape": {"mean": 0.04}, "flag_weeks": flags}, "model": {"sha256": "5" * 64},
               "decisions": [{"week_start": "2024-03-04T00:00:00", "recommendation": "retrain"},
                             {"week_start": "2024-03-11T00:00:00", "recommendation": None}]}
    monkeypatch.setattr(RV, "_json", lambda c, run, path: results)
    d = RV.departure(None, Run)
    assert d["recommendations"] == [{"week_start": "2024-03-04", "recommendation": "retrain"}]
    assert d["recorded"] is False and d["freeze_commit"] == "e" * 40 and d["unfinished"] is False
    assert d["model_sha256"] == "5" * 64 and d["flag_weeks"] == flags


@pytest.mark.parametrize("lang", LANGS)
def test_every_departure_item_has_its_sentence_and_an_unknown_one_fails_the_render(lang):
    import jinja2

    m = environment(lang).get_template(f"sentences.{lang}.md.j2").module
    said = [str(m.departure_item(k, 2024, [24, 168])) for k in RV.DEPARTURE["changes"]]
    assert len(set(said)) == len(said) and all(s.endswith(".") and s[0].isupper() for s in said)
    with pytest.raises(jinja2.UndefinedError):
        str(m.departure_item("C9", 2024, [24, 168]))


# --- the English is derived from the French -----------------------------------------------------------------------

def numbers(text: str, lang: str) -> list[str]:
    text = text.replace(" ", "").replace(" ", "")
    text = re.sub(r"`[^`]*`|\]\([^)]*\)", "", text)
    text = text.replace("1ᵉʳ janvier 2024", "2024-01-01")
    text = re.sub(r"\(\d+ \d{3} tirages\)", "", text) if lang == "fr" else re.sub(r"\(\d+,\d{3} draws\)", "", text)
    if lang == "en":
        text = re.sub(r"(?<=\d),(?=\d{3})", "", text)
    found = re.findall(r"\d+(?:[,.]\d+)*" if lang == "fr" else r"\d+(?:\.\d+)*", text)
    return sorted(x.replace(",", ".") for x in found)


@pytest.mark.parametrize("state", list(RP.STATES))
def test_the_english_says_the_same_numbers_as_the_french(state):
    ctx = RP.state(state)
    fr, en = (numbers(RP.render(ctx, lang), lang) for lang in LANGS)
    assert fr == en


# --- from a store: the chain, the records, a fresh clone ---------------------------------------------------------------

def clone(w, drop) -> None:
    """This copy's store lacks the runs `drop` names, with their diagnostics; its 2025 starts are the store's and,
    for each the store lacks, the ledger's entry (as `replay_starts` merges them)."""
    for run_id in drop:
        w.runs.pop(run_id, None)
        w.runs.pop(f"diag-{run_id}", None)
    entries = {e["run_id"]: e for e in w.entries}
    w.starts[:] = [s if s["run_id"] in w.runs else W.from_entry(entries[s["run_id"]]) for s in w.starts]


def rerun_2024(w, run_id="e" * 32) -> str:
    """This copy's own 2024 replay, on the render's code, after the first: a rerun of the 2024 replay of record."""
    w.add_replay(run_id, w.payloads["r"])
    w.add_diagnostics(run_id, w.payloads["diag"]["replay"])
    return run_id


@pytest.mark.parametrize("lang", LANGS)
def test_a_fresh_clone_after_a_2025_entry_renders_from_the_ledger(monkeypatch, lang):
    """A committed ledger holds v2's first 2024 entry and a finished 2025 test of record; the clone's store holds
    neither run, only its own 2024 chain. Each record is shown from its entry, labelled, the status from the record's
    entry, the clone's 2024 replay beside the 2024 record as a rerun, and what only the store holds replaced by the
    sentence that says where it is."""
    w = S.World().install(monkeypatch)
    w.add_2025()
    rerun = rerun_2024(w)
    clone(w, (S.REPLAY, "b" * 32, "dq25"))
    ctx = w.context()
    assert ctx["rec24"]["recorded"] and ctx["held"]["record"]["recorded"] and ctx["store_only"]
    assert ctx["rerun24"]["run_id"] == rerun and ctx["r_source"] == "rerun" and ctx["status"] == "confirmed"
    text = RP.render(ctx, lang)
    say = SAY[lang]
    assert text.count(f"**{say['ledger_label']}**") == 2 and say["store_only"] in text
    assert S.REPLAY in section(text, 8) and rerun in section(text, 8) and "b" * 32 in section(text, 9)
    status = RV.status_sentence(environment(lang), ctx, lang)
    with pytest.raises(ReportRefused, match="without its label"):
        RV.check_rendered(text.replace(say["ledger_label"], ""), lang, ctx, "validation", status)
    with pytest.raises(ReportRefused, match="without the sentence on what only the store holds"):
        RV.check_rendered(text.replace(say["store_only"], ""), lang, ctx, "validation", status)


@pytest.mark.parametrize("lang", LANGS)
def test_a_store_holding_the_2024_record_but_not_the_2025_one_renders_it_from_its_entry(monkeypatch, lang):
    w = S.World().install(monkeypatch)
    w.add_2025(departure="true", departure_from="5" * 32)  # it departed from a start that stopped, in no ledger
    clone(w, ("b" * 32, "dq25"))
    ctx = w.context()
    assert not ctx["rec24"]["recorded"] and ctx["held"]["record"]["recorded"] and ctx["r_source"] == "record"
    text = RP.render(ctx, lang)
    assert text.count(f"**{SAY[lang]['ledger_label']}**") == 1 and SAY[lang]["store_only"] in text
    nine = section(text, 9)
    assert ("le départ `" + "5" * 32 + "`, qui n'a jamais fini : il n'est dans aucun registre" if lang == "fr" else
            "the start `" + "5" * 32 + "`, which never finished: it is in no ledger") in nine


@pytest.mark.parametrize("lang", LANGS)
def test_a_report_from_the_store_renders_every_record_from_its_run(monkeypatch, lang):
    """Control: the store holds everything; nothing is labelled as recorded, and no store-only sentence is said."""
    w = S.World().install(monkeypatch)
    w.add_2025()
    ctx = w.context()
    assert not ctx["store_only"] and ctx["status"] == "confirmed" and ctx["r_source"] == "record"
    text = RP.render(ctx, lang)
    assert SAY[lang]["ledger_label"] not in text and SAY[lang]["store_only"] not in text
    assert ctx["held"]["findings"]["revised"]["rows"] and SAY[lang]["revised"] in section(text, 9)


# --- the 2025 vintage before any start ----------------------------------------------------------------------------

@pytest.fixture
def two_vintages(tmp_path, monkeypatch):
    """A scratch store and synthetic sources through 2025: `run(through)` makes a passing data-quality run and gives
    its run ID."""
    from test_pipeline_refuses import isolate

    from valiwatt import tracking
    from valiwatt.config import DEMAND_LAST
    from valiwatt.quality import run as dq

    raw = isolate(tmp_path, monkeypatch, 2025)
    old = raw[raw["ts_utc"] <= DEMAND_LAST].reset_index(drop=True)
    paths = {str(dq.hydroquebec.csv_path(2024)): old, str(dq.hydroquebec.csv_path(2025)): raw}
    monkeypatch.setattr(dq.hydroquebec, "load_raw", lambda path=None: paths[str(path)])

    def run(through):
        assert dq.run_dq(through) == 0
        return tracking.latest_run(dq.EXPERIMENT).info.run_id
    return run


def test_a_report_on_the_2025_vintage_before_any_2025_start_is_refused(two_vintages, monkeypatch):
    """No 2025 replay has started: the render refuses a data-quality run of the 2025 vintage, whether it is the
    latest or named, and any run while the latest is one; a through-2024 run renders once the latest is one again, and
    the 2025 run renders after a first start."""
    from valiwatt.report import render as rr

    W.install_lane_d(monkeypatch, starts=[])
    first = two_vintages(2024)
    late = two_vintages(2025)
    refusal = f"data-quality run {late} reaches 2025, and no 2025 replay has started"
    for named in (None, first, late):
        with pytest.raises(ReportRefused, match=refusal):
            rr.render(named)
    again = two_vintages(2024)  # the latest is a through-2024 run again
    with pytest.raises(ReportRefused, match=refusal):
        rr.render(late)
    assert [p.name for p in rr.render(again)] == ["dq.fr.md", "dq.en.md"]  # control
    assert [p.name for p in rr.render()] == ["dq.fr.md", "dq.en.md"]
    W.install_lane_d(monkeypatch, starts=[W.start("b" * 32, W.utc(10), True, W.gate("clean"))])
    assert [p.name for p in rr.render(late)] == ["dq.fr.md", "dq.en.md"]  # with one start, the 2025 run renders


def test_a_2025_vintage_data_quality_report_leaves_2025_to_the_validation_report(two_vintages, monkeypatch):
    """After a first 2025 start, a through-2025 data-quality run renders: its section 4.1 lists only the days before
    2025 no forecast fed, the hour of the forecast only for the years before 2025, and no 2025 lead at or above the
    bound of plan section 9.2 (2025's are the validation report's, from the run the test of record read), and says
    which of its counts cover the whole window, 2025 included. A through-2024 run says nothing of 2025."""
    import json as _json

    from conftest import synth_runs
    from test_pipeline_refuses import nbm_store

    from valiwatt import tracking
    from valiwatt.config import DEMAND_FIRST, data_last
    from valiwatt.quality import run as dq
    from valiwatt.report import render as rr

    runs = synth_runs(DEMAND_FIRST.isoformat(), data_last(2025).isoformat())
    gap = pd.Series(False, index=runs.index)
    for first in ("2024-06-10", "2025-06-10"):  # four days of no run: the days after them go unfed
        gap |= (runs["runtime"] >= pd.Timestamp(f"{first}T00:00Z")) & \
            (runs["runtime"] < pd.Timestamp(f"{first}T00:00Z") + pd.Timedelta(days=4))
    rt = pd.Timestamp("2025-06-01T01:00Z")  # and a 2025 point 76 hours after its issue: a 2025 lead finding
    long = pd.DataFrame([{"runtime": rt, "ftime": rt + pd.Timedelta(hours=76), "tmp_f": 60.0}])
    runs = pd.concat([runs[~gap], long], ignore_index=True).sort_values(["runtime", "ftime"])
    monkeypatch.setattr(dq.nbm, "load_runs", nbm_store(runs.drop_duplicates(["runtime", "ftime"], keep="last")
                                                      .reset_index(drop=True)))
    W.install_lane_d(monkeypatch, starts=[W.start("b" * 32, W.utc(10), True, W.gate("clean"))])
    early, late = two_vintages(2024), two_vintages(2025)
    results = _json.loads(Path(tracking.client().download_artifacts(late, "results/dq_results.json")).read_text())
    found = rr.nbm_findings(results)
    not_fed = found["not_fed"]
    before = [d for d in not_fed if str(d["day"])[:4] == "2024"]
    held = [d for d in not_fed if str(d["day"])[:4] == "2025"]
    assert before and held
    # The run holds 2025's lead finding and 2025's hour of the forecast: the page leaves both to the validation report.
    assert found["at_or_above"] == [2025] and "2025" in found["runs"]["run_hour_by_year"]
    hour_by_year = {"fr": "**Heure de la prévision utilisée, par année**", "en": "**Hour of the forecast used, by year**"}
    leads = {"fr": "**Les délais.**", "en": "**The leads.**"}
    whole = {"fr": "Les comptes de cette section portent sur toute la fenêtre, 2025 compris",
             "en": "This section's counts cover the whole window, 2025 included"}
    for path, lang in zip(rr.render(late), LANGS):
        body = path.read_text(encoding="utf-8")
        fed = next(x for x in brief(body, lang) if x.startswith("La prévision de température" if lang == "fr" else
                                                                  "v2's temperature forecast"))
        assert ("jours de la fenêtre (2025 compris)" if lang == "fr" else "days (2025 included)") in fed
        four = rr.forecast_section(body)
        days = next(x for x in four.split("\n\n") if x.startswith("**Les jours qu'aucune" if lang == "fr" else
                                                                     "**The days no forecast fed"))
        day = environment(lang).filters["day"]
        assert all(day(d["day"]) in days for d in before) and not any(day(d["day"]) in days for d in held), lang
        assert whole[lang] in four
        by_year = four.split(hour_by_year[lang], 1)[1].split("\n\n", 1)[0]
        lead = next(x for x in four.split("\n\n") if x.startswith(leads[lang]))
        assert "2024" in by_year and "2024" in lead, lang  # the control: both state the years before 2025
        assert "2025" not in by_year and "2025" not in lead and LEAD_FINDING[lang] not in lead, lang
    for path, lang in zip(rr.render(early), LANGS):
        body = path.read_text(encoding="utf-8")
        assert whole[lang] not in body and "2025" not in rr.forecast_section(body)
        assert ("(2025 compris)" if lang == "fr" else "(2025 included)") not in "".join(brief(body, lang))


def test_the_data_quality_report_states_a_lead_of_75_hours(tmp_path, monkeypatch):
    from conftest import synth_runs
    from test_pipeline_refuses import isolate, nbm_store

    from valiwatt.config import DEMAND_FIRST, data_last
    from valiwatt.quality import run as dq
    from valiwatt.report import render as rr

    raw = isolate(tmp_path, monkeypatch, 2024)
    monkeypatch.setattr(dq.hydroquebec, "load_raw", lambda path=None: raw)
    runs = synth_runs(DEMAND_FIRST.isoformat(), data_last(2024).isoformat())
    rt = pd.Timestamp("2022-06-01T01:00Z")
    long = pd.DataFrame([{"runtime": rt, "ftime": rt + pd.Timedelta(hours=76), "tmp_f": 60.0}])
    runs = pd.concat([runs, long], ignore_index=True).sort_values(["runtime", "ftime"])
    monkeypatch.setattr(dq.nbm, "load_runs", nbm_store(runs.drop_duplicates(["runtime", "ftime"], keep="last")
                                                      .reset_index(drop=True)))
    assert dq.run_dq(2024) == 0
    for path, lang in zip(rr.render(), LANGS):
        body = path.read_text(encoding="utf-8")
        assert LEAD_FINDING[lang] in body and "section 18" in body, lang
        four = rr.forecast_section(body)
        assert LEAD_FINDING[lang] in four and LEAD_FINDING[lang] in body.replace(four, "")  # « En bref » says it too
        with pytest.raises(ReportRefused, match="not stated in section 4.1"):  # and must not stand in for 4.1
            rr.check_dq(body.replace(four, four.replace(LEAD_FINDING[lang], "x")), lang, {"shown_at_or_above": [2022]})
    with pytest.raises(ReportRefused, match="lead at or above"):
        rr.check_dq(attribution.NBM["fr"] + attribution.NO_ENDORSEMENT["fr"], "fr", {"shown_at_or_above": [2022]})
    with pytest.raises(ReportRefused, match="holds no forecast checks"):
        rr.nbm_findings({"checks": {}, "run_id": "x"})


# --- statements that hold only on the data that makes them true ------------------------------------------------------

def test_the_lag_coefficients_said_positive_must_be():
    """Section 3 says both lag coefficients are positive (the echo rule assumes it, the freeze checks it): a replay
    whose model logs another sign, or none, is refused rather than described wrongly."""
    import copy

    for lag in ({"lag24": -0.1, "lag168": 0.2}, {"lag24": 0.4, "lag168": 0.0}, {"lag24": 0.4}, {}):
        v, dq, p, r, b, frozen, oos, diag = payloads()
        r = copy.deepcopy(r)
        r["model"]["lag_coefficients"] = lag
        with pytest.raises(ReportRefused, match="lag coefficients .* are not both positive"):
            RV.derive(v, dq, p, r, b, frozen, oos, diag)


def test_a_replay_s_longest_lead_unlike_its_data_quality_run_s_is_refused():
    """The longest lead of the replayed year is stated once: the diagnostics' figure beside the run-reuse measure must
    be the data-quality run's for that year (2023 and 2024 from the chain's run, 2025 from the record's)."""
    import copy

    v, dq, p, r, b, frozen, oos, diag = payloads()
    for key, year in (("pseudo", P_YEAR), ("replay", R_YEAR)):
        bad = copy.deepcopy(diag)
        bad[key]["reuse_sensitivity"]["longest_lead_h"] = 74.0
        with pytest.raises(ReportRefused, match=f"the {year} diagnostics state a longest lead of 74.0 h"):
            RV.derive(v, dq, p, r, b, frozen, oos, bad)
    RV.check_reuse_lead(diag["replay"], {R_YEAR: 71.0}, str(R_YEAR), "x")  # a year given as text reads the same
    held = copy.deepcopy(RP.clean_record())
    RV.check_held_diagnostics(held, RP.context()["sens"])  # control: 71 h on both sides
    held["record"]["diag"]["reuse_sensitivity"]["longest_lead_h"] = 74.0
    with pytest.raises(ReportRefused, match="the 2025 diagnostics state a longest lead of 74.0 h, the data-quality "
                                            "run the test of record read states 71.0 h"):
        RV.check_held_diagnostics(held, RP.context()["sens"])


@pytest.mark.parametrize("lang", LANGS)
def test_the_run_reuse_paragraph_says_what_its_placements_show(lang):
    """Every placement constructible and no week past its untouched days: the argument holds; one week past, at one
    offset: a finding against it; no placement constructible: the argument is not put to the test. The diagnostics
    in monitor/sensitivity.py `forecast_run_reuse`'s shape: each placement's offsets."""
    import copy

    words = {"fr": ("comme l'argument le dit", "constat contre l'argument des deux jours", "n'est pas mis à l'épreuve"),
             "en": ("as the argument says", "finding against the two-day argument", "is not put to the test")}[lang]
    v, dq, p, r, b, frozen, oos, diag = payloads()

    def said(change):
        d = copy.deepcopy(diag)
        change(d["replay"]["reuse_sensitivity"]["placements"])
        eight = section(RP.render(RP.context(diag=d), lang), 8)
        return [i for i, w in enumerate(words) if w in eight]

    assert said(lambda xs: None) == [0]
    assert said(lambda xs: xs[0]["offsets"][1].update(beyond_untouched=1)) == [1]
    assert said(lambda xs: [x.update(constructible=False, reason="no run") for x in xs]) == [2]
