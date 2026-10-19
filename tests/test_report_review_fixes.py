"""What the step-6 reviews of v2's first render found, held by tests: the revised records quoted only as evidence, the
forecast's weekly error derived from each replay's own hourly frame, the diagnostic runs in the provenance and the
stamp, the rehearsal's cold tail and busiest hours, the labels on 2024's figures and numbers, and the sentences that
claimed more than their runs (the freeze's chronology, the ledger's coverage, the busiest hours' population, the
availability margin, the test of record, the data-quality report's years and sources)."""

import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import report_payloads as RP
import report_store as S
import report_world as W
from test_report_render import brief, card_row, pct, section, two_vintages  # noqa: F401 (a fixture)

from valiwatt.report import validation as RV
from valiwatt.report.render import ReportRefused, environment

LANGS = RP.LANGS
P_YEAR, R_YEAR = RP.P_YEAR, RP.R_YEAR
SAY = RV.SAY


def label(lang: str, key: str) -> str:
    return environment(lang).get_template(f"sentences.{lang}.md.j2").module.LABEL[key]


# --- the revised records: every hour listed, ten quoted -----------------------------------------------------------

def revised_state(revised: int) -> dict:
    """A clean test of record whose data-quality run revised `revised` records of 2019-2024."""
    r = RP.record("b" * 32, W.utc(10))
    held = RP.held([W.start(r["run_id"], r["started_utc"], True, r["gate"])], {r["run_id"]: r},
                   RP.findings(revised=revised))
    return RP.context(held_view=held, rec24=RP.rec24_view())


def first_cells(lines: list[str], hours: set, cells: int) -> list[str]:
    """The table lines whose first cell is one of `hours`, with `cells` cells or more (4: values quoted; 2: hour and
    change only)."""
    return [x for x in lines if x.startswith("| ") and x[2:].split(" | ", 1)[0] in hours and x.count(" | ") + 1 == cells]


@pytest.mark.parametrize("lang", LANGS)
def test_a_large_revision_lists_every_hour_and_quotes_the_values_of_ten(lang):
    """A 2025 export that revises 1,000 records of 2019-2024: section 9 lists every revised hour with its change, and
    the old and new values of the first ten only, saying the full table stays in the data-quality run's artifact. A
    page that quotes an eleventh record's values, or drops a record, is refused."""
    ctx = revised_state(1000)
    rev = ctx["held"]["findings"]["revised"]
    assert (rev["records"], len(rev["quoted"]), len(rev["rest"]), rev["cap"]) == (1000, 10, 990, RV.QUOTED_REVISIONS)
    text = RP.render(ctx, lang)
    nine = section(text, 9)
    utc, mw = environment(lang).filters["utc"], environment(lang).filters["mw"]
    kind = SAY[lang]["kind"]
    lines = nine.splitlines()
    hours = {utc(x["utc"]) for x in rev["rows"]}
    assert len(hours) == 1000
    assert len(first_cells(lines, hours, 4)) == 10 and len(first_cells(lines, hours, 2)) == 990
    assert all(f"| {utc(x['utc'])} | {kind[x['kind']]} |" in lines for x in rev["rest"])
    assert ("l'artefact de l'exécution de qualité des données, jamais versé au dépôt" if lang == "fr" else
            "the data-quality run's artifact, never committed") in nine
    status = RV.status_sentence(environment(lang), ctx, lang)
    x = rev["rest"][0]
    bare = f"| {utc(x['utc'])} | {kind[x['kind']]} |\n"
    quoted = (f"| {utc(x['utc'])} | {kind[x['kind']]} | {mw(x['old_mw'], x['kind'] != 'added')} | "
              f"{mw(x['new_mw'], x['kind'] != 'removed')} |\n")
    with pytest.raises(ReportRefused, match="quotes the values of 11 revised records, more than the 10"):
        RV.check_rendered(text.replace(bare, quoted + bare, 1), lang, ctx, "validation", status)
    with pytest.raises(ReportRefused, match="not all listed"):
        RV.check_rendered(text.replace(bare, "", 1), lang, ctx, "validation", status)


@pytest.mark.parametrize("lang", LANGS)
def test_a_small_revision_quotes_each_record_s_values(lang):
    """Three revised records, fewer than the cap: each is quoted with its values, and there is no second table."""
    ctx = revised_state(3)
    nine = section(RP.render(ctx, lang), 9)
    utc = environment(lang).filters["utc"]
    hours = {utc(x["utc"]) for x in ctx["held"]["findings"]["revised"]["rows"]}
    assert len(first_cells(nine.splitlines(), hours, 4)) == 3 and not first_cells(nine.splitlines(), hours, 2)
    assert ("autres heures révisées" if lang == "fr" else "other revised hours") not in nine


# --- the forecast's weekly error ------------------------------------------------------------------------------------

def test_the_weekly_forecast_error_is_derived_from_the_replay_s_own_frame():
    """By the replay's weeks (Monday to Sunday), on the hours with a forecast and an observation; the frame must give
    the replay's logged forecast error month by month and season by season (monitor/run.py `forecast_error`
    recomputed). A frame without the observation, with fewer hours, with as many hours but other values or other
    hours, or a replay that logged none, is refused."""
    frame = RP.hourly(R_YEAR)
    logged = RP.forecast_error(frame)
    weeks = RV.weekly_forecast_error(frame, logged, R_YEAR)
    assert len(weeks) == 53 and all(pd.Timestamp(w["week_start"]).dayofweek == 0 for w in weeks)
    assert sum(w["hours"] for w in weeks) == sum(x["hours"] for x in logged["by_season"].values())
    err = (frame["temp_forecast"] - frame["temp_observed"]).dropna()
    first = err[pd.to_datetime(frame.loc[err.index, "day"]) < pd.Timestamp(weeks[1]["week_start"])]
    assert np.isclose(weeks[0]["mean_c"], first.mean()) and np.isclose(weeks[0]["mae_c"], first.abs().mean())
    with pytest.raises(ReportRefused, match=r"lacks \['temp_observed'\]"):
        RV.weekly_forecast_error(frame.drop(columns="temp_observed"), logged, R_YEAR)
    with pytest.raises(ReportRefused, match="another frame than the one the replay logged"):
        RV.weekly_forecast_error(frame.iloc[:-200], logged, R_YEAR)
    # The same number of hours, other values: one hour's forecast a degree warmer.
    warmer = frame.copy()
    hour = err.index[100]
    warmer.loc[hour, "temp_forecast"] += 1.0
    with pytest.raises(ReportRefused, match=r"by_month \d{4}-\d{2} mean_c: .* against .*another frame"):
        RV.weekly_forecast_error(warmer, logged, R_YEAR)
    # The same number of hours, other hours: one hour with both moved to an hour of another month without them.
    other = frame.copy()
    a = err.index[0]
    b = next(i for i in frame.index if pd.isna(frame.loc[i, "temp_observed"] - frame.loc[i, "temp_forecast"])
             and pd.Timestamp(frame.loc[i, "day"]).month != pd.Timestamp(frame.loc[a, "day"]).month)
    other.loc[b, ["temp_forecast", "temp_observed"]] = frame.loc[a, ["temp_forecast", "temp_observed"]].to_numpy()
    other.loc[a, "temp_observed"] = np.nan
    assert other["temp_forecast"].sub(other["temp_observed"]).notna().sum() == len(err)
    with pytest.raises(ReportRefused, match=r"\d+ hours against \d+.*another frame"):
        RV.weekly_forecast_error(other, logged, R_YEAR)
    for none in (None, {}):
        with pytest.raises(ReportRefused, match="logged no forecast error"):
            RV.weekly_forecast_error(frame, none, R_YEAR)


def test_a_week_that_opens_before_the_year_is_dated_by_its_monday():
    """2023-01-01, the rehearsal year's first day, is a Sunday: its hours open the week of Monday 2022-12-26, which
    holds that one day's hours, and the next week starts on Monday 2023-01-02."""
    frame = RP.hourly(2023)
    weeks = RV.weekly_forecast_error(frame, RP.forecast_error(frame), 2023)
    assert [w["week_start"] for w in weeks[:2]] == ["2022-12-26", "2023-01-02"]
    first = frame[pd.to_datetime(frame["day"]) == pd.Timestamp("2023-01-01")]
    assert weeks[0]["hours"] == int((first["temp_forecast"] - first["temp_observed"]).notna().sum())


@pytest.mark.parametrize("lang", LANGS)
def test_sections_7_and_8_show_the_weekly_forecast_error_labelled(lang):
    """The weekly table of each replay, in its own section, labelled with its year's evaluation label, said derived
    from the replay's hourly frame and without a threshold; every week's row printed."""
    ctx = RP.context()
    text = RP.render(ctx, lang)
    num, signed, day = (environment(lang).filters[k] for k in ("num", "signed", "day"))
    for n, key, lbl in ((7, "pseudo", "rehearsal"), (8, "replay", "seen_by_developers")):
        part = section(text, n)
        head = ("| Semaine du (" if lang == "fr" else "| Week of (") + f"_{label(lang, lbl)}_) |"
        assert head in part and "`frames/hourly.parquet`" in part, (n, lang)
        assert ("sans seuil" if lang == "fr" else "without a threshold") in part
        assert ("tenu à ce que le rejeu consigne" if lang == "fr" else "held to the replay's log") in part
        assert ("moyennes par saison que le rejeu consigne" if lang == "fr" else "seasonal means the replay logs") \
            not in part
        for w in ctx["weekly_error"][key]:
            assert f"| {day(w['week_start'])} | {signed(w['mean_c'], 2)} °C | {num(w['mae_c'], 2)} °C | " \
                   f"{num(w['hours'])} |" in part, (n, w)


def test_the_store_s_replays_carry_their_hourly_frames_into_the_weekly_table(monkeypatch):
    """`validation.context` reads frames/hourly.parquet of the rehearsal and of the 2024 replay it shows."""
    w = S.World().install(monkeypatch)
    assert w.context()["weekly_error"] == RP.weekly_error()
    w.files[("pseudo", "frames/hourly.parquet")] = RP.hourly(P_YEAR).drop(columns="temp_observed")
    with pytest.raises(ReportRefused, match="the 2023 replay's hourly frame lacks"):
        w.context()


# --- provenance ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("lang", LANGS)
def test_the_provenance_names_the_diagnostic_runs_the_sections_cite(lang):
    """Sections 7 and 8 cite the rehearsal's and the 2024 replay's diagnostic runs: the provenance table names both,
    each right after its replay."""
    ctx = RP.context()
    thirteen = section(RP.render(ctx, lang), 13)
    ids = [ctx["runs"][k]["id"] for k in ("pseudo_replay", "diag_pseudo_replay", "replay", "diag_replay")]
    at = [thirteen.find(f"`{x}`") for x in ids]
    assert all(i >= 0 for i in at) and at == sorted(at)


def test_the_chain_s_runs_include_its_diagnostic_runs(monkeypatch):
    """The stamp's runs (`ctx["runs"]`): each replay's diagnostic run after it, read from the store; none for a replay
    whose diagnostics did not finish."""
    w = S.World().install(monkeypatch)
    runs = w.context()["runs"]
    keys = list(runs)
    assert keys.index("diag_pseudo_replay") == keys.index("pseudo_replay") + 1
    assert keys.index("diag_replay") == keys.index("replay") + 1
    assert (runs["diag_pseudo_replay"]["id"], runs["diag_replay"]["id"]) == ("diag-pseudo", f"diag-{S.REPLAY}")
    w.runs["diag-pseudo"].info.status = "FAILED"
    assert "diag_pseudo_replay" not in w.context()["runs"]


# --- section 7, the labels, the sentences ----------------------------------------------------------------------------

@pytest.mark.parametrize("lang", LANGS)
def test_section_7_shows_the_rehearsal_s_cold_tail_and_busiest_hours(lang):
    """Section 1 says the busiest hours stand beside every verdict: the rehearsal's section shows its cold tail and its
    busiest hours, labelled, as section 8 does for 2024; the model card names the sections that hold them."""
    ctx = RP.context()
    text = RP.render(ctx, lang)
    seven = section(text, 7)
    words = label(lang, "rehearsal")
    assert ("Queue froide" if lang == "fr" else "Cold tail") + f" (_{words}_)" in seven
    assert ("| Heure finissant à (UTC) (_" if lang == "fr" else "| Hour ending (UTC) (_") + words in seven
    utc = environment(lang).filters["utc"]
    assert ctx["p"]["contrasts"]["peak"] and all(f"| {utc(h['utc'])} |" in seven for h in ctx["p"]["contrasts"]["peak"])
    status = card_row(text, "Statut" if lang == "fr" else "Status")
    assert status_sections(lang, False, False) in status
    assert ("sections 5 et 9)" if lang == "fr" else "sections 5 and 9)") not in status


def status_sections(lang: str, coverage_9: bool, busiest_9: bool) -> str:
    """The model card's Status row's list of the sections holding the coverage and the busiest hours."""
    fr = lang == "fr"

    def tail(nine: bool) -> str:
        return (", 8 et 9" if nine else " et 8") if fr else (", 8 and 9" if nine else " and 8")

    if fr:
        return (f"(couverture : sections 4 (les heures de pointe), 5, 7{tail(coverage_9)} ; "
                f"heures les plus chargées : sections 3, 4, 7{tail(busiest_9)})")
    return (f"(coverage: sections 4 (the peak hours), 5, 7{tail(coverage_9)}; "
            f"busiest hours: sections 3, 4, 7{tail(busiest_9)})")


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("name, coverage_9, busiest_9", [("none", False, False), ("unfinished", False, False),
                                                         ("confirmed", True, True), ("recorded", True, False)])
def test_the_status_row_names_section_9_only_once_it_holds_them(lang, name, coverage_9, busiest_9):
    """While 2025 is pending, or its test of record has not finished, section 9 holds neither a coverage nor the
    busiest hours, and the Status row does not name it; a finished test of record gives its coverage, and its busiest
    hours only when its results are in the store (a record read from the ledger has none)."""
    text = RP.render(RP.state(name), lang)
    status = card_row(text, "Statut" if lang == "fr" else "Status")
    assert status_sections(lang, coverage_9, busiest_9) in status, (name, status)
    nine = section(text, 9)
    peak = "| Heure finissant à (UTC) (_" if lang == "fr" else "| Hour ending (UTC) (_"
    assert (peak in nine) == busiest_9, name


@pytest.mark.parametrize("lang", LANGS)
def test_the_2024_figures_and_the_reconciliation_are_labelled_and_precise(lang):
    """Section 8's figures carry 2024's label in their alt text; v1's reconciliation prints both coverages to two
    decimals and their difference; the availability sensitivity is the cost of one later margin, not a bound."""
    ctx = RP.context()
    text = RP.render(ctx, lang)
    eight = section(text, 8)
    words = label(lang, "seen_by_developers")
    alts = re.findall(r"!\[([^\]]*)\]\(figures/validation/replay_2024_(?:weekly|drift)", eight)
    assert len(alts) == 2 and all(a.endswith(words) for a in alts), alts
    rc = ctx["v1"]["reconciliation"]
    # The gap between the two printed figures, so that a reader subtracting them finds it.
    gap = environment(lang).filters["signed"](round(100 * rc["coverage"], 2) - round(100 * rc["ledger_coverage"], 2),
                                              2) + " pt"
    assert f"{pct(rc['coverage'], 2, lang)} " in eight and f"{pct(rc['ledger_coverage'], 2, lang)} ({gap})" in eight
    longer = "un délai plus long n'est pas mesuré" if lang == "fr" else "a longer delay is not measured"
    assert longer in section(text, 2) and longer in section(text, 10)
    for gone in (("au plus, l'hypothèse", "borne ce qu'elle vaut") if lang == "fr" else
                 ("at most, the availability", "bounds what it is worth")):
        assert gone not in text


@pytest.mark.parametrize("lang", LANGS)
def test_the_reconciliation_s_gap_is_that_of_its_printed_figures(lang):
    """89.394% against 89.428%: printed 89.39% against 89.43%, the gap printed −0.04 pt (the printed figures'
    difference), never −0.03 pt (the unrounded one)."""
    ctx = RP.context()
    ctx["v1"]["reconciliation"].update(coverage=0.89394, ledger_coverage=0.89428)
    eight = section(RP.render(ctx, lang), 8)
    printed = f"{pct(0.89394, 2, lang)} " + ("contre " if lang == "fr" else "against ") + pct(0.89428, 2, lang)
    assert f"{printed} (\u22120{',' if lang == 'fr' else '.'}04 pt)" in eight, eight
    assert "\u22120,03 pt" not in eight and "\u22120.03 pt" not in eight


@pytest.mark.parametrize("lang", LANGS)
def test_the_busiest_hours_are_named_by_their_period(lang):
    """Section 3's busiest hours are 2020-2023's, among every hour with a demand (evaluate._peak), not the busiest of
    the common hours; section 1 names each period judged."""
    text = RP.render(RP.context(), lang)
    three = section(text, 3)
    assert ("heures les plus chargées de 2020–2023, parmi toutes les heures qui ont une demande" if lang == "fr" else
            "busiest hours of 2020–2023, among all the hours that have a demand") in three
    assert ("plus chargées de ces heures" if lang == "fr" else "busiest of these hours") not in three
    assert ("chaque période jugée, 2020–2023 comme une seule période puis chaque année rejouée" if lang == "fr" else
            "each period judged, 2020–2023 as one period, then each replayed year") in section(text, 1)


@pytest.mark.parametrize("lang", LANGS)
def test_the_pending_test_of_record_names_the_stopped_start_exception(lang):
    """Before any 2025 start, « En bref » and section 9 say the first start is the test of record unless it stops
    before it finishes and a dated ruling of the owner names the start that takes its place (plan section 11.3.5)."""
    text = RP.render(RP.context(), lang)
    words = ("s'arrête avant de finir et qu'une décision datée du propriétaire nomme le départ qui prend sa place"
             if lang == "fr" else "stops before it finishes and a dated ruling of the owner names the start that "
                                  "takes its place")
    assert words in "".join(brief(text, lang)) and words in section(text, 9) and "11.3.5" in section(text, 9)


@pytest.mark.parametrize("lang", LANGS)
def test_the_limits_give_the_forecast_s_download_date(lang):
    """Section 10's real-time line gives each source's download date, the NBM forecast's included (its fetch
    manifest's first day)."""
    ctx = RP.context()
    ten = section(RP.render(ctx, lang), 10)
    first = min(x["retrieved_utc"] for x in ctx["dq"]["sources"]["nbm"]["years"].values())[:10]
    line = next(x for x in ten.splitlines() if x.startswith("- **Données en temps réel" if lang == "fr" else
                                                             "- **Real-time data"))
    assert first in line and ("(prévisions NBM)" if lang == "fr" else "(NBM forecasts)") in line


def test_the_2024_label_names_what_its_test_checks():
    """The break's label claims what its test checks: the 2024 replay's contrasts and verdicts carry the label, not
    every rendered sentence."""
    from valiwatt.breaks import BREAKS

    row = next(x for x in BREAKS if x.test.endswith("::test_a_2024_v2_number_without_its_label_is_refused"))
    assert "contrastes ou les verdicts" in row.fr and "contrasts or verdicts" in row.en
    assert "Un nombre de v2" not in row.fr and "A v2 number" not in row.en


# --- the story -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("lang", LANGS)
def test_the_story_dates_the_freeze_and_scopes_the_ledger(lang):
    """The rules were frozen before v2's 2024 replay, not before any v2 replay (rehearsals of 2023 ran before the
    freeze, as the plan allows, though the one the report shows ran after it); the ledger holds the 2024 and 2025 replays since the history rewrite, not the rehearsal,
    and only v2's entries carry a label. 2024's numbers and figure in the story carry its label."""
    text = RP.render(RP.context(), lang, "story")
    if lang == "fr":
        assert "avant le rejeu de 2024 de v2" in text and "avant tout rejeu de v2" not in text
        assert "Chaque rejeu de 2024 ou de 2025 terminé depuis la réécriture de l'historique du dépôt" in text
        assert "(la répétition de 2023 ne l'est pas)" in text and "seules les entrées de v2 portent" in text
        assert "Sur 2024 (vu par les développeurs)," in text
    else:
        assert "before v2's 2024 replay" in text and "before any v2 replay" not in text
        assert "Every 2024 or 2025 replay finished since the repository's history was rewritten" in text
        assert "(the 2023 rehearsal is not)" in text and "only v2's entries carry an evaluation label" in text
        assert "On 2024 (seen by the developers)," in text
    assert re.search(r"!\[[^\]]*" + re.escape(label(lang, "seen_by_developers")) + r"\]\(figures/validation/replay_2024",
                     text)


def test_the_readme_dates_the_freeze_and_says_what_the_choices_were_made_on():
    """The README, hand-written, says what the report says: the rules frozen before v2's 2024 replay, v1's rule logic
    kept and what v2 re-derived, the rehearsal the report shows run on the frozen rules (the code hash moved after the
    tag, only in files outside the rules, and the model hash tied only for the 2024 replays), what backs the freeze's
    order and how to check it from a clone, the rehearsal needing no tag; the ledger without the rehearsal; the
    choices made after 2024 had been seen through v1; the post-hoc case of the status; CI on pull requests."""
    from valiwatt.paths import REPO_ROOT
    from valiwatt.report.validation import hand_written

    readme = " ".join(hand_written((REPO_ROOT / "README.md").read_text(encoding="utf-8")).split())  # the box aside
    for said in ("before v2's 2024 replay", "the 2023 rehearsal does not need it", "(the 2023 rehearsal has none)",
                 "v2 was nevertheless chosen after 2024 had been seen through v1", "is not a held-out test",
                 "what each choice was made on", "on every pull request, a fork's included",
                 "The rehearsal the report shows, like the 2024 replay, ran on the frozen rules",
                 "v2's monitoring rules keep v1's logic", "v2 re-derived the thresholds",
                 "nothing recorded at the freeze ties the rehearsal's model to it",
                 "Checking the freeze from a clone",
                 "Nothing outside the repository anchors that order",
                 "a longer delay is not measured"):
        assert said in readme, said
    for gone in ("tag before any v2 replay", "before either replay", "before any v2 result was seen",
                 "Design choices use 2019 to 2023 only", "not on pull requests",
                 "the 2023 rehearsal ran before the freeze", "and no longer delay", "ran on the frozen code",
                 "as plan section 10.6 allows", "no number in them is typed by hand", "carried over unchanged",
                 "only report code", "the model hash are unchanged"):
        assert gone not in readme, gone


# --- the data-quality report -----------------------------------------------------------------------------------------

def test_the_data_quality_report_names_its_local_years_and_the_forecast_source(two_vintages, monkeypatch):  # noqa: F811
    """The bounds row names the window's years in local standard time, derived (the through-2024 window ends at
    2025-01-01 05:00 UTC, an hour of 2024), never its UTC years; the sources table has the NBM forecast's row from the
    run's fetch manifest; the hours after the design years belong to the 2024 replay, seen by the developers."""
    import json as _json

    from valiwatt import tracking
    from valiwatt.config import data_last
    from valiwatt.report import render as rr

    assert rr.local_year(data_last(2024)) == 2024 and rr.local_year("2019-01-01T06:00:00+00:00") == 2019
    assert rr.local_year("2019-01-01T05:00:00+00:00") == 2018
    W.install_lane_d(monkeypatch, starts=[W.start("b" * 32, W.utc(10), True, W.gate("clean"))])
    for through in (2024, 2025):
        run_id = two_vintages(through)
        results = _json.loads(Path(tracking.client().download_artifacts(run_id, "results/dq_results.json"))
                              .read_text())
        src = rr.nbm_source(results)
        assert src["files"] == len(results["sources"]["nbm"]["years"]) > 0
        for path, lang in zip(rr.render(run_id), LANGS):
            body = path.read_text(encoding="utf-8")
            bounds = next(x for x in body.splitlines() if x.startswith("| Valeurs dans des bornes" if lang == "fr"
                                                                       else "| Values within loose physical bounds"))
            assert f"(2019–{through})" in bounds, (through, lang, bounds)
            row = next(x for x in body.splitlines() if x.startswith("| NOAA, "))
            assert f"| {src['first_retrieved']}" in row and "`docs/data-licences.md`" in row
            assert (f"{src['files']} fichiers annuels" if lang == "fr" else f"{src['files']} yearly files") in row
            caption = next(x for x in body.splitlines() if x.startswith("_Demande moyenne journalière" if lang == "fr"
                                                                        else "_Daily mean demand"))
            assert ("au rejeu de 2024, vu par les développeurs" if lang == "fr" else
                    "to the 2024 replay, seen by the developers") in caption, caption
            assert ("2025" in caption) == (through == 2025) and "production" not in caption
    with pytest.raises(ReportRefused, match="holds no NBM fetch manifest"):
        rr.nbm_source({"sources": {}, "run_id": "x"})


# --- CI --------------------------------------------------------------------------------------------------------------

def test_ci_runs_on_pull_requests_at_their_head_commit():
    """A fork's pull request runs the safety scan and the breaks: the workflow triggers on `pull_request` (never
    `pull_request_target`) and on branch pushes, and checks out the pull request's head commit with the whole history
    and the tags, so the history tests see the branch's own linear history rather than GitHub's merge commit."""
    import yaml

    from valiwatt.paths import REPO_ROOT

    text = (REPO_ROOT / ".github" / "workflows" / "checks.yml").read_text(encoding="utf-8")
    wf = yaml.safe_load(text)
    on = wf.get("on", wf.get(True))
    assert "pull_request" in on and "pull_request_target" not in on and on["push"]["branches"] == ["**"]
    checkout = next(s for s in wf["jobs"]["checks"]["steps"] if str(s.get("uses", "")).startswith("actions/checkout"))
    w = checkout["with"]
    assert "github.event.pull_request.head.sha" in w["ref"] and w["fetch-depth"] == 0 and w["fetch-tags"] is True
    runs = [s.get("run", "") for s in wf["jobs"]["checks"]["steps"]]
    assert any("valiwatt safety" in r for r in runs) and any('pytest -m "not network"' in r for r in runs)
