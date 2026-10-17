"""The report's wording, each sentence true on any data its branches allow: hour blocks in the conditional-coverage
judgement, the coverage period with both ends, the power horizon in the In brief, the one-day alarms counted per year,
false "recalibrate" in the limits, the level-shift months of the missed starts only, the standards table's scope, the
data-quality report's shared gaps, tense and forecast section, and the break labels. Rendered from the synthetic v2 contexts
(tests/report_payloads.py)."""

import copy

import pytest
import report_payloads as RP

from valiwatt.breaks import BREAKS
from valiwatt.report import validation as RV
from valiwatt.report.render import ReportRefused, environment, fmt_num

LANGS = ("fr", "en")
NBSP = " "
NNBSP = " "
BLOCKS = ("night", "morning", "day", "evening", "late")
LABEL = {"fr": {"night": "nuit (0–5 h)", "morning": "matin (6–9 h)", "day": "jour (10–15 h)",
                "evening": "soir (16–20 h)", "late": "fin de soirée (21–23 h)"},
         "en": {"night": "night (0–5 h)", "morning": "morning (6–9 h)", "day": "day (10–15 h)",
                "evening": "evening (16–20 h)", "late": "late evening (21–23 h)"}}
PROD = "gam_forecast"


def macros(lang):
    return environment(lang).get_template(f"sentences.{lang}.md.j2").module


def line_of(text: str, start: str) -> str:
    return next(x for x in text.splitlines() if x.startswith(start))


def cov(key, low, high):
    return {"key": key, "covered": (low + high) / 2, "covered_low": low, "covered_high": high}


def context(**override) -> dict:
    ctx = copy.deepcopy(RP.context())
    ctx.update(override)
    return ctx


def section(text: str, number: int) -> str:
    return text.split(f"\n## {number}. ", 1)[1].split("\n## ", 1)[0]


def pct(x, d, lang):
    return fmt_num(100 * x, d, lang) + (NBSP + "%" if lang == "fr" else "%")


# --- 1. hour blocks in the conditional-coverage judgement -----------------------------------------------------------

def test_derive_judges_the_hour_blocks_as_printed_in_the_tables_order():
    """The run lists the blocks in any order; derive judges the production intervals' blocks on their printed
    bounds, in the configured order."""
    v, dq, p, r, b, frozen, oos, diag = RP.payloads()
    v["coverage"][PROD]["relative"]["hour_group"] = [
        cov("late", 0.80, 0.8994),    # prints 89.9: under
        cov("day", 0.9006, 0.95),     # prints 90.1: over
        cov("night", 0.80, 0.8996),   # prints 90.0: holds the nominal
        cov("evening", 0.911, 0.944),  # over
        cov("morning", 0.85, 0.8949)]  # under
    sides = RV.derive(v, dq, p, r, b, frozen, oos, diag)["sides"]
    assert sides["hour_group"] == {"under": ["morning", "late"], "over": ["day", "evening"]}


def test_hour_block_coverage_rows_unlike_the_configured_blocks_are_refused():
    blocks = {k: [0, 1] for k in BLOCKS}
    rows = [cov(k, 0.85, 0.95) for k in BLOCKS]
    assert [x["key"] for x in RV.hour_blocks(rows[::-1], blocks)] == list(BLOCKS)
    for bad in (rows[:-1], [*rows, cov("dawn", 0.85, 0.95)], [*rows[:-1], rows[0]], [*rows, rows[0]]):
        with pytest.raises(ReportRefused, match="hour-block coverage rows"):
            RV.hour_blocks(bad, blocks)


SIDES = {"fr": ("Sous-couverts", "Sur-couverts", "Aucune saison, aucun bloc horaire ni aucun décile de charge",
                "Aucun groupe (saison, bloc horaire ou décile de charge)"),
         "en": ("Under-covered", "Over-covered", "No season, no hour block and no load decile",
                "No group (season, hour block or load decile)")}


@pytest.mark.parametrize("lang", LANGS)
def test_the_coverage_sentence_puts_each_hour_block_on_its_side(lang):
    """Hour blocks under, over or neither, with seasons and deciles present or not: each list names exactly its
    groups, as the tables name them, and a fallback is said only when no group of any table is on that side."""
    m, fr = macros(lang), lang == "fr"
    under_w, over_w, no_under, no_over = SIDES[lang]
    none = {"under": [], "over": []}
    labels = LABEL[lang]
    for hu, ho in ((["night"], []), ([], ["day", "evening"]), ([], []), (["night", "late"], ["day"])):
        for season, decile in ((none, none), ({"under": ["winter"], "over": ["summer"]}, {"under": [], "over": ["1"]})):
            hour = {"under": [labels[k] for k in hu], "over": [labels[k] for k in ho]}
            text = str(m.coverage_sides(season, decile, 0.9, hour))
            first, second = text.split(". ", 1)
            assert "None" not in text and "\n" not in text and text.endswith(".")
            assert first.startswith(under_w if (hu or season["under"]) else no_under)
            assert second.startswith(over_w if (ho or season["over"] or decile["over"]) else no_over)
            for k in BLOCKS:
                assert (labels[k] in first) == (k in hu) and (labels[k] in second) == (k in ho)
            if len(ho) == 2:
                assert (f"les blocs horaires {labels['day']} et {labels['evening']}" if fr else
                        f"hour blocks {labels['day']} and {labels['evening']}") in second
            if len(hu) == 1:
                assert (f"le bloc horaire {labels['night']}" if fr else f"hour block {labels['night']}") in first


@pytest.mark.parametrize("lang", LANGS)
def test_section_5_the_brief_and_the_limits_carry_the_hour_blocks(lang):
    """No group under-covered, two hour blocks over-covered: section 5 names them; the In brief and section 10 say
    that no hour block is under-covered either. Then a block under-covered: all three name it."""
    fr = lang == "fr"
    ctx = context()
    none = {"under": [], "over": []}
    real = {**ctx["sides"], "season": {"under": [], "over": ["summer"]}, "decile": none,
            "hour_group": {"under": [], "over": ["day", "evening"]}}
    text = RP.render({**ctx, "sides": real}, lang)
    s5 = section(text, 5)
    assert ("Sur-couverts, l'IC entièrement au-dessus : l'été ; les blocs horaires jour (10–15 h) et soir (16–20 h)."
            if fr else "Over-covered, the CI wholly above: summer; hour blocks day (10–15 h) and evening (16–20 h).") \
        in s5
    assert (SIDES[lang][2] + " n'a son IC 95") in s5 if fr else (SIDES[lang][2] + " has its 95% CI") in s5
    brief = line_of(text, "- " + ("Ses intervalles" if fr else "Its "))
    limit = line_of(section(text, 10), "- **" + ("Couverture" if fr else "Conditional"))
    under_words = ("dans ces groupes" if fr else "in these groups")
    assert under_words not in brief and under_words not in limit
    assert ("Aucune saison, aucun bloc horaire ni aucun décile n'est sous-couvert" if fr else
            "No season, no hour block and no decile is under-covered") in limit

    under = {**real, "hour_group": {"under": ["night"], "over": ["day"]}}
    text = RP.render({**ctx, "sides": under}, lang)
    named = "le bloc horaire nuit (0–5 h)" if fr else "hour block night (0–5 h)"
    s5 = section(text, 5)
    assert ("Sous-couverts, leur IC 95" if fr else "Under-covered, their 95% CI") in s5
    assert s5.count(named) == 1 and ("le bloc horaire jour (10–15 h)" if fr else "hour block day (10–15 h)") in s5
    brief = line_of(text, "- " + ("Ses intervalles" if fr else "Its "))
    assert (f"dans ces groupes : {named} (section 5)." if fr else f"in these groups: {named} (section 5).") in brief
    limit = line_of(section(text, 10), "- **" + ("Couverture" if fr else "Conditional"))
    assert (f"L'intervalle de v2 sous-couvre {named} (section 5)." if fr else
            f"v2's interval under-covers {named} (section 5).") in limit


# --- 7. the coverage period, both ends; 8. the power horizon in the In brief -----------------------------------------

@pytest.mark.parametrize("lang", LANGS)
def test_the_coverage_period_is_named_with_both_ends(lang):
    fr = lang == "fr"
    ctx = context()
    ctx["v"]["conformal"]["first_fold"], ctx["v"]["folds"]["last"] = "2031-04", "2034-09"
    text = RP.render(ctx, lang)
    period = "de 2031-04 à 2034-09" if fr else "from 2031-04 to 2034-09"
    assert f"du temps {period}," in line_of(text, "- Ses intervalles") if fr else \
        f"of the time {period}," in line_of(text, "- Its ")
    assert (f"Nominal 90{NBSP}%, {period}" if fr else f"Nominal 90%, {period}") in section(text, 5)
    assert "depuis 2031-04" not in text and "2031-04 on" not in text


@pytest.mark.parametrize("lang", LANGS)
def test_the_in_brief_gives_the_power_measures_own_horizon(lang):
    ctx = context()
    K = ctx["K"] = {**ctx["K"], "power_horizon": 17, "step_horizon": 19}
    line = line_of(RP.render(ctx, lang), "- " + ("Le rapport mesure" if lang == "fr" else "The report also"))
    rise, gap = line.split(f" ; sur {RP.R_YEAR}, un écart" if lang == "fr" else f"; on {RP.R_YEAR}, a sustained gap")
    assert f" {K['step_horizon']} " in rise and f" {K['power_horizon']} " not in rise
    assert (f"l'a donné en {K['power_horizon']} semaines à" if lang == "fr" else
            f"did within {K['power_horizon']} weeks at") in gap
    assert f" {K['step_horizon']} " not in gap


# --- 3. the one-day alarms, counted per year ------------------------------------------------------------------------

@pytest.mark.parametrize("lang", LANGS)
def test_the_one_day_alarms_are_counted_per_year_and_the_clean_split_is_scoped(lang):
    fr = lang == "fr"
    m = macros(lang)
    a = {"retrain": 3, "retrain_clean": 1, "recalibrate": 14, "cases": 1400}
    b = {"retrain": 0, "retrain_clean": 0, "recalibrate": 2, "cases": 1460}
    one = str(m.one_day_alarms([(2024, a)]))
    assert one.startswith(f"**Alertes des chocs d'un seul jour** : sur 2024, 3 « réentraîner » (dont 1 sans aide) et "
                          f"14 « recalibrer », sur 1{NNBSP}400 cas (départs × facteurs)." if fr else
                          "**Alarms from one-day shocks**: on 2024, 3 \"retrain\" (1 of them clean) and 14 "
                          "\"recalibrate\", out of 1,400 cases (starts × factors).")
    assert ("la part sans aide n'est mesurée que pour « réentraîner »" if fr else
            "the clean share is measured for \"retrain\" only") in one
    two = str(m.one_day_alarms([(2023, b), (2024, a)]))
    assert (f"sur 2023, 0 « réentraîner » (dont 0 sans aide) et 2 « recalibrer », sur 1{NNBSP}460 cas (départs × "
            "facteurs) ; sur 2024, 3" if fr else
            "on 2023, 0 \"retrain\" (0 of them clean) and 2 \"recalibrate\", out of 1,460 cases (starts × factors); "
            "on 2024, 3") in two
    import jinja2

    with pytest.raises(jinja2.UndefinedError):  # never called with no year: the template says there were none
        str(m.one_day_alarms([]))


@pytest.mark.parametrize("lang", LANGS)
def test_each_replay_section_reads_the_one_day_counts_from_its_own_years_rows(lang):
    """Through derive: the 2024 diagnostics' one-day rows with alarms, 2023's with none; section 8 gives 2024's
    counts, section 7 says 2023 gave none."""
    v, dq, p, r, b, frozen, oos, diag = RP.payloads()
    diag = copy.deepcopy(diag)
    rows = [x for x in diag["replay"]["sensitivity"]["episodes"]["rows"] if x["days"] == 1]
    for row in rows:
        row.update(retrain=2, retrain_clean=1, recalibrate=row["starts"] % 7)
    for row in diag["pseudo"]["sensitivity"]["episodes"]["rows"]:
        if row["days"] == 1:
            row.update(retrain=0, retrain_clean=0, recalibrate=0)
    derived = RV.derive(v, dq, p, r, b, frozen, oos, diag)
    one = derived["episode_limits"]["replay"]["one_day"]
    assert one == {"retrain": 2 * len(rows), "retrain_clean": len(rows),
                   "recalibrate": sum(x["starts"] % 7 for x in rows), "cases": sum(x["starts"] for x in rows)}
    assert derived["episode_limits"]["pseudo"]["one_day_alarms"] == 0
    text = RP.render({**context(), **derived, "diag": diag}, lang)
    loud = ((f"**Alertes des chocs d'un seul jour** : sur {RP.R_YEAR}, {2 * len(rows)} « réentraîner » (dont "
             f"{len(rows)} sans aide) et {one['recalibrate']} « recalibrer »") if lang == "fr" else
            (f"**Alarms from one-day shocks**: on {RP.R_YEAR}, {2 * len(rows)} \"retrain\" ({len(rows)} of them "
             f"clean) and {one['recalibrate']} \"recalibrate\""))
    assert loud in section(text, 8) and loud not in section(text, 7)
    assert (f"Aucun choc d'un seul jour n'a donné d'alerte sur {RP.P_YEAR}" if lang == "fr" else
            f"No one-day shock gave an alarm on {RP.P_YEAR}") in section(text, 7)


# --- 4. false "recalibrate" in the limits ---------------------------------------------------------------------------

@pytest.mark.parametrize("lang", LANGS)
def test_the_episode_limit_gives_each_years_false_recalibrate(lang):
    fr = lang == "fr"
    ctx = context()
    E23, E24 = ctx["episode_limits"]["pseudo"], ctx["episode_limits"]["replay"]
    assert E23["recal_worst"] and E24["recal_worst"] and E23["recal_share"] != E24["recal_share"]
    line = line_of(section(RP.render(ctx, lang), 10), "- **" + ("Épisodes" if fr else "Episodes"))
    one, two = line.split((" ; sur " if fr else "; on ") + str(RP.R_YEAR))
    for E, part, other in ((E23, one, E24), (E24, two, E23)):
        w = E["recal_worst"]
        assert (f"« recalibrer » jusqu'à {pct(E['recal_share'], 1, lang)} des départs ({w['days']} jours à × "
                if fr else f"\"recalibrate\" at up to {pct(E['recal_share'], 1, lang)} of starts ({w['days']} days "
                "at × ") in part
        assert pct(other["recal_share"], 1, lang) not in part
    assert ("peut donner une fausse alerte sans que le modèle soit en cause" if fr else
            "can give a false alarm with nothing wrong in the model") in line
    quiet = {k: {**x, "worst": None, "recal_worst": None} for k, x in ctx["episode_limits"].items()}
    line = line_of(section(RP.render({**ctx, "episode_limits": quiet}, lang), 10),
                   "- **" + ("Épisodes" if fr else "Episodes"))
    assert line.count("aucun « réentraîner », aucun « recalibrer »" if fr else
                      "no \"retrain\", no \"recalibrate\"") == 2
    assert ("Un épisode n'est pas une défaillance du modèle" if fr else "An episode is not a model failure") in line
    only_recal = {**quiet, "replay": {**quiet["replay"], "recal_worst": E24["recal_worst"],
                                      "recal_share": E24["recal_share"]}}
    line = line_of(section(RP.render({**ctx, "episode_limits": only_recal}, lang), 10),
                   "- **" + ("Épisodes" if fr else "Episodes"))
    assert ("peut donner une fausse alerte" if fr else "can give a false alarm") in line


# --- 5. the level-shift months: the missed starts only --------------------------------------------------------------

def test_level_shift_names_the_months_of_the_missed_starts():
    rows = [{"start": f"2024-{m:02d}-01", "shift": s, "weeks": w}
            for m, w in ((1, None), (4, 10), (7, None), (10, None)) for s in (0.1,)]
    rows += [{"start": "2024-01-01", "shift": -0.1, "weeks": 3}]
    L = RV.level_shift({"sensitivity": {"level_shifts": rows}}, 0.1)
    assert L["months"] == [1, 4, 7, 10] and L["missed_months"] == [1, 7, 10] and L["up_missed"] == 3


@pytest.mark.parametrize("lang", LANGS)
def test_the_level_limit_names_only_the_missed_starts(lang):
    fr = lang == "fr"
    ctx = context()
    lv, lv24 = ctx["level"], ctx["level24"]
    assert lv["missed_months"] == [1, 3, 6] and lv24["missed_months"] == [1] and lv["months"] == [1, 3, 6, 9]
    text = RP.render(ctx, lang)
    level = line_of(section(text, 10), "- **" + ("Changement" if fr else "Level shift"))
    assert ("à 3 départs sur 4 (départs de janvier, mars et juin)" if fr else
            "at 3 of 4 starts (the January, March and June starts)") in level
    assert ("à 1 départ sur 4 (départ de janvier)" if fr else "at 1 of 4 starts (the January start)") in level
    assert ("septembre" not in level) if fr else ("September" not in level)
    # The paragraphs that name the starts tested still name all four.
    assert ("première semaine jugée de janvier, mars, juin et septembre" if fr else
            "first judged week of January, March, June and September") in section(text, 7)


# --- 12, 13. the standards table's scope ----------------------------------------------------------------------------

@pytest.mark.parametrize("lang", LANGS)
def test_the_standards_table_claims_no_production_monitoring_and_names_overrides_apart_from_departures(lang):
    fr = lang == "fr"
    s12 = section(RP.render(context(), lang), 12)
    row = line_of(s12, "| NIST MEASURE")
    assert row.startswith("| NIST MEASURE 1.1, 2.1, 2.3, 2.5, 2.13 |") and "2.4" not in row
    gap = line_of(s12, "**Non couvert.**" if fr else "**Not covered.**")
    assert ("NIST MEASURE 2.4 (surveillance en production : simulée par le rejeu seulement)" if fr else
            "NIST MEASURE 2.4 (monitoring in production: simulated by the replay only)") in gap
    assert ("analyse des forçages (overrides) des résultats du modèle" if fr else
            "analysis of overrides of the model's outputs") in gap
    assert "dérogation" not in gap
    third = line_of(s12, "| SR 11-7, " + ("données et modèles de tiers" if fr else "third-party data and models"))
    assert "NOAA" in third


# --- 2, 9, 11. the data-quality report ------------------------------------------------------------------------------

def shared_gaps_block(lang: str) -> str:
    env = environment(lang)
    src = env.loader.get_source(env, f"dq.{lang}.md.j2")[0]
    start = src.index("{% set sg = c['weather_shared_gaps'] %}")
    return src[start:src.index("{% endif %}", src.index("{% else %}", start)) + len("{% endif %}")]


@pytest.mark.parametrize("lang", LANGS)
def test_the_shared_gaps_sentence_says_only_what_the_check_counted(lang):
    fr = lang == "fr"
    env = environment(lang)
    block = shared_gaps_block(lang)
    gap = {"start": "2022-04-22T04:00:00+00:00", "end": "2022-04-23T00:00:00+00:00", "hours": 21}
    for all_missing in (0, 11):  # not the real run's 9 hours, so a count typed by hand cannot pass
        c = {"weather_shared_gaps": {"metrics": {"hours_half_or_more_missing": 23, "hours_all_missing": all_missing},
                                     "evidence": [gap]},
             "weather_panel_coverage": {"metrics": {"stations": 7}}}
        text = env.from_string(block).render(c=c)
        said = ("Pendant ces 11 heures, aucune des 7 stations retenues ne répondait ; d'autres stations n'ont pas été "
                "vérifiées." if fr else "During those 11 hours, none of the 7 configured stations reported; other "
                "stations were not checked.")
        assert (said in text) == bool(all_missing)
        assert "réseau" not in text and "network" not in text and "aurait" not in text and "would" not in text


@pytest.mark.parametrize("lang", LANGS)
def test_the_data_quality_report_speaks_of_the_forecast_in_the_present(lang):
    env = environment(lang)
    src = env.loader.get_source(env, f"dq.{lang}.md.j2")[0]
    stale = (("sera rejouée", "aucune prévision n'est encore faite", "devront", "devra", "subira", "n'aurait qu'une")
             if lang == "fr" else ("will be replayed", "no forecast has been made", "will have to", "will carry",
                                   "would only have"))
    assert not [w for w in stale if w in src]
    for said in (("L'année suivante est rejouée heure par heure", "c'est le rapport de validation qui la mesure",
                  "le rapport de validation le dit de ses résultats", "c'est ce que la surveillance doit détecter "
                  "(rapport de validation)", "**Les heures les plus chargées.** Les {{ rep.top_hours|length }}")
                 if lang == "fr" else
                 ("The following year is replayed hour by hour",
                  "It measures no demand forecast's quality: the validation report does.",
                  "the validation report says so of its results", "that is what monitoring has to "
                  "detect (validation report)", "**The busiest hours.** The {{ rep.top_hours|length }}")):
        assert said in src, said


@pytest.mark.parametrize("lang", LANGS)
def test_the_temperature_forecast_is_v2_s_input_and_its_feeding_rule_reads_plainly(lang):
    """v2 forecasts demand, not temperature: the NBM guidance is the temperature forecast it uses (data-quality
    section 4.1). The feeding rule picks, among the freshest runs available at the origin, the freshest whose points
    bracket the day, in both reports."""
    env = environment(lang)
    dq = env.loader.get_source(env, f"dq.{lang}.md.j2")[0]
    val = env.loader.get_source(env, f"validation.{lang}.md.j2")[0]
    fr = lang == "fr"
    said = (((dq, "La prévision de température que v2 utilise est la guidance NBS du National Blend of Models de la "
                  "NOAA"),
             (dq, "Chaque jour est alimenté par la plus fraîche des prévisions dont les propres points encadrent ses "
                  "24 heures, parmi les {{ max_runs_back }} plus fraîches disponibles à l'origine du jour ;"),
             (val, "Pour le jour J, parmi les {{ K.max_runs_back }} prévisions les plus fraîches disponibles à "
                   "l'origine de J (J {{ K.origin_utc }} UTC), on prend la plus fraîche dont les propres points "
                   "encadrent chacune des 24 heures de J,"))
            if fr else
            ((dq, "The temperature forecast v2 uses is NOAA's National Blend of Models NBS guidance"),
             (dq, "Each day is fed by the freshest forecast whose own points bracket its 24 hours, among the "
                  "{{ max_runs_back }} freshest available at the day's origin;"),
             (val, "For day D, among the {{ K.max_runs_back }} freshest forecasts available at D's origin (D "
                   "{{ K.origin_utc }} UTC), the freshest whose own points bracket each of D's 24 hours is taken,")))
    for src, s in said:
        assert s in src, s
    wrong = ("La température que v2 prévoit", "la plus fraîche des {{") if fr else \
        ("The temperature v2 forecasts", "the freshest of the {{")
    assert not [w for w in wrong for src in (dq, val) if w in src]


# --- 10, 11. the break labels ---------------------------------------------------------------------------------------

def test_no_two_breaks_share_a_label():
    """Section 9 prints one row per break: two breaks with one label read as one break printed twice."""
    for lang in LANGS:
        labels = [getattr(x, lang) for x in BREAKS]
        assert len(set(labels)) == len(labels), [x for x in labels if labels.count(x) > 1]
    spring = {x.test[-4:]: x for x in BREAKS if "test_no_spring_verdict_contradicts" in x.test}
    assert spring["[fr]"].fr.endswith(", rapport français") and spring["[fr]"].en.endswith(", French report")
    assert spring["[en]"].fr.endswith(", rapport anglais") and spring["[en]"].en.endswith(", English report")


def test_the_off_grid_break_says_the_same_in_both_languages():
    """The break moves a demand record off the hourly grid (tests/test_replay.py): both labels say a demand record."""
    x = next(b for b in BREAKS if b.test.endswith("::test_duplicated_temperatures_and_an_off_grid_demand_record_fail_"
                                                   "their_day"))
    assert "enregistrement de demande hors grille" in x.fr and "off-grid demand record" in x.en
