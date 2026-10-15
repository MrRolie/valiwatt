"""The report's wording after the final fresh-context review, each sentence true on any data its branches allow: hour
blocks in the conditional-coverage judgement, the coverage period with both ends, the power horizon in the In brief,
the one-day alarms counted per year, false "recalibrate" in the limits, the level-shift months of the missed starts
only, the replays side by side with their model and flagged weeks, section 10's scope, the data-quality report's
shared gaps and tense, and the break labels."""

import itertools

import pytest
import test_report_render as R

from valiwatt.breaks import BREAKS
from valiwatt.report import validation as RV
from valiwatt.report.lineage import RecordedReplay
from valiwatt.report.render import ReportRefused, environment

LANGS = ("fr", "en")
NBSP = " "
BLOCKS = ("night", "morning", "day", "evening", "late")
LABEL = {"fr": {"night": "nuit (0–5 h)", "morning": "matin (6–9 h)", "day": "jour (10–15 h)",
                "evening": "soir (16–20 h)", "late": "fin de soirée (21–23 h)"},
         "en": {"night": "night (0–5 h)", "morning": "morning (6–9 h)", "day": "day (10–15 h)",
                "evening": "evening (16–20 h)", "late": "late evening (21–23 h)"}}


def macros(lang):
    return environment(lang).get_template(f"sentences.{lang}.md.j2").module


def line_of(text: str, start: str) -> str:
    return next(x for x in text.splitlines() if x.startswith(start))


def cov(key, low, high):
    return {"key": key, "covered": (low + high) / 2, "covered_low": low, "covered_high": high}


# --- 1. hour blocks in the conditional-coverage judgement -----------------------------------------------------------

def test_derive_judges_the_hour_blocks_as_printed_in_the_tables_order():
    """The run lists the blocks in any order; derive judges them on their printed bounds, in the configured order."""
    v, dq, p, r, b, frozen = R.payloads()
    v["coverage"]["relative"]["hour_group"] = [
        cov("late", 0.80, 0.8994),    # prints 89.9: under
        cov("day", 0.9006, 0.95),     # prints 90.1: over
        cov("night", 0.80, 0.8996),   # prints 90.0: holds the nominal
        cov("evening", 0.911, 0.944),  # over
        cov("morning", 0.85, 0.8949)]  # under
    sides = RV.derive(v, dq, p, r, b, frozen, R.oos_frame())["sides"]
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
def test_section_4_the_brief_and_the_limits_carry_the_hour_blocks(lang):
    """As on the real runs: no group under-covered, two hour blocks over-covered. Section 4 names them; the In brief
    and section 8 say that no hour block is under-covered either. Then a block under-covered: all three name it."""
    fr = lang == "fr"
    ctx = R.context()
    none = {"under": [], "over": []}
    real = {**ctx["sides"], "season": {"under": [], "over": ["summer"]}, "decile": none,
            "hour_group": {"under": [], "over": ["day", "evening"]}}
    text = R.render({**ctx, "sides": real}, lang)
    s4 = R.section(text, 4)
    over = (f"Sur-couverts, l'IC entièrement au-dessus : l'été ; les blocs horaires jour (10–15 h) et soir (16–20 h)."
            if fr else "Over-covered, the CI wholly above: summer; hour blocks day (10–15 h) and evening (16–20 h).")
    assert over in s4
    assert (SIDES[lang][2] + " n'a son IC 95") in s4 if fr else (SIDES[lang][2] + " has its 95% CI") in s4
    brief = line_of(text, "- " + ("Ses intervalles" if fr else "Its "))
    assert brief.endswith("Aucune saison ni aucun décile de charge n'est sous-couvert au sens de la section 4, ni aucun "
                          "bloc horaire." if fr else "No season and no load decile, nor any hour block, is under-covered "
                          "in the sense of section 4.")
    limit = line_of(R.section(text, 8), "- **" + ("Couverture" if fr else "Conditional"))
    assert ("Aucune saison, aucun bloc horaire ni aucun décile n'est sous-couvert" if fr else
            "No season, no hour block and no decile is under-covered") in limit

    under = {**real, "hour_group": {"under": ["night"], "over": ["day"]}}
    text = R.render({**ctx, "sides": under}, lang)
    named = "le bloc horaire nuit (0–5 h)" if fr else "hour block night (0–5 h)"
    s4 = R.section(text, 4)
    assert ("Sous-couverts, leur IC 95" if fr else "Under-covered, their 95% CI") in s4
    assert s4.count(named) == 1 and ("le bloc horaire jour (10–15 h)" if fr else "hour block day (10–15 h)") in s4
    brief = line_of(text, "- " + ("Ses intervalles" if fr else "Its "))
    assert (f"dans ces groupes : {named} (section 4)." if fr else f"in these groups: {named} (section 4).") in brief
    limit = line_of(R.section(text, 8), "- **" + ("Couverture" if fr else "Conditional"))
    assert (f"L'intervalle de production sous-couvre {named} (section 4)." if fr else
            f"The production interval under-covers {named} (section 4).") in limit


# --- 7. the coverage period, both ends; 8. the power horizon in the In brief -----------------------------------------

@pytest.mark.parametrize("lang", LANGS)
def test_the_coverage_period_is_named_with_both_ends(lang):
    fr = lang == "fr"
    ctx = R.context()
    ctx["v"]["conformal"]["first_fold"], ctx["v"]["folds"]["last"] = "2031-04", "2034-09"
    text = R.render(ctx, lang)
    period = "de 2031-04 à 2034-09" if fr else "from 2031-04 to 2034-09"
    assert f"du temps {period}," in line_of(text, "- Ses intervalles") if fr else \
        f"of the time {period}," in line_of(text, "- Its ")
    assert (f"Nominal 90{NBSP}%, {period}." if fr else f"Nominal 90%, {period}.") in R.section(text, 4)
    assert "depuis 2031-04" not in text and "2031-04 on" not in text


@pytest.mark.parametrize("lang", LANGS)
def test_the_in_brief_gives_the_power_measures_own_horizon(lang):
    ctx = R.context()
    K = ctx["K"]
    assert K["power_horizon"] != K["step_horizon"]
    line = line_of(R.render(ctx, lang), "- " + ("Le rapport mesure" if lang == "fr" else "The report also"))
    rise, gap = line.split(" ; sur 2024, un écart" if lang == "fr" else "; on 2024, a sustained gap")
    assert f" {K['step_horizon']} " in rise and f" {K['power_horizon']} " not in rise
    assert (f"l'a donné en {K['power_horizon']} semaines à" if lang == "fr" else
            f"yielded \"retrain\" within {K['power_horizon']} weeks at") in gap
    assert f" {K['step_horizon']} " not in gap


# --- 3. the one-day alarms, counted per year ------------------------------------------------------------------------

@pytest.mark.parametrize("lang", LANGS)
def test_the_one_day_alarms_are_counted_per_year_and_the_clean_split_is_scoped(lang):
    fr = lang == "fr"
    m = macros(lang)
    a = {"retrain": 3, "retrain_clean": 1, "recalibrate": 14, "cases": 1400}
    b = {"retrain": 0, "retrain_clean": 0, "recalibrate": 2, "cases": 1460}
    one = str(m.one_day_alarms([(2024, a)]))
    assert one.startswith("**Alertes des chocs d'un seul jour** : sur 2024, 3 « réentraîner » (dont 1 sans aide) et "
                          "14 « recalibrer », sur 1 400 cas (départs × facteurs)." if fr else
                          "**Alarms from one-day shocks**: on 2024, 3 \"retrain\" (1 of them clean) and 14 "
                          "\"recalibrate\", out of 1,400 cases (starts × factors).")
    assert ("la part sans aide n'est mesurée que pour « réentraîner »" if fr else
            "the clean share is measured for \"retrain\" only") in one
    two = str(m.one_day_alarms([(2023, b), (2024, a)]))
    assert ("sur 2023, 0 « réentraîner » (dont 0 sans aide) et 2 « recalibrer », sur 1 460 cas (départs × "
            "facteurs) ; sur 2024, 3" if fr else
            "on 2023, 0 \"retrain\" (0 of them clean) and 2 \"recalibrate\", out of 1,460 cases (starts × factors); "
            "on 2024, 3") in two
    import jinja2

    with pytest.raises(jinja2.UndefinedError):  # never called with no year: the template says there were none
        str(m.one_day_alarms([]))


@pytest.mark.parametrize("lang", LANGS)
def test_section_6_reads_the_one_day_counts_from_each_years_rows(lang):
    """Through derive: one-day rows of 2024 with alarms, 2023 with none; only 2024 is named, with its own counts."""
    v, dq, p, r, b, frozen = R.payloads()
    for row in r["sensitivity"]["episodes"]["rows"]:
        if row["days"] == 1:
            row.update(retrain=2, retrain_clean=1, recalibrate=row["starts"] % 7)
    derived = RV.derive(v, dq, p, r, b, frozen, R.oos_frame())
    one = derived["episode_limits"]["replay"]["one_day"]
    rows = [x for x in r["sensitivity"]["episodes"]["rows"] if x["days"] == 1]
    assert one == {"retrain": 8, "retrain_clean": 4, "recalibrate": sum(x["starts"] % 7 for x in rows),
                   "cases": sum(x["starts"] for x in rows)}
    s6 = R.section(R.render({**R.context(), **derived}, lang), 6)
    head = "**Alertes des chocs d'un seul jour** : sur 2024, 8 « réentraîner » (dont 4 sans aide) et " if lang == "fr" \
        else "**Alarms from one-day shocks**: on 2024, 8 \"retrain\" (4 of them clean) and "
    assert head + str(one["recalibrate"]) in s6 and ("sur 2023" if lang == "fr" else "on 2023,") not in \
        s6[s6.index(head):s6.index(head) + 300]


# --- 4. false "recalibrate" in the limits ---------------------------------------------------------------------------

@pytest.mark.parametrize("lang", LANGS)
def test_the_episode_limit_gives_each_years_false_recalibrate(lang):
    fr = lang == "fr"
    ctx = R.context()
    E23, E24 = ctx["episode_limits"]["pseudo"], ctx["episode_limits"]["replay"]
    assert E23["recal_worst"] and E24["recal_worst"] and E23["recal_share"] != E24["recal_share"]
    line = line_of(R.section(R.render(ctx, lang), 8), "- **" + ("Épisodes" if fr else "Episodes"))
    one, two = line.split((" ; sur " if fr else "; on ") + str(R.R_YEAR))
    for E, part, other in ((E23, one, E24), (E24, two, E23)):
        w = E["recal_worst"]
        assert (f"« recalibrer » jusqu'à {R.pct(E['recal_share'], 1, lang)} des départs ({w['days']} jours à × "
                if fr else f"\"recalibrate\" at up to {R.pct(E['recal_share'], 1, lang)} of starts ({w['days']} days "
                "at × ") in part
        assert R.pct(other["recal_share"], 1, lang) not in part
    assert ("peut donner une fausse alerte sans que le modèle soit en cause" if fr else
            "can give a false alarm with nothing wrong in the model") in line
    quiet = {k: {**x, "worst": None, "recal_worst": None} for k, x in ctx["episode_limits"].items()}
    line = line_of(R.section(R.render({**ctx, "episode_limits": quiet}, lang), 8),
                   "- **" + ("Épisodes" if fr else "Episodes"))
    assert line.count("aucun « réentraîner », aucun « recalibrer »" if fr else
                      "no \"retrain\", no \"recalibrate\"") == 2
    assert ("Un épisode n'est pas une défaillance du modèle" if fr else "An episode is not a model failure") in line
    only_recal = {**quiet, "replay": {**quiet["replay"], "recal_worst": E24["recal_worst"],
                                      "recal_share": E24["recal_share"]}}
    line = line_of(R.section(R.render({**ctx, "episode_limits": only_recal}, lang), 8),
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
    ctx = R.context()
    lv, lv24 = ctx["level"], ctx["level24"]
    assert lv["missed_months"] == [1, 3, 6] and lv24["missed_months"] == [1] and lv["months"] == [1, 3, 6, 9]
    text = R.render(ctx, lang)
    level = line_of(R.section(text, 8), "- **" + ("Changement" if fr else "Shift"))
    assert ("à 3 départs sur 4 (départs de janvier, mars et juin)" if fr else
            "at 3 of 4 starts (the January, March and June starts)") in level
    assert ("à 1 départ sur 4 (départ de janvier)" if fr else "at 1 of 4 starts (the January start)") in level
    assert ("septembre" not in level) if fr else ("September" not in level)
    # The paragraphs that name the starts tested still name all four.
    assert ("première semaine jugée de janvier, mars, juin et septembre" if fr else
            "first judged week of January, March, June and September") in R.section(text, 6)


# --- 14. the replays side by side: model and flagged weeks ----------------------------------------------------------

def table_rows(s7: str, lang: str) -> dict:
    table = s7[s7.index("| | " + ("Rejeu d'avant" if lang == "fr" else "Earlier replay")):].split("\n\n")[0]
    return {line.split(" | ")[0][2:]: line.split(" | ")[1:] for line in table.splitlines()[2:]}


@pytest.mark.parametrize("lang", LANGS)
def test_the_replays_side_by_side_show_their_model_and_flagged_weeks(lang):
    fr = lang == "fr"
    ctx = R.context(departures=[R.EARLIER])
    s7 = R.section(R.render(ctx, lang), 7)
    rows = table_rows(s7, lang)
    r = ctx["r"]
    assert rows["Modèle figé (empreinte)" if fr else "Frozen model (hash)"] == [
        f"`{R.EARLIER['model_sha256'][:12]}`", f"`{r['model']['sha256'][:12]}` |"]
    names = (("Semaines EAMP hors seuil", "Semaines biais au-dessus de sa bande", "Semaines biais au-dessous de sa bande",
              "Semaines couverture basse") if fr else
             ("Weeks with MAPE above its threshold", "Weeks with bias above its band", "Weeks with bias below its band",
              "Weeks with low coverage"))
    for key, name in zip(("mape_high", "bias_high", "bias_low", "coverage_low"), names, strict=True):
        assert rows[name] == [str(R.EARLIER["flag_weeks"][key]), f"{r['summary']['flag_weeks'][key]} |"]
    assert ("Les rejeux de ce tableau ont appliqué le même modèle figé, à la même empreinte : la dérogation n'a changé "
            "ni le modèle ni ses intervalles. Chaque rejeu juge ses semaines contre ses propres seuils." if fr else
            "The replays in this table applied the same frozen model, with the same hash: the departure changed "
            "neither the model nor its intervals. Each replay judges its weeks against its own thresholds.") in s7


@pytest.mark.parametrize("lang", LANGS)
def test_replays_with_different_models_each_show_their_own_hash(lang):
    """An earlier replay on another frozen model: each column prints its own replay's hash, and the sentence says
    the hashes differ instead of claiming the same model."""
    fr = lang == "fr"
    earlier = {**R.EARLIER, "model_sha256": "c" * 64}
    ctx = R.context(departures=[earlier])
    s7 = R.section(R.render(ctx, lang), 7)
    new = ctx["r"]["model"]["sha256"]
    assert new[:12] != "c" * 12
    assert table_rows(s7, lang)["Modèle figé (empreinte)" if fr else "Frozen model (hash)"] == [
        f"`{'c' * 12}`", f"`{new[:12]}` |"]
    assert ("Le modèle figé ou ses intervalles diffèrent d'un rejeu à l'autre (empreintes ci-dessus)." if fr else
            "The frozen model or its intervals differ from one replay to another (hashes above).") in s7
    assert ("même modèle figé" if fr else "same frozen model") not in s7


@pytest.mark.parametrize("lang", LANGS)
def test_the_model_sentence_is_true_for_every_mix_of_replays(lang):
    fr = lang == "fr"
    m = macros(lang)
    same, other = {"model_sha256": "a" * 64}, {"model_sha256": "b" * 64}
    ledger = {"model_sha256": None}
    SAME = "même modèle figé" if fr else "same frozen model"
    DIFFER = "diffèrent d'un rejeu à l'autre" if fr else "differ from one replay to another"
    ABSENT = "non consigné dans le registre" if fr else "not recorded in the replay ledger"
    THRESH = "contre ses propres seuils" if fr else "against its own thresholds"
    for mix in itertools.chain.from_iterable(itertools.combinations_with_replacement((same, other, ledger), n)
                                             for n in (1, 2)):
        text = str(m.departed_model(list(mix), "a" * 64))
        known = [d for d in mix if d["model_sha256"]]
        assert THRESH in text and "None" not in text and text.endswith(".")
        assert (SAME in text) == bool(known and all(d is same for d in known))
        assert (DIFFER in text) == any(d is other for d in known)
        assert (ABSENT in text) == (len(known) < len(mix))
        assert ("dont l'empreinte est consignée" in text if fr else "whose hash is recorded" in text) == (
            SAME in text and len(known) < len(mix))


@pytest.mark.parametrize("lang", LANGS)
def test_a_replay_from_the_ledger_shows_no_model_and_no_flagged_weeks(lang):
    entry = {"run_id": "9" * 32, "freeze_commit": "e" * 40, "mape": 0.0423, "coverage": 0.894,
             "recommendations": [{"week_start": "2024-03-04", "recommendation": "retrain"}]}
    d = RV.departure(None, RecordedReplay(entry))
    assert d["recorded"] is True and d["model_sha256"] is None and d["flag_weeks"] is None
    s7 = R.section(R.render(R.context(departures=[d]), lang), 7)
    rows = table_rows(s7, lang)
    assert rows["Modèle figé (empreinte)" if lang == "fr" else "Frozen model (hash)"][0] == "—"
    assert rows["Semaines couverture basse" if lang == "fr" else "Weeks with low coverage"][0] == "—"
    assert ("même modèle figé" if lang == "fr" else "same frozen model") not in s7
    assert ("— : non consigné dans le registre des rejeux." if lang == "fr" else
            "—: not recorded in the replay ledger.") in s7


# --- 12, 13. section 10's scope -------------------------------------------------------------------------------------

@pytest.mark.parametrize("lang", LANGS)
def test_section_10_claims_no_production_monitoring_and_names_overrides_apart_from_the_departure(lang):
    fr = lang == "fr"
    s10 = R.section(R.render(R.context(), lang), 10)
    row = line_of(s10, "| NIST MEASURE")
    assert row.startswith("| NIST MEASURE 1.1, 2.1, 2.3, 2.5, 2.13 |") and "2.4" not in row
    gap = line_of(s10, "**Non couvert.**" if fr else "**Not covered.**")
    assert ("NIST MEASURE 2.4 (surveillance en production : simulée par le rejeu seulement)" if fr else
            "NIST MEASURE 2.4 (monitoring in production: simulated by the replay only)") in gap
    assert ("analyse des forçages (overrides) des résultats du modèle" if fr else
            "analysis of overrides of the model's outputs") in gap
    assert "dérogation" not in gap


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
                 ("The following year is replayed hour by hour", "It measures no forecast's quality: the validation "
                  "report does.", "the validation report says so of its results", "that is what monitoring has to "
                  "detect (validation report)", "**The busiest hours.** The {{ rep.top_hours|length }}")):
        assert said in src, said


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
