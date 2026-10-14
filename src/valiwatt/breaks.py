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
    control: bool = False  # a clean control: nothing is injected, and it passes by staying quiet


def _b(group, test, fr, en, control=False):
    return Break(group, test, fr, en, control)


# The synthetic world the monitor's breaks run in (tests/replay_world.py): its drift alarm level, set by hand rather
# than by the quantile rule, and the rise in demand the level-shift break injects. The report prints both.
SYNTHETIC_WORLD = {"drift_alarm": 1.0, "level_shift": 0.12}


DQ, LOOK, CLOCK, MON, ACT, FREEZE, RENDER = ("data_quality", "lookahead", "clock", "monitors", "never_acts",
                                             "freeze", "render")
GROUPS = {
    DQ: ("Portes de qualité des données", "Data-quality gates"),
    LOOK: ("Pas d'information future", "No lookahead"),
    CLOCK: ("Horloge du rejeu", "Replay clock"),
    MON: ("Moniteurs et table de décision", "Monitors and decision table"),
    ACT: ("Le moniteur n'agit jamais", "The monitor never acts"),
    FREEZE: ("Règles gelées", "Frozen rules"),
    RENDER: ("Rendu des rapports", "Report rendering"),
}

T_DEM, T_WEA, T_PIPE = "tests/test_demand_gates.py", "tests/test_weather_gates.py", "tests/test_pipeline_refuses.py"
T_GRD, T_VAL, T_REP, T_DRF = "tests/test_guards.py", "tests/test_validate.py", "tests/test_replay.py", "tests/test_drift.py"
T_SAFE, T_FRZ, T_SEN = "tests/test_public_safety.py", "tests/test_freeze_gates.py", "tests/test_sensitivity.py"
T_LIN, T_BRK = "tests/test_report_lineage.py", "tests/test_breaks.py"
T_COV, T_SEN2, T_LED = "tests/test_report_coverage.py", "tests/test_report_sentences.py", "tests/test_replay_ledger.py"
T_ASOF, T_EVAL = "tests/test_as_of.py", "tests/test_evaluate.py"

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
    _b(DQ, f"{T_DEM}::test_a_misplaced_record_that_fits_two_empty_hours_is_not_moved_and_fails_the_gate",
       "Un enregistrement mal placé qui irait aussi bien dans deux heures vides n'est pas déplacé et fait échouer la porte",
       "A misplaced record that fits two empty hours equally well is not moved and fails the gate"),
    _b(DQ, f"{T_DEM}::test_an_out_of_bounds_duplicate_is_never_moved_into_a_usable_hour",
       "Un doublon hors bornes n'est jamais déplacé dans une heure utilisable, ni dans le rejeu",
       "An out-of-bounds duplicate is never moved into a usable hour, nor through the replay"),
    _b(DQ, f"{T_DEM}::test_an_out_of_bounds_value_that_reaches_a_usable_hour_is_marked_out_of_bounds",
       "Une valeur hors bornes qui atteint une heure utilisable est marquée hors bornes",
       "An out-of-bounds value that reaches a usable hour is marked out of bounds"),
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
       "Les seuils de dérive d'un rejeu ignorent l'année rejouée",
       "A replay's drift thresholds ignore the replayed year"),
    _b(LOOK, f"{T_FRZ}::test_the_frozen_model_refuses_training_rows_inside_its_embargo",
       "Le modèle figé refuse des lignes d'entraînement dans son embargo",
       "The frozen model refuses training rows inside its embargo"),
    _b(LOOK, f"{T_REP}::test_the_day_verdicts_read_no_record_after_the_last_day",
       "Le verdict de qualité des jours qui sert au calibrage ne lit aucun enregistrement postérieur au dernier jour",
       "The day-level data-quality verdict used for calibration reads no record after the last day"),
    _b(LOOK, f"{T_GRD}::test_an_origin_moved_earlier_than_the_lag_allows_is_refused",
       "Une origine avancée au-delà de ce que permet le retard de 24 heures est refusée",
       "An origin moved earlier than the 24-hour lag allows is refused"),
    _b(LOOK, f"{T_ASOF}::test_a_repaired_value_does_not_reach_lags_or_training_before_its_record_arrives",
       "Une valeur réparée avec un enregistrement arrivé plus tard n'entre ni dans les retards ni dans "
       "l'entraînement avant son arrivée",
       "A value repaired from a later record reaches no lag and no training before that record arrives"),
    _b(LOOK, f"{T_ASOF}::test_a_half_width_fixed_at_a_fit_clock_calibrates_only_on_the_days_scored_by_then",
       "Une demi-largeur fixée à l'ajustement d'un modèle n'est calibrée que sur les jours déjà notés à ce moment, "
       "pas sur une valeur réparée une heure plus tard",
       "A half-width fixed when its model is fitted is calibrated only on the days already scored then, not on a "
       "value repaired an hour later"),
    _b(LOOK, f"{T_EVAL}::test_rows_without_the_time_they_were_scored_calibrate_no_half_width",
       "Des erreurs hors échantillon qui ne disent pas quand elles ont été notées ne calibrent aucune demi-largeur",
       "Out-of-sample rows that do not say when they were scored calibrate no half-width"),
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
    _b(CLOCK, f"{T_REP}::test_a_day_scored_before_the_hour_after_its_last_hour_is_refused",
       "Un jour noté avant l'heure qui suit sa dernière heure est refusé",
       "A day scored before the hour after its last hour is refused"),
    _b(CLOCK, f"{T_REP}::test_a_forecast_built_on_rows_of_another_origin_or_day_is_refused[origin]",
       "Une prévision bâtie sur une ligne d'une autre origine est refusée",
       "A forecast built on a row of another origin is refused"),
    _b(CLOCK, f"{T_REP}::test_a_forecast_built_on_rows_of_another_origin_or_day_is_refused[day]",
       "Une prévision bâtie sur une ligne d'un autre jour est refusée", "A forecast built on a row of another day is refused"),
    _b(MON, f"{T_REP}::test_clean_world_yields_no_recommendation",
       "Témoin : des données propres ne donnent aucune recommandation", "Control: clean data yield no recommendation",
       control=True),
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
    _b(MON, f"{T_SEN}::test_a_level_shift_in_demand_is_measured_and_no_shift_finds_nothing",
       "La mesure des changements de niveau trouve une hausse de 12 % de la demande, et rien sans changement",
       "The level-shift measure finds a 12% rise in demand, and nothing without a change"),
    _b(MON, f"{T_SEN}::test_one_day_episodes_bring_no_alarm_and_every_start_is_counted",
       "La mesure des épisodes : un jour de choc ne donne aucune fausse alerte, et chaque départ est compté",
       "The episode measure: a one-day shock brings no false alarm, and every start is counted"),
    _b(MON, f"{T_SEN}::test_a_stretch_across_a_monday_is_measured_as_a_false_alarm_and_the_echo_exclusion_keeps_others",
       "La mesure des épisodes trouve la fausse alerte de cinq jours qui chevauchent un lundi, et l'écho que l'exclusion écarte",
       "The episode measure finds the false alarm of five days across a Monday, and the echo the exclusion keeps out"),
    _b(MON, f"{T_SEN}::test_an_alarm_resting_on_a_week_already_out_of_band_is_assisted_and_old_alarms_are_not_counted",
       "Une fausse alerte appuyée sur une semaine déjà hors bande est dite aidée ; une alerte d'origine n'est pas comptée",
       "A false alarm resting on a week already out of band is called assisted; an alarm the year already had is not counted"),
    _b(MON, f"{T_SEN}::test_the_echo_exclusion_only_ever_removes_or_delays_retrain",
       "L'exclusion de l'écho ne fait jamais qu'enlever ou retarder « réentraîner »",
       "The echo exclusion only ever removes or delays retrain"),
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
    _b(MON, f"{T_REP}::test_a_week_with_too_few_scored_hours_is_not_judged",
       "Une semaine de trop peu d'heures notées n'est pas jugée", "A week with too few scored hours is not judged"),
    _b(MON, f"{T_REP}::test_a_week_with_too_few_full_days_has_no_trimmed_statistic_and_is_never_broad",
       "Une semaine de trop peu de jours complets n'a pas de statistique tronquée et n'est jamais hors bande étendue",
       "A week with too few full days has no trimmed statistic and is never broadly out of band"),
    _b(MON, f"{T_REP}::test_one_predicate_says_which_weeks_are_judged",
       "Une semaine incomplète, une semaine avec un jour en échec et une semaine de trop peu d'heures ne sont pas "
       "jugées, chaque condition à elle seule",
       "A partial week, a week with a failed day and a week with too few hours are not judged, each condition alone"),
    _b(MON, f"{T_REP}::test_a_partial_week_with_enough_hours_is_not_judged",
       "Une semaine coupée par la fin de la période n'est pas jugée, même avec assez d'heures",
       "A week cut by the end of the period is not judged, even with enough hours"),
    _b(MON, f"{T_REP}::test_the_day_verdicts_for_calibration_are_the_replays_own",
       "Le verdict de qualité des jours lu par le calibrage est, jour par jour, celui du rejeu, sur sept défauts "
       "injectés et les jours propres",
       "The day-level data-quality verdict the calibration reads is, day by day, the replay's own, on seven injected "
       "faults and the clean days"),
    _b(MON, f"{T_VAL}::test_a_week_holding_a_day_the_replay_would_fail_does_not_calibrate_the_thresholds",
       "Une semaine contenant un jour que le rejeu ferait échouer ne sert pas au calcul des seuils, et la raison est "
       "consignée",
       "A week holding a day the replay would fail does not calibrate the thresholds, and why is logged"),
    _b(MON, f"{T_VAL}::test_weeks_cut_by_the_calibration_years_are_set_aside_and_a_day_without_a_verdict_is_refused",
       "Une semaine coupée par les années de calibrage est écartée ; un jour sans verdict de qualité fait refuser "
       "le calcul des seuils",
       "A week cut by the calibration years is set aside; a day without a data-quality verdict makes the thresholds "
       "refuse"),
    _b(ACT, f"{T_REP}::test_a_replay_that_changes_the_model_fails",
       "Un rejeu qui modifie le modèle ou ses intervalles échoue",
       "A replay that changes the model or its intervals fails"),
    _b(FREEZE, f"{T_FRZ}::test_no_tag_is_refused",
       "Sans l'étiquette de gel, le rejeu de 2024 est refusé", "Without the freeze tag, the 2024 replay is refused"),
    _b(FREEZE, f"{T_FRZ}::test_an_edited_rule_file_is_refused_and_named",
       "Un fichier de règles modifié après le gel est refusé et nommé",
       "A rule file edited after the freeze is refused and named"),
    _b(FREEZE, f"{T_FRZ}::test_a_rule_file_added_after_the_tag_is_refused",
       "Un fichier de règles ajouté après le gel est refusé", "A rule file added after the freeze is refused"),
    _b(FREEZE, f"{T_FRZ}::test_a_moved_tag_is_refused_unless_recorded_as_a_departure",
       "Une étiquette de gel déplacée est refusée sauf écart déclaré",
       "A moved freeze tag is refused unless recorded as a departure"),
    _b(FREEZE, f"{T_FRZ}::test_replay_called_directly_on_the_held_out_year_passes_the_freeze_gate",
       "Le rejeu appelé directement sur l'année réservée passe la porte du gel : sans étiquette, avec un fichier de "
       "règles modifié ou sous une étiquette déplacée, il est refusé",
       "The replay called directly on the held-out year passes the freeze gate: without the tag, with an edited rule "
       "file or under a moved tag, it is refused"),
    _b(FREEZE, f"{T_FRZ}::test_a_ledger_naming_an_earlier_freeze_commit_is_a_moved_tag_even_with_an_empty_store",
       "Le registre versionné des rejeux révèle une étiquette déplacée même sans dépôt MLflow : refus sauf dérogation "
       "consignée",
       "The committed replay ledger shows a moved tag even with no MLflow store: refused unless a departure is "
       "recorded"),
    _b(FREEZE, f"{T_FRZ}::test_a_ledger_that_cannot_be_read_is_refused[not_json]",
       "Un registre des rejeux illisible est refusé", "A replay ledger that cannot be parsed is refused"),
    _b(FREEZE, f"{T_FRZ}::test_a_ledger_that_cannot_be_read_is_refused[no_list]",
       "Un registre des rejeux sans liste de rejeux est refusé", "A replay ledger with no list of replays is refused"),
    _b(FREEZE, f"{T_FRZ}::test_a_ledger_that_cannot_be_read_is_refused[no_freeze_commit]",
       "Une entrée du registre sans commit de gel est refusée", "A ledger entry without a freeze commit is refused"),
    _b(FREEZE, f"{T_FRZ}::test_earlier_replays_are_found_even_when_deleted",
       "Les rejeux antérieurs sont retrouvés même supprimés", "Earlier replays are found even when deleted"),
    _b(FREEZE, f"{T_FRZ}::test_raw_files_other_than_the_checked_ones_are_refused",
       "Des fichiers bruts autres que ceux contrôlés sont refusés",
       "Raw files other than the ones checked are refused"),
    _b(FREEZE, f"{T_VAL}::test_raw_demand_other_than_the_checked_file_is_refused",
       "La conception refuse un fichier de demande brut autre que celui contrôlé",
       "Design code refuses a raw demand file other than the one checked"),
    _b(FREEZE, f"{T_FRZ}::test_a_registered_model_that_changed_during_the_replay_fails_it",
       "Un modèle enregistré qui change pendant le rejeu le fait échouer",
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
    _b(FREEZE, f"{T_FRZ}::test_the_replay_without_a_freeze_tag_stops_before_reading_any_input",
       "Sans l'étiquette de gel, le rejeu de 2024 s'arrête avant de lire la moindre donnée",
       "Without the freeze tag, the 2024 replay stops before reading any input"),
    _b(FREEZE, f"{T_FRZ}::test_the_replay_under_a_moved_tag_stops_before_reading_any_input",
       "Sous une étiquette de gel déplacée, le rejeu de 2024 s'arrête avant de lire la moindre donnée, même avec "
       "--departure sans dérogation consignée",
       "Under a moved freeze tag, the 2024 replay stops before reading any input, even with --departure and no "
       "recorded departure"),
    _b(FREEZE, f"{T_FRZ}::test_the_replay_refuses_a_frozen_model_from_another_validation_run",
       "Le rejeu de 2024 refuse un modèle figé construit sur une autre validation",
       "The 2024 replay refuses a frozen model built on another validation run"),
    _b(FREEZE, f"{T_FRZ}::test_the_replay_fails_when_the_registered_model_changes_during_it",
       "Le rejeu de 2024 échoue si le modèle enregistré change pendant qu'il tourne",
       "The 2024 replay fails when the registered model changes while it runs"),
    _b(FREEZE, f"{T_FRZ}::test_an_alias_pointing_at_another_years_model_is_refused",
       "Un alias qui pointe vers le modèle figé d'une autre année est refusé",
       "An alias pointing at another year's frozen model is refused"),
    _b(RENDER, f"{T_PIPE}::test_report_refuses_when_the_code_changed_since_the_run",
       "Un rapport dont le code a changé depuis l'exécution est refusé",
       "A report whose code changed since its run is refused"),
    _b(RENDER, f"{T_SAFE}::test_scan_fires_on_private_content",
       "L'analyse de sécurité publique réagit à un contenu privé", "The public-safety scan fires on private content"),
    _b(RENDER, f"{T_SAFE}::test_a_report_figure_passes_only_when_well_formed_with_clean_metadata",
       "Une figure dont les métadonnées portent un chemin privé, ou suivie d'octets cachés, est refusée",
       "A figure whose metadata carries a private path, or with bytes hidden after its end, is refused"),
    _b(RENDER, f"{T_SAFE}::test_history_scan_reads_deleted_files_and_leftover_refs",
       "Un fichier retiré plus tard de l'arbre, un chemin interdit ou une référence oubliée reste repéré dans l'historique",
       "A file removed from the tree later, a forbidden path or a leftover ref is still caught in the history"),
    _b(RENDER, f"{T_SAFE}::test_history_scan_reads_annotated_tag_messages",
       "Le message d'une étiquette annotée portant un chemin privé est repéré",
       "An annotated tag message carrying a private path is caught"),
    _b(RENDER, f"{T_SAFE}::test_scan_fires_on_home_shorthands_but_not_on_prose",
       "Les raccourcis du répertoire personnel réagissent, pas la prose ordinaire",
       "The home-directory shorthands fire; ordinary prose does not"),
    _b(RENDER, f"{T_SAFE}::test_a_figure_with_an_unknown_chunk_or_a_bad_checksum_is_refused",
       "Une figure portant un bloc hors liste ou une somme de contrôle fausse est refusée",
       "A figure with a chunk outside the allowlist or a bad checksum is refused"),
    _b(RENDER, f"{T_SAFE}::test_a_figure_whose_structure_breaks_the_png_rules_is_refused",
       "Une figure dont la structure enfreint les règles du PNG est refusée : octets dans le bloc de fin, bloc de taille "
       "fixe trop long, en-tête absent, bloc en double, données d'image absentes, coupées ou qui ne se décompressent pas "
       "exactement à la taille que l'en-tête annonce",
       "A figure whose structure breaks the PNG rules is refused: bytes in the end chunk, an oversized fixed-length "
       "chunk, a missing header, a chunk twice, image data missing, split, or not inflating to exactly the size the "
       "header states"),
    _b(RENDER, f"{T_SAFE}::test_a_fixed_length_chunk_is_read_by_the_scan",
       "Un chemin privé caché dans un bloc de taille fixe d'une figure est repéré",
       "A private path hidden in a fixed-length chunk of a figure is caught"),
    _b(RENDER, f"{T_LIN}::test_a_clean_chain_is_followed",
       "Témoin : une chaîne d'exécutions intacte est suivie jusqu'au bout",
       "Control: an intact run chain is followed to its end", control=True),
    _b(RENDER, f"{T_LIN}::test_a_broken_link_in_the_run_chain_is_refused[breaks_failed]",
       "Une exécution des bris marquée en échec rompt la chaîne du rapport", "A breaks run marked failed breaks the report's run chain"),
    _b(RENDER, f"{T_LIN}::test_a_broken_link_in_the_run_chain_is_refused[other_code]",
       "Une exécution faite avec un autre code rompt la chaîne du rapport", "A run made from other code breaks the report's run chain"),
    _b(RENDER, f"{T_LIN}::test_a_broken_link_in_the_run_chain_is_refused[frozen_for_another_year]",
       "Un modèle figé pour une autre année rompt la chaîne du rapport", "A model frozen for another year breaks the report's run chain"),
    _b(RENDER, f"{T_LIN}::test_a_broken_link_in_the_run_chain_is_refused[replay_under_another_freeze]",
       "Un rejeu fait sous un autre commit de gel rompt la chaîne du rapport",
       "A replay made under another freeze commit breaks the report's run chain"),
    _b(RENDER, f"{T_LIN}::test_a_broken_link_in_the_run_chain_is_refused[validation_on_another_dq]",
       "Une validation qui désigne un autre contrôle de qualité rompt la chaîne du rapport",
       "A validation naming another data-quality run breaks the report's run chain"),
    _b(RENDER, f"{T_LIN}::test_a_departure_replay_brings_the_replays_it_departs_from_and_names_them",
       "Un rejeu fait après une dérogation apporte les rejeux qu'il remplace ; s'il n'en nomme aucun, il est refusé",
       "A replay after a departure brings the replays it departs from; one that names none is refused"),
    _b(RENDER, f"{T_LIN}::test_a_recorded_departure_the_replay_does_not_carry_is_refused",
       "Une dérogation consignée dans `AGENTS.md` que le rejeu de 2024 ne porte pas rompt la chaîne du rapport, de "
       "même qu'un rejeu après une dérogation dont `AGENTS.md` ne nomme pas le commit de gel",
       "A departure recorded in `AGENTS.md` that the 2024 replay does not carry breaks the report's run chain, and so "
       "does a departure replay whose freeze commit `AGENTS.md` does not name"),
    _b(RENDER, f"{T_LIN}::test_with_no_departure_recorded_a_clean_chain_passes",
       "Témoin : sans dérogation consignée, une chaîne intacte passe ; avec une dérogation, le rejeu qui la porte passe",
       "Control: with no departure recorded, an intact chain passes; with one, the replay that carries it passes",
       control=True),
    _b(RENDER, f"{T_LIN}::test_a_departure_named_by_a_short_hash_is_refused",
       "Une dérogation consignée par un hachage de moins de 12 caractères rompt la chaîne du rapport",
       "A departure recorded by a hash shorter than 12 characters breaks the report's run chain"),
    _b(RENDER, f"{T_LIN}::test_a_departures_section_that_cannot_be_found_is_refused",
       "Une section des dérogations introuvable dans `AGENTS.md` rompt la chaîne du rapport",
       "A departures section that cannot be found in `AGENTS.md` breaks the report's run chain"),
    _b(RENDER, f"{T_COV}::test_a_season_missing_from_the_run_is_refused",
       "Une saison absente de la couverture du run de validation fait refuser le rapport",
       "A season missing from the validation run's coverage makes the report refuse"),
    _b(RENDER, f"{T_SEN2}::test_a_near_miss_week_missing_from_the_replay_is_refused",
       "Une semaine de la première formulation de la règle 2 absente des semaines jugées du rejeu fait refuser le rapport",
       "A week of rule 2's first wording missing from the replay's judged weeks makes the report refuse"),
    _b(RENDER, f"{T_SEN2}::test_a_failure_broad_in_both_weeks_without_a_retrain_is_refused",
       "Une défaillance étendue aux deux semaines sans « réentraîner » dans le rejeu fait refuser le rapport",
       "A failure broad in both weeks with no retrain in the replay makes the report refuse"),
    _b(RENDER, f"{T_SEN2}::test_the_breaks_run_must_say_which_breaks_are_controls",
       "Une exécution des bris qui ne marque pas ses témoins fait refuser le rapport",
       "A breaks run that does not mark its controls makes the report refuse"),
    _b(RENDER, f"{T_SEN2}::test_a_validation_frame_without_its_spring_is_refused",
       "Un cas du printemps 2020 sans heures de printemps fait refuser le rapport",
       "A spring 2020 case with no spring hours makes the report refuse"),
    _b(RENDER, f"{T_SEN2}::test_an_empty_power_table_is_refused",
       "Un tableau de puissance vide fait refuser le rapport", "An empty power table makes the report refuse"),
    _b(RENDER, f"{T_SEN2}::test_a_power_table_without_narrowed_interval_rows_is_refused",
       "Un tableau de puissance sans lignes d'intervalles réduits fait refuser le rapport",
       "A power table without narrowed-interval rows makes the report refuse"),
    _b(RENDER, f"{T_SEN2}::test_a_spring_difference_whose_sides_share_a_seed_is_refused",
       "Un intervalle de la différence du printemps 2020 dont deux tirages partagent une graine, ou fait d'un seul lot, "
       "fait refuser le rapport",
       "A spring 2020 interval of the difference with two draws sharing a seed, or made of one batch, makes the report "
       "refuse"),
    _b(RENDER, f"{T_SEN2}::test_a_bound_within_monte_carlo_error_of_zero_leaves_the_verdict_undetermined",
       "Une borne de l'intervalle de la différence que l'erreur de Monte-Carlo ou l'arrondi ne sépare pas de zéro "
       "laisse le verdict indéterminé",
       "A bound of the interval of the difference that Monte Carlo error or rounding cannot separate from zero leaves "
       "the verdict undetermined"),
    _b(RENDER, f"{T_SAFE}::test_an_agent_session_url_in_a_commit_message_is_caught",
       "L'adresse d'une session d'agent dans un message de commit, un message d'étiquette ou un fichier indexé est "
       "repérée", "An agent session's address in a commit message, a tag message or a staged file is caught"),
    _b(RENDER, f"{T_LED}::test_a_store_run_that_disagrees_with_its_ledger_entry_is_refused",
       "Une entrée du registre des rejeux réécrite à la main fait refuser la mise à jour du registre",
       "A replay-ledger entry rewritten by hand makes the ledger update refuse"),
    _b(RENDER, f"{T_LED}::test_a_ledger_whose_content_fails_the_scan_is_not_written",
       "Un registre des rejeux portant un nom privé, ou un champ hors de sa forme fixe, n'est pas écrit",
       "A replay ledger carrying a private name, or a field outside its fixed shape, is not written"),
    _b(RENDER, f"{T_LED}::test_a_replay_that_disagrees_with_its_ledger_entry_is_refused",
       "Un rejeu de 2024 qui ne concorde pas avec son entrée du registre rompt la chaîne du rapport",
       "A 2024 replay that disagrees with its ledger entry breaks the report's run chain"),
    _b(RENDER, f"{T_LED}::test_a_departed_replay_in_neither_the_store_nor_the_ledger_is_refused",
       "Un rejeu d'avant une dérogation absent du magasin et du registre, ou consigné comme incomplet, rompt la chaîne "
       "du rapport",
       "A replay from before a departure that is in neither the store nor the ledger, or is recorded as incomplete, "
       "breaks the report's run chain"),
    _b(RENDER, f"{T_LED}::test_a_departed_replay_absent_from_the_store_renders_from_the_ledger",
       "Témoin : un rejeu d'avant une dérogation absent du magasin est montré d'après le registre, avec la mention "
       "« consigné dans le registre, non rejoué ici »",
       "Control: a replay from before a departure that the store lacks is shown from the ledger, labelled \"recorded "
       "in the ledger, not re-run here\"", control=True),
    _b(RENDER, f"{T_LED}::test_a_departed_replay_that_disagrees_with_its_ledger_entry_is_refused",
       "Un rejeu d'avant une dérogation qui ne concorde pas avec son entrée du registre rompt la chaîne du rapport",
       "A replay from before a departure that disagrees with its ledger entry breaks the report's run chain"),
    _b(RENDER, "tests/test_report_stamp.py::test_a_ledger_changed_after_the_render_fails_the_stamp",
       "Un registre des rejeux modifié ou supprimé après le rendu fait échouer le test de l'estampille du rapport",
       "A replay ledger edited or removed after the render fails the report's stamp test"),
    _b(RENDER, f"{T_BRK}::test_a_breaks_run_with_a_break_that_did_not_fire_is_marked_failed",
       "Un bris qui ne s'est pas déclenché met l'exécution des bris en échec", "A break that did not fire marks the breaks run failed"),
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
    return [{"group": b.group, "test": b.test, "fr": b.fr, "en": b.en, "control": b.control,
             "outcome": got.get(b.test, "not_run")} for b in breaks]


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
