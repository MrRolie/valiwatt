"""`valiwatt breaks`: run every break as a tracked MLflow run, so the report can show each gate and monitor firing.

A break is a test that injects the fault a gate or monitor exists to catch and asserts that it fires (or, for a clean
control, that it stays quiet). BREAKS is the one list of them, with the words the report prints. The run executes
exactly these tests and fails when any of them is missing or did not pass: a break that was not run is not a break.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from valiwatt.paths import REPO_ROOT

EXPERIMENT = "valiwatt-breaks"


@dataclass(frozen=True)
class Break:
    group: str
    test: str  # pytest node id, relative to the repository
    fr: str
    en: str


def _b(group, test, fr, en):
    return Break(group, test, fr, en)


DQ, LOOK, CLOCK, MON, ACT, FREEZE, RENDER = ("data_quality", "lookahead", "clock", "monitors", "never_acts",
                                             "freeze", "render")
GROUPS = {
    DQ: ("Portes de qualité des données", "Data-quality gates"),
    LOOK: ("Pas d'information future", "No lookahead"),
    CLOCK: ("Horloge de la rejouée", "Replay clock"),
    MON: ("Moniteurs et table de décision", "Monitors and decision table"),
    ACT: ("Le moniteur n'agit jamais", "The monitor never acts"),
    FREEZE: ("Règles gelées", "Frozen rules"),
    RENDER: ("Rendu des rapports", "Report rendering"),
}

T_DEM, T_WEA, T_PIPE = "tests/test_demand_gates.py", "tests/test_weather_gates.py", "tests/test_pipeline_refuses.py"
T_GRD, T_VAL, T_REP, T_DRF = "tests/test_guards.py", "tests/test_validate.py", "tests/test_replay.py", "tests/test_drift.py"
T_SAFE, T_FRZ = "tests/test_public_safety.py", "tests/test_freeze_gates.py"

BREAKS = [
    _b(DQ, f"{T_DEM}::test_duplicate_without_known_shape_fails_the_gate",
       "Un doublon de forme inconnue fait échouer la porte", "A duplicate of no known shape fails the gate"),
    _b(DQ, f"{T_DEM}::test_ambiguous_misplaced_record_is_not_moved_and_fails_the_gate",
       "Un enregistrement mal placé mais ambigu n'est pas déplacé et fait échouer la porte",
       "An ambiguous misplaced record is not moved and fails the gate"),
    _b(DQ, f"{T_DEM}::test_record_outside_the_window_fails_the_gate",
       "Un enregistrement hors de la fenêtre fait échouer la porte", "A record outside the window fails the gate"),
    _b(DQ, f"{T_DEM}::test_out_of_bounds_value_fails_the_gate",
       "Une demande hors bornes fait échouer la porte", "A demand value out of bounds fails the gate"),
    _b(DQ, f"{T_DEM}::test_coverage_below_threshold_fails_the_gate",
       "Une couverture insuffisante fait échouer la porte", "Coverage below its threshold fails the gate"),
    _b(DQ, f"{T_WEA}::test_duplicate_rows_fail_the_index_gate",
       "Des lignes météo en double font échouer la porte d'index", "Duplicate weather rows fail the index gate"),
    _b(DQ, f"{T_WEA}::test_impossible_temperature_fails_the_bounds_gate",
       "Une température impossible fait échouer la porte des bornes", "An impossible temperature fails the bounds gate"),
    _b(DQ, f"{T_WEA}::test_sensor_outage_fails_the_coverage_gate",
       "Une panne de capteur fait échouer la porte de couverture", "A sensor outage fails the coverage gate"),
    _b(DQ, f"{T_WEA}::test_shared_outage_fails_the_panel_gate",
       "Une panne commune à plusieurs stations fait échouer la porte du panel",
       "An outage shared by several stations fails the panel gate"),
    _b(DQ, f"{T_PIPE}::test_failing_gate_stops_the_chain",
       "Une porte en échec arrête la chaîne : aucune donnée canonique n'est écrite",
       "A failing gate stops the chain: no canonical data is written"),
    _b(DQ, f"{T_PIPE}::test_raw_files_that_no_longer_match_their_manifest_are_refused",
       "Des fichiers bruts qui ne correspondent plus à leur manifeste sont refusés",
       "Raw files that no longer match their manifest are refused"),
    _b(DQ, f"{T_DEM}::test_a_record_off_the_hourly_grid_fails_the_window_gate_with_its_timestamp",
       "Un enregistrement hors de la grille horaire fait échouer la porte, avec son horodatage",
       "A record off the hourly grid fails the window gate, with its timestamp"),
    _b(DQ, f"{T_DEM}::test_an_out_of_bounds_value_inside_a_fall_back_pair_still_fails_the_bounds_gate",
       "Une valeur hors bornes cachée dans une paire du retour à l'heure normale fait quand même échouer la porte",
       "An out-of-bounds value inside a fall-back pair still fails the bounds gate"),
    _b(DQ, f"{T_DEM}::test_an_out_of_bounds_value_is_set_aside_before_a_repair_reads_it",
       "Une valeur hors bornes est écartée avant qu'une réparation ne la lise",
       "An out-of-bounds value is set aside before a repair reads it"),
    _b(DQ, f"{T_DEM}::test_an_out_of_bounds_neighbour_does_not_make_a_spike",
       "Une valeur hors bornes ne crée pas de pic chez ses voisines", "An out-of-bounds value makes no spike next to it"),
    _b(LOOK, f"{T_GRD}::test_an_input_after_the_origin_is_refused",
       "Une entrée postérieure à l'origine de la prévision est refusée", "An input read after the forecast's origin is refused"),
    _b(LOOK, f"{T_GRD}::test_a_training_row_inside_the_embargo_is_refused",
       "Une ligne d'entraînement dans l'embargo est refusée", "A training row inside the embargo is refused"),
    _b(LOOK, f"{T_GRD}::test_a_2024_row_is_refused_by_design_code",
       "Une ligne de 2024 est refusée par le code de conception", "A 2024 row is refused by design code"),
    _b(LOOK, f"{T_VAL}::test_validation_refuses_a_2024_row",
       "La validation refuse une ligne de 2024", "Validation refuses a 2024 row"),
    _b(LOOK, f"{T_REP}::test_a_drift_reference_holding_replayed_days_is_refused",
       "Une référence de dérive contenant des jours rejoués est refusée",
       "A drift reference holding replayed days is refused"),
    _b(LOOK, f"{T_REP}::test_thresholds_built_on_the_replayed_year_are_refused",
       "Des seuils tirés de l'année rejouée sont refusés", "Thresholds built on the replayed year are refused"),
    _b(LOOK, f"{T_VAL}::test_drift_thresholds_for_a_replay_ignore_the_replayed_year",
       "Les seuils de dérive d'une rejouée ignorent l'année rejouée",
       "A replay's drift thresholds ignore the replayed year"),
    _b(LOOK, f"{T_FRZ}::test_the_frozen_model_refuses_training_rows_inside_its_embargo",
       "Le modèle figé refuse des lignes d'entraînement dans son embargo",
       "The frozen model refuses training rows inside its embargo"),
    _b(CLOCK, f"{T_REP}::test_records_after_a_cut_change_nothing_judged_before_it",
       "Des données corrompues après un instant ne changent rien de ce qui a été jugé avant",
       "Records corrupted after a cut change nothing judged before it"),
    _b(CLOCK, f"{T_REP}::test_a_record_that_arrives_late_is_not_used_before_it_arrives",
       "Un enregistrement arrivé une semaine en retard n'est pas utilisé avant son arrivée",
       "A record that arrives a week late is not used before it arrives"),
    _b(CLOCK, f"{T_REP}::test_reading_past_the_clock_is_refused",
       "Lire au-delà de l'horloge, ou la faire reculer, est refusé", "Reading past the clock, or turning it back, is refused"),
    _b(CLOCK, f"{T_REP}::test_a_forecast_issued_after_its_origin_is_refused",
       "Une prévision émise après son origine est refusée", "A forecast issued after its origin is refused"),
    _b(MON, f"{T_REP}::test_clean_world_yields_no_recommendation",
       "Témoin : des données propres ne donnent aucune recommandation", "Control: clean data yield no recommendation"),
    _b(MON, f"{T_DRF}::test_seasonal_reference_stays_quiet_on_clean_data_and_fires_on_a_shift",
       "La référence saisonnière reste calme sur des données propres et réagit à un décalage",
       "The seasonal reference stays quiet on clean data and fires on a shift"),
    _b(MON, f"{T_REP}::test_temperature_drift_yields_investigate_with_performance_inside",
       "Une dérive de température, performance dans les seuils : examiner les données",
       "Temperature drift with performance inside its thresholds: investigate the data"),
    _b(MON, f"{T_REP}::test_a_sensor_gap_fails_data_quality_and_suppresses_performance",
       "Un trou de capteur fait échouer la qualité du jour et suspend les règles de performance",
       "A sensor gap fails the day's data quality and suspends the performance rules"),
    _b(MON, f"{T_REP}::test_missing_and_duplicated_demand_records_fail_their_day",
       "Un enregistrement manquant et un doublon font échouer leur jour",
       "A missing and a duplicated demand record fail their day"),
    _b(MON, f"{T_REP}::test_a_bad_week_and_its_lag_echo_do_not_yield_retrain[0.7]",
       "Une semaine basse (−30 %) et son écho par le retard d'une semaine ne donnent pas « réentraîner »",
       "A low week (−30%) and its echo through the one-week lag do not yield retrain"),
    _b(MON, f"{T_REP}::test_a_bad_week_and_its_lag_echo_do_not_yield_retrain[1.3]",
       "Une semaine haute (+30 %) et son écho, EAMP haute les deux semaines, ne donnent pas « réentraîner »",
       "A high week (+30%) and its echo, MAPE high in both weeks, do not yield retrain"),
    _b(MON, f"{T_REP}::test_a_mape_pair_with_bias_on_opposite_sides_is_an_echo_not_retrain",
       "Une EAMP haute deux semaines avec un biais de signes opposés est un écho ; sans inversion, elle déclenche",
       "MAPE high two weeks with bias on opposite sides is an echo; without the flip it fires"),
    _b(MON, f"{T_REP}::test_a_bias_beyond_the_same_side_three_weeks_running_yields_retrain",
       "Un biais du même côté trois semaines de suite donne « réentraîner » ; un écho ou une semaine non jugée l'interrompt",
       "Bias beyond the same side three weeks running yields retrain; an echo or an unjudged week breaks it"),
    _b(MON, f"{T_REP}::test_detection_power_finds_a_large_sustained_change_and_nothing_without_one",
       "La mesure de puissance trouve un écart soutenu de 8 % ou des intervalles réduits à 20 %, et rien sans changement",
       "The power measure finds a sustained 8% gap or intervals cut to 20%, and nothing without a change"),
    _b(MON, f"{T_REP}::test_two_contaminated_days_never_lift_the_trimmed_statistic_past_the_untouched_days",
       "Deux jours altérés ne portent jamais la statistique tronquée au-delà des jours intacts (2 000 tirages)",
       "Two altered days never lift the trimmed statistic past the untouched days (2,000 draws)"),
    _b(MON, f"{T_REP}::test_partial_weeks_have_no_drift_statistic",
       "Les semaines incomplètes n'ont pas de statistique de dérive", "Partial weeks have no drift statistic"),
    _b(MON, f"{T_REP}::test_the_last_day_is_judged_at_the_end_of_the_data_and_says_so",
       "Le dernier jour est jugé à la fin des données, et le dit", "The last day is judged at the end of the data, and says so"),
    _b(MON, f"{T_REP}::test_duplicated_temperatures_and_an_off_grid_demand_record_fail_their_day",
       "Des températures en double et un enregistrement hors grille font échouer leur jour",
       "Duplicated temperatures and an off-grid demand record fail their day"),
    _b(MON, f"{T_REP}::test_a_level_shift_yields_retrain",
       "Un changement de niveau de la demande : réentraîner", "A shift in the demand level: retrain"),
    _b(MON, f"{T_REP}::test_deflated_intervals_yield_recalibrate",
       "Des intervalles trop étroits : recalibrer", "Intervals made too narrow: recalibrate"),
    _b(MON, f"{T_REP}::test_two_rules_at_once_yield_one_recommendation_with_both_pieces_of_evidence",
       "Deux règles à la fois : une recommandation, les deux preuves",
       "Two rules at once: one recommendation with both pieces of evidence"),
    _b(MON, f"{T_REP}::test_a_one_day_shock_never_yields_retrain[2022-01-15]",
       "Un choc d'un jour (samedi) ne donne pas « réentraîner »", "A one-day shock (Saturday) does not yield retrain"),
    _b(MON, f"{T_REP}::test_a_one_day_shock_never_yields_retrain[2022-01-16]",
       "Un choc d'un jour (dimanche) ne donne pas « réentraîner »", "A one-day shock (Sunday) does not yield retrain"),
    _b(ACT, f"{T_REP}::test_a_replay_that_changes_the_model_fails",
       "Une rejouée qui modifie le modèle ou ses intervalles échoue",
       "A replay that changes the model or its intervals fails"),
    _b(FREEZE, f"{T_FRZ}::test_no_tag_is_refused",
       "Sans l'étiquette de gel, la rejouée de 2024 est refusée", "Without the freeze tag, the 2024 replay is refused"),
    _b(FREEZE, f"{T_FRZ}::test_an_edited_rule_file_is_refused_and_named",
       "Un fichier de règles modifié après le gel est refusé et nommé",
       "A rule file edited after the freeze is refused and named"),
    _b(FREEZE, f"{T_FRZ}::test_a_rule_file_added_after_the_tag_is_refused",
       "Un fichier de règles ajouté après le gel est refusé", "A rule file added after the freeze is refused"),
    _b(FREEZE, f"{T_FRZ}::test_a_moved_tag_is_refused_unless_recorded_as_a_departure",
       "Une étiquette de gel déplacée est refusée sauf écart déclaré",
       "A moved freeze tag is refused unless recorded as a departure"),
    _b(FREEZE, f"{T_FRZ}::test_earlier_replays_are_found_even_when_deleted",
       "Les rejouées antérieures sont retrouvées même supprimées", "Earlier replays are found even when deleted"),
    _b(FREEZE, f"{T_FRZ}::test_raw_files_other_than_the_checked_ones_are_refused",
       "Des fichiers bruts autres que ceux contrôlés sont refusés",
       "Raw files other than the ones checked are refused"),
    _b(FREEZE, f"{T_FRZ}::test_a_registered_model_that_changed_during_the_replay_fails_it",
       "Un modèle enregistré qui change pendant la rejouée la fait échouer",
       "A registered model that changed during the replay fails it"),
    _b(FREEZE, f"{T_FRZ}::test_a_frozen_model_from_another_validation_year_or_rule_set_is_refused",
       "Un modèle figé d'une autre validation, d'une autre année ou d'autres règles est refusé",
       "A frozen model from another validation run, year or rule set is refused"),
    _b(FREEZE, f"{T_FRZ}::test_validation_from_another_data_quality_run_or_rule_set_is_refused",
       "Une validation, ou un contrôle de qualité, fait sur d'autres données ou d'autres règles est refusé",
       "A validation or data-quality run made on other data or other rules is refused"),
    _b(FREEZE, f"{T_FRZ}::test_a_frozen_model_file_that_no_longer_matches_its_hash_is_refused",
       "Un fichier de modèle figé qui ne correspond plus à son empreinte est refusé",
       "A frozen model file that no longer matches its hash is refused"),
    _b(FREEZE, f"{T_FRZ}::test_the_registered_model_is_refused_when_its_run_records_another_hash",
       "Le modèle enregistré est refusé si son exécution porte une autre empreinte",
       "The registered model is refused when its run records another hash"),
    _b(RENDER, f"{T_PIPE}::test_report_refuses_when_the_code_changed_since_the_run",
       "Un rapport dont le code a changé depuis l'exécution est refusé",
       "A report whose code changed since its run is refused"),
    _b(RENDER, f"{T_SAFE}::test_scan_fires_on_private_content",
       "L'analyse de sécurité publique réagit à un contenu privé", "The public-safety scan fires on private content"),
]


def _outcomes(xml_path: Path) -> dict[str, str]:
    out = {}
    for case in ET.parse(xml_path).getroot().iter("testcase"):
        node = case.get("classname", "").replace(".", "/") + ".py::" + case.get("name", "")
        if case.find("failure") is not None or case.find("error") is not None:
            out[node] = "failed"
        elif case.find("skipped") is not None:
            out[node] = "skipped"
        else:
            out[node] = "passed"
    return out


def run_tests(breaks: list[Break] = BREAKS) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        xml = Path(tmp) / "breaks.xml"
        subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={xml}",
                        *[b.test for b in breaks]], cwd=REPO_ROOT, check=False)
        got = _outcomes(xml) if xml.exists() else {}
    return [{"group": b.group, "test": b.test, "fr": b.fr, "en": b.en, "outcome": got.get(b.test, "not_run")}
            for b in breaks]


def run_breaks() -> int:
    import mlflow

    from valiwatt.monitor.rules import rules_sha256
    from valiwatt.quality.checks import jsonable
    from valiwatt.tracking import start_run

    rows = run_tests()
    fired = sum(r["outcome"] == "passed" for r in rows)
    with start_run(EXPERIMENT, "breaks", tags={"kind": "breaks", "rules_sha256": rules_sha256()}) as run:
        mlflow.log_metrics({"breaks": len(rows), "fired": fired, "not_fired": len(rows) - fired})
        mlflow.log_text(json.dumps(jsonable({"breaks": rows, "groups": GROUPS}), indent=2, ensure_ascii=False),
                        "results/breaks.json")
        mlflow.set_tag("status", "complete" if fired == len(rows) else "failed")
    bad = [r for r in rows if r["outcome"] != "passed"]
    print(f"breaks run {run.info.run_id}: {fired}/{len(rows)} fired" + "".join(
        f"\n  {r['outcome']}: {r['test']}" for r in bad))
    return 0 if not bad else 1
