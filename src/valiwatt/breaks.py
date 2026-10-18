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
T_ASOF, T_EVAL, T_WORD = "tests/test_as_of.py", "tests/test_evaluate.py", "tests/test_report_wording.py"
T_NBM, T_FEAT, T_FETCH = "tests/test_nbm_source.py", "tests/test_features.py", "tests/test_fetch_guards.py"
T_DEC, T_MOD, T_WF = "tests/test_decision_rule.py", "tests/test_model.py", "tests/test_walkforward.py"
T_FRO, T_R2, T_REND = "tests/test_frozen.py", "tests/test_replay_v2.py", "tests/test_report_render.py"
T_E2E, T_STAMP, T_CLI = "tests/test_report_end_to_end.py", "tests/test_report_stamp.py", "tests/test_cli.py"
T_DEP, T_TAGS, T_PLAN = "tests/test_departure_record.py", "tests/test_tags.py", "tests/test_plan_record.py"

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
    _b(DQ, f"{T_PIPE}::test_a_window_month_missing_from_a_station_s_manifest_is_refused",
       "Un mois de la fenêtre absent du manifeste d'une station est refusé avant qu'aucun fichier de cette station "
       "ne soit haché",
       "A window month missing from a station's manifest is refused before any of that station's files is hashed"),
    _b(DQ, f"{T_PIPE}::test_a_manifest_listing_every_window_month_passes_and_drops_the_months_outside",
       "Témoin : un manifeste qui liste chaque mois de la fenêtre passe, et ses mois hors de la fenêtre sont écartés",
       "Control: a manifest listing every month of the window passes, and its months outside the window are dropped",
       control=True),
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
       "Des températures en double et un enregistrement de demande hors grille font échouer leur jour",
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
    _b(RENDER, f"{T_WORD}::test_hour_block_coverage_rows_unlike_the_configured_blocks_are_refused",
       "Un bloc horaire absent de la couverture du run de validation, ou un bloc en trop, fait refuser le rapport",
       "An hour block missing from the validation run's coverage, or one too many, makes the report refuse"),
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
    _b(RENDER, f"{T_SEN2}::test_no_spring_verdict_contradicts_its_own_printed_numbers[fr]",
       "Une borne de l'intervalle de la différence imprimée à au plus k erreurs types imprimées de zéro laisse le verdict "
       "indéterminé, et une borne imprimée plus loin ne le laisse jamais : le verdict ne contredit pas ses nombres, "
       "rapport français",
       "A bound of the interval of the difference printed no more than k printed standard errors from zero leaves the "
       "verdict undetermined, and a bound printed farther out never does: the verdict never contradicts its numbers, "
       "French report"),
    _b(RENDER, f"{T_SEN2}::test_no_spring_verdict_contradicts_its_own_printed_numbers[en]",
       "Une borne de l'intervalle de la différence imprimée à au plus k erreurs types imprimées de zéro laisse le verdict "
       "indéterminé, et une borne imprimée plus loin ne le laisse jamais : le verdict ne contredit pas ses nombres, "
       "rapport anglais",
       "A bound of the interval of the difference printed no more than k printed standard errors from zero leaves the "
       "verdict undetermined, and a bound printed farther out never does: the verdict never contradicts its numbers, "
       "English report"),
    _b(LOOK, f"{T_EVAL}::test_a_row_scored_at_the_fit_clock_calibrates_and_one_scored_an_hour_later_does_not",
       "Une ligne notée une heure après l'horloge d'ajustement ne calibre pas la demi-largeur, une ligne notée à "
       "l'horloge même la calibre ; le dernier jour des données, noté à la fin des données, ne calibre pas une "
       "demi-largeur fixée à ce moment",
       "A row scored an hour after the fit clock does not calibrate the half-width and one scored at the fit clock "
       "itself does; the last day of the data, scored at the end of the data, does not calibrate a half-width fixed "
       "then"),
    _b(RENDER, "tests/test_report_render.py::test_a_2024_replay_without_its_sensitivity_measures_is_refused",
       "Un rejeu de 2024 sans ses mesures de fausses alertes et d'angles morts fait refuser le rapport",
       "A 2024 replay without its false-alarm and blind-spot measures makes the report refuse"),
    _b(RENDER, "tests/test_report_render.py::test_2024_measures_made_with_another_design_are_refused",
       "Des mesures de 2024 faites avec d'autres durées, facteurs, changements de niveau ou bruits que celles de 2023 "
       "font refuser le rapport",
       "2024 measures made with other lengths, factors, level shifts or noise than 2023's make the report refuse"),
    _b(RENDER, "tests/test_report_render.py::test_a_replay_departing_from_replays_under_two_freeze_commits_is_refused",
       "Un rejeu de 2024 qui déroge à des rejeux faits sous deux commits de gel, une dérogation que le rapport ne "
       "décrit pas, fait refuser le rapport",
       "A 2024 replay departing from replays made under two freeze commits, a departure the report does not describe, "
       "makes the report refuse"),
    # v2 (docs/plan-v2.md, section 12): the forecast input, its fetch and its data quality.
    _b(DQ, f"{T_NBM}::test_a_response_for_another_station_or_model_is_refused",
       "Une réponse de prévisions pour une autre station ou un autre modèle est refusée",
       "A forecast response for another station or model is refused"),
    _b(DQ, f"{T_NBM}::test_a_non_numeric_temperature_is_refused",
       "Une température prévue qui n'est ni un nombre ni vide est refusée",
       "A forecast temperature that is neither a number nor blank is refused"),
    _b(DQ, f"{T_NBM}::test_an_empty_response_is_refused",
       "Une réponse de prévisions vide est refusée", "An empty forecast response is refused"),
    _b(DQ, f"{T_NBM}::test_a_runtime_outside_the_requested_year_is_refused",
       "Une prévision émise hors de l'année demandée est refusée",
       "A forecast run outside the requested year is refused"),
    _b(DQ, f"{T_NBM}::test_a_refused_response_writes_nothing",
       "Une réponse de prévisions refusée n'écrit rien", "A refused forecast response writes nothing"),
    _b(DQ, f"{T_NBM}::test_a_clean_year_verifies_and_the_next_years_first_run_is_inside_its_request",
       "Témoin : une année de prévisions propre est vérifiée, et la première prévision de l'année suivante est dans sa "
       "requête",
       "Control: a clean year of forecasts verifies, and the next year's first run is inside its request",
       control=True),
    _b(DQ, f"{T_NBM}::test_an_undated_point_is_refused_at_verification_and_load",
       "Un point de prévision sans heure d'émission ou sans heure de validité est refusé à la vérification et au "
       "chargement, avec ses lignes",
       "A forecast point without its run time or its valid time is refused at verification and at load, naming its "
       "lines"),
    _b(DQ, f"{T_NBM}::test_two_values_for_one_point_refuse_the_load",
       "Deux valeurs pour un même point de prévision font refuser le chargement",
       "Two values for one forecast point refuse the load"),
    _b(DQ, f"{T_NBM}::test_identical_duplicates_are_dropped_and_counted",
       "Témoin : des points de prévision en double identiques sont écartés et comptés, sans refus",
       "Control: identical duplicate forecast points are dropped and counted, with no refusal", control=True),
    _b(DQ, f"{T_NBM}::test_a_primary_station_other_than_7025251_refuses_the_pairing",
       "Une station principale autre que celle de Montréal-Trudeau fait refuser l'appariement de la prévision",
       "A primary station other than Montréal-Trudeau's refuses the forecast's pairing"),
    _b(DQ, f"{T_NBM}::test_a_file_that_no_longer_matches_its_manifest_is_refused",
       "Un fichier de prévisions qui ne correspond plus à son manifeste est refusé",
       "A forecast file that no longer matches its manifest is refused"),
    _b(DQ, f"{T_WEA}::test_clean_forecast_passes_its_gates",
       "Témoin : une prévision propre passe ses portes", "Control: a clean forecast passes its gates", control=True),
    _b(DQ, f"{T_WEA}::test_an_impossible_forecast_temperature_fails_the_nbm_bounds_gate",
       "Une température prévue impossible fait échouer la porte des bornes de la prévision",
       "An impossible forecast temperature fails the forecast's bounds gate"),
    _b(DQ, f"{T_WEA}::test_forecast_days_below_coverage_fail_the_nbm_coverage_gate",
       "Trop peu de jours alimentés par une prévision font échouer la porte de couverture de la prévision ; un jour "
       "sans prévision à lui, alimenté par celle de la veille, ne la fait pas échouer",
       "Too few days fed by a forecast fail the forecast's coverage gate; a day without runs of its own, fed by the "
       "eve's run, does not"),
    _b(DQ, f"{T_WEA}::test_the_nbm_coverage_gate_passes_at_98_percent_of_days_fed_and_fails_below",
       "49 jours alimentés sur 50 (98 %) passent la porte de couverture de la prévision ; 48 sur 50, ou 48 sur 49, la "
       "font échouer",
       "49 of 50 days fed (98%) pass the forecast's coverage gate; 48 of 50, or 48 of 49, fail it"),
    _b(DQ, f"{T_WEA}::test_station_and_forecast_share_one_bounds_filter",
       "La station et la prévision passent par un seul filtre de bornes",
       "The station and the forecast share one bounds filter"),
    _b(DQ, f"{T_WEA}::test_the_forecast_error_is_read_on_the_design_days_only",
       "L'erreur de la prévision contre la station ne lit que les heures de la fenêtre de conception, et jamais une "
       "observation hors bornes",
       "The forecast's error against the station reads only the design window's hours, never an observation outside "
       "the bounds"),
    _b(DQ, f"{T_FEAT}::test_a_run_missing_one_bracket_point_gives_way_to_the_next",
       "Une prévision à laquelle manque un point d'encadrement cède la place à la suivante",
       "A run missing one bracketing point gives way to the next"),
    _b(DQ, f"{T_FEAT}::test_a_point_outside_the_bounds_does_not_cover",
       "Une prévision dont un point d'encadrement est hors bornes ne couvre pas le jour",
       "A run with a bracketing point outside the bounds does not cover the day"),
    _b(DQ, f"{T_FEAT}::test_a_blank_point_does_not_cover",
       "Une prévision dont un point d'encadrement est vide ne couvre pas le jour",
       "A run with a blank bracketing point does not cover the day"),
    _b(DQ, f"{T_FEAT}::test_no_covering_run_among_twelve_leaves_the_day_without_a_forecast_and_says_why",
       "Sans prévision couvrante parmi les douze plus récentes, le jour reste sans prévision, avec sa raison",
       "With no covering run among the twelve freshest, the day has no forecast and says why"),
    _b(DQ, f"{T_FEAT}::test_hourly_values_are_linear_in_time_inside_one_run_and_in_celsius",
       "Témoin : les valeurs horaires sont linéaires dans le temps entre les points d'une même prévision, en degrés "
       "Celsius",
       "Control: hourly values are linear in time between one run's points, in degrees Celsius", control=True),
    _b(DQ, f"{T_FEAT}::test_two_runs_are_never_mixed",
       "Les points de deux prévisions ne sont jamais mélangés", "Points of two runs are never mixed"),
    _b(DQ, f"{T_PIPE}::test_a_failing_nbm_gate_stops_the_chain",
       "Une porte de la prévision qui échoue arrête la chaîne", "A failing forecast gate stops the chain"),
    _b(DQ, f"{T_PIPE}::test_data_quality_checks_the_rows_the_loader_returns",
       "La qualité des données vérifie exactement les prévisions que le chargement remet ensuite, et refuse un point "
       "sans heure d'émission au lieu de l'écarter en silence",
       "Data quality checks exactly the forecast rows the loader hands on, and refuses a point without its run time "
       "instead of dropping it silently"),
    _b(DQ, f"{T_PIPE}::test_dq_runs_load_runs_against_its_own_manifest_hash",
       "Des fichiers de prévisions autres que ceux qu'a vérifiés l'exécution de qualité des données sont refusés",
       "Forecast files other than those the data-quality run checked are refused"),
    _b(DQ, f"{T_PIPE}::test_hydroquebec_records_after_the_window_are_set_aside_and_counted",
       "Témoin : les enregistrements d'Hydro-Québec après la fenêtre sont écartés au chargement et comptés",
       "Control: Hydro-Québec records after the window are set aside at load and counted", control=True),
    _b(DQ, f"{T_PIPE}::test_revised_2019_2024_records_are_listed_by_the_2025_data_quality_run",
       "Témoin : les enregistrements de 2019 à 2024 révisés sont listés par l'exécution de qualité des données "
       "jusqu'à 2025",
       "Control: the revised 2019-2024 records are listed by the through-2025 data-quality run", control=True),
    _b(DQ, f"{T_PIPE}::test_the_2025_dq_run_logs_its_revision_artifact",
       "Témoin : l'exécution de qualité des données jusqu'à 2025 consigne la liste des révisions, vide ou non",
       "Control: the through-2025 data-quality run logs its list of revisions, empty or not", control=True),
    _b(DQ, f"{T_PIPE}::test_a_lead_at_or_above_75_hours_is_reported_against_the_two_day_argument",
       "Témoin : une échéance de 75 heures ou plus est signalée contre l'argument des deux jours",
       "Control: a lead of 75 hours or more is reported against the two-day argument", control=True),
    _b(DQ, f"{T_FETCH}::test_every_hydroquebec_request_is_bounded_by_the_portal",
       "Chaque requête à Hydro-Québec, jusqu'à 2024 ou 2025, est bornée par le portail à la fin de l'année de sa "
       "fenêtre, et son compte est demandé sous la même borne",
       "Every Hydro-Québec request, through 2024 or 2025, is bounded by the portal at the end of its window's year "
       "and its count is asked under that same bound"),
    _b(DQ, f"{T_FETCH}::test_a_response_holding_a_record_past_its_bound_is_refused_and_writes_nothing",
       "Une réponse contenant un enregistrement au-delà de sa borne est refusée et rien n'est écrit",
       "A response holding a record past its bound is refused and nothing is written"),
    _b(DQ, f"{T_FETCH}::test_the_bound_is_the_last_hour_of_the_window",
       "Témoin : la borne de chaque requête est la dernière heure de la fenêtre",
       "Control: each request's bound is the window's last hour", control=True),
    _b(DQ, f"{T_FETCH}::test_a_2024_export_ending_at_the_window_s_last_hour_is_written",
       "Témoin : un export de 2024 qui finit à la dernière heure de la fenêtre est écrit",
       "Control: a 2024 export ending at the window's last hour is written", control=True),
    _b(DQ, f"{T_FETCH}::test_a_plain_fetch_never_asks_the_portal_again_for_a_verified_file",
       "Témoin : un téléchargement simple ne redemande jamais au portail un fichier déjà vérifié",
       "Control: a plain fetch never asks the portal again for a verified file", control=True),
    _b(DQ, f"{T_FETCH}::test_a_forced_2025_fetch_of_the_weather_files_is_refused",
       "Un téléchargement forcé de 2025 des fichiers météo est refusé avant toute requête",
       "A forced 2025 fetch of the weather files is refused before any request"),
    _b(DQ, f"{T_CLI}::test_fetch_through_2025_goes_through_the_sources_gate",
       "La commande « fetch » jusqu'à 2025 passe par la porte de chaque source avant toute requête, et refuse, avant "
       "qu'aucune source ne s'exécute, de forcer une autre source qu'Hydro-Québec",
       "The fetch command through 2025 goes through each source's gate before any request, and refuses, before any "
       "source runs, to force a source other than Hydro-Québec"),
    _b(DQ, f"{T_R2}::test_a_day_no_run_fed_is_not_predicted_and_fails_forecast_missing",
       "Un jour qu'aucune prévision météo n'alimente n'est pas prévu et échoue (prévision manquante) : examiner les "
       "données",
       "A day no forecast run fed is not predicted and fails as forecast missing: investigate the data"),
    _b(DQ, f"{T_VAL}::test_a_design_temperature_outside_the_bounds_is_blank_as_in_the_replay",
       "Une température de conception hors des bornes est vide, comme dans le rejeu",
       "A design temperature outside the bounds is blank, as in the replay"),
    # v2: no lookahead, the forecast's availability and the knots.
    _b(LOOK, f"{T_NBM}::test_load_runs_reads_only_the_listed_years",
       "Seuls les fichiers de prévisions des années nommées sont lus",
       "Only the forecast files of the listed years are read"),
    _b(LOOK, f"{T_FEAT}::test_the_freshest_run_available_by_the_origin_feeds_the_day",
       "Témoin : la prévision la plus récente disponible à l'origine alimente le jour",
       "Control: the freshest run available by the origin feeds the day", control=True),
    _b(LOOK, f"{T_FEAT}::test_a_run_available_after_the_origin_is_never_used",
       "Une prévision disponible après l'origine n'est jamais utilisée, même absurde, et la suivante l'est",
       "A run available after the origin is never used, even with absurd values, and the next-freshest is"),
    _b(LOOK, f"{T_FEAT}::test_an_undated_run_is_refused_by_the_run_rule",
       "La règle du choix de la prévision refuse une prévision sans heure d'émission : sa disponibilité ne peut être "
       "établie",
       "The run rule refuses a forecast run without its run time: its availability cannot be established"),
    _b(LOOK, f"{T_FEAT}::test_the_eve_of_the_design_edge_keeps_its_forecast",
       "Témoin : le 31 décembre 2023 est prévu par sa propre prévision de 01 h UTC, qui lit son point du 1er janvier "
       "2024 à 06 h UTC",
       "Control: 31 December 2023 is forecast from its own 01 UTC run, which reads that run's 1 January 2024 06 UTC "
       "point", control=True),
    _b(LOOK, f"{T_FEAT}::test_build_without_forecasts_keeps_every_v1_column",
       "Témoin : sans prévision, la table de v1 garde chaque colonne et chaque valeur",
       "Control: without a forecast, the v1 table keeps every column and every value", control=True),
    _b(LOOK, f"{T_FEAT}::test_build_refuses_a_finite_forecast_without_its_availability",
       "La table des variables refuse une prévision météo finie dont la prévision n'a pas d'heure de disponibilité, "
       "ou une heure postérieure à l'origine de sa ligne",
       "The feature table refuses a finite weather forecast whose run has no availability time, or one after its "
       "row's origin"),
    _b(LOOK, f"{T_GRD}::test_a_row_whose_forecast_became_available_after_its_origin_is_refused",
       "Une ligne dont la prévision est devenue disponible après son origine est refusée",
       "A row whose forecast became available after its origin is refused"),
    _b(LOOK, f"{T_GRD}::test_a_forecast_available_at_the_origin_passes",
       "Témoin : une prévision disponible à l'origine même est acceptée",
       "Control: a forecast available at the origin itself passes", control=True),
    _b(LOOK, f"{T_GRD}::test_a_finite_forecast_without_its_availability_is_refused",
       "Une ligne dont la prévision météo n'a pas d'heure de disponibilité est refusée",
       "A row whose weather forecast has no availability time is refused"),
    _b(LOOK, f"{T_GRD}::test_a_row_without_its_origin_is_refused",
       "Une ligne sans origine est refusée, même avec une prévision disponible 24 heures après son heure cible, et "
       "même sans aucune entrée de prévision",
       "A row without its origin is refused, even with a forecast available 24 hours after its target hour, and even "
       "with no forecast input at all"),
    _b(LOOK, f"{T_WF}::test_a_fold_trains_only_on_rows_whose_forecast_was_known_at_their_origin",
       "La validation glissante refuse, avant tout ajustement, une ligne d'entraînement d'un pli, même fournie à part, "
       "dont la prévision n'a pas de disponibilité, est disponible après son origine, ou qui n'a pas d'origine",
       "Walk-forward refuses, before any fit, a fold's training row, even one supplied separately, whose forecast has "
       "no availability or became available after its origin, or that has no origin"),
    _b(LOOK, f"{T_WF}::test_a_fold_forecasts_only_rows_whose_forecast_was_known_at_their_origin",
       "La validation glissante refuse, avant tout ajustement, une ligne de test d'un pli dont la prévision n'a pas de "
       "disponibilité, est disponible après son origine, ou qui n'a pas d'origine : elle n'atteint jamais "
       "l'étalonnage des intervalles",
       "Walk-forward refuses, before any fit, a fold's test row whose forecast has no availability or became "
       "available after its origin, or that has no origin: it never reaches the interval calibration"),
    _b(LOOK, f"{T_MOD}::test_a_fit_on_a_forecast_without_its_availability_is_refused",
       "Un ajustement sur la prévision météo, le GAM ou les moindres carrés, refuse avant de placer ses nœuds une "
       "ligne dont la prévision n'a pas de disponibilité, est disponible après son origine, ou qui n'a pas d'origine",
       "A fit on the weather forecast, the GAM or least squares, refuses before placing its knots a row whose "
       "forecast has no availability or became available after its origin, or that has no origin"),
    _b(LOOK, f"{T_MOD}::test_a_fit_whose_knots_saw_a_test_row_is_refused",
       "Des nœuds de spline placés aussi sur des heures de test font refuser l'ajustement, même pour une seule heure "
       "de test qui ne déplace aucun nœud d'un centième de degré ; des nœuds décalés de 1,1e-12 °C de la règle sont "
       "refusés",
       "Spline knots placed on test hours as well are refused by the fit, even for a single test hour that moves no "
       "knot by a hundredth of a degree; knots 1.1e-12 °C off the rule are refused"),
    _b(LOOK, f"{T_MOD}::test_a_fit_on_its_own_rows_passes_the_knot_check",
       "Témoin : un ajustement sur ses seules heures utilisables passe la vérification des nœuds, à sa tolérance de "
       "1e-12 °C près, et prévoit encore les heures sans cible",
       "Control: a fit on its own usable hours passes the knot check, to its 1e-12 °C tolerance, and still forecasts "
       "the hours without a target", control=True),
    _b(LOOK, f"{T_FRO}::test_a_freeze_whose_knots_are_not_the_rule_on_its_training_rows_is_refused",
       "Un gel dont les nœuds ne suivent pas la règle sur ses propres lignes d'entraînement est refusé",
       "A freeze whose knots are not the rule on its own training rows is refused"),
    _b(LOOK, f"{T_EVAL}::test_the_eve_of_the_replayed_year_never_calibrates_a_half_width",
       "La veille de l'année rejouée ne calibre jamais une demi-largeur, ni au gel de 2024 ni à celui de 2023",
       "The eve of the replayed year never calibrates a half-width, in the 2024 freeze or the 2023 one"),
    _b(LOOK, f"{T_EVAL}::test_rows_without_a_day_calibrate_no_half_width",
       "Des erreurs hors échantillon qui ne disent pas leur jour ne calibrent aucune demi-largeur",
       "Out-of-sample rows that do not say their day calibrate no half-width"),
    _b(LOOK, f"{T_FRO}::test_the_2024_freeze_half_width_leaves_the_eve_out",
       "La demi-largeur du gel de 2024 laisse de côté la veille de l'année rejouée",
       "The 2024 freeze's half-width leaves out the eve of the replayed year"),
    _b(LOOK, f"{T_VAL}::test_a_forecast_run_available_after_the_design_window_is_refused",
       "Une alimentation de conception contenant une prévision disponible après la fenêtre de conception est refusée",
       "A design feed holding a forecast run available after the design window is refused"),
    _b(LOOK, f"{T_VAL}::test_design_code_reads_only_the_design_years_run_files",
       "Le code de conception ne lit que les fichiers de prévisions des années de conception",
       "Design code reads only the design years' forecast run files"),
    _b(LOOK, f"{T_VAL}::test_the_2024_boundary_run_in_the_2023_file_is_kept_when_available_in_time",
       "Témoin : la prévision de 01 h UTC du 1er janvier 2024, dans le fichier de 2023 et disponible à temps, est "
       "gardée",
       "Control: the 1 January 2024 01 UTC run in the 2023 file, available in time, is kept", control=True),
    _b(LOOK, f"{T_VAL}::test_cold_c_comes_from_design_forecast_temperatures_only",
       "Le seuil de froid vient des seules températures prévues de la période de conception",
       "The cold threshold comes from the design forecast temperatures only"),
    _b(LOOK, f"{T_VAL}::test_run_validate_binds_every_key_validation_inputs_returns",
       "La validation reçoit chaque entrée qu'elle prépare et ne demande que les années de conception",
       "Validation binds every input it prepares and asks the loader for the design years only"),
    _b(LOOK, f"{T_VAL}::test_the_forecast_record_is_logged_per_design_day",
       "Témoin : le registre de la prévision est consigné pour chaque jour de conception",
       "Control: the forecast record is logged for every design day", control=True),
    _b(LOOK, f"{T_ASOF}::test_the_design_forecast_temperature_at_each_origin_uses_only_runs_available_by_then",
       "La température prévue à chaque origine de conception n'utilise que les prévisions disponibles à ce moment",
       "Each design origin's forecast temperature uses only the runs available by then"),
    _b(LOOK, f"{T_R2}::test_the_replay_and_validation_feed_the_same_run_on_a_sparse_archive",
       "Sur une archive lacunaire, le rejeu et la validation retiennent la même prévision pour chaque jour",
       "On a sparse archive, the replay and validation feed each day from the same run"),
    _b(LOOK, f"{T_R2}::test_a_temperature_drift_reference_holding_replayed_days_is_refused",
       "Une référence de dérive de température contenant des jours rejoués est refusée",
       "A temperature-drift reference holding replayed days is refused"),
    _b(LOOK, f"{T_R2}::test_comparators_are_scored_on_the_inputs_recorded_at_issue",
       "Les modèles de comparaison sont notés sur les entrées enregistrées à l'émission",
       "The comparison models are scored on the inputs recorded at issue"),
    _b(LOOK, f"{T_R2}::test_a_rehearsal_whose_issued_forecast_differs_from_validation_s_is_refused",
       "Une répétition dont la prévision émise diffère de celle de la validation est refusée",
       "A rehearsal whose issued forecast differs from validation's is refused"),
    _b(LOOK, f"{T_R2}::test_the_2023_rehearsal_runs_end_to_end_and_refuses_an_input_validation_did_not_issue",
       "La répétition de 2023 s'exécute de bout en bout et refuse une entrée que la validation n'a pas émise",
       "The 2023 rehearsal runs end to end and refuses an input validation did not issue"),
    _b(LOOK, f"{T_R2}::test_the_2024_replay_reads_no_record_of_2025",
       "Le rejeu de 2024 ne lit aucun enregistrement de 2025", "The 2024 replay reads no record of 2025"),
    _b(LOOK, f"{T_R2}::test_the_evidence_replay_receives_no_record_of_2025",
       "Le rejeu de la preuve ne reçoit aucun enregistrement de 2025",
       "The evidence's replay receives no record of 2025"),
    # v2: the replay's clock on the forecast feed.
    _b(CLOCK, f"{T_R2}::test_the_replay_sees_a_run_only_once_it_is_available",
       "Le rejeu ne voit une prévision qu'une fois disponible, et le verdict des jours non plus",
       "The replay sees a forecast run only once it is available, and so does the day verdict"),
    # v2: the monitors on the forecast input, and the decision rule of plan section 8.
    _b(MON, f"{T_VAL}::test_drift_temperature_thresholds_read_the_forecast_input",
       "Les seuils de dérive de température lisent la prévision émise, pas l'observation",
       "The temperature drift thresholds read the issued forecast, not the observation"),
    _b(MON, f"{T_VAL}::test_a_day_no_run_fed_fails_forecast_missing_in_the_verdicts_validation_calibrates_on",
       "Un jour qu'aucune prévision n'a alimenté échoue au critère « prévision manquante » dans les verdicts du jour "
       "que lit le calibrage de la validation, comme dans le rejeu",
       "A day no forecast fed fails the forecast-missing criterion in the day verdicts validation's calibration reads, "
       "as in the replay"),
    _b(MON, f"{T_R2}::test_forecast_input_drift_with_performance_inside_yields_investigate",
       "Une dérive de la température prévue, performance dans les bornes, donne « examiner les données »",
       "Drift of the forecast temperature input, with performance inside, yields investigate the data"),
    _b(MON, f"{T_R2}::test_the_reuse_measure_feeds_one_corrupted_run_to_two_days",
       "Témoin : la mesure de réutilisation fait alimenter deux jours par une même prévision faussée",
       "Control: the reuse measure feeds one corrupted run to two days", control=True),
    _b(MON, f"{T_R2}::test_the_reuse_measure_never_claims_two_days_from_a_run_that_misses_one",
       "La mesure de réutilisation ne présente jamais comme deux jours une prévision qui en manque un",
       "The reuse measure never reports two days from a run that misses one"),
    _b(MON, f"{T_R2}::test_replay_without_runs_is_v1_s",
       "Témoin : sans prévisions météo, le rejeu est celui de la v1",
       "Control: without forecast runs, the replay is v1's", control=True),
    _b(MON, f"{T_R2}::test_the_decision_rule_runs_on_the_replayed_year_with_its_label",
       "Témoin : la règle de décision s'applique à l'année rejouée, avec son étiquette d'évaluation",
       "Control: the decision rule runs on the replayed year, with its evaluation label", control=True),
    _b(MON, f"{T_SEN}::test_the_v2_measures_run_with_the_forecast_feed",
       "Témoin : les mesures de sensibilité de v2 s'exécutent avec le flux de prévisions",
       "Control: v2's sensitivity measures run with the forecast feed", control=True),
    _b(MON, f"{T_DEC}::test_a_contrast_whose_interval_lies_below_zero_is_better",
       "Un contraste dont l'intervalle est sous zéro est meilleur",
       "A contrast whose interval lies below zero is better"),
    _b(MON, f"{T_DEC}::test_a_contrast_whose_interval_lies_above_zero_is_worse",
       "Un contraste dont l'intervalle est au-dessus de zéro est moins bon",
       "A contrast whose interval lies above zero is worse"),
    _b(MON, f"{T_DEC}::test_a_contrast_whose_interval_holds_zero_is_not_separated",
       "Un contraste dont l'intervalle contient zéro n'est pas départagé",
       "A contrast whose interval holds zero is not separated"),
    _b(MON, f"{T_DEC}::test_a_bound_printing_as_zero_is_not_separated",
       "Une borne qui s'imprime 0,00 point ne départage pas et une borne qui s'imprime 0,01 point départage, des deux "
       "côtés : l'état suit les bornes imprimées",
       "A bound that prints as 0.00 points does not separate and one that prints as 0.01 points does, on both sides: "
       "the state follows the printed bounds"),
    _b(MON, f"{T_DEC}::test_a_better_contrast_whose_cold_tail_is_not_worse_is_better[shipped]",
       "Un contraste meilleur dont la queue froide n'est pas moins bonne reçoit le verdict meilleur, contre v1 tel que "
       "livré",
       "A better contrast whose cold tail is not worse gets the verdict better, against v1 as shipped"),
    _b(MON, f"{T_DEC}::test_a_better_contrast_whose_cold_tail_is_not_worse_is_better[family]",
       "Un contraste meilleur dont la queue froide n'est pas moins bonne reçoit le verdict meilleur, contre la forme "
       "de v1 sur la prévision",
       "A better contrast whose cold tail is not worse gets the verdict better, against v1's form on the forecast"),
    _b(MON, f"{T_DEC}::test_a_better_contrast_whose_cold_tail_is_not_testable_is_better_cold_untested[shipped]",
       "Un contraste meilleur dont la queue froide n'est pas testable reçoit le verdict meilleur en moyenne, queue "
       "froide non testable, contre v1 tel que livré",
       "A better contrast whose cold tail is not testable gets the verdict better on average, the cold tail not "
       "testable, against v1 as shipped"),
    _b(MON, f"{T_DEC}::test_a_better_contrast_whose_cold_tail_is_not_testable_is_better_cold_untested[family]",
       "Un contraste meilleur dont la queue froide n'est pas testable reçoit le verdict meilleur en moyenne, queue "
       "froide non testable, contre la forme de v1 sur la prévision",
       "A better contrast whose cold tail is not testable gets the verdict better on average, the cold tail not "
       "testable, against v1's form on the forecast"),
    _b(MON, f"{T_DEC}::test_a_contrast_not_separated_or_better_with_a_worse_cold_tail_is_not_shown_better[shipped]",
       "Un contraste non départagé, ou meilleur avec une queue froide moins bonne, reçoit le verdict pas montré "
       "meilleur, contre v1 tel que livré",
       "A contrast not separated, or better with a worse cold tail, gets the verdict not shown better, against v1 as "
       "shipped"),
    _b(MON, f"{T_DEC}::test_a_contrast_not_separated_or_better_with_a_worse_cold_tail_is_not_shown_better[family]",
       "Un contraste non départagé, ou meilleur avec une queue froide moins bonne, reçoit le verdict pas montré "
       "meilleur, contre la forme de v1 sur la prévision",
       "A contrast not separated, or better with a worse cold tail, gets the verdict not shown better, against v1's "
       "form on the forecast"),
    _b(MON, f"{T_DEC}::test_a_worse_contrast_is_worse[shipped]",
       "Un contraste moins bon reçoit le verdict moins bon, contre v1 tel que livré",
       "A worse contrast gets the verdict worse, against v1 as shipped"),
    _b(MON, f"{T_DEC}::test_a_worse_contrast_is_worse[family]",
       "Un contraste moins bon reçoit le verdict moins bon, contre la forme de v1 sur la prévision",
       "A worse contrast gets the verdict worse, against v1's form on the forecast"),
    _b(MON, f"{T_DEC}::test_a_verdict_on_an_unknown_state_is_refused",
       "Un verdict sur un état inconnu est refusé", "A verdict on an unknown state is refused"),
    _b(MON, f"{T_DEC}::test_v2_closer_on_every_hour_is_better_on_both_comparisons",
       "Témoin : un v2 plus proche à chaque heure est meilleur sur les deux comparaisons",
       "Control: a v2 closer on every hour is better on both comparisons", control=True),
    _b(MON, f"{T_DEC}::test_a_contrast_is_scored_on_the_common_hours_only",
       "Des heures où un modèle a une prévision et un autre non, loin de la cible et froides, ne changent ni les "
       "contrastes ni la queue froide : tout est mesuré sur les heures communes",
       "Hours where one model has a forecast and another has none, far off and cold, change neither the contrasts nor "
       "the cold tail: everything is scored on the common hours"),
    _b(MON, f"{T_DEC}::test_the_contrasts_are_errors_hourly_ape_at_the_ruled_bootstrap",
       "Les contrastes qui décident des verdicts, ceux de la queue froide compris, sont la différence appariée de "
       "l'erreur absolue horaire rapportée à la demande observée, sur les heures communes, à mille "
       "rééchantillonnages par semaines et graine 0 : une autre métrique, une autre graine ou un autre nombre de "
       "rééchantillonnages échoue",
       "The contrasts that decide the verdicts, the cold tail's included, are the paired difference in hourly absolute "
       "error over the observed demand, on the common hours, at a thousand week-block resamples and seed 0: another "
       "metric, seed or number of resamples fails"),
    _b(MON, f"{T_DEC}::test_the_three_contrasts_are_additive",
       "Sur les mêmes heures, les trois contrastes s'additionnent : changement entier = famille + entrée",
       "On one set of hours the three contrasts add up: the whole change = family + input"),
    _b(MON, f"{T_DEC}::test_the_peak_hours_are_the_busiest_of_the_year_with_missing_forecasts_kept",
       "Les cinq heures les plus chargées sont prises sur toute l'année selon la demande, une prévision manquante "
       "gardée avec sa raison, et des heures plus calmes ne les remplacent pas",
       "The five busiest hours are taken over the whole year by demand, a missing forecast kept with its reason, and "
       "quieter hours never replace them"),
    _b(MON, f"{T_DEC}::test_the_cold_tail_needs_120_hours_in_at_least_4_weeks",
       "La queue froide n'est pas testable à 119 heures en 6 semaines, ni à 120 heures en 3 semaines, ni à 119 heures "
       "sous le seuil plus une au seuil ; elle l'est à 120 heures en 4 semaines",
       "The cold tail is not testable at 119 hours in 6 weeks, at 120 hours in 3 weeks, or at 119 hours below the "
       "threshold plus one at it; it is at 120 hours in 4 weeks"),
    _b(MON, f"{T_DEC}::test_the_cold_tail_is_not_testable_at_119_hours_and_testable_at_120",
       "La queue froide n'est pas testable à 119 heures froides et l'est à 120, et le verdict le dit",
       "The cold tail is not testable at 119 cold hours and is at 120, and the verdict says so"),
    _b(MON, f"{T_DEC}::test_a_better_contrast_with_a_worse_cold_tail_is_not_shown_better",
       "Un v2 meilleur en moyenne mais moins bon sur les heures froides n'est pas montré meilleur",
       "A v2 better on average but worse on the cold hours is not shown better"),
    _b(MON, f"{T_DEC}::test_the_cold_threshold_reads_inputs_only",
       "Le seuil froid ne lit que les températures prévues : changer toutes les demandes ne le change pas",
       "The cold threshold reads the forecast temperatures only: changing every demand value leaves it unchanged"),
    _b(MON, f"{T_DEC}::test_the_status_follows_the_test_of_record",
       "Le statut de la fiche du modèle suit le test de référence : candidat sans départ de 2025, inachevé tant qu'il "
       "n'a pas fini, quelle que soit sa position, même avant qu'elle soit consignée, a posteriori s'il l'est, un "
       "statut sur données réservées seulement sur un 2025 propre et fini ; un test fini dont la position n'est ni "
       "propre ni a posteriori est refusé",
       "The model card's status follows the test of record: candidate with no 2025 start, unfinished until it "
       "finishes whatever its standing, even before its standing is logged, post hoc when it is, a held-out status "
       "only on a finished clean 2025; a finished test whose standing is neither clean nor post hoc is refused"),
    _b(MON, f"{T_DEC}::test_only_a_clean_test_of_record_is_labelled_held_out",
       "Seul un test de référence propre est étiqueté « données réservées » : un test de référence a posteriori est "
       "étiqueté a posteriori, un départ qui suit un départ fini est vu par les développeurs, et un départ qui suit "
       "un départ arrêté sans la décision du propriétaire qui le nomme est refusé",
       "Only a clean test of record is labelled held out: a post-hoc one is labelled post hoc, a start after a "
       "finished one is seen by the developers, and a start after a stopped one without the owner's ruling naming it "
       "is refused"),
    # v2: the monitor never acts, the comparison models included.
    _b(ACT, f"{T_R2}::test_comparators_crossing_their_thresholds_yield_no_recommendation",
       "Témoin : des modèles de comparaison hors de leurs seuils ne déclenchent aucune recommandation",
       "Control: comparison models crossing their thresholds yield no recommendation", control=True),
    _b(ACT, f"{T_R2}::test_a_comparator_changed_during_the_replay_fails_it",
       "Un modèle de comparaison modifié pendant le rejeu le fait échouer",
       "A comparison model changed during the replay fails it"),
    # v2: the frozen models.
    _b(FREEZE, f"{T_MOD}::test_the_gam_predicts_what_the_ruled_pipeline_predicts",
       "Le GAM prévoit ce que prévoit le pipeline scikit-learn tranché par le propriétaire, mêmes colonnes, même "
       "normalisation et même ridge, extrapolation sous et au-dessus de la plage d'entraînement comprise",
       "The GAM forecasts what the scikit-learn pipeline the owner ruled forecasts, with the same columns, scaler and "
       "ridge, extrapolation below and above the training range included"),
    _b(FREEZE, f"{T_WF}::test_models_none_is_v1_exactly",
       "Témoin : sans les modèles de v2, la validation glissante reste exactement celle de v1",
       "Control: without v2's models, the walk-forward is exactly v1's", control=True),
    _b(FREEZE, f"{T_VAL}::test_models_none_is_v1_validation_exactly",
       "Témoin : sans les modèles de v2, la validation est exactement celle de v1",
       "Control: without the v2 models, validation is exactly v1's", control=True),
    _b(FREEZE, f"{T_FRO}::test_a_frozen_gam_round_trips_through_json_and_predicts_as_fitted",
       "Témoin : le GAM figé, relu de son fichier, prévoit ce que prévoient le GAM ajusté et le pipeline scikit-learn "
       "tranché, construit à part, au-delà de la plage d'entraînement compris",
       "Control: the frozen GAM, read back from its file, forecasts what the fitted GAM and the ruled scikit-learn "
       "pipeline, built independently, forecast, past the training range included", control=True),
    _b(FREEZE, f"{T_FRO}::test_a_frozen_gam_with_an_altered_knot_scale_or_coefficient_is_refused",
       "Un GAM figé dont un nœud, une échelle, un coefficient ou tout autre champ de son empreinte a changé est refusé",
       "A frozen GAM whose knot, scale, coefficient or any other hashed field changed is refused"),
    _b(FREEZE, f"{T_FRO}::test_a_frozen_gam_with_another_degree_extrapolation_or_bias_is_refused",
       "Un GAM figé d'un autre degré, d'une autre extrapolation ou d'un autre réglage de biais est refusé, et chacun "
       "de ces réglages change l'empreinte",
       "A frozen GAM with another degree, extrapolation or bias setting is refused, and each setting changes the hash"),
    _b(FREEZE, f"{T_FRO}::test_a_frozen_gam_whose_knots_are_not_ten_finite_increasing_values_is_refused",
       "Un GAM figé dont les nœuds ne sont pas dix valeurs finies strictement croissantes est refusé",
       "A frozen GAM whose knots are not ten finite, strictly increasing values is refused"),
    _b(FREEZE, f"{T_FRO}::test_a_frozen_gam_forecasts_from_its_file_whatever_the_module_now_says[extrapolation]",
       "Le GAM figé prévoit à partir de son seul fichier : l'extrapolation du module changée après le gel ne change ni "
       "sa prévision ni ses courbes, et son fichier relu est refusé",
       "The frozen GAM forecasts from its file alone: the module's extrapolation changed after the freeze changes "
       "neither its forecast nor its curves, and its file read again is refused"),
    _b(FREEZE, f"{T_FRO}::test_a_frozen_gam_forecasts_from_its_file_whatever_the_module_now_says[degree]",
       "Le GAM figé prévoit à partir de son seul fichier : le degré du module changé après le gel ne change ni sa "
       "prévision ni ses courbes, et son fichier relu est refusé",
       "The frozen GAM forecasts from its file alone: the module's spline degree changed after the freeze changes "
       "neither its forecast nor its curves, and its file read again is refused"),
    _b(FREEZE, f"{T_FRO}::test_a_frozen_gam_forecasts_from_its_file_whatever_the_module_now_says[include_bias]",
       "Le GAM figé prévoit à partir de son seul fichier : la colonne de biais du module changée après le gel ne "
       "change ni sa prévision ni ses courbes, et son fichier relu est refusé",
       "The frozen GAM forecasts from its file alone: the module's bias setting changed after the freeze changes "
       "neither its forecast nor its curves, and its file read again is refused"),
    _b(FREEZE, f"{T_FRO}::test_a_frozen_gam_forecasts_from_its_file_whatever_the_module_now_says[n_knots]",
       "Le GAM figé prévoit à partir de son seul fichier : le nombre de nœuds du module changé après le gel ne change "
       "ni sa prévision ni ses courbes, et son fichier relu est refusé",
       "The frozen GAM forecasts from its file alone: the module's number of knots changed after the freeze changes "
       "neither its forecast nor its curves, and its file read again is refused"),
    _b(FREEZE, f"{T_FRO}::test_a_frozen_gam_forecasts_from_its_file_whatever_the_module_now_says[day_types]",
       "Le GAM figé prévoit à partir de son seul fichier : les types de jour du module remis dans un autre ordre après "
       "le gel, son fichier se relit sous la même empreinte et prévoit exactement la même chose",
       "The frozen GAM forecasts from its file alone: with the module's day types reordered after the freeze, its "
       "file reads back under the same hash and forecasts exactly the same"),
    _b(FREEZE, f"{T_FRO}::test_a_frozen_gam_forecasts_from_its_file_whatever_the_module_now_says[day_types_added]",
       "Le GAM figé prévoit à partir de son seul fichier : un type de jour ajouté au module après le gel, son fichier "
       "se relit sous la même empreinte et prévoit exactement la même chose",
       "The frozen GAM forecasts from its file alone: with a day type added to the module after the freeze, its file "
       "reads back under the same hash and forecasts exactly the same"),
    _b(FREEZE, f"{T_FRO}::test_a_frozen_least_squares_forecasts_from_its_file_whatever_the_module_now_says"
               "[hour_blocks]",
       "Les moindres carrés figés prévoient à partir de leur seul fichier : les blocs horaires du module changés après "
       "le gel (l'heure 6 passée de la matinée à la nuit) ne changent ni leur prévision ni leurs courbes",
       "Frozen least squares forecast from their file alone: the module's hour blocks changed after the freeze (hour 6 "
       "moved from the morning to the night) change neither their forecast nor their curves"),
    _b(FREEZE, f"{T_FRO}::test_a_frozen_least_squares_forecasts_from_its_file_whatever_the_module_now_says[base_c]",
       "Les moindres carrés figés prévoient à partir de leur seul fichier : la température de base du module changée "
       "après le gel, et les degrés-heures de la table calculés avec elle, ne changent ni leur prévision ni leurs "
       "courbes",
       "Frozen least squares forecast from their file alone: the module's base temperature changed after the freeze, "
       "and the table's degree-hours computed with it, change neither their forecast nor their curves"),
    _b(FREEZE, f"{T_FRO}::test_a_frozen_least_squares_stores_and_hashes_its_hour_blocks_and_base",
       "Les blocs horaires et la température de base des moindres carrés figés sont dans leur fichier et leur "
       "empreinte : une autre prévision vient avec une autre empreinte, et un fichier modifié est refusé",
       "Frozen least squares' hour blocks and base temperature are in their file and their hash: another forecast "
       "comes with another hash, and an edited file is refused"),
    _b(FREEZE, f"{T_FRO}::test_a_frozen_least_squares_with_blocks_or_a_base_it_cannot_use_is_refused",
       "Des moindres carrés figés dont les blocs ne partagent pas les heures 0 à 23, dont la base n'est pas un nombre "
       "fini, ou dont les colonnes ne sont pas celles que leurs blocs engendrent, sont refusés",
       "Frozen least squares whose blocks do not partition the hours 0 to 23, whose base is not a finite number, or "
       "whose columns are not those their blocks generate, are refused"),
    _b(FREEZE, f"{T_FRO}::test_a_row_whose_day_type_or_hour_is_not_known_is_never_forecast",
       "Une ligne sans type de jour ou d'une heure hors de 0 à 23 n'est jamais prévue, et un type de jour que les "
       "cellules stockées ne couvrent pas est refusé, pour les deux familles",
       "A row without a day type or with an hour outside 0 to 23 is never forecast, and a day type the stored cells do "
       "not cover is refused, for both families"),
    _b(FREEZE, f"{T_FRO}::test_an_hour_given_as_text_is_forecast_as_its_number",
       "Une heure donnée en texte (« 6 ») est l'heure 6 partout : ses cellules, ses blocs et ses courbes sont ceux de "
       "l'heure entière, jamais une ligne tenue pour connue et prévue de ses seuls retards, pour les deux familles",
       "An hour given as text (\"6\") is hour 6 everywhere: its cells, blocks and curves are those of the whole hour, "
       "never a row counted as known and forecast from its lags alone, for both families"),
    _b(FREEZE, f"{T_FRO}::test_a_frozen_least_squares_predicts_as_the_previous_formula_on_the_pipeline_s_rows",
       "Témoin : sur les lignes que produit la chaîne, les moindres carrés figés prévoient exactement ce que prévoyait "
       "la formule précédente (0 MW d'écart)",
       "Control: on the rows the pipeline produces, frozen least squares forecast exactly what the previous formula "
       "forecast (0 MW apart)", control=True),
    _b(FREEZE, f"{T_R2}::test_the_frozen_least_squares_score_the_replay_as_the_previous_formula",
       "Témoin : les modèles de comparaison notent chaque heure rejouée exactement comme la formule précédente "
       "(0 MW d'écart)",
       "Control: the comparison models score every replayed hour exactly as the previous formula did (0 MW apart)",
       control=True),
    _b(FREEZE, f"{T_FRO}::test_a_frozen_gam_reads_its_cells_in_their_stored_order_and_refuses_columns_it_cannot_"
               "rebuild",
       "Un GAM figé dont les colonnes ne sont pas celles que ses nœuds, ses réglages et ses types de jour engendrent, "
       "nom pour nom et dans l'ordre, est refusé à la prévision ; écrit avec ses types de jour dans un autre ordre, il "
       "prévoit la même chose",
       "A frozen GAM whose columns are not those its knots, settings and day types generate, name for name and in "
       "order, is refused when it forecasts; written with its day types in another order, it forecasts the same"),
    _b(FREEZE, f"{T_FRO}::test_a_production_model_with_a_lag_coefficient_not_positive_is_refused",
       "Un modèle de production dont un coefficient de retard n'est pas positif est refusé",
       "A production model with a lag coefficient that is not positive is refused"),
    _b(FREEZE, f"{T_FRO}::test_the_freeze_refuses_a_production_model_whose_lag_coefficient_is_not_positive",
       "Le gel refuse, avant toute consignation, un modèle de production dont un coefficient de retard n'est pas "
       "positif",
       "The freeze refuses, before logging anything, a production model whose lag coefficient is not positive"),
    _b(FREEZE, f"{T_FRO}::test_a_frozen_ols_keeps_v1_s_forecast",
       "Témoin : les moindres carrés figés gardent la prévision de v1",
       "Control: the frozen least squares keep v1's forecast", control=True),
    _b(FREEZE, f"{T_FRO}::test_comparators_are_logged_and_reloaded_against_their_hashes",
       "Un modèle de comparaison modifié, remplacé par un autre modèle, figé pour une autre année ou manquant est "
       "refusé",
       "A comparison model that was edited, replaced by another model, frozen for another year or missing is refused"),
    # v2: the freeze gate, the tags and the records.
    _b(FREEZE, f"{T_PIPE}::test_a_real_data_run_without_the_first_run_tag_is_refused",
       "Un calcul sur données réelles hors de la descendance de l'étiquette v2-first-run est refusé",
       "A real-data run from a commit that does not descend from the v2-first-run tag is refused"),
    _b(FREEZE, f"{T_PIPE}::test_a_real_data_run_from_a_descendant_of_the_first_run_tag_passes",
       "Témoin : un calcul sur données réelles depuis un descendant de l'étiquette v2-first-run passe",
       "Control: a real-data run from a descendant of the v2-first-run tag passes", control=True),
    _b(FREEZE, f"{T_FETCH}::test_a_2025_fetch_before_the_freeze_is_refused",
       "Un téléchargement de 2025 avant le gel est refusé", "A 2025 fetch before the freeze is refused"),
    _b(FREEZE, f"{T_FETCH}::test_a_2025_fetch_from_a_dirty_tree_is_refused",
       "Un téléchargement de 2025 depuis un arbre de travail aux changements non validés est refusé",
       "A 2025 fetch from a tree with uncommitted changes is refused"),
    _b(FREEZE, f"{T_FETCH}::test_a_partial_2025_export_is_refused_and_writes_nothing",
       "Un export de 2025 incomplet est refusé et rien n'est écrit",
       "A partial 2025 export is refused and nothing is written"),
    _b(FREEZE, f"{T_FRZ}::test_a_dependency_changed_after_the_v2_tag_is_refused_and_named",
       "Une dépendance modifiée après l'étiquette de gel de v2 est refusée et nommée",
       "A dependency changed after the v2 freeze tag is refused and named"),
    _b(FREEZE, f"{T_FRZ}::test_v1_replays_never_count_as_a_moved_v2_tag",
       "Témoin : les rejeux de v1 ne comptent jamais comme un déplacement de l'étiquette de gel de v2",
       "Control: v1's replays never count as a moved v2 freeze tag", control=True),
    _b(FREEZE, f"{T_FRZ}::test_a_v2_replay_under_a_moved_v2_tag_is_refused_unless_recorded",
       "Un rejeu de v2 sous une étiquette de gel de v2 déplacée est refusé sauf dérogation consignée",
       "A v2 replay under a moved v2 freeze tag is refused unless the departure is recorded"),
    _b(FREEZE, f"{T_FRZ}::test_a_2025_replay_run_on_its_own_sees_a_tag_moved_since_v2_s_2024_replay",
       "Un rejeu de 2025 lancé seul voit une étiquette déplacée depuis le rejeu de 2024 de v2 : la vérification suit "
       "le gel, pas l'année",
       "A 2025 replay run on its own sees a tag moved since v2's 2024 replay: the check is scoped by freeze, not by "
       "year"),
    _b(FREEZE, f"{T_FRZ}::test_an_unfinished_start_counts_for_the_moved_tag_check",
       "Un départ inachevé compte pour la vérification de l'étiquette déplacée",
       "An unfinished start counts for the moved-tag check"),
    _b(FREEZE, f"{T_FRZ}::test_a_stored_replay_without_a_freeze_tag_counts_as_v1_s",
       "Un rejeu stocké sans étiquette de gel compte comme un rejeu de v1",
       "A stored replay without a freeze tag counts as v1's"),
    _b(FREEZE, f"{T_FRZ}::test_every_freeze_tag_in_the_repository_is_listed_in_freezes",
       "Une étiquette nommée comme un gel que la configuration ne liste pas est refusée",
       "A tag named like a freeze that the configuration does not list is refused"),
    _b(FREEZE, f"{T_FRZ}::test_recorded_departures_reads_the_section_of_its_tag",
       "Témoin : les dérogations consignées sont lues dans la section de leur étiquette de gel",
       "Control: recorded departures are read from the section of their freeze tag", control=True),
    _b(FREEZE, f"{T_FRZ}::test_a_v2_departure_entry_naming_only_the_new_freeze_commit_is_refused",
       "Une entrée de dérogation de v2 qui ne nomme que le nouveau commit de gel est refusée : ni le commit d'où "
       "l'étiquette est partie ni le changement de règle entre les deux ne sont consignés",
       "A v2 departure entry naming only the new freeze commit is refused: neither the commit the tag moved from nor "
       "the rule change between them is recorded"),
    _b(FREEZE, f"{T_FRZ}::test_a_v2_departure_entry_not_naming_the_commit_the_tag_moved_from_is_refused",
       "Une entrée de dérogation de v2 qui ne nomme pas le commit de gel d'où l'étiquette est partie, celui d'un rejeu "
       "antérieur, est refusée",
       "A v2 departure entry that does not name the freeze commit the tag moved from, an earlier replay's, is "
       "refused"),
    _b(FREEZE, f"{T_FRZ}::test_a_v2_departure_entry_missing_a_change_commit_is_refused",
       "Une entrée de dérogation de v2 qui omet un commit de règle entre ses deux commits de gel, lu commit par "
       "commit, est refusée et le commit est nommé",
       "A v2 departure entry that leaves out a rule commit between its two freeze commits, read commit by commit, is "
       "refused and the commit named"),
    _b(FREEZE, f"{T_FRZ}::test_a_v2_departure_entry_naming_an_unrelated_commit_is_refused",
       "Une entrée de dérogation de v2 qui nomme un commit de son déplacement ne touchant ni une règle ni une "
       "dépendance est refusée",
       "A v2 departure entry naming a commit of its move that touches no rule file or dependency is refused"),
    _b(FREEZE, f"{T_FRZ}::test_a_v2_departure_record_whose_moves_do_not_join_is_refused",
       "Un registre de dérogations de v2 dont le second déplacement ne part pas du commit où le premier a mené "
       "l'étiquette est refusé",
       "A v2 departure record whose second move does not start at the commit the first moved the tag to is refused"),
    _b(FREEZE, f"{T_FRZ}::test_a_v2_tag_moved_back_without_a_departure_entry_is_refused",
       "Une étiquette de gel de v2 ramenée sur un commit d'arrivée antérieur, sans entrée qui consigne ce "
       "déplacement, est refusée ; sur le dernier commit d'arrivée, elle passe",
       "A v2 freeze tag moved back to an earlier moved-to commit, with no entry recording that move, is refused; on "
       "the last moved-to commit it passes"),
    _b(FREEZE, f"{T_FRZ}::test_the_2025_gate_refuses_an_incomplete_departure_record_before_the_run_opens",
       "La porte de 2025 refuse un registre de dérogations incomplet avant l'ouverture de l'exécution, et rien ne "
       "s'ouvre",
       "The 2025 gate refuses an incomplete departure record before the run opens, and nothing opens"),
    _b(FREEZE, f"{T_DEP}::test_before_the_v2_freeze_a_v2_record_fails",
       "Avant l'étiquette de gel de v2, une entrée du registre sous elle, une section de v2 qui nomme un commit, une "
       "section absente ou une tête qui ne descend pas de v1 font échouer l'historique",
       "Before the v2 freeze tag, a ledger entry under it, a v2 section naming a commit, a missing section or a head "
       "that does not descend from v1 fails the history check"),
    _b(FREEZE, f"{T_DEP}::test_a_v2_departure_naming_the_freeze_commit_it_moved_from_passes",
       "Témoin : une dérogation de v2 qui nomme le commit de gel d'où l'étiquette est partie passe l'historique de v2",
       "Control: a v2 departure naming the freeze commit it moved from passes v2's history check", control=True),
    _b(FREEZE, f"{T_DEP}::test_a_recorded_v2_dependency_departure_passes",
       "Témoin : un changement de dépendance consigné comme dérogation passe l'historique de v2",
       "Control: a dependency change recorded as a departure passes v2's history check", control=True),
    _b(FREEZE, f"{T_DEP}::test_a_v2_rule_change_after_the_tag_fails",
       "Un commit de règle après l'étiquette de gel de v2, non consigné, fait échouer l'historique",
       "A rule commit after the v2 freeze tag, not recorded, fails the history check"),
    _b(FREEZE, f"{T_DEP}::test_a_v2_dependency_change_after_the_tag_fails[pyproject.toml]",
       "Un changement de pyproject.toml après l'étiquette de gel de v2, non consigné, fait échouer l'historique",
       "A pyproject.toml change after the v2 freeze tag, not recorded, fails the history check"),
    _b(FREEZE, f"{T_DEP}::test_a_v2_dependency_change_after_the_tag_fails[uv.lock]",
       "Un changement de uv.lock après l'étiquette de gel de v2, non consigné, fait échouer l'historique",
       "A uv.lock change after the v2 freeze tag, not recorded, fails the history check"),
    _b(FREEZE, f"{T_DEP}::test_a_v2_rule_change_reverted_with_the_tag_unmoved_fails",
       "Un changement de règle annulé ensuite, l'étiquette restée en place, fait échouer l'historique lu commit par "
       "commit",
       "A rule change reverted later, the tag left in place, fails the history read commit by commit"),
    _b(FREEZE, f"{T_DEP}::test_a_v2_departure_missing_a_change_commit_fails",
       "Une dérogation de v2 qui ne nomme pas un commit de changement de son déplacement fait échouer l'historique",
       "A v2 departure that does not name a change commit of its move fails the history check"),
    _b(FREEZE, f"{T_DEP}::test_a_v2_departure_naming_an_unrelated_commit_fails",
       "Une dérogation de v2 qui nomme un commit ni changement de son déplacement ni commit de gel fait échouer "
       "l'historique",
       "A v2 departure naming a commit that is neither a change of its move nor a freeze commit fails the history "
       "check"),
    _b(FREEZE, f"{T_DEP}::test_a_v2_departure_not_naming_its_freeze_commits_fails",
       "Une dérogation de v2 qui ne nomme pas le commit d'où l'étiquette est partie, ou celui où elle est arrivée, "
       "fait échouer l'historique",
       "A v2 departure that does not name the commit the tag moved from, or the one it moved to, fails the history "
       "check"),
    _b(FREEZE, f"{T_DEP}::test_a_v2_move_without_its_departure_entry_fails",
       "Un déplacement de l'étiquette de gel de v2 sans entrée de dérogation, ou une entrée sans déplacement, fait "
       "échouer l'historique",
       "A move of the v2 freeze tag without its departure entry, or an entry with no move, fails the history check"),
    _b(FREEZE, f"{T_DEP}::test_a_v2_section_entry_with_another_head_fails",
       "Une entrée de la section de v2 qui nomme un commit sans être une dérogation ni la décision du propriétaire sur "
       "2025 avec son issue fait échouer l'historique",
       "A v2 section entry naming a commit that is neither a departure nor the owner's ruling on 2025 with its "
       "outcome fails the history check"),
    _b(FREEZE, f"{T_DEP}::test_a_v2_tag_moved_without_its_pin_fails",
       "Une étiquette de gel de v2 déplacée sans que son épingle soit validée fait échouer l'historique dès le commit "
       "suivant",
       "A v2 freeze tag moved without its pin committed fails the history check from the next commit on"),
    _b(FREEZE, f"{T_TAGS}::test_a_pinned_tag_moved_fails",
       "Une étiquette déplacée hors du commit de son épingle fait échouer le test des étiquettes",
       "A tag moved off its pinned commit fails the tags test"),
    _b(FREEZE, f"{T_PLAN}::test_a_rule_change_after_the_first_run_not_named_fails[src/valiwatt/monitor/rules.py]",
       "Un changement de fichier de règles après v2-first-run que la section 18 du plan ne nomme pas fait échouer "
       "l'historique du plan",
       "A rule-file change after v2-first-run that the plan's section 18 does not name fails the plan's history "
       "check"),
    _b(FREEZE, f"{T_PLAN}::test_a_rule_change_after_the_first_run_not_named_fails[pyproject.toml]",
       "Un changement de pyproject.toml après v2-first-run que la section 18 du plan ne nomme pas fait échouer "
       "l'historique du plan",
       "A pyproject.toml change after v2-first-run that the plan's section 18 does not name fails the plan's history "
       "check"),
    _b(FREEZE, f"{T_PLAN}::test_a_rule_change_after_the_first_run_not_named_fails[uv.lock]",
       "Un changement de uv.lock après v2-first-run que la section 18 du plan ne nomme pas fait échouer l'historique "
       "du plan",
       "A uv.lock change after v2-first-run that the plan's section 18 does not name fails the plan's history check"),
    _b(FREEZE, f"{T_PLAN}::test_a_plan_edit_outside_section_18_not_named_fails",
       "Une modification du plan de v2 après plan-v2, hors de sa section 18 et non nommée par elle, fait échouer "
       "l'historique du plan",
       "An edit of v2's plan after plan-v2, outside its section 18 and not named there, fails the plan's history "
       "check"),
    _b(FREEZE, f"{T_PLAN}::test_a_section_18_entry_rewritten_must_be_named",
       "Une entrée de la section 18 du plan réécrite sans être nommée fait échouer l'historique du plan",
       "A section 18 entry of the plan rewritten without being named fails the plan's history check"),
    # v2: the 2025 gate (plan sections 11.3.4 and 11.3.5), its starts, its diagnostics and the evidence.
    _b(FREEZE, f"{T_FRZ}::test_a_clean_2025_start_with_nothing_changed_passes",
       "Témoin : un départ de 2025 propre, rien n'ayant changé, passe la porte",
       "Control: a clean 2025 start with nothing changed passes the gate", control=True),
    _b(FREEZE, f"{T_FRZ}::test_the_2025_gate_walks_every_commit_after_the_first_freeze_commit",
       "La porte de 2025 parcourt chaque commit après le premier commit de gel et refuse un changement de règle ou de "
       "dépendance, même annulé ensuite, en nommant les commits et les fichiers ; un commit vide passe",
       "The 2025 gate walks every commit after the first freeze commit and refuses a rule or dependency change, even "
       "one reverted later, naming the commits and files; an empty commit passes"),
    _b(FREEZE, f"{T_FRZ}::test_the_2025_gate_refuses_a_merge_a_shallow_clone_and_a_head_off_the_freeze_commit",
       "La porte de 2025 refuse une fusion, un clone superficiel et une tête qui ne descend pas du commit de gel",
       "The 2025 gate refuses a merge, a shallow clone and a head that does not descend from the freeze commit"),
    _b(FREEZE, f"{T_FRZ}::test_a_fix_after_a_failed_first_2024_start_refuses_the_2025_gate",
       "Un correctif après l'échec du premier départ de 2024 fait refuser la porte de 2025, qui le nomme",
       "A fix after a failed first 2024 start refuses the 2025 gate, which names it"),
    _b(FREEZE, f"{T_FRZ}::test_an_owner_s_ruling_entry_lifts_a_refusal_with_its_outcome[clean]",
       "La décision du propriétaire, avec sa preuve, lève un refus avec son issue : propre, étiqueté "
       "« données réservées »",
       "The owner's ruling entry, with its evidence, lifts a refusal with its outcome: clean, labelled held out"),
    _b(FREEZE, f"{T_FRZ}::test_an_owner_s_ruling_entry_lifts_a_refusal_with_its_outcome[post_hoc]",
       "La décision du propriétaire, avec sa preuve, lève un refus avec son issue : a posteriori, étiqueté a "
       "posteriori",
       "The owner's ruling entry, with its evidence, lifts a refusal with its outcome: post hoc, labelled post hoc"),
    _b(FREEZE, f"{T_FRZ}::test_a_ruling_entry_that_does_not_apply_leaves_the_refusal",
       "Une décision du propriétaire qui ne s'applique pas laisse le refus : suivie d'un changement de règle, sur un "
       "commit dont un départ antérieur descend, introuvable, ou sans exécution de preuve finie faite d'un arbre "
       "propre",
       "An owner's ruling that does not apply leaves the refusal: followed by a rule change, on a commit an earlier "
       "start descends past, unresolved, or without a finished evidence run made from a clean tree"),
    _b(FREEZE, f"{T_FRZ}::test_a_2025_start_whose_frozen_artifacts_differ_is_refused",
       "Un départ de 2025 dont un artefact figé diffère de la première entrée de 2024 de v2 est refusé",
       "A 2025 start one of whose frozen artifacts differs from v2's first 2024 entry is refused"),
    _b(FREEZE, f"{T_FRZ}::test_a_2025_start_without_its_runs_in_the_store_is_refused",
       "Un départ de 2025 dont les exécutions manquent au magasin, comme dans une copie fraîche, est refusé",
       "A 2025 start whose runs are missing from the store, as in a fresh clone, is refused"),
    _b(FREEZE, f"{T_FRZ}::test_a_2025_gate_refuses_a_data_quality_run_that_is_not_a_passing_through_2025_run",
       "La porte de 2025 refuse un contrôle de qualité des données qui n'est pas un contrôle réussi jusqu'à 2025",
       "The 2025 gate refuses a data-quality run that is not a passing through-2025 run"),
    _b(FREEZE, f"{T_FRZ}::test_a_2025_gate_refuses_a_raw_file_the_data_quality_run_did_not_check",
       "La porte de 2025 refuse un fichier brut que le contrôle de qualité des données n'a pas vérifié, sans lire "
       "aucune demande",
       "The 2025 gate refuses a raw file the data-quality run did not check, reading no demand record"),
    _b(FREEZE, f"{T_FRZ}::test_a_2025_start_while_another_is_running_is_refused",
       "Un départ de 2025 pendant qu'un autre est en cours est refusé",
       "A 2025 start while another is running is refused"),
    _b(FREEZE, f"{T_FRZ}::test_two_simultaneous_2025_starts_open_one_run",
       "Deux départs de 2025 simultanés n'ouvrent qu'une exécution ; l'autre est refusé",
       "Two simultaneous 2025 starts open one run; the other is refused"),
    _b(FREEZE, f"{T_FRZ}::test_a_2025_diagnostic_run_without_its_finished_replay_refuses_the_gate",
       "Une exécution de diagnostic de 2025 sans son rejeu fini et complet fait refuser la porte",
       "A 2025 diagnostic run without its finished, complete replay refuses the gate"),
    _b(FREEZE, f"{T_FRZ}::test_a_2025_replay_without_a_v2_2024_ledger_entry_is_refused",
       "Un rejeu de 2025 sans entrée de 2024 de v2 au registre des rejeux est refusé",
       "A 2025 replay without a v2 2024 entry in the replay ledger is refused"),
    _b(FREEZE, f"{T_FRZ}::test_after_an_unfinished_test_of_record_a_later_start_refuses_until_a_ruling",
       "Après un test de référence inachevé, un départ suivant est refusé, en disant si l'exécution arrêtée tient un "
       "résultat, jusqu'à une décision du propriétaire qui nomme le départ arrêté",
       "After an unfinished test of record, a later start refuses, saying whether the stopped run holds any result, "
       "until an owner's ruling names the stopped start"),
    _b(FREEZE, f"{T_FRZ}::test_a_later_2025_start_is_tagged_seen_by_developers",
       "Un départ de 2025 ultérieur à un test de référence fini est étiqueté « vu par les développeurs »",
       "A 2025 start after a finished test of record is tagged seen by the developers"),
    _b(FREEZE, f"{T_FRZ}::test_the_replay_function_refuses_2025_on_its_own",
       "La fonction de rejeu, appelée seule, refuse 2025", "The replay function, called on its own, refuses 2025"),
    _b(FREEZE, f"{T_R2}::test_the_2025_replay_refuses_until_the_window_reaches_its_end",
       "Le rejeu de 2025 est refusé tant que la fenêtre des données n'atteint pas la fin de 2025",
       "The 2025 replay is refused until the data window reaches the end of 2025"),
    _b(FREEZE, f"{T_R2}::test_the_2025_replay_refuses_a_dirty_tree",
       "Le rejeu de 2025 refuse un arbre de travail aux changements non validés",
       "The 2025 replay refuses a working tree with uncommitted changes"),
    _b(FREEZE, f"{T_R2}::test_the_2025_replay_uses_the_artifacts_frozen_for_2024",
       "Le rejeu de 2025 lit les artefacts figés pour 2024, jamais les plus récents",
       "The 2025 replay reads the artifacts frozen for 2024, never the latest"),
    _b(FREEZE, f"{T_R2}::test_a_2025_replay_stopped_midway_leaves_its_started_run",
       "Un rejeu de 2025 arrêté en cours laisse son exécution démarrée, avec l'étape atteinte et la classe de son "
       "erreur ; arrêté par un contrôle, il ne tient aucune métrique ni aucun résultat",
       "A 2025 replay stopped midway leaves its started run, with the step it reached and its error's class; one "
       "stopped by a check holds no metric and no result"),
    _b(FREEZE, f"{T_R2}::test_a_2025_start_stopped_while_its_gate_record_is_logged_keeps_its_error_class",
       "Un départ de 2025 arrêté pendant qu'il enregistre la fiche de sa porte garde la classe de son erreur, et le "
       "refus du départ suivant la nomme",
       "A 2025 start stopped while its gate record is logged keeps its error's class, and the next start's refusal "
       "names it"),
    _b(FREEZE, f"{T_R2}::test_a_2025_run_exists_only_with_its_tags",
       "Une exécution de 2025 n'existe qu'avec ses étiquettes : leur calcul en échec n'en crée aucune, et un arrêt "
       "juste après sa création laisse un départ arrêté que la porte suivante voit",
       "A 2025 run exists only with its tags: their computation failing creates none, and a stop right after its "
       "creation leaves a stopped start the next gate sees"),
    _b(FREEZE, f"{T_R2}::test_a_2025_start_refuses_an_ambient_mlflow_environment_before_any_run_exists[run_id]",
       "Un départ de 2025 sous une variable d'environnement MLflow non permise (MLFLOW_RUN_ID) est refusé avant qu'aucune "
       "exécution n'existe : ce n'est pas un départ",
       "A 2025 start under an MLflow environment variable not allowed (MLFLOW_RUN_ID) is refused before any run exists: it is "
       "no start"),
    _b(FREEZE, f"{T_R2}::test_a_2025_start_refuses_an_ambient_mlflow_environment_before_any_run_exists[run_context]",
       "Un départ de 2025 sous une variable d'environnement MLflow non permise (MLFLOW_RUN_CONTEXT) est refusé avant qu'aucune "
       "exécution n'existe : ce n'est pas un départ",
       "A 2025 start under an MLflow environment variable not allowed (MLFLOW_RUN_CONTEXT) is refused before any run exists: it is "
       "no start"),
    _b(FREEZE, f"{T_R2}::test_a_2025_start_refuses_an_ambient_mlflow_environment_before_any_run_exists[experiment_name]",
       "Un départ de 2025 sous une variable d'environnement MLflow non permise (MLFLOW_EXPERIMENT_NAME) est refusé avant qu'aucune "
       "exécution n'existe : ce n'est pas un départ",
       "A 2025 start under an MLflow environment variable not allowed (MLFLOW_EXPERIMENT_NAME) is refused before any run exists: it is "
       "no start"),
    _b(FREEZE, f"{T_R2}::test_a_2025_start_refuses_an_ambient_mlflow_environment_before_any_run_exists[system_metrics]",
       "Un départ de 2025 sous une variable d'environnement MLflow non permise (MLFLOW_ENABLE_SYSTEM_METRICS_LOGGING) est refusé avant qu'aucune "
       "exécution n'existe : ce n'est pas un départ",
       "A 2025 start under an MLflow environment variable not allowed (MLFLOW_ENABLE_SYSTEM_METRICS_LOGGING) is refused before any run exists: it is "
       "no start"),
    _b(FREEZE, f"{T_R2}::test_a_2025_start_is_a_fresh_run_whatever_run_id_the_environment_names",
       "Un départ de 2025 est toujours une exécution neuve de l'expérience de suivi, même quand l'environnement nomme "
       "une exécution plus ancienne d'une autre expérience (MLFLOW_RUN_ID) : la porte suivante le voit",
       "A 2025 start is always a fresh run of the monitoring experiment, even when the environment names an older run "
       "of another experiment (MLFLOW_RUN_ID): the next gate sees it"),
    _b(FREEZE, f"{T_R2}::test_a_run_that_could_not_be_entered_is_refused_before_it_exists",
       "Une exécution qui ne pourrait pas être ouverte (une autre déjà active, une autre expérience choisie) est refusée "
       "avant d'exister ; si l'ouverture échoue quand même, elle finit en échec avec la classe de son erreur, jamais "
       "« en cours » : un départ de 2025 ainsi arrêté est un départ arrêté",
       "A run that could not be entered (another already active, another experiment selected) is refused before it "
       "exists; should entering fail all the same, it ends failed with its error's class, never running: a 2025 start "
       "so stopped is a stopped start"),
    _b(FREEZE, f"{T_R2}::test_a_gate_refusal_opens_no_run",
       "Témoin : un refus de la porte n'ouvre aucune exécution", "Control: a gate refusal opens no run", control=True),
    _b(FREEZE, f"{T_R2}::test_the_replay_function_refuses_2025_without_its_open_started_run",
       "La fonction de rejeu refuse 2025 sans son exécution ouverte et démarrée, de ce code et de ce genre",
       "The replay function refuses 2025 without its open, started run, of this code and of this kind"),
    _b(FREEZE, f"{T_R2}::test_the_diagnostics_run_after_the_replay_has_finished",
       "Les diagnostics s'exécutent après la fin du rejeu, dans leur propre exécution ; leur échec laisse le rejeu "
       "fini",
       "The diagnostics run after the replay has finished, in a run of their own; their failure leaves the replay "
       "finished"),
    _b(FREEZE, f"{T_R2}::test_two_workers_replay_2025_under_the_diagnostic_run",
       "Deux processus rejouent 2025 sous l'exécution de diagnostic, chacun la cherchant dans le magasin ; un "
       "identifiant que le magasin n'a pas est refusé dans le processus",
       "Two worker processes replay 2025 under the diagnostic run, each looking it up in the store; a run ID the "
       "store does not hold is refused inside a worker"),
    _b(FREEZE, f"{T_R2}::test_the_evidence_compares_every_column_and_decides_nothing",
       "La preuve compare chaque colonne de chaque heure et ne décide rien",
       "The evidence compares every column of every hour and decides nothing"),
    _b(FREEZE, f"{T_R2}::test_the_evidence_refuses_a_dirty_tree",
       "La preuve refuse un arbre de travail aux changements non validés",
       "The evidence refuses a working tree with uncommitted changes"),
    _b(FREEZE, f"{T_R2}::test_the_evidence_refuses_a_data_quality_run_that_is_not_a_passing_through_2025_run",
       "La preuve refuse un contrôle de qualité des données qui n'est pas un contrôle réussi jusqu'à 2025",
       "The evidence refuses a data-quality run that is not a passing through-2025 run"),
    _b(FREEZE, f"{T_CLI}::test_all_never_runs_the_2025_replay",
       "La commande « all » ne rejoue jamais 2025 ni ne lance la preuve, quels que soient ses drapeaux, et refuse "
       "--through",
       "The all command never replays 2025 nor runs the evidence, whatever its flags, and refuses --through"),
    _b(FREEZE, f"{T_CLI}::test_no_flag_but_departure_reaches_the_2025_replay",
       "Aucun drapeau autre que --departure n'atteint le rejeu de 2025",
       "No flag but --departure reaches the 2025 replay"),
    _b(FREEZE, f"{T_CLI}::test_replay_accepts_2025_and_freeze_does_not",
       "La commande « freeze » refuse 2025 : 2025 est jugé par les artefacts figés pour 2024",
       "The freeze command refuses 2025: 2025 is judged by the artifacts frozen for 2024"),
    # v2: the replay ledger.
    _b(RENDER, f"{T_LED}::test_the_v1_entries_equal_those_of_the_ledger_at_tag_v1",
       "Une entrée de v1 du registre des rejeux modifiée depuis l'étiquette v1 est repérée",
       "A v1 replay-ledger entry changed since tag v1 is caught"),
    _b(RENDER, f"{T_LED}::test_a_v2_entry_of_another_year_is_refused",
       "Une entrée de v2 d'une année que son gel ne rejoue pas est refusée",
       "A v2 entry of a year its freeze does not replay is refused"),
    _b(RENDER, f"{T_LED}::test_an_entry_of_an_unknown_freeze_tag_is_refused",
       "Une entrée du registre sous une étiquette de gel inconnue est refusée",
       "A ledger entry under an unknown freeze tag is refused"),
    _b(RENDER, f"{T_LED}::test_a_v1_entry_with_v2_fields_is_refused",
       "Une entrée de v1 qui porte les champs de v2 est refusée", "A v1 entry carrying v2's fields is refused"),
    _b(RENDER, f"{T_LED}::test_a_v2_entry_without_its_evaluation_label_is_refused",
       "Une entrée de v2 sans son étiquette d'évaluation est refusée",
       "A v2 entry without its evaluation label is refused"),
    _b(RENDER, f"{T_LED}::test_a_v2_label_other_than_its_starts_give_is_refused",
       "Une étiquette d'évaluation autre que celle que donnent les départs de 2025 est refusée",
       "An evaluation label other than the one the 2025 starts give is refused"),
    _b(RENDER, f"{T_LED}::test_a_post_hoc_test_of_record_labelled_held_out_is_refused",
       "Un test de référence a posteriori étiqueté « données réservées » est refusé",
       "A post-hoc test of record labelled held out is refused"),
    _b(RENDER, f"{T_LED}::test_a_ruled_test_of_record_after_a_stopped_start_passes[clean-held_out]",
       "Témoin : après un départ arrêté, le départ que la décision du propriétaire nomme devient le test de référence, "
       "propre et étiqueté « données réservées »",
       "Control: after a stopped start, the start the owner's ruling names becomes the test of record, clean and "
       "labelled held out", control=True),
    _b(RENDER, f"{T_LED}::test_a_ruled_test_of_record_after_a_stopped_start_passes[post_hoc-post_hoc]",
       "Témoin : après un départ arrêté, le départ que la décision du propriétaire nomme devient le test de référence, "
       "a posteriori et étiqueté a posteriori",
       "Control: after a stopped start, the start the owner's ruling names becomes the test of record, post hoc and "
       "labelled post hoc", control=True),
    _b(RENDER, f"{T_LED}::test_a_later_2025_entry_labelled_seen_by_developers_passes",
       "Témoin : un rejeu de 2025 refait, étiqueté « vu par les développeurs », passe",
       "Control: a later 2025 rerun labelled seen by the developers passes", control=True),
    _b(RENDER, f"{T_LED}::test_a_2024_entry_with_a_standing_or_a_2025_entry_without_one_is_refused",
       "Une entrée de 2024 qui porte une position, ou de 2025 qui n'en porte pas, est refusée",
       "A 2024 entry with a standing, or a 2025 entry without one, is refused"),
    _b(RENDER, f"{T_LED}::test_a_non_departure_v2_entry_under_another_freeze_commit_is_refused",
       "Une entrée de v2 qui n'est pas une dérogation, sous un autre commit de gel que la première entrée de 2024 de "
       "v2, est refusée",
       "A v2 entry that is no departure, under another freeze commit than v2's first 2024 entry, is refused"),
    _b(RENDER, f"{T_LED}::test_a_v2_2025_entry_before_any_v2_2024_entry_is_refused",
       "Une entrée de 2025 antérieure à toute entrée de 2024 de v2 est refusée",
       "A 2025 entry before any v2 2024 entry is refused"),
    _b(RENDER, f"{T_LED}::test_a_store_run_differing_from_its_v2_entry_in_one_v2_field_is_refused"
               "[evaluation-seen_by_developers]",
       "Un rejeu du magasin qui diffère de son entrée de v2 par l'étiquette d'évaluation est refusé",
       "A stored replay differing from its v2 entry in its evaluation label is refused"),
    _b(RENDER, f"{T_LED}::test_a_store_run_differing_from_its_v2_entry_in_one_v2_field_is_refused"
               "[model_sha256-ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff]",
       "Un rejeu du magasin qui diffère de son entrée de v2 par l'empreinte du modèle de production est refusé",
       "A stored replay differing from its v2 entry in the production model's hash is refused"),
    _b(RENDER, f"{T_LED}::test_a_store_run_differing_from_its_v2_entry_in_one_v2_field_is_refused[verdict-value2]",
       "Un rejeu du magasin qui diffère de son entrée de v2 par les verdicts de la règle de décision est refusé",
       "A stored replay differing from its v2 entry in the decision rule's verdicts is refused"),
    _b(RENDER, f"{T_LED}::test_a_store_run_differing_from_its_v2_entry_in_one_v2_field_is_refused"
               "[frozen_sha256-0000000000000000000000000000000000000000000000000000000000000000]",
       "Un rejeu du magasin qui diffère de son entrée de v2 par l'empreinte des artefacts figés est refusé",
       "A stored replay differing from its v2 entry in the frozen artifacts' hash is refused"),
    _b(RENDER, f"{T_LED}::test_a_store_run_differing_from_its_v2_entry_in_one_v2_field_is_refused[standing-post_hoc]",
       "Un rejeu du magasin qui diffère de son entrée de v2 par la position est refusé",
       "A stored replay differing from its v2 entry in its standing is refused"),
    _b(RENDER, f"{T_LED}::test_the_earliest_started_2025_replay_is_the_test_of_record",
       "Un départ de 2025 plus tardif présenté comme test de référence est refusé",
       "A later 2025 start offered as the test of record is refused"),
    _b(RENDER, f"{T_LED}::test_a_stopped_first_start_gives_way_only_to_the_start_its_ruling_names",
       "Après un départ arrêté, un départ sans la décision du propriétaire qui le nomme ne devient pas le test de "
       "référence",
       "After a stopped start, a start without the owner's ruling naming it does not become the test of record"),
    _b(RENDER, f"{T_LED}::test_a_finished_replay_not_marked_complete_stays_out_of_the_ledger",
       "Un rejeu que MLflow dit fini mais qui ne s'est pas marqué complet n'entre pas au registre des rejeux",
       "A replay MLflow calls finished that never marked itself complete stays out of the replay ledger"),
    # v2: the report's run chain and its 2025 records.
    _b(RENDER, f"{T_LIN}::test_the_chain_follows_the_v2_2024_replay_not_a_v1_or_a_2025_one",
       "Témoin : la chaîne suit le rejeu de 2024 de v2, pas un rejeu de v1 ni un rejeu de 2025",
       "Control: the chain follows v2's 2024 replay, not a v1 replay nor a 2025 one", control=True),
    _b(RENDER, f"{T_LIN}::test_a_clean_2025_with_nothing_changed_renders_beside_the_2024_replay_of_record[fr]",
       "Témoin : un 2025 propre, sans changement, est rendu à côté du rejeu de 2024 de référence, le rejeu refait à "
       "côté, rapport français",
       "Control: a clean 2025 with nothing changed renders beside the 2024 replay of record, the rerun beside it, "
       "French report", control=True),
    _b(RENDER, f"{T_LIN}::test_a_clean_2025_with_nothing_changed_renders_beside_the_2024_replay_of_record[en]",
       "Témoin : un 2025 propre, sans changement, est rendu à côté du rejeu de 2024 de référence, le rejeu refait à "
       "côté, rapport anglais",
       "Control: a clean 2025 with nothing changed renders beside the 2024 replay of record, the rerun beside it, "
       "English report", control=True),
    _b(RENDER, f"{T_LIN}::test_a_2025_replay_whose_frozen_hashes_differ_from_the_2024_record_s_is_refused",
       "Un rejeu de 2025 jugé par des artefacts figés d'autres empreintes que ceux du rejeu de 2024 de référence rompt "
       "la chaîne du rapport",
       "A 2025 replay judged by frozen artifacts with other hashes than the 2024 replay of record's breaks the "
       "report's run chain"),
    _b(RENDER, f"{T_LIN}::test_a_2025_rerun_whose_frozen_hashes_differ_from_the_2024_record_s_is_refused",
       "Un rejeu de 2025 refait, jugé par des artefacts figés dont une empreinte diffère de celles du rejeu de 2024 de "
       "référence, rompt la chaîne du rapport",
       "A 2025 rerun judged by frozen artifacts one of whose hashes differs from the 2024 replay of record's breaks "
       "the report's run chain"),
    _b(RENDER, f"{T_LIN}::test_a_2025_record_whose_data_quality_run_does_not_reach_the_end_of_2025_is_refused",
       "Un rejeu de 2025, le test de référence ou un rejeu refait, dont l'exécution de qualité des données ne va pas "
       "jusqu'à la fin de 2025 rompt la chaîne du rapport",
       "A 2025 replay, the test of record or a rerun, whose data-quality run does not reach the end of 2025 breaks "
       "the report's run chain"),
    _b(RENDER, f"{T_LIN}::test_an_unfinished_test_of_record_judged_by_other_frozen_artifacts_is_refused",
       "Un test de référence de 2025 inachevé dont le registre de la porte nomme d'autres artefacts figés que ceux du "
       "rejeu de 2024 de référence rompt la chaîne du rapport",
       "An unfinished 2025 test of record whose gate's record names other frozen artifacts than the 2024 replay of "
       "record's breaks the report's run chain"),
    _b(RENDER, f"{T_LIN}::test_a_2024_replay_tagged_with_another_evaluation_is_refused",
       "Un rejeu de 2024 étiqueté autrement que « vu par les développeurs », ou sans étiquette, fait refuser le "
       "rapport",
       "A 2024 replay tagged with another evaluation label than seen by the developers, or with none, makes the "
       "report refuse"),
    _b(RENDER, f"{T_LIN}::test_a_record_from_older_code_is_stated_not_refused[fr]",
       "Témoin : un rejeu de référence fait sur un code plus ancien est rendu avec son code, pas refusé, rapport "
       "français",
       "Control: a record made on older code renders with its code stated, not refused, French report", control=True),
    _b(RENDER, f"{T_LIN}::test_a_record_from_older_code_is_stated_not_refused[en]",
       "Témoin : un rejeu de référence fait sur un code plus ancien est rendu avec son code, pas refusé, rapport "
       "anglais",
       "Control: a record made on older code renders with its code stated, not refused, English report", control=True),
    _b(RENDER, f"{T_LIN}::test_a_comparator_hash_that_differs_from_the_replay_s_is_refused",
       "Un modèle de comparaison dont l'empreinte diffère de celle que le rejeu a consignée rompt la chaîne du rapport",
       "A comparison model whose hash differs from the one the replay logged breaks the report's run chain"),
    _b(RENDER, f"{T_LIN}::test_a_v2_departure_from_an_unfinished_start_and_two_freeze_commits_renders[fr]",
       "Une dérogation de v2 dont un rejeu d'avant n'est ni dans le magasin ni dans le registre, ou dont un commit de "
       "gel n'est nommé par aucune dérogation, rompt la chaîne ; un départ inachevé est montré comme tel, rapport "
       "français",
       "A v2 departure from a replay in neither the store nor the ledger, or from a freeze commit no departure entry "
       "names, breaks the chain; an unfinished start is shown as unfinished, French report"),
    _b(RENDER, f"{T_LIN}::test_a_v2_departure_from_an_unfinished_start_and_two_freeze_commits_renders[en]",
       "Une dérogation de v2 dont un rejeu d'avant n'est ni dans le magasin ni dans le registre, ou dont un commit de "
       "gel n'est nommé par aucune dérogation, rompt la chaîne ; un départ inachevé est montré comme tel, rapport "
       "anglais",
       "A v2 departure from a replay in neither the store nor the ledger, or from a freeze commit no departure entry "
       "names, breaks the chain; an unfinished start is shown as unfinished, English report"),
    _b(RENDER, f"{T_LIN}::test_v1_history_comes_only_from_rules_frozen_entries",
       "L'historique de v1 ne vient que de ses entrées « rules-frozen » ; sans son entrée simple, la chaîne est rompue",
       "v1's history comes only from its rules-frozen entries; without its plain entry the chain breaks"),
    _b(RENDER, f"{T_LIN}::test_check_recorded_reads_the_v2_section",
       "Une étiquette de gel ramenée à un commit d'avant, ou posée sur un commit de changement nommé, est refusée ; "
       "une décision du propriétaire sur 2025 n'est pas une dérogation",
       "A freeze tag moved back to an earlier commit, or onto a named change commit, is refused; an owner's ruling on "
       "2025 is no departure"),
    _b(RENDER, f"{T_LIN}::test_check_recorded_refuses_an_incomplete_v2_departure_record",
       "Le rapport refuse un registre de dérogations de v2 incomplet, par la règle même de la porte du rejeu",
       "The report refuses an incomplete v2 departure record, by the replay gate's own rule"),
    _b(RENDER, f"{T_LIN}::test_check_named_refuses_a_predecessor_under_a_change_commit_the_entry_names",
       "Un rejeu d'avant une dérogation fait sous un commit de changement que l'entrée nomme, ni commit de départ ni "
       "commit d'arrivée de l'étiquette, rompt la chaîne du rapport",
       "A replay from before a departure made under a change commit the entry names, neither a commit the tag moved "
       "from nor one it moved to, breaks the report's run chain"),
    _b(RENDER, f"{T_LIN}::test_the_report_prints_the_standing_its_gate_logged",
       "Une position que le registre de la porte n'a pas consignée, ou un statut tiré d'autre chose que le test de "
       "référence, fait refuser le rapport",
       "A standing the gate's record did not log, or a status drawn from anything but the test of record, makes the "
       "report refuse"),
    _b(RENDER, f"{T_LIN}::test_a_finished_2025_replay_without_its_gate_record_is_refused",
       "Un rejeu de 2025 fini sans sa position ou sans le registre de sa porte rompt la chaîne du rapport",
       "A finished 2025 replay without its standing or its gate's record breaks the report's run chain"),
    _b(RENDER, f"{T_LIN}::test_the_ruling_entry_the_gate_read_is_printed[fr]",
       "Témoin : la décision du propriétaire que la porte a lue est imprimée, avec le départ arrêté qu'elle nomme, "
       "rapport français",
       "Control: the owner's ruling the gate read is printed, with the stopped start it names, French report",
       control=True),
    _b(RENDER, f"{T_LIN}::test_the_ruling_entry_the_gate_read_is_printed[en]",
       "Témoin : la décision du propriétaire que la porte a lue est imprimée, avec le départ arrêté qu'elle nomme, "
       "rapport anglais",
       "Control: the owner's ruling the gate read is printed, with the stopped start it names, English report",
       control=True),
    # v2: what the report renders, its labels and the 2025 test of record.
    _b(RENDER, f"{T_REND}::test_each_status_names_its_scope_in_both_languages[fr]",
       "Un rapport ou une histoire qui n'ouvre pas sur sa phrase de statut, quel que soit le statut, est refusé, "
       "rapport français",
       "A report or story that does not open with its status sentence, whatever the status, is refused, French "
       "report"),
    _b(RENDER, f"{T_REND}::test_each_status_names_its_scope_in_both_languages[en]",
       "Un rapport ou une histoire qui n'ouvre pas sur sa phrase de statut, quel que soit le statut, est refusé, "
       "rapport anglais",
       "A report or story that does not open with its status sentence, whatever the status, is refused, English "
       "report"),
    _b(RENDER, f"{T_REND}::test_a_2024_v2_number_without_its_label_is_refused",
       "Un rejeu de 2024 dont les contrastes ou les verdicts ne portent pas l'étiquette « vu par les développeurs » "
       "fait refuser le rapport",
       "A 2024 replay whose contrasts or verdicts do not carry the seen-by-the-developers label makes the report "
       "refuse"),
    _b(RENDER, f"{T_REND}::test_a_better_claim_without_the_held_out_label_is_refused[fr]",
       "Une affirmation « meilleur » sans l'étiquette des données réservées, dans la phrase ou sur la page, fait "
       "refuser le rapport, rapport français",
       "A better claim without the held-out label, in the sentence or on the page, makes the report refuse, French "
       "report"),
    _b(RENDER, f"{T_REND}::test_a_better_claim_without_the_held_out_label_is_refused[en]",
       "Une affirmation « meilleur » sans l'étiquette des données réservées, dans la phrase ou sur la page, fait "
       "refuser le rapport, rapport anglais",
       "A better claim without the held-out label, in the sentence or on the page, makes the report refuse, English "
       "report"),
    _b(RENDER, f"{T_REND}::test_a_better_claim_from_a_2025_that_is_not_clean_is_refused[fr]",
       "« Meilleur » tiré d'un 2025 qui n'est pas propre fait refuser le rapport, rapport français",
       "A better claim from a 2025 that is not clean makes the report refuse, French report"),
    _b(RENDER, f"{T_REND}::test_a_better_claim_from_a_2025_that_is_not_clean_is_refused[en]",
       "« Meilleur » tiré d'un 2025 qui n'est pas propre fait refuser le rapport, rapport anglais",
       "A better claim from a 2025 that is not clean makes the report refuse, English report"),
    _b(RENDER, f"{T_REND}::test_a_2025_verdict_without_a_2025_replay_is_refused[fr]",
       "Un verdict de 2025 sans rejeu de 2025 fait refuser le rapport, rapport français",
       "A 2025 verdict without a 2025 replay makes the report refuse, French report"),
    _b(RENDER, f"{T_REND}::test_a_2025_verdict_without_a_2025_replay_is_refused[en]",
       "Un verdict de 2025 sans rejeu de 2025 fait refuser le rapport, rapport anglais",
       "A 2025 verdict without a 2025 replay makes the report refuse, English report"),
    _b(RENDER, f"{T_REND}::test_v1_2024_numbers_come_from_its_ledger_entries[fr]",
       "Un nombre de v1 sur 2024 tiré d'ailleurs que de ses entrées du registre fait refuser le rapport, rapport "
       "français",
       "A v1 number on 2024 taken from anywhere but its ledger entries makes the report refuse, French report"),
    _b(RENDER, f"{T_REND}::test_v1_2024_numbers_come_from_its_ledger_entries[en]",
       "Un nombre de v1 sur 2024 tiré d'ailleurs que de ses entrées du registre fait refuser le rapport, rapport "
       "anglais",
       "A v1 number on 2024 taken from anywhere but its ledger entries makes the report refuse, English report"),
    _b(RENDER, f"{T_REND}::test_an_unfinished_2025_start_not_listed_is_refused[fr]",
       "Un départ de 2025 inachevé qui n'est pas listé à la section 9 fait refuser le rapport, rapport français",
       "An unfinished 2025 start not listed in section 9 makes the report refuse, French report"),
    _b(RENDER, f"{T_REND}::test_an_unfinished_2025_start_not_listed_is_refused[en]",
       "Un départ de 2025 inachevé qui n'est pas listé à la section 9 fait refuser le rapport, rapport anglais",
       "An unfinished 2025 start not listed in section 9 makes the report refuse, English report"),
    _b(RENDER, f"{T_REND}::test_a_later_passing_rerun_never_overrides_the_test_of_record[fr]",
       "Un rejeu de 2025 refait et meilleur ne remplace jamais le test de référence : statut, affirmations retirées et "
       "recommandation restent les siens, rapport français",
       "A later passing 2025 rerun never overrides the test of record: the status, the withdrawn claims and the "
       "recommendation stay its own, French report"),
    _b(RENDER, f"{T_REND}::test_a_later_passing_rerun_never_overrides_the_test_of_record[en]",
       "Un rejeu de 2025 refait et meilleur ne remplace jamais le test de référence : statut, affirmations retirées et "
       "recommandation restent les siens, rapport anglais",
       "A later passing 2025 rerun never overrides the test of record: the status, the withdrawn claims and the "
       "recommendation stay its own, English report"),
    _b(RENDER, f"{T_REND}::test_a_clean_2025_not_confirmed_without_its_recommendation_is_refused[not_confirmed-fr]",
       "Un 2025 propre qui ne confirme pas v2, sans sa recommandation ou sans ouvrir « En bref » et le récit, fait "
       "refuser le rapport (non confirmé), rapport français",
       "A clean 2025 that does not confirm v2, without its recommendation or without opening the In brief and the "
       "story, makes the report refuse (not confirmed), French report"),
    _b(RENDER, f"{T_REND}::test_a_clean_2025_not_confirmed_without_its_recommendation_is_refused[not_confirmed-en]",
       "Un 2025 propre qui ne confirme pas v2, sans sa recommandation ou sans ouvrir « En bref » et le récit, fait "
       "refuser le rapport (non confirmé), rapport anglais",
       "A clean 2025 that does not confirm v2, without its recommendation or without opening the In brief and the "
       "story, makes the report refuse (not confirmed), English report"),
    _b(RENDER, f"{T_REND}::test_a_clean_2025_not_confirmed_without_its_recommendation_is_refused[input_only-fr]",
       "Un 2025 propre qui ne confirme pas v2, sans sa recommandation ou sans ouvrir « En bref » et le récit, fait "
       "refuser le rapport (meilleur que v1 tel que livré seulement), rapport français",
       "A clean 2025 that does not confirm v2, without its recommendation or without opening the In brief and the "
       "story, makes the report refuse (better than v1 as shipped only), French report"),
    _b(RENDER, f"{T_REND}::test_a_clean_2025_not_confirmed_without_its_recommendation_is_refused[input_only-en]",
       "Un 2025 propre qui ne confirme pas v2, sans sa recommandation ou sans ouvrir « En bref » et le récit, fait "
       "refuser le rapport (meilleur que v1 tel que livré seulement), rapport anglais",
       "A clean 2025 that does not confirm v2, without its recommendation or without opening the In brief and the "
       "story, makes the report refuse (better than v1 as shipped only), English report"),
    _b(RENDER, f"{T_REND}::test_an_unfinished_test_of_record_renders_no_2025_claim[fr]",
       "Un test de référence inachevé rendu avec une affirmation sur 2025, ou avec le verdict d'un autre départ, fait "
       "refuser le rapport, rapport français",
       "An unfinished test of record rendered with a 2025 claim, or with another start's verdict, makes the report "
       "refuse, French report"),
    _b(RENDER, f"{T_REND}::test_an_unfinished_test_of_record_renders_no_2025_claim[en]",
       "Un test de référence inachevé rendu avec une affirmation sur 2025, ou avec le verdict d'un autre départ, fait "
       "refuser le rapport, rapport anglais",
       "An unfinished test of record rendered with a 2025 claim, or with another start's verdict, makes the report "
       "refuse, English report"),
    _b(RENDER, f"{T_REND}::test_a_stopped_start_followed_by_a_ruled_test_of_record_renders[fr]",
       "Un départ arrêté omis, ou listé après le test de référence, fait refuser le rapport ; listé d'abord, le "
       "rapport est rendu, rapport français",
       "A stopped start left out, or listed after the test of record, makes the report refuse; listed first, the "
       "report renders, French report"),
    _b(RENDER, f"{T_REND}::test_a_stopped_start_followed_by_a_ruled_test_of_record_renders[en]",
       "Un départ arrêté omis, ou listé après le test de référence, fait refuser le rapport ; listé d'abord, le "
       "rapport est rendu, rapport anglais",
       "A stopped start left out, or listed after the test of record, makes the report refuse; listed first, the "
       "report renders, English report"),
    _b(RENDER, f"{T_REND}::test_a_2025_lead_of_75_hours_in_the_record_s_data_quality_run_must_be_stated[fr]",
       "Un délai de 75 heures ou plus en 2025 dans l'exécution de qualité des données du test de référence, non énoncé "
       "à la section 9, fait refuser le rapport, même quand la section 2 énonce celui d'une autre année ; et "
       "inversement, rapport français",
       "A 2025 lead of 75 hours or more in the test of record's data-quality run not stated in section 9 makes the "
       "report refuse, even when section 2 states another year's; and the reverse, French report"),
    _b(RENDER, f"{T_REND}::test_a_2025_lead_of_75_hours_in_the_record_s_data_quality_run_must_be_stated[en]",
       "Un délai de 75 heures ou plus en 2025 dans l'exécution de qualité des données du test de référence, non énoncé "
       "à la section 9, fait refuser le rapport, même quand la section 2 énonce celui d'une autre année ; et "
       "inversement, rapport anglais",
       "A 2025 lead of 75 hours or more in the test of record's data-quality run not stated in section 9 makes the "
       "report refuse, even when section 2 states another year's; and the reverse, English report"),
    _b(RENDER, f"{T_REND}::test_2025_s_days_not_fed_are_2025_s_alone",
       "Un jour de 2024 sans prévision, que liste l'exécution de qualité des données jusqu'à 2025, n'est pas compté "
       "parmi les jours de 2025",
       "A 2024 day without a forecast, listed by the through-2025 data-quality run, is not counted among 2025's days"),
    _b(RENDER, f"{T_REND}::test_the_revised_records_are_listed_whatever_the_standing[clean-fr]",
       "Les enregistrements de 2019 à 2024 révisés sont listés à la section 9, chacun avec ses valeurs (deux "
       "enregistrements d'une même heure montrés tous deux), quelle que soit la position de 2025 ; une page qui en "
       "omet un, imprime une autre valeur ou le liste ailleurs est refusée "
       "(2025 propre), rapport français",
       "The 2019–2024 records revised are listed in section 9, each with its values (two records of one hour both "
       "shown), whatever 2025's standing; a page "
       "that drops one, prints another value or lists it elsewhere is refused (2025 clean), French report"),
    _b(RENDER, f"{T_REND}::test_the_revised_records_are_listed_whatever_the_standing[clean-en]",
       "Les enregistrements de 2019 à 2024 révisés sont listés à la section 9, chacun avec ses valeurs (deux "
       "enregistrements d'une même heure montrés tous deux), quelle que soit la position de 2025 ; une page qui en "
       "omet un, imprime une autre valeur ou le liste ailleurs est refusée "
       "(2025 propre), rapport anglais",
       "The 2019–2024 records revised are listed in section 9, each with its values (two records of one hour both "
       "shown), whatever 2025's standing; a page "
       "that drops one, prints another value or lists it elsewhere is refused (2025 clean), English report"),
    _b(RENDER, f"{T_REND}::test_the_revised_records_are_listed_whatever_the_standing[post_hoc-fr]",
       "Les enregistrements de 2019 à 2024 révisés sont listés à la section 9, chacun avec ses valeurs (deux "
       "enregistrements d'une même heure montrés tous deux), quelle que soit la position de 2025 ; une page qui en "
       "omet un, imprime une autre valeur ou le liste ailleurs est refusée "
       "(2025 a posteriori), rapport français",
       "The 2019–2024 records revised are listed in section 9, each with its values (two records of one hour both "
       "shown), whatever 2025's standing; a page "
       "that drops one, prints another value or lists it elsewhere is refused (2025 post hoc), French report"),
    _b(RENDER, f"{T_REND}::test_the_revised_records_are_listed_whatever_the_standing[post_hoc-en]",
       "Les enregistrements de 2019 à 2024 révisés sont listés à la section 9, chacun avec ses valeurs (deux "
       "enregistrements d'une même heure montrés tous deux), quelle que soit la position de 2025 ; une page qui en "
       "omet un, imprime une autre valeur ou le liste ailleurs est refusée "
       "(2025 a posteriori), rapport anglais",
       "The 2019–2024 records revised are listed in section 9, each with its values (two records of one hour both "
       "shown), whatever 2025's standing; a page "
       "that drops one, prints another value or lists it elsewhere is refused (2025 post hoc), English report"),
    _b(RENDER, f"{T_REND}::test_the_standards_table_says_held_out_only_of_a_clean_finished_test_of_record[fr]",
       "Le tableau des normes ne dit « données réservées : fait » que d'un test de référence de 2025 fini et propre ; "
       "a posteriori, il dit que ce n'est pas un contrôle sur des données réservées, rapport français",
       "The standards table says held-out data done only of a clean, finished 2025 test of record; post hoc, it says "
       "it is no back-test on held-out data, French report"),
    _b(RENDER, f"{T_REND}::test_the_standards_table_says_held_out_only_of_a_clean_finished_test_of_record[en]",
       "Le tableau des normes ne dit « données réservées : fait » que d'un test de référence de 2025 fini et propre ; "
       "a posteriori, il dit que ce n'est pas un contrôle sur des données réservées, rapport anglais",
       "The standards table says held-out data done only of a clean, finished 2025 test of record; post hoc, it says "
       "it is no back-test on held-out data, English report"),
    _b(RENDER, f"{T_REND}::test_the_2025_coverage_is_given_with_its_interval_and_whether_it_holds_the_nominal[fr]",
       "La couverture de 2025 est donnée avec son intervalle à 95 % et s'il contient le nominal de 90 %, jugé sur les "
       "bornes imprimées, rapport français",
       "The 2025 coverage is given with its 95% interval and whether it holds the 90% nominal, judged on the bounds "
       "as printed, French report"),
    _b(RENDER, f"{T_REND}::test_the_2025_coverage_is_given_with_its_interval_and_whether_it_holds_the_nominal[en]",
       "La couverture de 2025 est donnée avec son intervalle à 95 % et s'il contient le nominal de 90 %, jugé sur les "
       "bornes imprimées, rapport anglais",
       "The 2025 coverage is given with its 95% interval and whether it holds the 90% nominal, judged on the bounds "
       "as printed, English report"),
    _b(RENDER, f"{T_REND}::test_the_run_reuse_paragraph_says_what_its_placements_show[fr]",
       "La phrase de la mesure de réutilisation suit ses placements : une semaine au-delà de ses jours intacts, à un "
       "seul décalage, est un constat contre l'argument des deux jours ; sans placement constructible, l'argument "
       "n'est pas mis à l'épreuve, rapport français",
       "The run-reuse paragraph follows its placements: one week past its untouched days, at a single offset, is a "
       "finding against the two-day argument; with no placement constructible, the argument is not put to the test, "
       "French report"),
    _b(RENDER, f"{T_REND}::test_the_run_reuse_paragraph_says_what_its_placements_show[en]",
       "La phrase de la mesure de réutilisation suit ses placements : une semaine au-delà de ses jours intacts, à un "
       "seul décalage, est un constat contre l'argument des deux jours ; sans placement constructible, l'argument "
       "n'est pas mis à l'épreuve, rapport anglais",
       "The run-reuse paragraph follows its placements: one week past its untouched days, at a single offset, is a "
       "finding against the two-day argument; with no placement constructible, the argument is not put to the test, "
       "English report"),
    _b(RENDER, f"{T_REND}::test_a_lead_of_75_hours_is_stated_against_the_two_day_argument[fr]",
       "Un délai de 75 heures ou plus de 2019 à 2024 non dit, comme constat contre l'argument des deux jours, fait "
       "refuser le rapport, rapport français",
       "A lead of 75 hours or more in 2019-2024 not stated as a finding against the two-day argument makes the report "
       "refuse, French report"),
    _b(RENDER, f"{T_REND}::test_a_lead_of_75_hours_is_stated_against_the_two_day_argument[en]",
       "Un délai de 75 heures ou plus de 2019 à 2024 non dit, comme constat contre l'argument des deux jours, fait "
       "refuser le rapport, rapport anglais",
       "A lead of 75 hours or more in 2019-2024 not stated as a finding against the two-day argument makes the report "
       "refuse, English report"),
    _b(RENDER, f"{T_REND}::test_the_data_quality_report_states_a_lead_of_75_hours",
       "Un délai de 75 heures ou plus non énoncé à la section 4.1 du rapport de qualité des données le fait refuser, "
       "même si « En bref » l'énonce",
       "A lead of 75 hours or more not stated in section 4.1 of the data-quality report makes it refuse, even when "
       "In brief states it"),
    _b(RENDER, f"{T_REND}::test_a_2025_vintage_data_quality_report_leaves_2025_to_the_validation_report",
       "Le rapport de qualité des données d'une exécution jusqu'à 2025 ne liste aucun jour de 2025 sans prévision, ni "
       "l'heure de la prévision de 2025, ni un délai de 2025 de 75 heures ou plus, et dit quels comptes comprennent "
       "2025",
       "The data-quality report of a through-2025 run lists no 2025 day without a forecast, no 2025 hour of the "
       "forecast and no 2025 lead of 75 hours or more, and says which counts include 2025"),
    _b(RENDER, f"{T_REND}::test_a_report_on_the_2025_vintage_before_any_2025_start_is_refused",
       "Avant tout départ de 2025, une exécution de qualité des données qui atteint 2025, nommée ou la plus récente, "
       "fait refuser le rendu, qui nomme cette exécution",
       "Before any 2025 start, a data-quality run that reaches 2025, named or the latest, makes the render refuse, "
       "naming that run"),
    _b(RENDER, f"{T_CLI}::test_report_refuses_a_2025_data_quality_run_named_by_id_before_any_2025_start",
       "Par la commande, avant tout départ de 2025, le rapport sur une exécution de qualité des données jusqu'à 2025 "
       "nommée par son identifiant est refusé, en nommant l'exécution, et le dossier du rapport reste vide",
       "Through the command, before any 2025 start, the report on a through-2025 data-quality run named by its ID is "
       "refused, naming the run, and the report folder stays empty"),
    _b(RENDER, f"{T_REND}::test_a_fresh_clone_after_a_2025_entry_renders_from_the_ledger[fr]",
       "Une copie fraîche montre chaque rejeu de référence d'après le registre, étiqueté ; sans l'étiquette ou sans la "
       "phrase sur le magasin, le rapport est refusé, rapport français",
       "A fresh clone shows each record from the ledger, labelled; without the label or the sentence on the store, "
       "the report refuses, French report"),
    _b(RENDER, f"{T_REND}::test_a_fresh_clone_after_a_2025_entry_renders_from_the_ledger[en]",
       "Une copie fraîche montre chaque rejeu de référence d'après le registre, étiqueté ; sans l'étiquette ou sans la "
       "phrase sur le magasin, le rapport est refusé, rapport anglais",
       "A fresh clone shows each record from the ledger, labelled; without the label or the sentence on the store, "
       "the report refuses, English report"),
    _b(RENDER, f"{T_REND}::test_a_store_holding_the_2024_record_but_not_the_2025_one_renders_it_from_its_entry[fr]",
       "Témoin : un magasin sans le test de référence de 2025 le montre d'après son entrée, avec le départ arrêté dont "
       "il dérive, rapport français",
       "Control: a store without the 2025 test of record shows it from its entry, with the stopped start it departs "
       "from, French report", control=True),
    _b(RENDER, f"{T_REND}::test_a_store_holding_the_2024_record_but_not_the_2025_one_renders_it_from_its_entry[en]",
       "Témoin : un magasin sans le test de référence de 2025 le montre d'après son entrée, avec le départ arrêté dont "
       "il dérive, rapport anglais",
       "Control: a store without the 2025 test of record shows it from its entry, with the stopped start it departs "
       "from, English report", control=True),
    _b(RENDER, f"{T_REND}::test_a_report_from_the_store_renders_every_record_from_its_run[fr]",
       "Témoin : avec tout dans le magasin, chaque rejeu de référence est rendu d'après son exécution, sans mention du "
       "registre, rapport français",
       "Control: with everything in the store, each record renders from its run, with no ledger label, French report",
       control=True),
    _b(RENDER, f"{T_REND}::test_a_report_from_the_store_renders_every_record_from_its_run[en]",
       "Témoin : avec tout dans le magasin, chaque rejeu de référence est rendu d'après son exécution, sans mention du "
       "registre, rapport anglais",
       "Control: with everything in the store, each record renders from its run, with no ledger label, English "
       "report", control=True),
    _b(RENDER, f"{T_REND}::test_the_attribution_block_carries_noaa_and_iem[fr]",
       "Un rapport, de validation ou de qualité des données, sans la ligne de la NOAA et de l'IEM, ou sans la ligne "
       "élargie de non-approbation, est refusé, rapport français",
       "A report, validation or data quality, without the NOAA and IEM line, or without the widened no-endorsement "
       "line, is refused, French report"),
    _b(RENDER, f"{T_REND}::test_the_attribution_block_carries_noaa_and_iem[en]",
       "Un rapport, de validation ou de qualité des données, sans la ligne de la NOAA et de l'IEM, ou sans la ligne "
       "élargie de non-approbation, est refusé, rapport anglais",
       "A report, validation or data quality, without the NOAA and IEM line, or without the widened no-endorsement "
       "line, is refused, English report"),
    _b(RENDER, f"{T_REND}::test_the_attribution_lines_are_the_owner_s_ruled_wording",
       "Des lignes d'attribution qui diffèrent du libellé que le propriétaire a fixé dans `AGENTS.md` font échouer le "
       "test",
       "Attribution lines that differ from the wording the owner ruled in `AGENTS.md` fail the test"),
    _b(RENDER, f"{T_REND}::test_the_ruled_wording_test_reads_agents_md_not_the_code",
       "Un mot changé dans `attribution.py` fait échouer la comparaison avec `AGENTS.md`",
       "One word changed in `attribution.py` fails the comparison with `AGENTS.md`"),
    _b(RENDER, f"{T_REND}::test_diagnostics_that_did_not_finish_are_said_so[fr]",
       "Témoin : des diagnostics qui n'ont pas fini sont dits tels, à la place de leurs mesures, rapport français",
       "Control: diagnostics that did not finish are said so, in place of their measures, French report", control=True),
    _b(RENDER, f"{T_REND}::test_diagnostics_that_did_not_finish_are_said_so[en]",
       "Témoin : des diagnostics qui n'ont pas fini sont dits tels, à la place de leurs mesures, rapport anglais",
       "Control: diagnostics that did not finish are said so, in place of their measures, English report",
       control=True),
    _b(RENDER, f"{T_REND}::test_2025_measures_made_with_another_design_are_refused",
       "Des mesures de 2025 faites avec une autre conception que celles de 2023 et 2024 font refuser le rapport",
       "2025 measures made with another design than 2023's and 2024's make the report refuse"),
    _b(RENDER, f"{T_REND}::test_the_lag_coefficients_said_positive_must_be",
       "Un rejeu dont les coefficients des retards ne sont pas tous deux positifs fait refuser le rapport, qui les dit "
       "positifs",
       "A replay whose lag coefficients are not both positive makes the report, which says they are, refuse"),
    _b(RENDER, f"{T_REND}::test_a_replay_s_longest_lead_unlike_its_data_quality_run_s_is_refused",
       "Un plus long délai de diagnostic qui diffère de celui de l'exécution de qualité des données fait refuser le "
       "rapport",
       "A diagnostic longest lead unlike the data-quality run's makes the report refuse"),
    _b(RENDER, f"{T_REND}::test_the_selection_s_values_are_checked_against_the_validation_run",
       "Des valeurs du choix qui ne concordent pas avec l'exécution de validation font refuser le rapport",
       "Selection values that disagree with the validation run make the report refuse"),
    _b(RENDER, f"{T_E2E}::test_the_report_renders_what_the_replay_writes",
       "Témoin : les deux pages, dans les deux langues, sont rendues d'après ce qu'écrivent le rejeu, ses diagnostics, "
       "la porte de 2025 et le registre, et les lignes d'épisodes du rapport ont les clés de celles que la mesure "
       "construit",
       "Control: both pages, in both languages, render from what the replay, its diagnostics, the 2025 gate and the "
       "ledger write, and the report's episode rows have the keys of the rows the measure builds", control=True),
    _b(RENDER, f"{T_STAMP}::test_the_stamp_carries_the_standing_of_2025",
       "Témoin : l'estampille porte la position de 2025 du test de référence, jamais celle d'un rejeu refait",
       "Control: the stamp carries the test of record's 2025 standing, never a rerun's", control=True),
    _b(RENDER, f"{T_SAFE}::test_the_v2_figures_and_templates_pass_the_scan",
       "Témoin : les figures et les gabarits de v2 passent l'analyse de sécurité publique",
       "Control: v2's figures and templates pass the public-safety scan", control=True),
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
