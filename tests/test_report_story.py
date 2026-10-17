"""The story, the short version of the validation report read first, rendered IN FULL in both languages from the
synthetic v2 contexts the validation tests use (tests/report_payloads.py), where values differ from one another: it
opens with the status (and the recommendation when there is one), each value lands in its own place, every conditional
sentence has a true branch for each case its data allow, every link points at a heading of the full report rendered
from the same context, no number is typed in the templates, the English says the same numbers as the French, and
`render_validation` writes the story beside the full report and lists it in the stamp."""

import copy
import hashlib
import json
import re
from pathlib import Path

import pytest
import report_payloads as RP
import report_store as S

from valiwatt.report import validation as RV
from valiwatt.report.render import TEMPLATES, ReportRefused, environment, fmt_num

LANGS = RP.LANGS
P_YEAR, R_YEAR, H_YEAR = RP.P_YEAR, RP.R_YEAR, RP.H_YEAR
COLD = {"fr": ("Où elle casse : le froid", "Le froid"), "en": ("Where it breaks: the cold", "The cold")}
HEADINGS = {
    "fr": ("La question", "La prévision", None, "Quelle confiance lui accorder ?", f"{R_YEAR}, rejouée mais déjà vue",
           "Le silence est-il une bonne nouvelle ?", "Le registre d'honnêteté"),
    "en": ("The question", "The forecast", None, "How much confidence does it deserve?",
           f"{R_YEAR}, replayed but already seen", "Is silence good news?", "The honesty ledger")}
LINK = r"\[section (\d+)\]\((validation\.(?:fr|en)\.md)#([^)]+)\)"


def context(state: str = "none", **override) -> dict:
    """A fresh copy: the payloads' contexts share their derived values between calls."""
    ctx = copy.deepcopy(RP.state(state))
    ctx.update(override)
    return ctx


def render(ctx: dict, lang: str, check: bool = True) -> str:
    return RP.render(ctx, lang, doc="story", check=check)


def beats(text: str) -> dict:
    """Each beat's heading and its body, in order."""
    parts = re.split(r"^## ", text, flags=re.M)[1:]
    return {p.split("\n", 1)[0]: p.split("\n", 1)[1] for p in parts}


def beat(text: str, n: int) -> str:
    return list(beats(text).values())[n - 1]


def pct(x, d, lang):
    return fmt_num(100 * x, d, lang) + (" %" if lang == "fr" else "%")


def slug(heading: str) -> str:
    """The anchor GitHub gives a heading: lower case, emphasis and punctuation dropped, spaces to hyphens."""
    return re.sub(r"[^\w\- ]", "", re.sub(r"[`*_]", "", heading.strip()).lower()).replace(" ", "-")


# --- the full render ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("state", list(RP.STATES))
def test_the_story_renders_in_full_seven_beats_each_ending_with_its_link(state, lang):
    ctx = context(state)
    text = render(ctx, lang)  # with the render's own checks: it opens with the status
    assert "None" not in text and "{{" not in text and "{%" not in text and "{#" not in text
    peak = ctx["v"]["cases"]["peak"]
    cold = COLD[lang][0 if peak["evaluated"] and peak["evaluated"] - peak["covered"] else 1]
    assert tuple(beats(text)) == tuple(cold if h is None else h for h in HEADINGS[lang])
    for line in RP.attribution(lang):
        assert f"> {line}" in text
    assert f"[{'rapport complet' if lang == 'fr' else 'full report'}](validation.{lang}.md)" in text
    assert f"`{'a' * 12}`" in text and f"`{ctx['r_id']}`" in text  # the commit and the replay run the header names
    for body in beats(text).values():
        last = [p for p in body.strip().split("\n\n") if not p.startswith("![")][-1]
        assert re.fullmatch(rf".*\({LINK}(, {LINK})*\)", last, re.S), last[-200:]
    # each beat links to the sections that carry its rest, in paragraph order
    five = [5, 10] if ctx["sides"]["season"]["under"] else [5]
    expected = [[1], [2, 3], [3, 4], five, [8], [7, 8], [9, 10]]
    assert [[int(n) for n, *_ in re.findall(LINK, body)] for body in beats(text).values()] == expected


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("state", ["none", "reruns"])
def test_every_link_points_at_a_heading_of_the_full_report(state, lang):
    """The anchors are GitHub's slugs of the full report's own headings, rendered from the same context."""
    ctx = context(state)
    full = RP.render(ctx, lang)
    anchors = {slug(h): int(h.split(".")[0]) for h in re.findall(r"^## (\d+\. .+)$", full, re.M)}
    links = re.findall(LINK, render(ctx, lang))
    assert len(links) >= 12
    for number, target, anchor in links:
        assert target == f"validation.{lang}.md"
        assert anchors.get(anchor) == int(number), (anchor, sorted(anchors))


def test_no_number_is_typed_in_the_story_templates():
    """Outside its expressions, a template's text holds no digit but the calendar's 24 hours and the names v1, v2."""
    for lang in LANGS:
        src = (TEMPLATES / f"story.{lang}.md.j2").read_text(encoding="utf-8")
        text = re.sub(r"\{#.*?#\}|\{%.*?%\}|\{\{.*?\}\}", " ", src, flags=re.S)
        assert set(re.findall(r"\d+", re.sub(r"\bv[12]\b", "", text))) == {"24"}, lang


def numbers(text: str, lang: str) -> list[str]:
    text = re.sub(r"`[^`]*`|\]\([^)]*\)|https?://[^\s)]+|CC BY-NC 4\.0", "", text.replace(" ", "").replace(" ", ""))
    if lang == "en":
        text = re.sub(r"(?<=\d),(?=\d{3})", "", text)
    found = re.findall(r"\d+(?:,\d+)?" if lang == "fr" else r"\d+(?:\.\d+)?", text)
    return sorted(x.replace(",", ".") for x in found)


@pytest.mark.parametrize("state", list(RP.STATES))
def test_the_english_says_the_same_numbers_as_the_french(state):
    ctx = context(state)
    assert numbers(render(ctx, "fr"), "fr") == numbers(render(ctx, "en"), "en")


# --- the question: the status first ---------------------------------------------------------------------------------

@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("state", ["none", "confirmed", "input_only", "not_confirmed", "post_hoc", "unfinished"])
def test_the_question_opens_with_the_status_and_its_recommendation(state, lang):
    ctx = context(state)
    text = render(ctx, lang)
    one = beat(text, 1)
    status = RV.status_sentence(environment(lang), ctx, lang)
    assert one.strip().startswith(status)
    rec = ctx["recommendation"]
    words = RV.SAY[lang]["recommendation"]
    assert [k for k, w in words.items() if w in one] == ([rec["kind"]] if rec else [])
    if rec is not None:
        with pytest.raises(ReportRefused, match="does not open with the 2025 result"):
            RV.check_rendered(text.replace(status, ""), lang, ctx, "story", status)


# --- each value in its own place ------------------------------------------------------------------------------------

@pytest.mark.parametrize("lang", LANGS)
def test_each_number_lands_in_its_own_beat_and_place(lang):
    ctx = context()
    ctx["K"] = {**ctx["K"], "power_horizon": 17, "step_horizon": 19}  # the code's constants, made distinct
    ctx["r"]["verdict"] = {**ctx["r"]["verdict"], "shipped": "better", "family": "not_shown_better"}  # distinct
    text = render(ctx, lang)
    sel, P, C, r = ctx["sel"], ctx["v"]["performance"], ctx["C"], ctx["r"]  # C: the production model's coverage
    m = sel["models"]

    two = beat(text, 2)
    a, b, c = (pct(sel["mape"][m[k]], 2, lang) for k in ("production", "family", "shipped"))
    assert two.index(a) < two.index(b) < two.index(c) < two.index(pct(P["gam_observed"]["overall"]["mean"], 2, lang))
    assert "Montréal" in two and fmt_num(sel["hours"], 0, lang) in two

    three = beat(text, 3)
    seasons = sorted(P[m["production"]]["season"], key=lambda x: x["ape"])
    deciles = sorted(P[m["production"]]["decile"], key=lambda x: x["ape"])
    low, high = pct(seasons[0]["ape"], 2, lang), pct(seasons[-1]["ape"], 2, lang)
    word = {"fr": {"winter": "en hiver", "spring": "au printemps", "summer": "en été", "fall": "en automne"},
            "en": {s: f"in {s}" for s in ("winter", "spring", "summer", "fall")}}[lang]
    assert three.index(f"{low} {word[seasons[0]['key']]}") < three.index(f"{high} {word[seasons[-1]['key']]}")
    d0, d1 = deciles[0], deciles[-1]
    assert (f"{pct(d0['ape'], 2, lang)} (décile {d0['key']}) à {pct(d1['ape'], 2, lang)} (décile {d1['key']})"
            if lang == "fr" else
            f"{pct(d0['ape'], 2, lang)} (decile {d0['key']}) to {pct(d1['ape'], 2, lang)} (decile {d1['key']})") in three
    assert fmt_num(ctx["v"]["contrasts"]["cold"]["threshold_c"], 1, lang) + " °C" in three

    four = beat(text, 4)
    assert pct(C["relative"]["overall"]["mean"], 1, lang) in four and pct(ctx["nominal"], 0, lang) in four
    assert ctx["v"]["conformal"]["first_fold"] in four and ctx["v"]["folds"]["last"] in four

    five = beat(text, 5)
    assert five.index(pct(r["summary"]["mape"]["mean"], 2, lang)) < five.index(pct(r["summary"]["coverage"]["mean"],
                                                                                     1, lang))
    assert ctx["frozen"][R_YEAR]["params"]["train_last_day"] in five
    verdict = environment(lang).get_template(f"sentences.{lang}.md.j2").module.VERDICT
    assert five.index(verdict[r["verdict"]["shipped"]]) < five.index(verdict[r["verdict"]["family"]])

    six = beat(text, 6)
    L, lv, lv24 = ctx["limits"]["replay"], ctx["level"], ctx["level24"]
    s = L["smallest_by_sign"]
    caught, cases = s["up"]["caught"] + s["down"]["caught"], s["up"]["cases"] + s["down"]["cases"]
    if lang == "fr":
        assert f"±{pct(L['smallest'], 0, lang)} l'a donné à {caught} des {cases} cas" in six
        assert (f"de {pct(lv['size'], 0, lang)} a donné « réentraîner » en 19 semaines à "
                f"{lv['up_n'] - lv['up_missed']} des {lv['up_n']} départs sur {P_YEAR} (l'année de répétition), et à "
                f"{lv24['up_n'] - lv24['up_missed']} des {lv24['up_n']} sur {R_YEAR} (vue par les développeurs)") in six
    else:
        assert f"±{pct(L['smallest'], 0, lang)} did at {caught} of {cases} cases" in six
        assert (f"{pct(lv['size'], 0, lang)} rise yielded \"retrain\" within 19 weeks at "
                f"{lv['up_n'] - lv['up_missed']} of {lv['up_n']} starts on {P_YEAR} (the rehearsal year), and at "
                f"{lv24['up_n'] - lv24['up_missed']} of {lv24['up_n']} on {R_YEAR} (seen by the developers)") in six
    assert " 17 " in six and (lv["up_n"] - lv["up_missed"]) != (lv24["up_n"] - lv24["up_missed"])


@pytest.mark.parametrize("lang", LANGS)
def test_the_header_says_when_the_code_was_not_committed(lang):
    words = "avec des modifications non validées" if lang == "fr" else "with uncommitted changes"
    assert words not in render(context(), lang)
    assert words in render(context(dirty=True), lang).split("\n## ")[0]


@pytest.mark.parametrize("lang", LANGS)
def test_the_choice_years_claim_an_advantage_only_when_v2_has_the_lowest_error(lang):
    ctx = context()
    m, mape = ctx["sel"]["models"], ctx["sel"]["mape"]
    lead = ("l'avantage de v2 y est biaisé vers le haut" if lang == "fr" else "v2's advantage there is biased upward")
    behind = ("v2 n'a pas l'erreur la plus basse des trois" if lang == "fr" else
              "v2 does not have the lowest error of the three")
    best = mape[m["production"]] < min(mape[m["family"]], mape[m["shipped"]])
    for text in (beat(render(ctx, lang), 2), RP.render(ctx, lang)):
        assert (lead in text) == best and (behind in text) == (not best)
    worse = {**mape, m["production"]: max(mape.values()) + 0.001}
    ctx = context(sel={**ctx["sel"], "mape": worse})
    two = beat(render(ctx, lang, check=False), 2)
    assert behind in two and lead not in two


# --- the 2024 beat: the recommendations, the verdicts, a departure ------------------------------------------------

@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("kept", [0, 1, 2])
def test_the_recommendations_sentence_for_none_one_and_several(lang, kept):
    ctx = context()
    decisions = ctx["r"]["decisions"]
    chosen = [d for d in decisions if d["recommendation"]]
    assert len(chosen) >= 2
    for d in chosen[kept:]:
        d["recommendation"] = None
    five = beat(render(ctx, lang), 5)
    said = {"fr": ("Le moniteur n'a rien recommandé de toute l'année.", "Le moniteur a fait une recommandation : ",
                   f"Le moniteur a fait {kept} recommandations : "),
            "en": ("The monitor recommended nothing all year.", "The monitor made one recommendation: ",
                   f"The monitor made {kept} recommandations: ".replace("recommandations", "recommendations"))}[lang]
    assert [i for i, x in enumerate(said) if x in five] == [kept]
    for d in chosen[:kept]:
        assert str(d["week_start"])[:10] in five


VERDICT_LINE = {"fr": ("Un succès sur une année déjà vue est une preuve faible", "est un constat contre v2."),
                "en": ("A success on a year already seen is weak evidence", "is a finding against v2.")}


@pytest.mark.parametrize("lang", LANGS)
def test_the_2024_beat_calls_a_success_weak_and_a_failure_a_finding(lang):
    weak, against = VERDICT_LINE[lang]
    for shipped, family, finding in (("better", "better", False), ("better_cold_untested", "better", False),
                                     ("not_shown_better", "better", True), ("better", "worse", True)):
        ctx = context()
        ctx["r"]["verdict"] = {**ctx["r"]["verdict"], "shipped": shipped, "family": family}
        five = beat(render(ctx, lang, check=False), 5)
        assert (weak in five) == (not finding) and (against in five) == finding, (shipped, family)


@pytest.mark.parametrize("lang", LANGS)
def test_a_departure_makes_the_2024_beat_post_hoc(lang):
    post_hoc = {"fr": "il est **a posteriori**", "en": "it is **post hoc**"}[lang]
    plain = render(context(), lang)
    assert post_hoc not in plain
    assert ("aucune dérogation ne les a changées depuis" if lang == "fr" else
            "no departure has changed them since") in beat(plain, 7)
    assert f"`{RP.W.FREEZE_TAG}`" in beat(plain, 7)
    earlier = {"id": "1" * 32, "freeze_commit": RP.W.F0, "recorded": False, "unfinished": False, "absent": False,
               "summary": {"mape": {"mean": 0.04}, "coverage": {"mean": 0.9}}, "recommendations": []}
    text = render(context(departures=[earlier]), lang)
    five, seven = beat(text, 5), beat(text, 7)
    assert five.index(post_hoc) < five.index(pct(context()["r"]["summary"]["mape"]["mean"], 2, lang))
    assert ("changées par une dérogation consignée" if lang == "fr" else "changed by a departure recorded") in seven


# --- the cold peak --------------------------------------------------------------------------------------------------

def peaked(evaluated, covered, above, in_month=True):
    ctx = context(peak_above=above, peak_in_month=in_month)
    ctx["v"]["cases"]["peak"].update(evaluated=evaluated, covered=covered)
    return ctx


PEAK = {"fr": {"none": "aucune fourchette n'est calculée.",
               "all": "la demande est restée dans la fourchette à 90 % chaque fois (6 heures jugées).",
               "out_up": "la demande est sortie de la fourchette à 90 % à chacune des 2 heures jugées, chaque "
                         "fois par le haut.",
               "both": "la demande est restée dans la fourchette à 90 % à 1 des 6 heures jugées ; elle l'a "
                       "dépassée par le haut à 3 et par le bas à 2.",
               "below": "la demande est restée dans la fourchette à 90 % à 1 des 6 heures jugées ; elle est "
                        "passée sous la fourchette à 5."},
        "en": {"none": "no range is computed.",
               "all": "demand stayed inside the 90% range every time (6 hours judged).",
               "out_up": "demand left the 90% range at each of the 2 hours judged, above it every time.",
               "both": "demand stayed inside the 90% range at 1 of the 6 hours judged; it went above it at 3 and "
                       "below it at 2.",
               "below": "demand stayed inside the 90% range at 1 of the 6 hours judged; it fell below the range at "
                        "5."}}


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("case, args, cold", [
    ("none", (0, 0, 0), False), ("all", (6, 6, 0), False), ("out_up", (2, 0, 2), True), ("both", (6, 1, 3), True),
    ("below", (6, 1, 0), True)])
def test_the_cold_peak_sentence_for_each_count(lang, case, args, cold):
    ctx = peaked(*args)
    assert ctx["nominal"] == 0.9
    text = render(ctx, lang)
    assert list(beats(text))[2] == COLD[lang][0 if cold else 1]
    three = beat(text, 3)
    for other, sentence in PEAK[lang].items():
        assert (sentence in three) == (other == case), (case, other)
    month = ctx["v"]["cases"]["peak"]["named_month"]
    assert month in three and month not in beat(render(peaked(*args, in_month=False), lang), 3)


# --- coverage sides -------------------------------------------------------------------------------------------------

def sided(season=((), ()), decile=((), ()), hour=((), ()), overall=((), ())):
    def side(pair):
        return {"under": list(pair[0]), "over": list(pair[1])}
    return context(sides={"season": side(season), "decile": side(decile), "hour_group": side(hour),
                          "overall": side(overall)})


SIDES = {"fr": {"under": "Elle promet trop là où", "over": "Elle est plus large qu'il ne faut là où",
                "no_under": "Aucune saison, aucun bloc horaire ni aucun décile de charge prévue n'en sort",
                "no_over": "Dans aucun groupe la demande ne reste dedans",
                "caveat": "le moniteur ne le signale pas comme une dégradation",
                "o_under": ": moins que promis,", "o_over": ": plus que promis,",
                "o_in": ": l'écart à la promesse reste dans l'incertitude de la mesure"},
         "en": {"under": "It promises too much where", "over": "It is wider than needed where",
                "no_under": "No season, no hour block and no forecast-load decile leaves it",
                "no_over": "In no group does demand stay inside",
                "caveat": "the monitor does not flag it as a degradation",
                "o_under": ": less than promised,", "o_over": ": more than promised,",
                "o_in": ": the gap to the promise stays within the uncertainty of the measurement"}}


@pytest.mark.parametrize("lang", LANGS)
def test_the_coverage_sides_name_each_group_or_say_none(lang):
    S_ = SIDES[lang]
    four = beat(render(sided(), lang), 4)
    assert S_["no_under"] in four and S_["no_over"] in four and S_["under"] not in four and S_["over"] not in four
    assert [n for n, *_ in re.findall(LINK, four)] == ["5"] and S_["caveat"] not in four and S_["o_in"] in four
    four = beat(render(sided(season=(("winter",), ("summer",)), decile=(("9",), ("2",)), hour=(("night",), ("day",))),
                       lang), 4)
    under, over = four.split(S_["over"])
    hb = context()["v"]["config"]["hour_blocks"]
    night, day = (f"{hb[b][0]}–{hb[b][1]} h" for b in ("night", "day"))
    if lang == "fr":
        assert f"l'hiver ; le bloc horaire nuit ({night}) ; le décile de charge prévue 9." in under
        assert f"l'été ; le bloc horaire jour ({day}) ; le décile de charge prévue 2." in over
    else:
        assert f"winter; hour block night ({night}); forecast-load decile 9." in under
        assert f"summer; hour block day ({day}); forecast-load decile 2." in over
    assert S_["caveat"] in over and S_["no_under"] not in four and S_["no_over"] not in four
    assert [n for n, *_ in re.findall(LINK, four)] == ["5", "10"]  # the winter caveat's rest is in the limitations
    four = beat(render(sided(decile=(("10",), ())), lang), 4)
    assert S_["under"] in four and S_["no_over"] in four and S_["caveat"] not in four
    for key, overall in (("o_under", (("all",), ())), ("o_over", ((), ("all",))), ("o_in", ((), ()))):
        four = beat(render(sided(overall=overall), lang), 4)
        assert [k for k in ("o_under", "o_over", "o_in") if S_[k] in four] == [key]


# --- what the alarm can and cannot see ------------------------------------------------------------------------------

SILENCE = {"fr": ("Le silence n'est donc pas une preuve", "À chacun des départs mesurés",
                  "même le plus grand écart mesuré entre la demande et la prévision, ±",
                  "Les mesures des changements de niveau n'ont pas fini"),
           "en": ("Silence is therefore no proof", "At each of the starts measured",
                  "even the largest gap measured between demand and forecast, ±",
                  "The level-shift measures did not finish")}


def limited(every_from="smallest", up_missed=0, up_missed24=0, pseudo_every_from="smallest"):
    """Both years' power tables (2024: `every_from`, 2023: `pseudo_every_from`) and level shifts, as detection_limits
    can give them: when every case from the smallest size on was caught, the smallest size's cases all were."""
    ctx = context()
    for key, frm in (("replay", every_from), ("pseudo", pseudo_every_from)):
        L = ctx["limits"][key]
        L["every_from"] = L["smallest"] if frm == "smallest" else frm
        if L["every_from"] == L["smallest"]:
            L["smallest_by_sign"] = {s: {"caught": n["cases"], "cases": n["cases"]}
                                     for s, n in L["smallest_by_sign"].items()}
    ctx["level"]["up_missed"], ctx["level24"]["up_missed"] = up_missed, up_missed24
    return ctx


@pytest.mark.parametrize("lang", LANGS)
def test_silence_is_called_no_proof_only_when_a_change_went_unnoticed(lang):
    no_proof, all_caught, largest, unfinished = SILENCE[lang]
    six = beat(render(limited(), lang), 6)
    assert all_caught in six and no_proof not in six and largest not in six
    assert f"{all_caught}, " + (f"sur {P_YEAR} comme sur {R_YEAR}," if lang == "fr" else
                                f"on {P_YEAR} as on {R_YEAR},") in six
    for ctx in (limited(every_from=None), limited(every_from=0.04), limited(up_missed=1), limited(up_missed24=2),
                limited(pseudo_every_from=None), limited(pseudo_every_from=0.04)):
        six = beat(render(ctx, lang), 6)
        assert no_proof in six and all_caught not in six
    six = beat(render(limited(every_from=None), lang), 6)
    assert largest + pct(context()["limits"]["replay"]["sizes"][-1], 0, lang) in six
    diag = RP.payloads()[7]
    six = beat(render(copy.deepcopy(RP.context(diag={"pseudo": diag["pseudo"], "replay": None})), lang), 6)
    assert unfinished in six and all_caught not in six and no_proof not in six


# --- the honesty ledger: 2025 pending, started, finished --------------------------------------------------------------

LEDGER = {"fr": (f"**En attente :** {H_YEAR} jugera", f"Le test de référence de {H_YEAR} a commencé et n'a pas fini",
                 f"Le test de référence de {H_YEAR} a fini, sa position"),
          "en": (f"**Pending:** {H_YEAR} will judge", f"The {H_YEAR} test of record started and did not finish",
                 f"The {H_YEAR} test of record finished, its standing")}


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("state, said", [("none", 0), ("unfinished", 1), ("confirmed", 2), ("post_hoc", 2)])
def test_the_honesty_ledger_says_where_2025_stands(state, said, lang):
    ctx = context(state)
    seven = beat(render(ctx, lang), 7)
    assert [i for i, x in enumerate(LEDGER[lang]) if x in seven] == [said]
    if said == 2:
        R = ctx["held"]["record"]
        m = environment(lang).get_template(f"sentences.{lang}.md.j2").module
        assert m.OUTCOME[R["standing"]] in seven and m.VERDICT[R["verdict"]["shipped"]] in seven
        assert m.LABEL[R["evaluation"]] in seven


# --- written by render_validation, listed in the stamp --------------------------------------------------------------

def test_render_validation_writes_the_story_and_the_stamp_lists_it(tmp_path, monkeypatch):
    """End to end on the fake store: the story is written beside the full report, its links resolve to the headings
    of the full report written with it, its attribution is there, and the stamp lists its hash."""
    from test_report_stamp import render_world

    from valiwatt import attribution

    w = S.World().install(monkeypatch)
    w.add_2025()
    render_world(w, tmp_path, monkeypatch)
    out = tmp_path / "report"
    stamp = json.loads((out / "stamp.json").read_text(encoding="utf-8"))
    retrieved = min(s["first_retrieved_utc"] for s in w.payloads["dq"]["sources"]["eccc"])[:10]
    for lang in LANGS:
        body = (out / f"story.{lang}.md").read_bytes()
        assert stamp["files"][f"story.{lang}.md"] == hashlib.sha256(body).hexdigest()
        story, full = body.decode("utf-8"), (out / f"validation.{lang}.md").read_text(encoding="utf-8")
        for line in attribution.lines(lang, retrieved=retrieved):
            assert line in story
        anchors = {slug(h) for h in re.findall(r"^## (\d+\. .+)$", full, re.M)}
        links = re.findall(LINK, story)
        assert links and all(a in anchors for _, _, a in links)
    assert stamp["standing_2025"] == {"record_run_id": "b" * 32, "finished": True, "standing": "clean"}


def test_the_readme_points_to_the_story_first():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    story, full = readme.index("docs/report/story.fr.md"), readme.index("docs/report/validation.fr.md")
    assert story < full and "docs/report/story.en.md" in readme
