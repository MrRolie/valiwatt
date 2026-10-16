"""The story, the short version of the validation report read first, rendered IN FULL in both languages from the stub
context the validation tests use (test_report_render.context()), where every value differs from every other: each
value must land in its own place, every conditional sentence has a true branch for each case its data allow, every
link points at a heading of the full report, no number is typed in the templates, the English says the same numbers
as the French, and `render_validation` writes the story beside the full report and lists it in the stamp."""

import copy
import hashlib
import json
import re
from pathlib import Path

import pytest
import test_report_render as R

from valiwatt.report import validation as RV
from valiwatt.report.render import TEMPLATES, environment

LANGS = ("fr", "en")
P_YEAR, R_YEAR = R.P_YEAR, R.R_YEAR
HEADINGS = {
    "fr": ("La question", "La prévision", "Où elle casse : le froid", "Quelle confiance lui accorder ?",
           f"Le vrai test : {R_YEAR}", "Le silence est-il une bonne nouvelle ?", "Le registre d'honnêteté"),
    "en": ("The question", "The forecast", "Where it breaks: the cold", "How sure it is", f"The real test: {R_YEAR}",
           "Is silence good news?", "The honesty ledger")}
COLD = {"fr": "Le froid", "en": "The cold"}


def context(departures=(), **override) -> dict:
    """A fresh copy: `test_report_render.context` shares its derived values between calls."""
    ctx = copy.deepcopy(R.context(departures=departures))
    ctx.update(override)
    return ctx


def render(ctx: dict, lang: str) -> str:
    return environment(lang).get_template(f"story.{lang}.md.j2").render(**ctx, fig=R.FIG, lang=lang,
                                                                         attribution=R.ATTRIBUTION)


def beats(text: str) -> dict:
    """Each beat's heading and its body, in order."""
    parts = re.split(r"^## ", text, flags=re.M)[1:]
    return {p.split("\n", 1)[0]: p.split("\n", 1)[1] for p in parts}


def beat(text: str, n: int) -> str:
    return list(beats(text).values())[n - 1]


def pct(x, d, lang):
    return R.pct(x, d, lang)


def slug(heading: str) -> str:
    """The anchor GitHub gives a heading: lower case, punctuation dropped, spaces to hyphens."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


LINK = r"\[section (\d+)\]\((validation\.(?:fr|en)\.md)#([^)]+)\)"


# --- the full render ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("departures", [(), (R.EARLIER,)])
def test_the_story_renders_in_full_seven_beats_each_ending_with_its_link(lang, departures):
    ctx = context(departures)
    text = render(ctx, lang)
    assert "None" not in text and "{{" not in text and "{%" not in text and "{#" not in text
    assert tuple(beats(text)) == HEADINGS[lang]
    for line in R.ATTRIBUTION:
        assert f"> {line}" in text
    assert f"[{'rapport complet' if lang == 'fr' else 'full report'}](validation.{lang}.md)" in text
    assert f"`{'a' * 12}`" in text and f"`{'0' * 31}6`" in text  # the commit and the replay run, as the header names them
    for body in beats(text).values():
        last = [p for p in body.strip().split("\n\n") if not p.startswith("![")][-1]
        assert re.fullmatch(rf".*\({LINK}(, {LINK})*\)", last, re.S), last[-200:]
    # each beat links to the sections that carry its rest, in paragraph order: the methods; the validation; the
    # validation and the test cases; the intervals (and the limitations when a season is under-covered); the 2024
    # replay; the 2023 rerun and the 2024 replay; the breaks (the ledger's refusal) and the limitations
    four = [4, 8] if ctx["sides"]["season"]["under"] else [4]
    expected = [[1], [2], [2, 3], four, [7], [6, 7], [9, 8]]
    assert [[int(n) for n, *_ in re.findall(LINK, body)] for body in beats(text).values()] == expected


@pytest.mark.parametrize("lang", LANGS)
def test_every_link_points_at_a_heading_of_the_full_report(lang):
    """The anchors are GitHub's slugs of the full report's own headings, rendered from the same context."""
    ctx = context((R.EARLIER,))
    full = R.render(ctx, lang)
    anchors = {slug(h): int(h.split(".")[0]) for h in re.findall(r"^## (\d+\. .+)$", full, re.M)}
    links = re.findall(LINK, render(ctx, lang))
    assert len(links) >= 9
    for number, target, anchor in links:
        assert target == f"validation.{lang}.md"
        assert anchors.get(anchor) == int(number), (anchor, sorted(anchors))


def test_no_number_is_typed_in_the_story_templates():
    """Outside its expressions, a template's text holds no digit but the calendar's 24 hours."""
    for lang in LANGS:
        src = (TEMPLATES / f"story.{lang}.md.j2").read_text(encoding="utf-8")
        text = re.sub(r"\{#.*?#\}|\{%.*?%\}|\{\{.*?\}\}", "", src, flags=re.S)
        assert set(re.findall(r"\d+", text)) == {"24"}, lang


def numbers(text: str, lang: str) -> list[str]:
    found = re.findall(r"\d+(?:,\d+)?" if lang == "fr" else r"\d+(?:\.\d+)?", text)
    return sorted(x.replace(",", ".") for x in found)


@pytest.mark.parametrize("departures", [(), (R.EARLIER,), ({**R.EARLIER, "recorded": True, **R.NOT_IN_LEDGER},)])
def test_the_english_says_the_same_numbers_as_the_french(departures):
    ctx = context(departures)
    assert numbers(render(ctx, "fr"), "fr") == numbers(render(ctx, "en"), "en")


# --- each value in its own place ------------------------------------------------------------------------------------

@pytest.mark.parametrize("lang", LANGS)
def test_each_number_lands_in_its_own_beat_and_place(lang):
    ctx = context()
    K = {**ctx["K"], "power_horizon": 17, "step_horizon": 19}  # the code's constants, made distinct
    ctx["K"] = K
    text = render(ctx, lang)
    P, C, r = ctx["v"]["performance"], ctx["v"]["coverage"], ctx["r"]

    two = beat(text, 2)
    a, b, o = (pct(P[k]["overall"]["mean"], 2, lang) for k in ("persistence", "baseline", "observed"))
    assert two.index(a) < two.index(b) < two.index(o)
    assert "Montréal" in two and f"{K['validation_years'][0]}" in two

    three = beat(text, 3)
    seasons = sorted(P["persistence"]["season"], key=lambda x: x["ape"])
    deciles = sorted(P["persistence"]["decile"], key=lambda x: x["ape"])
    low, high = pct(seasons[0]["ape"], 2, lang), pct(seasons[-1]["ape"], 2, lang)
    assert three.index(low) < three.index(high)
    word = {"fr": {"winter": "en hiver", "spring": "au printemps", "summer": "en été", "fall": "en automne"},
            "en": {s: f"in {s}" for s in ("winter", "spring", "summer", "fall")}}[lang]
    assert f"{low} {word[seasons[0]['key']]}" in three and f"{high} {word[seasons[-1]['key']]}" in three
    d0, d1 = deciles[0], deciles[-1]
    assert (f"{pct(d0['ape'], 2, lang)} (décile {d0['key']}) à {pct(d1['ape'], 2, lang)} (décile {d1['key']})"
            if lang == "fr" else
            f"{pct(d0['ape'], 2, lang)} (decile {d0['key']}) to {pct(d1['ape'], 2, lang)} (decile {d1['key']})") in three
    gaps = [(h["target"] - h["pred_observed"]) / h["target"] for h in ctx["v"]["cases"]["peak"]["hours"]]
    assert three.index(pct(min(gaps), 1, lang)) < three.index(pct(max(gaps), 1, lang))

    four = beat(text, 4)
    assert pct(C["relative"]["overall"]["mean"], 1, lang) in four and pct(ctx["nominal"], 0, lang) in four
    assert ctx["v"]["conformal"]["first_fold"] in four

    five = beat(text, 5)
    assert five.index(pct(r["summary"]["mape"]["mean"], 2, lang)) < five.index(pct(r["summary"]["coverage"]["mean"],
                                                                                     1, lang))
    assert ctx["frozen"][R_YEAR]["params"]["train_last_day"] in five

    six = beat(text, 6)
    L, lv, lv24 = ctx["limits"]["replay"], ctx["level"], ctx["level24"]
    s = L["smallest_by_sign"]
    caught = s["up"]["caught"] + s["down"]["caught"]
    cases = s["up"]["cases"] + s["down"]["cases"]
    if lang == "fr":
        assert (f"±{pct(L['every_from'], 0, lang)} ou plus entre la demande et la prévision a donné « réentraîner » "
                f"en 17 semaines au plus à chacun des {L['starts']} départs") in six
        assert f"±{pct(L['smallest'], 0, lang)} l'a donné à {caught} des {cases} cas" in six
        assert (f"de {pct(lv['size'], 0, lang)} a donné « réentraîner » en 19 semaines à "
                f"{lv['up_n'] - lv['up_missed']} des {lv['up_n']} départs sur {P_YEAR} (l'année de conception des "
                f"règles), et à {lv24['up_n'] - lv24['up_missed']} des {lv24['up_n']} sur {R_YEAR} (hors échantillon, "
                "mesure ajoutée après les résultats)") in six
    else:
        assert (f"±{pct(L['every_from'], 0, lang)} or more between demand and forecast yielded \"retrain\" within 17 "
                f"weeks at each of the {L['starts']} starts") in six
        assert f"±{pct(L['smallest'], 0, lang)} yielded it at {caught} of {cases} cases" in six
        assert (f"{pct(lv['size'], 0, lang)} rise yielded \"retrain\" within 19 weeks at "
                f"{lv['up_n'] - lv['up_missed']} of {lv['up_n']} starts on {P_YEAR} (the year the rules were designed "
                f"on), and at {lv24['up_n'] - lv24['up_missed']} of {lv24['up_n']} on {R_YEAR} (out of sample, a "
                "measurement added after the results)") in six
    assert (lv["up_n"] - lv["up_missed"], lv24["up_n"] - lv24["up_missed"]) == (1, 3)  # distinct, so not swapped


@pytest.mark.parametrize("lang", LANGS)
def test_the_header_says_when_the_code_was_not_committed(lang):
    words = "avec des modifications non validées" if lang == "fr" else "with uncommitted changes"
    assert words not in render(context(), lang)
    assert words in render(context(dirty=True), lang).split("\n## ")[0]


# --- the recommendations: none, one, several ------------------------------------------------------------------------

REC = {"fr": ("Le moniteur n'a rien recommandé de toute l'année.",
              "Le moniteur a fait une recommandation : semaine du 2024-11-04 : réentraîner.",
              "Le moniteur a fait 2 recommandations : semaine du 2024-11-04 : réentraîner ; semaine du 2024-12-09 : "
              "examiner les données."),
       "en": ("The monitor recommended nothing all year.",
              "The monitor made one recommendation: week of 2024-11-04: retrain.",
              "The monitor made 2 recommendations: week of 2024-11-04: retrain; week of 2024-12-09: investigate the "
              "data.")}


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("kept", [0, 1, 2])
def test_the_recommendations_sentence_for_none_one_and_several(lang, kept):
    ctx = context()
    decisions = ctx["r"]["decisions"]
    for d in decisions[kept:]:
        d["recommendation"] = None
    five = beat(render(ctx, lang), 5)
    for i, sentence in enumerate(REC[lang]):
        assert (sentence in five) == (i == kept), (kept, sentence)


# --- the cold peak --------------------------------------------------------------------------------------------------

def peaked(evaluated, covered, above, hours_below_observed=True, in_month=True):
    ctx = context(peak_above=above, peak_in_month=in_month)
    peak = ctx["v"]["cases"]["peak"]
    peak.update(evaluated=evaluated, covered=covered)
    if not hours_below_observed:
        peak["hours"][0]["pred_observed"] = peak["hours"][0]["target"] * 1.01
    return ctx


FR_IN = "la demande est restée dans la fourchette à 90\u00a0% "
PEAK = {"fr": {"none": "aucune fourchette n'est calculée.",
               "all": FR_IN + "chaque fois (6 heures jugées).",
               "both": FR_IN + "à 1 des 6 heures jugées ; elle l'a dépassée par le haut à 3 et par le bas à 2.",
               "above": FR_IN + "à 0 des 2 heures jugées ; elle l'a dépassée par le haut à 2.",
               "below": FR_IN + "à 1 des 6 heures jugées ; elle est passée sous la fourchette à 5."},
        "en": {"none": "no range is computed.",
               "all": "demand stayed inside the 90% range every time (6 hours judged).",
               "both": "demand stayed inside the 90% range at 1 of 6 hours judged; it went above it at 3 and below it "
                       "at 2.",
               "above": "demand stayed inside the 90% range at 0 of 2 hours judged; it went above it at 2.",
               "below": "demand stayed inside the 90% range at 1 of 6 hours judged; it fell below the range at 5."}}


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("case, args, cold", [
    ("none", (0, 0, 0), False), ("all", (6, 6, 0), False), ("both", (6, 1, 3), True), ("above", (2, 0, 2), True),
    ("below", (6, 1, 0), True)])
def test_the_cold_peak_sentence_for_each_count(lang, case, args, cold):
    text = render(peaked(*args), lang)
    heading = list(beats(text))[2]
    assert heading == (HEADINGS[lang][2] if cold else COLD[lang])
    three = beat(text, 3)
    for other, sentence in PEAK[lang].items():
        assert (sentence in three) == (other == case), (case, other)


@pytest.mark.parametrize("lang", LANGS)
def test_the_observed_weather_clause_follows_the_sign_of_the_gaps(lang):
    under = "la prévision reste de" if lang == "fr" else "the forecast stays"
    spread = ("l'écart relatif entre la demande et la prévision va de" if lang == "fr" else
              "the relative gap between demand and forecast runs from")
    side = ("(positif quand la prévision est sous la demande)" if lang == "fr" else
            "(positive when the forecast is below demand)")
    cause = "la cause n'est pas testée" if lang == "fr" else "the cause is not tested"
    for below, ctx in ((True, peaked(2, 0, 2)), (False, peaked(2, 0, 2, hours_below_observed=False))):
        three = beat(render(ctx, lang), 3)
        assert (under in three) == below and (spread in three) == (not below) and (side in three) == (not below)
        assert cause in three
    # gaps of both signs are printed signed: the forecast above demand at one hour, below it at the other
    three = beat(render(peaked(2, 0, 2, hours_below_observed=False), lang), 3)
    unit = r"\s?%"  # sp() spaces its sign as the full report does
    assert re.search(rf"{spread} \u22121[.,]0{unit} à \+\d+[.,]\d{unit}" if lang == "fr" else
                     rf"{spread} \u22121[.,]0{unit} to \+\d+[.,]\d{unit}", three), three
    month = "toutes pendant la pointe de froid de 2023-02" if lang == "fr" else "all during the cold peak of 2023-02"
    assert month in render(peaked(2, 0, 2), lang) and month not in render(peaked(2, 0, 2, in_month=False), lang)


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
         "en": {"under": "It promises too much where", "over": "It is wider than it needs to be where",
                "no_under": "No season, no hour block and no forecast-load decile has demand falling outside",
                "no_over": "In no group does demand stay inside",
                "caveat": "the monitor does not flag it as decay",
                "o_under": ": less than promised,", "o_over": ": more than promised,",
                "o_in": ": the gap to the promise stays within the uncertainty of the measurement"}}


@pytest.mark.parametrize("lang", LANGS)
def test_the_coverage_sides_name_each_group_or_say_none(lang):
    S = SIDES[lang]
    four = beat(render(sided(), lang), 4)
    assert S["no_under"] in four and S["no_over"] in four and S["under"] not in four and S["over"] not in four
    assert [n for n, *_ in re.findall(LINK, four)] == ["4"]
    assert S["caveat"] not in four and S["o_in"] in four
    four = beat(render(sided(season=(("winter",), ("summer",)), decile=(("9",), ("2",)),
                             hour=(("night",), ("day",))), lang), 4)
    under, over = four.split(S["over"])
    if lang == "fr":
        assert "l'hiver ; le bloc horaire nuit (0–5 h) ; le décile de charge prévue 9." in under
        assert "l'été ; le bloc horaire jour (10–15 h) ; le décile de charge prévue 2." in over
    else:
        assert "winter; hour block night (0–5 h); forecast-load decile 9." in under
        assert "summer; hour block day (10–15 h); forecast-load decile 2." in over
    assert S["caveat"] in over and S["no_under"] not in four and S["no_over"] not in four
    assert [n for n, *_ in re.findall(LINK, four)] == ["4", "8"]  # the winter caveat's rest is in the limitations
    # an under-covered decile alone: the groups are named, and the season caveat is not said
    four = beat(render(sided(decile=(("10",), ())), lang), 4)
    assert S["under"] in four and S["no_over"] in four and S["caveat"] not in four
    assert [n for n, *_ in re.findall(LINK, four)] == ["4"]
    # an hour block alone on either side: the block is named, and "no group" is not said on that side
    night = "le bloc horaire nuit (0–5 h)" if lang == "fr" else "hour block night (0–5 h)"
    day = "le bloc horaire jour (10–15 h)" if lang == "fr" else "hour block day (10–15 h)"
    four = beat(render(sided(hour=(("night",), ())), lang), 4)
    assert S["under"] in four and S["no_under"] not in four and S["no_over"] in four and S["over"] not in four
    assert night in four.split(S["no_over"])[0].split(S["under"])[1]
    four = beat(render(sided(hour=((), ("day",))), lang), 4)
    assert S["over"] in four and S["no_over"] not in four and S["no_under"] in four and S["under"] not in four
    assert day in four.split(S["over"])[1]
    for key, overall in (("o_under", (("all",), ())), ("o_over", ((), ("all",))), ("o_in", ((), ()))):
        four = beat(render(sided(overall=overall), lang), 4)
        assert [k for k in ("o_under", "o_over", "o_in") if S[k] in four] == [key]


# --- what the alarm can and cannot see ------------------------------------------------------------------------------

SILENCE = {"fr": ("Le silence n'est donc pas une preuve", "À chacun des départs mesurés",
                  "même le plus grand écart mesuré entre la demande et la prévision, ±"),
           "en": ("Silence is therefore no proof", "At each of the starts measured",
                  "even the largest gap measured between demand and forecast, ±")}


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
    no_proof, all_caught, largest = SILENCE[lang]
    six = beat(render(limited(), lang), 6)
    assert all_caught in six and no_proof not in six and largest not in six
    both = (f"sur {P_YEAR} comme sur {R_YEAR}" if lang == "fr" else f"on {P_YEAR} as on {R_YEAR}")
    assert f"{all_caught}, {both}," in six
    s = context()["limits"]["replay"]["smallest_by_sign"]
    cases = s["up"]["cases"] + s["down"]["cases"]
    assert (f"{cases} des {cases} cas" if lang == "fr" else f"{cases} of {cases} cases") in six
    # a miss on either year's power table, or on either year's level shift, and silence is no proof
    for ctx in (limited(every_from=None), limited(every_from=0.04), limited(up_missed=1), limited(up_missed24=2),
                limited(pseudo_every_from=None), limited(pseudo_every_from=0.04)):
        six = beat(render(ctx, lang), 6)
        assert no_proof in six and all_caught not in six
    six = beat(render(limited(every_from=None), lang), 6)
    assert largest + pct(context()["limits"]["replay"]["sizes"][-1], 0, lang) in six


# --- the honesty ledger: no departure, a departure that moved the result, one that did not --------------------------

POST_HOC = {"fr": "**a posteriori**", "en": "**post hoc**"}
# the departure clause says this replay ran again after the change, so the numbers that follow it are the rerun's
RERUN = {"fr": "ce rejeu a été refait après ce changement : il est **a posteriori**.",
         "en": "this replay ran again after that change: it is **post hoc**."}
LEDGER = {"fr": ("Le résultat de 2024 n'a pas bougé", "Le résultat de 2024 a bougé",
                 "aucune dérogation ne les a changées depuis", "ont appliqué le même modèle figé",
                 "Le modèle figé diffère d'un rejeu à l'autre", "Les rejeux dont l'empreinte est consignée"),
          "en": ("The 2024 result did not move", "The 2024 result moved",
                 "no departure has changed them since", "applied the same frozen model",
                 "The frozen model differs from one replay to another", "The replays whose hash is recorded")}


@pytest.mark.parametrize("lang", LANGS)
def test_a_plain_replay_says_the_rules_did_not_change(lang):
    text = render(context(), lang)
    same, moved, plain, model, *_ = LEDGER[lang]
    seven = beat(text, 7)
    assert plain in seven and "`rules-frozen`" in seven and f"`{'f' * 12}`" in seven
    assert same not in seven and moved not in seven and model not in seven
    assert POST_HOC[lang] not in text and ("a posteriori" if lang == "fr" else "post hoc") not in text
    assert ("refait" if lang == "fr" else "ran again") not in text


def matching(recorded=False, **summary):
    """A departure whose printed headline matches the 2024 replay's (or differs where `summary` says)."""
    ctx = context()
    r = ctx["r"]["summary"]
    recs = [{"week_start": str(d["week_start"])[:10], "recommendation": d["recommendation"]}
            for d in ctx["r"]["decisions"] if d["recommendation"]]
    d = {**R.EARLIER, "summary": {"mape": {"mean": r["mape"]["mean"] + 1e-6}, "coverage": {"mean": r["coverage"]["mean"]},
                                  "recommendations": {}}, "recommendations": recs}
    d["summary"].update(summary)
    if recorded:
        d.update(recorded=True, **R.NOT_IN_LEDGER)
    return d


@pytest.mark.parametrize("lang", LANGS)
def test_a_departure_whose_printed_headline_held_says_it_did_not_move(lang):
    same, moved, plain, model, *_ = LEDGER[lang]
    ctx = context((matching(),))
    text = render(ctx, lang)
    seven = beat(text, 7)
    assert same in seven and moved not in seven and plain not in seven and model in seven
    assert pct(ctx["r"]["summary"]["mape"]["mean"], 2, lang) in seven
    assert ("mêmes recommandations" if lang == "fr" else "the same recommendations") in seven
    assert POST_HOC[lang] in beat(text, 5) and ("2031-02-03" not in seven)
    five = beat(text, 5)
    assert RERUN[lang] in five
    assert five.index(RERUN[lang]) < five.index(pct(ctx["r"]["summary"]["mape"]["mean"], 2, lang))
    assert ("le " if lang == "fr" else "on ") + RV.DEPARTURE["date"] in seven
    # no recommendation either side
    ctx = context((matching(),))
    for d in ctx["r"]["decisions"]:
        d["recommendation"] = None
    ctx["departures"][0]["recommendations"] = []
    seven = beat(render(ctx, lang), 7)
    assert same in seven and ("aucune recommandation, avant comme après" if lang == "fr" else
                              "no recommendation, before and after") in seven


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("change", ["mape", "coverage", "recommendations"])
def test_a_departure_that_moved_the_printed_headline_shows_both(lang, change):
    same, moved, *_ = LEDGER[lang]
    d = matching()
    if change == "mape":
        d["summary"]["mape"] = {"mean": d["summary"]["mape"]["mean"] + 0.0002}
    elif change == "coverage":
        d["summary"]["coverage"] = {"mean": d["summary"]["coverage"]["mean"] + 0.002}
    else:
        d["recommendations"] = [{"week_start": "2024-06-17", "recommendation": "recalibrate"}]
    ctx = context((d,))
    seven = beat(render(ctx, lang), 7)
    assert moved in seven and same not in seven
    before, after = seven.split("après, " if lang == "fr" else "after, ")[-2:]
    assert pct(d["summary"]["mape"]["mean"], 2, lang) in before
    assert pct(ctx["r"]["summary"]["mape"]["mean"], 2, lang) in after
    assert pct(d["summary"]["coverage"]["mean"], 1, lang) in before
    if change == "recommendations":
        assert ("2024-06-17 : recalibrer" if lang == "fr" else "2024-06-17: recalibrate") in before


@pytest.mark.parametrize("lang", LANGS)
def test_the_frozen_model_is_said_only_from_the_hashes_recorded(lang):
    same, moved, plain, model, differs, partly = LEDGER[lang]
    seven = beat(render(context((matching(recorded=True),)), lang), 7)
    assert model not in seven and differs not in seven and same in seven
    seven = beat(render(context(({**matching(), "model_sha256": "9" * 64},)), lang), 7)
    assert differs in seven and model not in seven
    seven = beat(render(context((matching(recorded=True), matching())), lang), 7)
    assert partly + (" ont appliqué" if lang == "fr" else " applied") in seven


# --- written by render_validation, listed in the stamp --------------------------------------------------------------

def test_render_validation_writes_the_story_and_the_stamp_lists_it(tmp_path, monkeypatch):
    """The run chain, the data-quality render and the figures are stubbed; the templates are the real ones."""
    from types import SimpleNamespace

    import valiwatt.monitor.rules as rules
    import valiwatt.monitor.run as monitor_run
    import valiwatt.tracking as tracking
    from valiwatt import attribution
    from valiwatt.report import ledger

    out = tmp_path / "report"
    out.mkdir()
    monkeypatch.setattr(RV, "OUT", out)
    monkeypatch.setattr(RV, "STAMP", out / "stamp.json")
    monkeypatch.setattr(RV, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(ledger, "LEDGER", tmp_path / "replay-ledger.json")
    monkeypatch.setattr(tracking, "code_sha256", lambda: "c" * 64)
    monkeypatch.setattr(rules, "rules_sha256", lambda: "d" * 64)
    monkeypatch.setattr(monitor_run, "check_freeze", lambda: {"freeze_commit": "f" * 40})

    def run(run_id):
        return SimpleNamespace(info=SimpleNamespace(run_id=run_id))

    chain = SimpleNamespace(dq=run("a" * 32), replay=run("0" * 32), freeze={"freeze_commit": "f" * 40})
    monkeypatch.setattr(RV, "follow", lambda code, rules_sha, freeze: chain)
    monkeypatch.setattr(RV, "render", lambda run_id: [])
    ctx = context((R.EARLIER,))
    monkeypatch.setattr(RV, "context", lambda chain_, code, rules_sha: ctx)
    monkeypatch.setattr(RV, "figures", lambda ctx_, lang: R.FIG)

    written = RV.render_validation()
    assert [p.name for p in written] == ["validation.fr.md", "story.fr.md", "validation.en.md", "story.en.md"]
    stamp = json.loads((out / "stamp.json").read_text(encoding="utf-8"))
    retrieved = min(s["first_retrieved_utc"] for s in ctx["dq"]["sources"]["eccc"])[:10]
    for lang in LANGS:
        body = (out / f"story.{lang}.md").read_bytes()
        assert stamp["files"][f"story.{lang}.md"] == hashlib.sha256(body).hexdigest()
        want = environment(lang).get_template(f"story.{lang}.md.j2").render(
            **ctx, fig=R.FIG, lang=lang, attribution=attribution.lines(lang, retrieved=retrieved))
        assert body.decode("utf-8") == want
        for line in attribution.lines(lang, retrieved=retrieved):
            assert line in want
    assert set(stamp["files"]) == {f"{doc}.{lang}.md" for doc in ("validation", "story") for lang in LANGS}


def test_the_readme_points_to_the_story_first():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    story, full = readme.index("docs/report/story.fr.md"), readme.index("docs/report/validation.fr.md")
    assert story < full and "docs/report/story.en.md" in readme
