"""The breaks run counts a break as fired only when its test ran and passed."""

from valiwatt.breaks import BREAKS, GROUPS, Break, _outcomes, run_tests


def test_a_break_that_does_not_exist_is_not_counted_as_fired():
    rows = run_tests([Break("lookahead", "tests/test_guards.py::test_no_such_break", "x", "x")])
    assert rows[0]["outcome"] == "not_run"


def test_junit_outcomes_read_failures_and_parameters(tmp_path):
    xml = tmp_path / "r.xml"
    xml.write_text('<testsuites><testsuite>'
                   '<testcase classname="tests.test_a" name="test_x[1]"/>'
                   '<testcase classname="tests.test_a" name="test_y"><failure message="m"/></testcase>'
                   '<testcase classname="tests.test_a" name="test_z"><skipped/></testcase>'
                   '</testsuite></testsuites>')
    assert _outcomes(xml) == {"tests/test_a.py::test_x[1]": "passed", "tests/test_a.py::test_y": "failed",
                              "tests/test_a.py::test_z": "skipped"}


def test_every_break_names_a_known_group_and_both_languages():
    assert len({b.test for b in BREAKS}) == len(BREAKS)
    assert all(b.group in GROUPS and b.fr and b.en for b in BREAKS)


def test_every_break_names_a_test_node_pytest_collects():
    """A break's id must be a node pytest reports under that exact name (a parametrized test needs its parameter id):
    the breaks run looks each id up in the junit outcomes, and an id that matches no node counts as not fired."""
    import subprocess
    import sys

    from valiwatt.paths import REPO_ROOT

    files = sorted({b.test.split("::")[0] for b in BREAKS})
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *files],
                         cwd=REPO_ROOT, capture_output=True, text=True, check=False).stdout
    collected = {line.strip() for line in out.splitlines() if "::" in line}
    assert collected, out[-2000:]
    assert sorted(b.test for b in BREAKS if b.test not in collected) == []


def test_a_breaks_run_with_a_break_that_did_not_fire_is_marked_failed(tmp_path, monkeypatch):
    from functools import partial

    from valiwatt import breaks, tracking

    monkeypatch.setattr(tracking, "MLRUNS", tmp_path / "mlruns")
    missing = Break("lookahead", "tests/test_guards.py::test_no_such_break", "x", "x")
    monkeypatch.setattr(breaks, "run_tests", partial(run_tests, [missing]))
    assert breaks.run_breaks() == 1
    run = tracking.latest_run(breaks.EXPERIMENT)
    assert run.data.tags["status"] == "failed"
    assert run.data.metrics["fired"] == 0 and run.data.metrics["not_fired"] == 1


# docs/plan-v2.md, section 12, with the gates of sections 10 and 11.3 it names: each gate, the breaks that make it
# fire. Every node id here must be a registered break, so a break dropped from BREAKS fails the test below.
FEAT, NBM, WEA = "tests/test_features.py::", "tests/test_nbm_source.py::", "tests/test_weather_gates.py::"
FETCH, GRD, VAL = "tests/test_fetch_guards.py::", "tests/test_guards.py::", "tests/test_validate.py::"
MOD, FRO, EVAL = "tests/test_model.py::", "tests/test_frozen.py::", "tests/test_evaluate.py::"
DEC, R2, FRZ = "tests/test_decision_rule.py::", "tests/test_replay_v2.py::", "tests/test_freeze_gates.py::"
PIPE, LED, LIN = "tests/test_pipeline_refuses.py::", "tests/test_replay_ledger.py::", "tests/test_report_lineage.py::"
REND, CLI, DEP = "tests/test_report_render.py::", "tests/test_cli.py::", "tests/test_departure_record.py::"
TAGS, PLAN = "tests/test_tags.py::", "tests/test_plan_record.py::"


def both(node: str) -> tuple[str, ...]:
    return node + "[fr]", node + "[en]"


VERDICTS = tuple(f"{DEC}{t}[{c}]" for t in (
    "test_a_better_contrast_whose_cold_tail_is_not_worse_is_better",
    "test_a_better_contrast_whose_cold_tail_is_not_testable_is_better_cold_untested",
    "test_a_contrast_not_separated_or_better_with_a_worse_cold_tail_is_not_shown_better",
    "test_a_worse_contrast_is_worse") for c in ("shipped", "family"))
V2_FIELDS = tuple(f"{LED}test_a_store_run_differing_from_its_v2_entry_in_one_v2_field_is_refused[{p}]" for p in (
    "evaluation-seen_by_developers", f"model_sha256-{'f' * 64}", "verdict-value2", f"frozen_sha256-{'0' * 64}",
    "standing-post_hoc"))

PLAN_12 = {
    # Forecast feed
    "a run available after the origin never used, the next-freshest is": (
        FEAT + "test_a_run_available_after_the_origin_is_never_used",),
    "a run lacking a bracket point gives way to the next": (
        FEAT + "test_a_run_missing_one_bracket_point_gives_way_to_the_next",),
    "a run with a blank point gives way to the next": (FEAT + "test_a_blank_point_does_not_cover",),
    "a run with a point outside the bounds gives way to the next": (
        FEAT + "test_a_point_outside_the_bounds_does_not_cover",),
    "no covering run among twelve leaves the day without a forecast, saying why": (
        FEAT + "test_no_covering_run_among_twelve_leaves_the_day_without_a_forecast_and_says_why",),
    "two runs never interpolated together": (FEAT + "test_two_runs_are_never_mixed",),
    "values linear in time inside one run, in Celsius": (
        FEAT + "test_hourly_values_are_linear_in_time_inside_one_run_and_in_celsius",),
    "2023-12-31 forecast from its own day's run (control)": (
        FEAT + "test_the_eve_of_the_design_edge_keeps_its_forecast",
        VAL + "test_the_2024_boundary_run_in_the_2023_file_is_kept_when_available_in_time"),
    "the forecast feed by run identity on a sparse archive": (
        R2 + "test_the_replay_and_validation_feed_the_same_run_on_a_sparse_archive",),
    # Forecast fetch and data quality
    "a response for another station or model refused": (
        NBM + "test_a_response_for_another_station_or_model_is_refused",),
    "an impossible temperature refused, a point outside the bounds failing its gate": (
        NBM + "test_a_non_numeric_temperature_is_refused",
        WEA + "test_an_impossible_forecast_temperature_fails_the_nbm_bounds_gate"),
    "an empty response refused": (NBM + "test_an_empty_response_is_refused",),
    "a forecast file that no longer matches its manifest refused": (
        NBM + "test_a_file_that_no_longer_matches_its_manifest_is_refused",),
    "two values for one point refuse the load": (NBM + "test_two_values_for_one_point_refuse_the_load",),
    "identical duplicates pass and are counted (control)": (NBM + "test_identical_duplicates_are_dropped_and_counted",),
    "a forecast coverage below 98 % fails its gate": (
        WEA + "test_forecast_days_below_coverage_fail_the_nbm_coverage_gate",
        WEA + "test_the_nbm_coverage_gate_passes_at_98_percent_of_days_fed_and_fails_below"),
    "a primary station other than the airport's refuses the pairing": (
        NBM + "test_a_primary_station_other_than_7025251_refuses_the_pairing",),
    "every Hydro-Québec request bounded by the portal, its count under the same filter": (
        FETCH + "test_every_hydroquebec_request_is_bounded_by_the_portal",),
    "a response past its bound refused, nothing written": (
        FETCH + "test_a_response_holding_a_record_past_its_bound_is_refused_and_writes_nothing",),
    "a forced 2025 fetch of the weather files refused before any request": (
        FETCH + "test_a_forced_2025_fetch_of_the_weather_files_is_refused",
        CLI + "test_fetch_through_2025_goes_through_the_sources_gate"),
    # Lookahead
    "a row whose forecast became available after its origin refused": (
        GRD + "test_a_row_whose_forecast_became_available_after_its_origin_is_refused",
        GRD + "test_a_finite_forecast_without_its_availability_is_refused",
        GRD + "test_a_row_without_its_origin_is_refused"),
    "a design feed holding a run available after the design window refused": (
        VAL + "test_a_forecast_run_available_after_the_design_window_is_refused",),
    "the knot check in the fit": (MOD + "test_a_fit_whose_knots_saw_a_test_row_is_refused",
                                  MOD + "test_a_fit_on_its_own_rows_passes_the_knot_check"),
    "the knot check in the freeze": (
        FRO + "test_a_freeze_whose_knots_are_not_the_rule_on_its_training_rows_is_refused",),
    "the eve of the replayed year never calibrates a half-width, in either freeze": (
        EVAL + "test_the_eve_of_the_replayed_year_never_calibrates_a_half_width",
        FRO + "test_the_2024_freeze_half_width_leaves_the_eve_out"),
    "a design temperature outside the bounds blank as in the replay": (
        VAL + "test_a_design_temperature_outside_the_bounds_is_blank_as_in_the_replay",),
    # Frozen models
    "an altered knot, scale or coefficient fails the hash": (
        FRO + "test_a_frozen_gam_with_an_altered_knot_scale_or_coefficient_is_refused",),
    "the frozen GAM's spline settings: another one refused, each in the hash": (
        FRO + "test_a_frozen_gam_with_another_degree_extrapolation_or_bias_is_refused",
        *(FRO + f"test_a_frozen_gam_forecasts_from_its_file_whatever_the_module_now_says[{s}]"
          for s in ("extrapolation", "degree", "include_bias", "n_knots"))),
    "a production model with a lag coefficient not positive refused": (
        FRO + "test_a_production_model_with_a_lag_coefficient_not_positive_is_refused",
        FRO + "test_the_freeze_refuses_a_production_model_whose_lag_coefficient_is_not_positive"),
    "a comparison model changed during the replay fails it": (
        R2 + "test_a_comparator_changed_during_the_replay_fails_it",
        FRO + "test_comparators_are_logged_and_reloaded_against_their_hashes"),
    "the frozen GAM predicts what the ruled pipeline predicts": (
        MOD + "test_the_gam_predicts_what_the_ruled_pipeline_predicts",
        FRO + "test_a_frozen_gam_round_trips_through_json_and_predicts_as_fitted"),
    # Monitors
    "a day no run fed fails forecast_missing and yields investigate": (
        R2 + "test_a_day_no_run_fed_is_not_predicted_and_fails_forecast_missing",
        VAL + "test_a_day_no_run_fed_fails_forecast_missing_in_the_verdicts_validation_calibrates_on"),
    "forecast-input drift with performance inside yields investigate": (
        R2 + "test_forecast_input_drift_with_performance_inside_yields_investigate",),
    "the comparison models crossing their thresholds yield nothing": (
        R2 + "test_comparators_crossing_their_thresholds_yield_no_recommendation",),
    "a rehearsal whose issued forecast or feeding run differs from validation's refused": (
        R2 + "test_a_rehearsal_whose_issued_forecast_differs_from_validation_s_is_refused",
        R2 + "test_the_2023_rehearsal_runs_end_to_end_and_refuses_an_input_validation_did_not_issue"),
    "the reuse measure never claims two days from a run that misses one": (
        R2 + "test_the_reuse_measure_never_claims_two_days_from_a_run_that_misses_one",),
    # Decision rule
    "each state": (DEC + "test_a_contrast_whose_interval_lies_below_zero_is_better",
                   DEC + "test_a_contrast_whose_interval_lies_above_zero_is_worse",
                   DEC + "test_a_contrast_whose_interval_holds_zero_is_not_separated"),
    "each verdict": VERDICTS,
    "both sides of the printed-bound rule": (DEC + "test_a_bound_printing_as_zero_is_not_separated",),
    "the cold tail's weeks: not testable below 120 hours or 4 weeks": (
        DEC + "test_the_cold_tail_needs_120_hours_in_at_least_4_weeks",
        DEC + "test_the_cold_tail_is_not_testable_at_119_hours_and_testable_at_120"),
    "a contrast scored on the common hours only": (DEC + "test_a_contrast_is_scored_on_the_common_hours_only",),
    "the additivity of the three contrasts": (DEC + "test_the_three_contrasts_are_additive",),
    "the busiest hours of the year, a missing forecast kept with its reason": (
        DEC + "test_the_peak_hours_are_the_busiest_of_the_year_with_missing_forecasts_kept",),
    "each status, the held-out ones only on a clean 2025": (DEC + "test_the_status_follows_the_test_of_record",),
    # The 2025 gate
    "no commit since the freeze and every condition met: clean (control), revised records too": (
        FRZ + "test_a_clean_2025_start_with_nothing_changed_passes",),
    "the commit walk: rule, dependency and reverted changes refuse; an empty commit passes": (
        FRZ + "test_the_2025_gate_walks_every_commit_after_the_first_freeze_commit",),
    "the walk from the earliest 2024 start after a first 2024 start that stopped": (
        FRZ + "test_a_fix_after_a_failed_first_2024_start_refuses_the_2025_gate",),
    "the walk's merge, shallow clone and off-freeze refusals": (
        FRZ + "test_the_2025_gate_refuses_a_merge_a_shallow_clone_and_a_head_off_the_freeze_commit",),
    "the ruling entry that applies, clean or post hoc (controls)": (
        FRZ + "test_an_owner_s_ruling_entry_lifts_a_refusal_with_its_outcome[clean]",
        FRZ + "test_an_owner_s_ruling_entry_lifts_a_refusal_with_its_outcome[post_hoc]"),
    "each ruling entry that does not apply, without its evidence run or with one from a dirty tree": (
        FRZ + "test_a_ruling_entry_that_does_not_apply_leaves_the_refusal",),
    "the frozen artifacts' hashes": (FRZ + "test_a_2025_start_whose_frozen_artifacts_differ_is_refused",
                                     R2 + "test_the_2025_replay_uses_the_artifacts_frozen_for_2024"),
    "v2's first 2024 runs in the store": (FRZ + "test_a_2025_start_without_its_runs_in_the_store_is_refused",),
    "the passing through-2025 data-quality run": (
        FRZ + "test_a_2025_gate_refuses_a_data_quality_run_that_is_not_a_passing_through_2025_run",
        R2 + "test_the_2025_replay_refuses_until_the_window_reaches_its_end"),
    "the raw files' hashes with no record read, the reference read without its target": (
        FRZ + "test_a_2025_gate_refuses_a_raw_file_the_data_quality_run_did_not_check",),
    "the dirty tree": (R2 + "test_the_2025_replay_refuses_a_dirty_tree",),
    "a running start": (FRZ + "test_a_2025_start_while_another_is_running_is_refused",),
    "two simultaneous callers, of which one starts": (FRZ + "test_two_simultaneous_2025_starts_open_one_run",),
    "a 2025 diagnostic run without its finished replay": (
        FRZ + "test_a_2025_diagnostic_run_without_its_finished_replay_refuses_the_gate",),
    "the v2 2024 entry": (FRZ + "test_a_2025_replay_without_a_v2_2024_ledger_entry_is_refused",),
    "the unfinished test of record, the ruling naming the stopped start, a start the store lacks": (
        FRZ + "test_after_an_unfinished_test_of_record_a_later_start_refuses_until_a_ruling",),
    "once a test of record has finished, a later start is a rerun (control)": (
        FRZ + "test_a_later_2025_start_is_tagged_seen_by_developers",),
    "a gate's refusal opens no run": (
        R2 + "test_a_gate_refusal_opens_no_run",
        FRZ + "test_the_2025_gate_refuses_an_incomplete_departure_record_before_the_run_opens"),
    # 2025 runs and diagnostics
    "the replay reads 2025 only for its started run; a diagnostic run without its finished replay refused": (
        R2 + "test_the_replay_function_refuses_2025_without_its_open_started_run",
        FRZ + "test_the_replay_function_refuses_2025_on_its_own"),
    "the started run with every tag and the step it reached; stopped by a check, no result": (
        R2 + "test_a_2025_replay_stopped_midway_leaves_its_started_run",),
    "the diagnostics after the replay, a failing one leaving it finished": (
        R2 + "test_the_diagnostics_run_after_the_replay_has_finished",),
    "the two-worker diagnostics, an unknown run ID refused in a worker": (
        R2 + "test_two_workers_replay_2025_under_the_diagnostic_run",),
    "the evidence run compares every hour and decides nothing": (
        R2 + "test_the_evidence_compares_every_column_and_decides_nothing",
        R2 + "test_the_evidence_refuses_a_data_quality_run_that_is_not_a_passing_through_2025_run"),
    "the evidence refuses a dirty tree": (R2 + "test_the_evidence_refuses_a_dirty_tree",),
    "the evidence's replay receives no record of 2025": (R2 + "test_the_evidence_replay_receives_no_record_of_2025",),
    # Freeze and records
    "a v2 replay without rules-frozen-v2": (FRZ + "test_no_tag_is_refused",),
    "a rule file or a dependency changed after the v2 tag": (
        FRZ + "test_an_edited_rule_file_is_refused_and_named",
        FRZ + "test_a_dependency_changed_after_the_v2_tag_is_refused_and_named"),
    "a moved v2 tag not recorded": (
        FRZ + "test_a_v2_replay_under_a_moved_v2_tag_is_refused_unless_recorded",
        FRZ + "test_a_v2_tag_moved_back_without_a_departure_entry_is_refused"),
    "the moved-tag check scoped by freeze": (
        FRZ + "test_a_2025_replay_run_on_its_own_sees_a_tag_moved_since_v2_s_2024_replay",
        FRZ + "test_an_unfinished_start_counts_for_the_moved_tag_check"),
    "v1's replays never count as a moved v2 tag (control)": (FRZ + "test_v1_replays_never_count_as_a_moved_v2_tag",),
    "a replay stored without a freeze tag counts as v1's": (
        FRZ + "test_a_stored_replay_without_a_freeze_tag_counts_as_v1_s",),
    "an unlisted freeze tag": (FRZ + "test_every_freeze_tag_in_the_repository_is_listed_in_freezes",),
    "a tag that moved from its pin": (TAGS + "test_a_pinned_tag_moved_fails",
                                      DEP + "test_a_v2_tag_moved_without_its_pin_fails"),
    "a real-data run without v2-first-run": (PIPE + "test_a_real_data_run_without_the_first_run_tag_is_refused",),
    "an unrecorded rule change after v2-first-run": tuple(
        PLAN + f"test_a_rule_change_after_the_first_run_not_named_fails[{p}]"
        for p in ("src/valiwatt/monitor/rules.py", "pyproject.toml", "uv.lock")),
    "an edit of the plan after plan-v2 not listed": (
        PLAN + "test_a_plan_edit_outside_section_18_not_named_fails",
        PLAN + "test_a_section_18_entry_rewritten_must_be_named"),
    "a v1 ledger entry edited": (LED + "test_the_v1_entries_equal_those_of_the_ledger_at_tag_v1",),
    "a ledger entry of another year or tag": (LED + "test_a_v2_entry_of_another_year_is_refused",
                                              LED + "test_an_entry_of_an_unknown_freeze_tag_is_refused"),
    "a v2 entry without its evaluation label": (LED + "test_a_v2_entry_without_its_evaluation_label_is_refused",),
    "the evaluation label, checked against the starts, the ruling and the standing": (
        LED + "test_a_v2_label_other_than_its_starts_give_is_refused",
        LED + "test_a_post_hoc_test_of_record_labelled_held_out_is_refused",
        DEC + "test_only_a_clean_test_of_record_is_labelled_held_out"),
    "the standing in each v2 entry": (
        LED + "test_a_2024_entry_with_a_standing_or_a_2025_entry_without_one_is_refused", V2_FIELDS[-1]),
    "the store against the entry on each of the five v2 fields": V2_FIELDS,
    "the shared freeze commit of v2 ledger entries": (
        LED + "test_a_non_departure_v2_entry_under_another_freeze_commit_is_refused",),
    "a v2 section entry neither a departure nor a ruling with its outcome": (
        DEP + "test_a_v2_section_entry_with_another_head_fails",),
    "the v2-scoped departure record before the v2 freeze": (DEP + "test_before_the_v2_freeze_a_v2_record_fails",),
    "a rule or dependency commit after the v2 tag not named by its move's entry": (
        DEP + "test_a_v2_rule_change_after_the_tag_fails",
        DEP + "test_a_v2_dependency_change_after_the_tag_fails[pyproject.toml]",
        DEP + "test_a_v2_dependency_change_after_the_tag_fails[uv.lock]",
        DEP + "test_a_v2_rule_change_reverted_with_the_tag_unmoved_fails",
        DEP + "test_a_v2_departure_missing_a_change_commit_fails",
        DEP + "test_a_v2_move_without_its_departure_entry_fails",
        FRZ + "test_a_v2_departure_entry_missing_a_change_commit_is_refused"),
    "a commit a v2 departure entry names that is neither a change nor a freeze commit": (
        DEP + "test_a_v2_departure_naming_an_unrelated_commit_fails",
        FRZ + "test_a_v2_departure_entry_naming_an_unrelated_commit_is_refused"),
    "a v2 departure entry naming the commits the tag moved from and to, its moves joined": (
        DEP + "test_a_v2_departure_not_naming_its_freeze_commits_fails",
        FRZ + "test_a_v2_departure_entry_naming_only_the_new_freeze_commit_is_refused",
        FRZ + "test_a_v2_departure_entry_not_naming_the_commit_the_tag_moved_from_is_refused",
        FRZ + "test_a_v2_departure_record_whose_moves_do_not_join_is_refused"),
    "a departure naming the freeze commit it moved from, and a dependency departure, pass (controls)": (
        DEP + "test_a_v2_departure_naming_the_freeze_commit_it_moved_from_passes",
        DEP + "test_a_recorded_v2_dependency_departure_passes"),
    "a 2025 fetch before the freeze, from a dirty tree or of a partial year": (
        FETCH + "test_a_2025_fetch_before_the_freeze_is_refused",
        FETCH + "test_a_2025_fetch_from_a_dirty_tree_is_refused",
        FETCH + "test_a_partial_2025_export_is_refused_and_writes_nothing"),
    "the 2025 replay outside `all`, no flag but --departure reaching it": (
        CLI + "test_all_never_runs_the_2025_replay", CLI + "test_no_flag_but_departure_reaches_the_2025_replay"),
    "a 2024 replay reading a record of 2025": (R2 + "test_the_2024_replay_reads_no_record_of_2025",),
    # Render
    "a v2 number on 2024 without its label": (
        REND + "test_a_2024_v2_number_without_its_label_is_refused",
        LIN + "test_a_2024_replay_tagged_with_another_evaluation_is_refused"),
    "a better sentence without held out, or from a 2025 that is not clean": (
        *both(REND + "test_a_better_claim_without_the_held_out_label_is_refused"),
        *both(REND + "test_a_better_claim_from_a_2025_that_is_not_clean_is_refused")),
    "a 2025 verdict without a 2025 replay": both(REND + "test_a_2025_verdict_without_a_2025_replay_is_refused"),
    "the test of record: the earliest start or, after one that stopped, the start the ruling let run": (
        LED + "test_the_earliest_started_2025_replay_is_the_test_of_record",
        LED + "test_a_stopped_first_start_gives_way_only_to_the_start_its_ruling_names"),
    "the stopped start listed first, its ruled test of record rendering (control)": (
        *both(REND + "test_a_stopped_start_followed_by_a_ruled_test_of_record_renders"),
        LED + "test_a_ruled_test_of_record_after_a_stopped_start_passes[clean-held_out]",
        LED + "test_a_ruled_test_of_record_after_a_stopped_start_passes[post_hoc-post_hoc]"),
    "an unfinished start not listed": both(REND + "test_an_unfinished_2025_start_not_listed_is_refused"),
    "the status and recommendation from the test of record": (
        *both(REND + "test_a_later_passing_rerun_never_overrides_the_test_of_record"),
        LIN + "test_the_report_prints_the_standing_its_gate_logged"),
    "the render of a clean 2025 that does not confirm v2": (
        *(REND + f"test_a_clean_2025_not_confirmed_without_its_recommendation_is_refused[{s}-{lang}]"
          for s in ("not_confirmed", "input_only") for lang in ("fr", "en")),
        *both(REND + "test_each_status_names_its_scope_in_both_languages")),
    "an unfinished test of record leaving no claim": both(
        REND + "test_an_unfinished_test_of_record_renders_no_2025_claim"),
    "the revised records listed whatever the standing": tuple(
        REND + f"test_the_revised_records_are_listed_whatever_the_standing[{s}-{lang}]"
        for s in ("clean", "post_hoc") for lang in ("fr", "en")),
    "the 2025 data-quality findings from the test of record's run, a lead of 75 h or more stated": (
        *both(REND + "test_a_2025_lead_of_75_hours_in_the_record_s_data_quality_run_must_be_stated"),
        REND + "test_a_2025_vintage_data_quality_report_leaves_2025_to_the_validation_report",
        LIN + "test_a_2025_record_whose_data_quality_run_does_not_reach_the_end_of_2025_is_refused"),
    "the standing printed as the gate logged it, a 2025 record without that log refused": (
        LIN + "test_the_report_prints_the_standing_its_gate_logged",
        LIN + "test_a_finished_2025_replay_without_its_gate_record_is_refused"),
    "the 2025 replay's frozen hashes against the 2024 replay of record's": (
        LIN + "test_a_2025_replay_whose_frozen_hashes_differ_from_the_2024_record_s_is_refused",
        LIN + "test_a_2025_rerun_whose_frozen_hashes_differ_from_the_2024_record_s_is_refused",
        LIN + "test_an_unfinished_test_of_record_judged_by_other_frozen_artifacts_is_refused"),
    "the clean no-change 2025 that renders (control)": both(
        LIN + "test_a_clean_2025_with_nothing_changed_renders_beside_the_2024_replay_of_record"),
    "the v2 departure from an unfinished start": both(
        LIN + "test_a_v2_departure_from_an_unfinished_start_and_two_freeze_commits_renders"),
    "the fresh clone's ledger-only render and its label": (
        *both(REND + "test_a_fresh_clone_after_a_2025_entry_renders_from_the_ledger"),
        *both(REND + "test_a_store_holding_the_2024_record_but_not_the_2025_one_renders_it_from_its_entry")),
    "the report refused on the 2025 vintage before any 2025 start, the latest run or one named by its ID": (
        REND + "test_a_report_on_the_2025_vintage_before_any_2025_start_is_refused",
        CLI + "test_report_refuses_a_2025_data_quality_run_named_by_id_before_any_2025_start"),
    "v1's 2024 numbers from its ledger entries only": (
        *both(REND + "test_v1_2024_numbers_come_from_its_ledger_entries"),
        LIN + "test_v1_history_comes_only_from_rules_frozen_entries"),
    "the NOAA and IEM attribution, in the owner's approved wording": (
        *both(REND + "test_the_attribution_block_carries_noaa_and_iem"),
        REND + "test_the_attribution_lines_are_the_owner_s_ruled_wording",
        REND + "test_the_ruled_wording_test_reads_agents_md_not_the_code"),
}


def test_every_new_gate_named_in_the_plan_has_a_break():
    """Each gate of docs/plan-v2.md section 12 has at least one break, and every break listed for it is in BREAKS: a
    gate whose break is dropped from the registry is no longer seen to fire, and this test fails."""
    registered = {b.test for b in BREAKS}
    assert [gate for gate, tests in PLAN_12.items() if not tests] == []
    assert {gate: missing for gate, tests in PLAN_12.items()
            if (missing := [t for t in tests if t not in registered])} == {}


# docs/plan-v3.md, section 12: each line, the breaks that make it fire. Every node id here must be a registered break,
# so a v3 break dropped from BREAKS fails the test below, and every line of the section is here or in PENDING_V3.
FN3, GN3, MV3 = "tests/test_features_noon.py::", "tests/test_guards_noon.py::", "tests/test_model_v3.py::"
IV3, DV3, VA3 = "tests/test_intervals_v3.py::", "tests/test_decision_rule_v3.py::", "tests/test_validate_v3.py::"
FZ3, RP3, QV3 = "tests/test_freeze_v3.py::", "tests/test_reproduction_v3.py::", "tests/test_quality_v3.py::"
F3, FR3, V2S = "tests/test_fetch_v3.py::", "tests/test_firstrun_v3.py::", "tests/test_v2store.py::"
RN3, RH3, GT3 = "tests/test_replay_noon.py::", "tests/test_rehearsal_v3.py::", "tests/test_gate_v3.py::"
TG3, FRC, DR3 = "tests/test_tags_v3.py::", "tests/test_freeze_record.py::", "tests/test_departure_record_v3.py::"
RV3 = "tests/test_report_v3.py::"

PLAN_V3_12 = {
    "The spike scale": (
        QV3 + "test_the_v3_data_quality_run_at_noon_on_clean_data",
        RN3 + "test_the_noon_replay_s_spike_scale_is_cut_at_the_year_s_first_label",
    ),
    "The clocks": (
        FN3 + "test_a_noon_day_carries_exactly_its_24_labels_and_is_scored_the_next_morning",
        IV3 + "test_a_day_is_scored_at_d_plus_1_0600_utc_whatever_the_issue_time",
        QV3 + "test_a_noon_days_verdict_reads_only_the_raw_records_stamped_in_its_own_hours[2019-01-06T18:00Z--1]",
        QV3 + "test_a_noon_days_verdict_reads_only_the_raw_records_stamped_in_its_own_hours[2019-01-07T05:00Z--1]",
        QV3 + "test_a_noon_days_verdict_reads_only_the_raw_records_stamped_in_its_own_hours[2019-01-07T06:00Z-0]",
        QV3 + "test_a_noon_days_verdict_reads_only_the_raw_records_stamped_in_its_own_hours[2019-01-08T05:00Z-0]",
        QV3 + "test_a_noon_days_verdict_reads_only_the_raw_records_stamped_in_its_own_hours[2019-01-08T06:00Z-1]",
        RN3 + "test_a_noon_day_is_issued_at_noon_carries_its_24_labels_and_is_scored_at_d_plus_1_0600",
        RN3 + "test_the_noon_replay_judges_each_day_as_validation_does",
    ),
    "The noon origin": (
        FN3 + "test_a_run_available_at_17_01_is_never_used_for_the_day_and_the_next_freshest_is",
        FN3 + "test_the_noon_lags_are_read_as_of_the_noon_origin",
        GN3 + "test_a_noon_table_passes_its_guards_under_the_48_hour_term",
        GN3 + "test_no_24_hour_input_enters_a_noon_table[lag24]",
        GN3 + "test_no_24_hour_input_enters_a_noon_table[temp_persistence]",
        MV3 + "test_no_lag_of_24_hours_enters_a_noon_model",
        FN3 + "test_the_lag_days_mean_reads_d_minus_2s_24_labels_and_nothing_after",
        GN3 + "test_a_lag_days_mean_read_after_the_origin_is_refused",
        RN3 + "test_the_replay_never_feeds_a_day_a_run_available_after_its_noon_origin[after_origin]",
        RN3 + "test_the_replay_never_feeds_a_day_a_run_available_after_its_noon_origin[control]",
    ),
    "Wind": (
        NBM + "test_a_header_without_the_wind_speed_is_refused",
        FN3 + "test_the_noon_feed_refuses_runs_without_wind",
        FN3 + "test_a_noon_table_refuses_a_forecast_issued_without_wind",
        WEA + "test_the_wind_gate_refuses_runs_without_wind",
        NBM + "test_a_wind_speed_that_is_not_a_whole_number_is_refused",
        WEA + "test_the_wind_bounds_gate_is_pinned_at_both_edges",
        FN3 + "test_a_wind_point_outside_the_bounds_does_not_cover",
        QV3 + "test_a_wind_point_above_100_knots_fails_the_v3_run_and_its_day_takes_the_next_run",
        FN3 + "test_a_run_with_one_blank_wind_point_gives_way_to_the_next_though_its_temperatures_are_whole",
        QV3 + "test_the_wind_clause_lists_the_days_whose_run_changes_and_no_other",
        WEA + "test_the_noon_feed_passes_its_gates_and_reports_its_reuse_bound",
    ),
    "The lag day's mean": (
        FN3 + "test_19_usable_hours_leave_the_lag_days_mean_missing_and_20_compute_it",
        FN3 + "test_a_duplicated_label_is_refused_rather_than_counted_twice",
        MV3 + "test_every_noon_model_fits_and_forecasts_v3_s_usable_rows",
        VA3 + "test_the_lag_day_and_the_unfed_day_are_dropped_with_their_reasons",
        QV3 + "test_the_lag_day_finding_lists_the_days_whose_lag_day_holds_19_usable_hours_and_not_20",
        QV3 + "test_a_noon_day_without_its_feed_or_its_lag_days_mean_fails_its_verdict",
    ),
    "Intervals": (
        IV3 + "test_a_cold_bin_of_119_scores_or_of_120_in_3_weeks_takes_the_pooled_half_width[119-6-True]",
        IV3 + "test_a_cold_bin_of_119_scores_or_of_120_in_3_weeks_takes_the_pooled_half_width[120-3-True]",
        IV3 + "test_a_cold_bin_of_119_scores_or_of_120_in_3_weeks_takes_the_pooled_half_width[120-4-False]",
        FZ3 + "test_a_cold_bin_of_119_scores_or_of_120_in_3_weeks_takes_the_pooled_half_width_and_says_so[119-6-True]",
        FZ3 + "test_a_cold_bin_of_119_scores_or_of_120_in_3_weeks_takes_the_pooled_half_width_and_says_so[120-3-True]",
        FZ3 + "test_a_cold_bin_of_119_scores_or_of_120_in_3_weeks_takes_the_pooled_half_width_and_says_so[120-4-False]",
        IV3 + "test_a_fold_whose_cold_bin_falls_back_says_so_on_every_interval",
        VA3 + "test_every_interval_says_whether_its_cold_bin_fell_back_and_the_single_half_width_is_beside",
        VA3 + "test_a_temperature_issued_after_a_fold_s_clock_leaves_its_bin_edge_unchanged",
        MV3 + "test_a_binned_fit_stores_the_rule_on_its_own_usable_rows",
        MV3 + "test_the_bin_edge_check_refuses_an_edge_that_is_not_the_rule_on_its_rows",
        FZ3 + "test_a_freeze_whose_bin_edge_is_not_the_rule_on_its_own_rows_is_refused[v3]",
        FZ3 + "test_a_freeze_whose_bin_edge_is_not_the_rule_on_its_own_rows_is_refused[v1_noon]",
    ),
    "The verdicts": (
        DV3 + "test_each_line_of_the_terms_verdict[case0-not_testable]",
        DV3 + "test_each_line_of_the_terms_verdict[case1-not_testable]",
        DV3 + "test_each_line_of_the_terms_verdict[case2-worse]",
        DV3 + "test_each_line_of_the_terms_verdict[case3-worse]",
        DV3 + "test_each_line_of_the_terms_verdict[case4-not_shown_better]",
        DV3 + "test_each_line_of_the_terms_verdict[case5-not_shown_better]",
        DV3 + "test_each_line_of_the_terms_verdict[case6-not_shown_better]",
        DV3 + "test_each_line_of_the_terms_verdict[case7-not_shown_better]",
        DV3 + "test_each_line_of_the_terms_verdict[case8-better]",
        DV3 + "test_each_line_of_the_terms_verdict[case9-better]",
        DV3 + "test_each_line_of_the_terms_verdict[case10-better_windows_cold_untested]",
        DV3 + "test_each_line_of_the_terms_verdict[case11-better_windows_cold_untested]",
        DV3 + "test_a_gain_in_the_event_windows_paid_for_by_worse_all_hours_is_not_shown_better",
        DV3 + "test_a_gain_in_the_event_windows_paid_for_by_a_worse_cold_tail_is_not_shown_better",
        DV3 + "test_too_few_event_window_hours_are_not_testable",
        DV3 + "test_the_family_verdict_is_v2_s_rule_on_the_family_contrast",
    ),
    "The event windows": (
        DV3 + "test_the_event_windows_are_the_rate_s_peak_hours_by_local_clock",
        DV3 + "test_on_a_march_day_under_daylight_time_the_windows_are_hours_ending_11_to_14_and_21_to_00_utc",
        DV3 + "test_excluded_days_and_days_outside_the_winter_hold_no_window[2023-12-25-0]",
    ),
    "The window ranking": (
        DV3 + "test_k_rounds_half_up_and_never_exceeds_w",
        DV3 + "test_the_window_ranking_breaks_ties_by_date_then_morning_and_counts_overlap",
        DV3 + "test_a_window_missing_one_forecast_hour_is_not_eligible_and_is_listed",
    ),
    "The freeze": (
        FZ3 + "test_a_production_model_with_a_lag_coefficient_at_or_below_zero_is_refused",
        FZ3 + "test_a_production_model_whose_terms_are_not_the_ruled_ones_is_refused",
        FZ3 + "test_freeze_v3_refuses_before_logging_a_production_model_the_rules_refuse[lag48]",
        FZ3 + "test_freeze_v3_refuses_before_logging_a_production_model_the_rules_refuse[lag168]",
        FZ3 + "test_freeze_v3_refuses_before_logging_a_production_model_the_rules_refuse[terms]",
        FZ3 + "test_a_term_definition_other_than_the_ruled_one_is_refused[base_c-17.0]",
        FZ3 + "test_a_term_definition_other_than_the_ruled_one_is_refused[lag_day_min_hours-19]",
        FZ3 + "test_a_term_definition_other_than_the_ruled_one_is_refused[knots_to_kmh-1.85]",
        FZ3 + "test_a_term_definition_other_than_the_ruled_one_is_refused[lag_day_first_h--48]",
        FZ3 + "test_a_term_definition_other_than_the_ruled_one_is_refused[base_c-True]",
        MV3 + "test_term_definitions_other_than_the_ruled_ones_are_refused",
    ),
    "Tags": (
        TG3 + "test_creating_the_four_v3_tags_on_v2_s_code_leaves_v2_s_freeze_check_passing",
        TG3 + "test_a_v3_tag_named_like_a_v2_freeze_stops_v2_s_freeze_check",
    ),
    "Data": (
        QV3 + "test_a_raw_file_whose_hash_differs_from_v2s_is_refused[hq]",
        QV3 + "test_a_raw_file_whose_hash_differs_from_v2s_is_refused[eccc_file]",
        QV3 + "test_a_raw_file_whose_hash_differs_from_v2s_is_refused[eccc_manifest]",
        QV3 + "test_a_raw_file_whose_hash_differs_from_v2s_is_refused[nbm_file]",
        QV3 + "test_a_raw_file_whose_hash_differs_from_v2s_is_refused[nbm_manifest]",
        QV3 + "test_a_link_in_place_of_a_byte_copy_is_refused[file_symlink]",
        QV3 + "test_a_link_in_place_of_a_byte_copy_is_refused[directory_symlink]",
        QV3 + "test_a_link_in_place_of_a_byte_copy_is_refused[hard_link]",
        QV3 + "test_the_v3_run_refuses_a_file_that_is_not_v2s_before_it_reads_any_file",
        QV3 + "test_byte_copies_of_v2s_files_pass_and_a_fresh_clone_is_not_run",
        QV3 + "test_without_a_read_of_v2s_store_dq_v3_refuses_unless_told_it_is_a_fresh_clone",
        F3 + "test_a_v3_fetch_past_2024_is_refused_before_any_request[None]",
        F3 + "test_a_v3_fetch_past_2024_is_refused_before_any_request[hq]",
        F3 + "test_a_v3_fetch_past_2024_is_refused_before_any_request[eccc]",
        F3 + "test_a_v3_fetch_past_2024_is_refused_before_any_request[nbm]",
        F3 + "test_a_v3_fetch_through_2024_reaches_each_source[hq]",
        F3 + "test_a_v3_fetch_through_2024_reaches_each_source[eccc]",
        F3 + "test_a_v3_fetch_through_2024_reaches_each_source[nbm]",
        F3 + "test_a_v3_fetch_is_never_forced",
    ),
    "v2's store": (
        V2S + "test_the_read_of_v2s_store_leaves_it_byte_identical_and_logs_what_it_read",
        V2S + "test_v2s_database_is_opened_read_only",
        V2S + "test_no_other_v3_command_opens_v2s_store",
        V2S + "test_v2s_store_is_read_once_and_a_second_read_is_refused_before_it_opens_anything",
        V2S + "test_read_v2_refuses_a_path_that_is_v3s_own_store[same]",
        V2S + "test_read_v2_refuses_a_path_that_is_v3s_own_store[path_inside_own]",
        V2S + "test_read_v2_refuses_a_path_that_is_v3s_own_store[own_inside_path]",
        V2S + "test_v2s_database_is_opened_immutable_and_refused_beside_a_write_ahead_log",
        V2S + "test_a_v3_command_whose_own_store_is_v2s_refuses_before_it_logs[dq-v3]",
        V2S + "test_a_v3_command_whose_own_store_is_v2s_refuses_before_it_logs[validate-v3]",
        V2S + "test_a_v3_command_whose_own_store_is_v2s_refuses_before_it_logs[freeze-v3]",
        V2S + "test_a_v3_command_whose_own_store_is_v2s_refuses_before_it_logs[read-v2]",
    ),
    "The 2025 gate": (
        GT3 + "test_a_clean_2025_start_with_everything_in_order_passes",
        *(GT3 + f"test_the_v3_start_refuses_while_v2_s_test_of_record_has_not_finished[{c}]"
          for c in ("no_ref", "no_ledger", "no_entry", "invalid_ledger", "bad_shape")),
        GT3 + "test_the_v3_start_reads_v2_s_test_of_record_at_origin_when_v2_s_branch_is_absent",
        GT3 + "test_the_gate_reads_v2_s_ledger_at_the_ref_never_the_working_copy",
        *(GT3 + f"test_an_artifact_differing_from_the_freeze_record_is_refused[{a}]"
          for a in ("production", "v2_noon", "v1_noon", "v2_midnight", "thresholds", "cold_c", "reference")),
        *(GT3 + f"test_a_named_run_from_a_dirty_tree_or_other_rules_is_refused[{c}]"
          for c in ("dirty", "rules", "pre_freeze", "end_time", "unfinished", "git_commit", "dq_hashes", "absent",
                    "reproduction", "fresh_clone", "byte_copies", "model")),
        FRC + "test_a_second_commit_touching_the_freeze_record_fails",
        GT3 + "test_an_export_retrieved_before_the_freeze_record_refuses_h[record_commit]",
        GT3 + "test_an_export_retrieved_before_the_freeze_record_refuses_h[run_end]",
        GT3 + "test_a_rule_change_committed_after_the_2025_time_refuses_i[after]",
        GT3 + "test_a_rule_change_committed_after_the_2025_time_refuses_i[before]",
        DR3 + "test_a_clean_ruling_on_h_or_i_is_refused[h]",
        DR3 + "test_a_clean_ruling_on_h_or_i_is_refused[i]",
        GT3 + "test_a_clean_ruling_on_h_or_i_is_refused_and_a_post_hoc_one_lifts_it[h]",
        GT3 + "test_a_clean_ruling_on_h_or_i_is_refused_and_a_post_hoc_one_lifts_it[i]",
        GT3 + "test_a_ruling_on_v2_first_lifts_g_only",
        *(GT3 + f"test_a_later_data_quality_run_that_did_not_pass_refuses_d[{c}]"
          for c in ("failed", "running", "deleted", "control")),
        *(GT3 + f"test_a_completed_test_in_ledger_v3_never_becomes_a_stopped_start[{c}]"
          for c in ("failed", "code", "control")),
        GT3 + "test_a_commit_between_the_gate_and_the_run_refuses[before_open]",
        GT3 + "test_a_commit_between_the_gate_and_the_run_refuses[at_open]",
        GT3 + "test_a_non_ascii_rule_file_committed_after_the_2025_time_refuses_the_start",
        *(GT3 + f"test_a_rule_file_named_with_a_line_separator_after_the_2025_time_is_seen_by_a_and_i[{c}]"
          for c in ("separator", "quote")),
        GT3 + "test_a_later_data_quality_run_through_2024_is_never_d_s_latest_run",
        GT3 + "test_a_deleted_data_quality_run_that_finished_and_passed_passes_d",
        *(GT3 + f"test_a_rule_file_named_like_an_exempt_one_after_the_2025_time_is_seen_by_a_and_i[{c}]"
          for c in ("pycache", "exempt_prefix")),
        *(GT3 + f"test_a_replaced_or_grafted_history_refuses_the_2025_gate[{c}]"
          for c in ("replace", "replacements_off", "grafts")),
        *(GT3 + f"test_a_stopped_2025_start_retagged_is_never_left_out_of_the_starts[{c}]"
          for c in ("freeze_tag", "year", "git_commit", "freeze_commit", "control")),
    ),
    "Calibration bins": (
        IV3 + "test_two_folds_whose_edges_differ_bin_the_same_score_by_the_edge_of_the_fit_being_calibrated",
        VA3 + "test_each_fold_bins_its_calibration_by_the_edge_of_the_fit_being_calibrated",
    ),
    "The window ranking's W": (
        DV3 + "test_k_rounds_half_up_and_never_exceeds_w",
        DV3 + "test_w_counts_each_movable_excluded_day_by_its_own_date",
    ),
    "The rehearsal": (
        *(RH3 + f"test_a_rehearsal_that_differs_from_validation_refuses_to_log[{f}]"
          for f in ("run", "rank", "reason", "temp", "wind", "daymean", "lagday", "bin", "interval", "half_width",
                    "edge")),
        RH3 + "test_a_validation_run_without_the_january_fold_s_interval_layer_refuses_to_log",
        RH3 + "test_a_rehearsal_identical_to_validation_logs",
    ),
    "The freeze record": (
        FRC + "test_the_record_is_read_as_the_commit_that_added_it_holds_it",
        GT3 + "test_a_freeze_record_whose_commit_is_not_an_ancestor_of_the_start_refuses_a",
        *(GT3 + f"test_a_freeze_record_committed_again_on_a_sibling_history_refuses_a[{c}]"
          for c in ("sibling", "keep_ref", "stash_ref", "reflog", "off_head", "control")),
        *(GT3 + f"test_named_runs_off_the_start_s_history_are_checked_against_the_record_s_commit[{c}]"
          for c in ("unruled", "ruled")),
        *(FRC + f"test_the_writer_refuses_while_a_replace_ref_or_a_grafts_file_exists[{c}]"
          for c in ("replace", "grafts")),
        *(GT3 + f"test_a_ruling_and_its_evidence_lift_a_freeze_record_off_the_start_s_history[{c}]"
          for c in ("clean", "post_hoc")),
        *(FRC + f"test_a_plain_merge_carrying_the_record_adds_nothing[{c}]" for c in ("changed", "dropped", "plain")),
        *(GT3 + f"test_a_plain_merge_of_the_record_s_branch_is_no_second_addition[{c}]" for c in ("changed", "plain")),
        *(RV3 + f"test_a_freeze_record_off_head_s_history_is_shown_and_said_so[{c}]" for c in ("off_head", "control")),
    ),
    "First run": (
        FR3 + "test_a_v3_real_data_run_without_the_v3_first_run_tag_is_refused",
        FR3 + "test_a_v3_real_data_run_from_a_descendant_of_the_v3_first_run_tag_passes",
        FR3 + "test_every_v3_real_data_command_calls_the_guard_before_opening_a_file",
    ),
    "The 2025 time": (
        GT3 + "test_a_refetched_export_with_a_v2_2025_entry_before_the_record_refuses_h",
        GT3 + "test_a_and_i_firing_give_a_post_hoc_standing_whatever_the_order[i_then_a]",
        GT3 + "test_a_and_i_firing_give_a_post_hoc_standing_whatever_the_order[a_then_i]",
        GT3 + "test_a_start_after_a_stopped_one_with_a_clean_f_ruling_stays_post_hoc_when_h_or_i_fires[h]",
        GT3 + "test_a_start_after_a_stopped_one_with_a_clean_f_ruling_stays_post_hoc_when_h_or_i_fires[i]",
        *(GT3 + f"test_the_2025_time_never_moves_later_so_h_still_fires_after_a_refetch[{t}]"
          for t in ("dq_retrieved", "earlier_start")),
        GT3 + "test_a_data_quality_run_s_start_time_is_no_reading_of_the_2025_time",
        GT3 + "test_the_2025_time_never_moves_later_so_i_still_fires_after_a_refetch",
        *(GT3 + f"test_an_early_retrieval_on_a_later_page_of_data_quality_runs_still_fires_h[{c}]"
          for c in ("paged", "control")),
    ),
    "Reproduction": (
        RP3 + "test_the_same_hours_and_values_pass_and_say_what_was_compared",
        RP3 + "test_a_difference_of_5e_7_mw_passes_and_one_of_2e_6_is_refused",
        RP3 + "test_another_set_of_hours_with_a_forecast_is_refused[missing]",
        RP3 + "test_another_set_of_hours_with_a_forecast_is_refused[extra]",
        RP3 + "test_another_set_of_hours_with_a_forecast_is_refused[nan_in_v3]",
        RP3 + "test_another_set_of_hours_with_a_forecast_is_refused[nan_in_v2]",
        RP3 + "test_hours_outside_2020_2023_are_not_compared",
        VA3 + "test_a_failed_reproduction_refuses_before_any_run_is_logged[value]",
        VA3 + "test_a_failed_reproduction_refuses_before_any_run_is_logged[hour]",
        VA3 + "test_v2_s_form_at_midnight_is_v2_s_validation_exactly",
    ),
}
# The lines whose gates a later slice builds. Empty since slice 4: every line's breaks fire on synthetic chains and
# scratch clones (the tags' on a clone of v2's commit 3a1cd4a8c763), none waits for a real v3 tag. PENDING_V3 must be
# empty at the freeze: v3-rules-frozen is not tagged while a line of section 12 has no break.
PENDING_V3: set[str] = set()


def plan_v3_section_12_lines() -> list[str]:
    """The lines of docs/plan-v3.md section 12, by their italic heads."""
    import re

    from valiwatt.paths import REPO_ROOT

    text = (REPO_ROOT / "docs" / "plan-v3.md").read_text(encoding="utf-8")
    section = text[text.index("\n## 12. "):text.index("\n## 13. ")]
    return re.findall(r"^- \*(.+?):\*", section, flags=re.M)


def test_every_line_of_plan_v3_section_12_has_a_break_or_is_pending():
    """Each line of docs/plan-v3.md section 12 has at least one break, every break listed for it is in BREAKS, or the
    line waits in PENDING_V3 for the slice that builds its gate; no line is both."""
    registered = {b.test for b in BREAKS}
    lines = plan_v3_section_12_lines()
    assert len(lines) == len(set(lines)) == 21
    assert sorted(set(PLAN_V3_12) | PENDING_V3) == sorted(lines) and not set(PLAN_V3_12) & PENDING_V3
    assert [line for line, tests in PLAN_V3_12.items() if not tests] == []
    assert {line: missing for line, tests in PLAN_V3_12.items()
            if (missing := [t for t in tests if t not in registered])} == {}
