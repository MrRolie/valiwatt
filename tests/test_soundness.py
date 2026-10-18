"""Conceptual soundness, added after the freeze, deciding nothing (report/soundness.py; AGENTS.md, the owner's ruling of
2026-10-17). Each check is made to fire on a synthetic curve, fold or model with the defect, and a clean control stays
quiet; the refit variant the fold measures are built on reproduces validation's out-of-sample forecasts on the
synthetic world, and one that does not stops the run; the report refuses a soundness run that is not the chain's, a
soundness section whose numbers are not its run's, and a finding printed without its label."""

from __future__ import annotations

import copy
import dataclasses

import numpy as np
import pandas as pd
import pytest
import replay_world as RW
import report_payloads as RP
import report_store as S

from valiwatt.forecast import model
from valiwatt.forecast.guards import LookaheadError
from valiwatt.licences import parse_quotes
from valiwatt.paths import REPO_ROOT
from valiwatt.report import lineage
from valiwatt.report import soundness as SN
from valiwatt.report import validation as RV
from valiwatt.report.render import ReportRefused, environment

LANGS = ("fr", "en")
TEMPS = np.arange(-30.0, 36.0)


def curve(slope_cold: float = 400.0, slope_warm: float = 100.0, hours=range(24), edit=None) -> pd.DataFrame:
    """Each hour's curve relative to 18 °C on whole degrees: heating below, cooling above; `edit(hour, temps,
    effect)` returns an altered effect."""
    rows = []
    for h in hours:
        e = np.where(TEMPS < 18, slope_cold * (18 - TEMPS), slope_warm * (TEMPS - 18)).astype(float)
        if edit is not None:
            e = edit(h, TEMPS, e.copy())
        rows.append(pd.DataFrame({"hour": h, "temp_c": TEMPS, "effect_mw": e}))
    return pd.concat(rows, ignore_index=True)


def no_failure(year: str = "2024") -> dict:
    """The measures `findings` reads, every check passed, for one freeze and both curve kinds."""
    return {"shape": {year: {k: [] for k in SN.KINDS}}, "not_positive": {year: {k: [] for k in SN.KINDS}}}


# --- 1. shape ----------------------------------------------------------------------------------------------------

def test_a_curve_that_falls_one_step_colder_below_the_base_is_a_finding():
    tol = SN.TOLERANCE_MW

    def dip(h, t, e):
        if h == 7:
            e[t == 0.0] = e[t == 1.0][0] - 50.0  # one step colder, from 1 to 0 °C, lowers demand by 50 MW
        if h == 8:
            e[t == 17.0] = e[t == 18.0][0] - 50.0  # the step that ends at the base, from 18 to 17 °C
        if h == 9:
            e[t == -10.0] = e[t == -9.0][0] - 1.5 * tol  # just past the tolerance
        return e

    breaks = SN.shape_breaks(curve(edit=dip), -28.0, 34.0)
    # each dip breaks its step; the step after it, back on the line, rises, and stays quiet
    assert breaks == [{"hour": 7, "colder_c": 0.0, "warmer_c": 1.0, "side": "cold", "size_mw": -50.0},
                      {"hour": 8, "colder_c": 17.0, "warmer_c": 18.0, "side": "cold", "size_mw": -50.0},
                      {"hour": 9, "colder_c": -10.0, "warmer_c": -9.0, "side": "cold", "size_mw": -1.5 * tol}]
    R = no_failure() | {"shape": {"2024": {"production": breaks, "no_lag": []}}}
    assert SN.findings(R) == [{"measure": "shape", "year": 2024, "kind": "production", "count": 3, "sides": ["cold"],
                               "worst": breaks[0]}]


def test_a_curve_that_falls_one_step_warmer_above_the_base_is_a_finding():
    def dip(h, t, e):
        if h == 15:
            e[t == 25.0] = e[t == 24.0][0] - 30.0
        if h == 16:
            e[t == 19.0] = e[t == 18.0][0] - 50.0  # the step that starts at the base, from 18 to 19 °C
        return e

    breaks = SN.shape_breaks(curve(edit=dip), -28.0, 34.0)
    assert [(b["hour"], b["colder_c"], b["warmer_c"], b["side"], b["size_mw"]) for b in breaks] == [
        (15, 24.0, 25.0, "warm", -30.0), (16, 18.0, 19.0, "warm", -50.0)]
    R = no_failure("2023") | {"shape": {"2023": {"production": [], "no_lag": breaks}}}
    assert SN.findings(R) == [{"measure": "shape", "year": 2023, "kind": "no_lag", "count": 2, "sides": ["warm"],
                               "worst": breaks[1]}]


def test_a_clean_curve_and_a_reversal_within_the_tolerance_stay_quiet():
    assert SN.shape_breaks(curve(), -28.0, 34.0) == []

    def wobble(h, t, e):
        e[t == -10.0] = e[t == -9.0][0] - 0.5 * SN.TOLERANCE_MW  # inside the tolerance
        return e

    assert SN.shape_breaks(curve(edit=wobble), -28.0, 34.0) == []


def test_a_reversal_outside_the_training_range_is_not_checked():
    def dip(h, t, e):
        e[t == -30.0] = e[t == -29.0][0] - 500.0
        return e

    assert SN.shape_breaks(curve(edit=dip), -28.0, 34.0) == []
    assert len(SN.shape_breaks(curve(edit=dip), -30.0, 34.0)) == 24


def test_the_base_must_be_on_the_grid():
    with pytest.raises(SN.SoundnessError, match="grid"):
        SN.shape_breaks(curve(), -28.0, 34.0, base=17.5)


# --- 2. size -------------------------------------------------------------------------------------------------------

def test_a_cold_slope_that_is_not_positive_is_a_finding():
    flat = SN.slopes(curve(slope_cold=0.0), -28.0, 34.0)
    assert {(r["hour"], r["span"]) for r in SN.not_positive(flat)} == {
        (h, f"cold {SN.span_key(a, b)}") for h in range(24) for a, b in SN.COLD_SPANS}
    clean = SN.slopes(curve(), -28.0, 34.0)
    assert SN.not_positive(clean) == []
    assert all(r[SN.cold_key()] == pytest.approx(400.0) and r["warm 18..28"] == pytest.approx(100.0) for r in clean)
    one = SN.not_positive(SN.slopes(curve(slope_cold=0.0, hours=[3]), -28.0, 34.0))
    R = no_failure() | {"not_positive": {"2024": {"production": [], "no_lag": one}}}
    assert SN.findings(R) == [{"measure": "size", "year": 2024, "kind": "no_lag", "count": 2, "hours": [3]}]
    assert SN.findings(no_failure()) == []


def test_the_public_figure_is_quoted_from_its_provenance_page():
    doc = (REPO_ROOT / "docs" / "public-figures.md").read_text(encoding="utf-8")
    quotes = parse_quotes(doc)
    assert [(q.url, q.text) for q in quotes] == [(SN.PUBLIC_FIGURE["url"], SN.PUBLIC_FIGURE["quote"])]
    assert f"{SN.PUBLIC_FIGURE['mw_per_c']:.0f}\u00a0MW" in SN.PUBLIC_FIGURE["quote"]
    assert SN.PUBLIC_FIGURE["published"] in doc and "not like for like" in doc


@pytest.mark.network
def test_the_public_figure_is_still_on_its_page():
    import requests

    from valiwatt.licences import contains, page_text
    from valiwatt.sources.http import USER_AGENT

    r = requests.get(SN.PUBLIC_FIGURE["url"], timeout=60, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    assert contains(page_text(r.text, r.headers.get("content-type", "")), SN.PUBLIC_FIGURE["quote"])


# --- 3. stability --------------------------------------------------------------------------------------------------

def folds_with(slopes: list[float], lag24=0.4, lag168=0.07) -> list[dict]:
    return [{"fold": f"2021-{i + 1:02d}", "train_min_c": -20.0, "train_max_c": 30.0, "slope": {7: s},
             "lag24": lag24, "lag168": lag168} for i, s in enumerate(slopes)]


def test_a_fold_whose_slope_leaves_the_band_is_flagged():
    base = [300.0 + 10 * i for i in range(20)]
    clean = SN.spread(folds_with(base), {"2024": {7: 350.0}})
    assert clean["flagged"] == [] and clean["frozen_flagged"] == []
    out = SN.spread(folds_with(base + [5000.0]), {"2024": {7: 350.0}, "2023": {7: -3000.0}})
    assert [(x["fold"], x["hour"]) for x in out["flagged"]] == [("2021-21", 7)]
    assert [x["year"] for x in out["frozen_flagged"]] == [2023]
    assert SN.findings(no_failure() | {"stability": out}) == [
        {"measure": "stability_folds", "count": 1, "folds": ["2021-21"]},
        {"measure": "stability_freezes", "count": 1, "years": [2023]}]
    assert SN.findings(no_failure() | {"stability": clean}) == []


def test_a_freeze_just_outside_the_band_is_flagged_only_past_its_factor():
    """A freeze's slope outside the folds' band (5th to 95th percentile) by up to FOLD_FACTOR times the band's width,
    the edge included, is not flagged (on the real runs the two freezes sit just below the band at 22 h); one MW past
    that edge, it is, on either side."""
    base = [300.0 + 10 * i for i in range(20)]
    p5, p95 = (float(v) for v in np.quantile(base, [SN.SPREAD[0], SN.SPREAD[-1]]))
    edge = SN.FOLD_FACTOR * (p95 - p5)
    quiet = SN.spread(folds_with(base), {"2024": {7: p5 - 1.0}, "2023": {7: p95 + edge}})
    assert quiet["frozen_flagged"] == []
    out = SN.spread(folds_with(base), {"2024": {7: p5 - edge - 1.0}, "2023": {7: p95 + edge + 1.0}})
    assert sorted(x["year"] for x in out["frozen_flagged"]) == [2023, 2024]


def test_the_freezes_difference_is_over_their_common_range():
    a = curve()
    b = curve(edit=lambda h, t, e: e + np.where(t < -25, 1e6, 0.0))  # differs only below the common range
    d = SN.freeze_difference(a, b, (-24.0, 30.0), (-28.0, 34.0))
    assert d["common_range"] == [-24.0, 30.0]
    assert all(r["max_abs_mw"] == 0.0 and r["slope_change_pct"] == 0.0 for r in d["rows"])


# --- 4. the lags ---------------------------------------------------------------------------------------------------

def test_lags_that_are_not_positive_or_do_not_fade_are_findings():
    clean = SN.lag_reading({2024: {"lag24": 0.39, "lag168": 0.06}}, folds_with([300.0] * 4))
    assert clean["freezes"]["2024"]["positive"] and clean["freezes"]["2024"]["fades"]
    assert clean["freezes"]["2024"]["carry_per_1000_mw"] == pytest.approx(390.0)
    assert clean["folds_not_positive"] == [] and clean["folds_not_fading"] == []
    bad = SN.lag_reading({2024: {"lag24": 0.95, "lag168": 0.10}, 2023: {"lag24": 0.4, "lag168": -0.01}},
                         folds_with([300.0] * 2, lag24=1.2))
    R = {"shape": {}, "not_positive": {}, "lags": bad}
    got = SN.findings(R)
    assert {(x["measure"], x.get("year")) for x in got} == {("lags", 2024), ("lags", 2023), ("lags_folds", None)}


# --- the refit variant, its control, and the design window -----------------------------------------------------------

def test_the_refit_variant_reproduces_validation_s_forecasts():
    """The control: the GAM refitted at the ruled settings by the soundness module gives validation's out-of-sample
    forecasts of the production model on every hour, to CONTROL_MW."""
    s = RW.soundness_world()
    ruled = SN.fold_fits(s.f, s.folds)[model.RIDGE_ALPHA]["out"]
    got = SN.control(ruled, s.oos)
    assert got["hours"] == int(s.oos["pred_gam_forecast"].notna().sum()) and got["max_abs_mw"] <= SN.CONTROL_MW


def test_a_refit_variant_that_does_not_reproduce_validation_stops_the_run():
    s = RW.soundness_world()
    other = SN.fold_fits(s.f, s.folds, alphas=(2.0,))[2.0]["out"]  # another alpha: not validation's fit
    with pytest.raises(SN.SoundnessError, match="does not reproduce"):
        SN.control(other, s.oos)
    ruled = SN.fold_fits(s.f, s.folds[:1])[model.RIDGE_ALPHA]["out"]
    with pytest.raises(SN.SoundnessError, match="not the same hours"):
        SN.control(ruled, s.oos)
    oos = s.oos.loc[ruled.index]
    SN.control(ruled, oos)  # control: the first fold on its own hours
    at = oos.index[oos["pred_gam_forecast"].notna().to_numpy()][5]
    hole = ruled.copy()
    hole.loc[at, "pred"] = np.nan  # the refit has no forecast on an hour validation scored
    with pytest.raises(SN.SoundnessError, match="lack forecasts on different hours"):
        SN.control(hole, oos)
    other = oos.copy()
    other.loc[at, "pred_gam_forecast"] = np.nan  # and the reverse
    with pytest.raises(SN.SoundnessError, match="lack forecasts on different hours"):
        SN.control(ruled, other)


def test_the_soundness_folds_refuse_a_row_after_the_design_window():
    s = RW.soundness_world()
    late = s.f.copy()
    late.loc[late.index[:24], "day"] = pd.Timestamp("2024-01-02")
    with pytest.raises(LookaheadError):
        SN.fold_fits(late, s.folds[:1])
    with pytest.raises(LookaheadError):
        SN.input_rows_of(late, dataclasses.replace(s.frozen[2022], train_last_day="2024-12-24"))


def test_each_parameter_variant_is_its_own_fit():
    """Each setting of VARIANTS changed alone gives another fit than the ruled one: a knot variant's folds carry its
    number of knots, a ridge alpha's is the refit at that alpha; none is the ruled fit under another name."""
    s = RW.soundness_world()
    folds = s.folds[:2]
    alphas = tuple(sorted({model.RIDGE_ALPHA} | {v for k, v in SN.VARIANTS if k == "ridge_alpha"}))
    by_alpha = SN.fold_fits(s.f, folds, alphas=alphas)
    ruled = by_alpha[model.RIDGE_ALPHA]
    got = SN.variant_fits(s.f, folds, by_alpha)
    assert list(got) == list(SN.VARIANTS)
    for (setting, value), v in got.items():
        assert v["out"].index.equals(ruled["out"].index)
        assert not np.allclose(v["out"]["pred"], ruled["out"]["pred"], rtol=0, atol=1e-9, equal_nan=True), (
            setting, value)
        if setting == "n_knots":
            assert [len(x["knots"]) for x in v["fits"]] == [value] * len(folds)
        else:
            assert v is by_alpha[value] and [len(x["knots"]) for x in v["fits"]] == [model.N_KNOTS] * len(folds)


def test_the_knot_variants_are_the_quantile_rule_with_their_own_count():
    t = RW.soundness_world().f["temp_forecast"].dropna().to_numpy()
    for n in (8, 10, 12):
        assert np.allclose(SN.variant_knots(t, n), np.percentile(t, np.linspace(0, 100, n)), atol=1e-12)
    assert np.array_equal(SN.variant_knots(t, model.N_KNOTS), model._place_knots(t))


# --- 5. sensitivity ------------------------------------------------------------------------------------------------

def test_the_input_move_is_held_to_the_curves():
    s = RW.soundness_world()
    m = s.frozen[2022]
    out = SN.input_sensitivity(m, s.input_rows)
    assert out["consistent"] and out["consistency_max_abs_mw"] <= SN.CONSISTENCY_MW and out["rows"] > 0
    assert set(out["moves"]) == {"-1", "+1"}
    # the curve values are model.gam_partial_effects' at each row's own hour and temperature
    hours, temps = np.array([0, 7, 23]), np.array([-10.0, 0.0, 22.0])
    pe = model.gam_partial_effects(np.asarray(m.knots), m.beta, m.scale, temps, ref_c=SN.BASE_C)
    want = [float(pe[(pe["hour"] == h) & (pe["temp_c"] == t)]["effect_mw"].iloc[0]) for h, t in zip(hours, temps)]
    assert np.allclose(SN.curve_values(m, hours, temps), want, atol=1e-9)


def test_curves_that_are_not_the_model_s_make_the_input_move_a_finding(monkeypatch):
    s = RW.soundness_world()
    other = s.frozen[2021]
    real = SN.curve_values
    monkeypatch.setattr(SN, "curve_values", lambda frozen, hours, temps: real(other, hours, temps))
    out = SN.input_sensitivity(s.frozen[2022], s.input_rows)
    assert not out["consistent"]
    assert [x["measure"] for x in SN.findings({"shape": {}, "not_positive": {}, "input": out})] == ["input"]


def test_a_partial_effects_table_that_is_not_the_model_s_is_refused():
    s = RW.soundness_world()
    SN.curves_from_table(s.tables[2022], s.frozen[2022])  # control
    bad = s.tables[2022].copy()
    bad.loc[bad.index[3], "effect_mw"] += 1.0
    with pytest.raises(SN.SoundnessError, match="not its model's curves"):
        SN.curves_from_table(bad, s.frozen[2022])


def test_a_parameter_variant_scored_on_other_hours_is_refused():
    s = RW.soundness_world()
    ruled = SN.fold_fits(s.f, s.folds[:2])[model.RIDGE_ALPHA]["out"]
    same = SN.parameter_sensitivity(ruled, {("ridge_alpha", 1.0): ruled}, n_boot=50)
    assert same["variants"][0]["difference"] == 0.0 and same["label"] == SN.PARAMETER_LABEL
    with pytest.raises(SN.SoundnessError, match="other hours"):
        SN.parameter_sensitivity(ruled, {("n_knots", 8): ruled.iloc[:-5]}, n_boot=50)


def test_the_whole_measure_on_the_synthetic_world_carries_its_label_and_every_measure():
    R = RW.soundness_results()
    assert R["label"] == SN.LABEL and R["input"]["label"] == SN.INPUT_LABEL
    assert R["parameters"]["label"] == SN.PARAMETER_LABEL
    assert [[v["setting"], v["value"]] for v in R["parameters"]["variants"]] == [list(x) for x in SN.VARIANTS]
    # each variant scored is its own fit, not the ruled one: its MAPE differs from the ruled fit's
    assert all(v["difference"] != 0.0 and v["mape"] != R["parameters"]["ruled"]["mape"]
               for v in R["parameters"]["variants"])
    assert R["control"]["max_abs_mw"] <= SN.CONTROL_MW and R["stability"]["folds"] == 12
    assert set(R["freezes"]) == {"2021", "2022"} and R["settings"]["heating_base_c"] == 18.0
    assert SN.view(R | {"run_id": "x"}, "x")["last"] == 2022


# --- the tracked run, against a real run store -----------------------------------------------------------------------

def test_the_soundness_run_reads_the_chain_s_frozen_runs_and_logs_every_measure(tmp_path, monkeypatch):
    """`valiwatt soundness` against a real MLflow store: a data-quality run, a validation run and two frozen runs
    registered under their aliases (the synthetic world's 2021 and 2022 freezes standing for 2023 and 2024), the
    design data the synthetic world's. The run loads the frozen models through their aliases, logs the measures, names
    the runs it read, and the report's lineage follows it; a frozen run built on another validation run refuses it."""
    import json

    from valiwatt import paths, tracking
    from valiwatt.forecast import freeze as fz
    from valiwatt.forecast import validate as vd
    from valiwatt.monitor.rules import rules_sha256

    monkeypatch.setattr(tracking, "MLRUNS", tmp_path / "mlruns")
    monkeypatch.setattr(paths, "MLRUNS", tmp_path / "mlruns")
    monkeypatch.setattr(SN, "FREEZES", (2021, 2022))
    s, w = RW.soundness_world(), RW.world_v2()
    dq = RW._log_dq(2024)
    val = RW.log_validation(w, dq)
    frozen_ids = {}
    for year, m in s.frozen.items():
        example = s.f[fz.input_columns(m.family, m.scenario)].dropna().tail(24).astype({"hour": float})
        run, _ = fz.log_frozen(m, fz.coefficient_table(m), example, {"validation_run_id": val, "dq_run_id": dq,
                                                                      "rules_sha256": rules_sha256()},
                               comparators=w.comparators, extras={"partial_effects.csv": s.tables[year]})
        frozen_ids[year] = run.info.run_id
    c = tracking.client()
    monkeypatch.setattr(vd, "load_inputs", lambda: (c.get_run(dq), None, None))
    monkeypatch.setattr(SN, "design_inputs", lambda dq_run, weather, last: {
        "f": s.f, "train_at": None, "folds": s.folds, "table": s.f})
    assert SN.run_soundness(n_boot=50) == 0
    run = tracking.latest_run(SN.EXPERIMENT, "tags.kind = 'soundness'")
    tags = run.data.tags
    assert (tags["dq_run_id"], tags["validation_run_id"], tags["status"], tags["label"]) == (dq, val, "complete",
                                                                                               SN.LABEL)
    assert {y: tags[f"frozen_run_id_{y}"] for y in s.frozen} == frozen_ids
    assert {y: tags[f"coefficient_sha256_{y}"] for y in s.frozen} == {y: m.sha256() for y, m in s.frozen.items()}
    from pathlib import Path

    got = Path(c.download_artifacts(run.info.run_id, "results/soundness.json", str(tmp_path)))
    results = json.loads(got.read_text(encoding="utf-8"))
    assert results["run_id"] == run.info.run_id and results["control"]["max_abs_mw"] <= SN.CONTROL_MW
    assert SN.view(results, run.info.run_id)["freezes"]["2022"]["sha256"] == s.frozen[2022].sha256()
    frozen_runs = {y: c.get_run(i) for y, i in frozen_ids.items()}
    assert lineage._soundness(frozen_runs, val).info.run_id == run.info.run_id
    with pytest.raises(lineage.ChainBroken, match="validation_run_id"):
        lineage._soundness(frozen_runs, "another")
    c.set_tag(frozen_ids[2021], "dq_run_id", "another")
    with pytest.raises(SN.SoundnessError, match="another data-quality run"):
        SN.run_soundness(n_boot=50)
    c.set_tag(frozen_ids[2021], "dq_run_id", dq)
    c.set_tag(frozen_ids[2021], "validation_run_id", "another")
    with pytest.raises(Exception, match="another validation run"):
        SN.run_soundness(n_boot=50)


# --- the report: the chain's run, its numbers, its label ------------------------------------------------------------

def test_a_soundness_run_that_is_not_the_chain_s_is_refused(monkeypatch):
    w = S.World().install(monkeypatch)
    assert w.follow().soundness.info.run_id == RP.SOUND_RUN  # control
    for tag, value in (("frozen_run_id_2023", "f2022"), ("validation_run_id", "val2"),
                       ("coefficient_sha256_2024", "0" * 64), ("code_sha256", "0" * 64)):
        x = S.World().install(monkeypatch)
        x.runs[RP.SOUND_RUN].data.tags[tag] = value
        with pytest.raises(lineage.ChainBroken, match="soundness"):
            x.follow()
    x = S.World().install(monkeypatch)
    x.runs[RP.SOUND_RUN].data.tags["frozen_run_id_2024"] = "f2025"
    with pytest.raises(lineage.ChainBroken, match="no finished soundness run"):
        x.follow()


def test_the_soundness_run_is_in_the_provenance_and_the_stamp(monkeypatch):
    w = S.World().install(monkeypatch)
    ctx = w.context()
    assert ctx["runs"]["soundness"]["id"] == RP.SOUND_RUN and ctx["sound"]["run_id"] == RP.SOUND_RUN
    for lang in LANGS:
        assert f"`{RP.SOUND_RUN}`" in RV.numbered_section(RP.render(ctx, lang), 13)


def test_soundness_results_of_another_model_or_unlabelled_are_refused(monkeypatch):
    w = S.World().install(monkeypatch)
    w.files[(RP.SOUND_RUN, "results/soundness.json")]["freezes"]["2024"]["sha256"] = "0" * 64
    with pytest.raises(ReportRefused, match="measured a 2024 model"):
        w.context()
    w = S.World().install(monkeypatch)
    w.files[(RP.SOUND_RUN, "results/soundness.json")]["label"] = "held_out"
    with pytest.raises(ReportRefused, match="label"):
        w.context()


def test_soundness_results_that_name_another_run_are_refused(monkeypatch):
    w = S.World().install(monkeypatch)
    w.files[(RP.SOUND_RUN, "results/soundness.json")]["run_id"] = "60" * 16
    with pytest.raises(ReportRefused, match="name run"):
        w.context()


@pytest.mark.parametrize("lang", LANGS)
def test_a_soundness_section_that_does_not_name_its_run_is_refused(lang):
    ctx = RP.context()
    text = RP.render(ctx, lang)  # control
    status = RV.status_sentence(environment(lang), ctx, lang)
    part = RV.soundness_section(text)
    named = f"`{RP.SOUND_RUN}`"
    assert named in part and named in RV.numbered_section(text, 13)
    for other in ("", f"`{'60' * 16}`"):  # removed from section 3.1, or another run's there; section 13 unchanged
        altered = text.replace(part, part.replace(named, other))
        assert altered != text and named in RV.numbered_section(altered, 13)
        with pytest.raises(ReportRefused, match="does not name the soundness run"):
            RV.check_rendered(altered, lang, ctx, "validation", status)
    elsewhere = copy.deepcopy(ctx)
    elsewhere["runs"]["soundness"]["id"] = "60" * 16  # the section's numbers not the run the provenance names
    with pytest.raises(ReportRefused, match="does not name the soundness run"):
        RV.check_rendered(text, lang, elsewhere, "validation", status)


@pytest.mark.parametrize("lang", LANGS)
def test_a_failed_check_not_stated_as_a_finding_is_refused(lang, monkeypatch):
    """Each failed check of the run is a finding: a run whose list of findings leaves one out (or states one whose
    check passed) is refused, at the store and at the render, so that no failing table prints beside "no check of
    this section failed"."""
    ctx = RP.context()
    measures = [x["measure"] for x in ctx["sound"]["findings"]]
    assert {"shape", "size", "stability_folds", "stability_freezes"} <= set(measures)  # the synthetic run's
    for drop in ("shape", "size", "stability_folds", "stability_freezes"):
        other = copy.deepcopy(ctx)
        other["sound"]["findings"] = [x for x in other["sound"]["findings"] if x["measure"] != drop]
        with pytest.raises(ReportRefused, match="not their failed checks"):
            RP.render(other, lang)
    other = copy.deepcopy(ctx)
    other["sound"]["findings"] = []
    with pytest.raises(ReportRefused, match="not their failed checks"):
        RP.render(other, lang)
    other = copy.deepcopy(ctx)
    other["sound"]["findings"].append({"measure": "input", "max_abs_mw": 1.0})  # a check that passed
    with pytest.raises(ReportRefused, match="not their failed checks"):
        RP.render(other, lang)
    w = S.World().install(monkeypatch)
    w.files[(RP.SOUND_RUN, "results/soundness.json")]["findings"] = []
    with pytest.raises(ReportRefused, match="not their failed checks"):
        w.context()


@pytest.mark.parametrize("lang", LANGS)
def test_the_story_and_section_12_without_the_soundness_label_are_refused(lang):
    ctx = RP.context()
    story, text = RP.render(ctx, lang, "story"), RP.render(ctx, lang)  # control
    status = RV.status_sentence(environment(lang), ctx, lang)
    say = RV.SAY[lang]
    altered = story.replace(say["story_soundness"], "")
    assert altered != story
    with pytest.raises(ReportRefused, match="story states the soundness"):
        RV.check_rendered(altered, lang, ctx, "story", status)
    twelve = RV.numbered_section(text, 12)
    altered = text.replace(twelve, twelve.replace(say["sr_soundness"], ""))
    assert altered != text
    with pytest.raises(ReportRefused, match="section 12"):
        RV.check_rendered(altered, lang, ctx, "validation", status)


@pytest.mark.parametrize("lang", LANGS)
def test_a_soundness_section_whose_numbers_are_not_its_run_s_is_refused(lang):
    ctx = RP.context()
    text = RP.render(ctx, lang)  # control: the render's own checks pass
    row = RV.slope_rows(ctx["sound"], lang)[7]
    altered = text.replace(row, row.replace(" | ", " | 1", 1))
    assert altered != text
    with pytest.raises(ReportRefused, match="not its run's"):
        RV.check_rendered(altered, lang, ctx, "validation", RV.status_sentence(environment(lang), ctx, lang))
    other = copy.deepcopy(ctx)
    other["sound"]["slopes"][str(other["sound"]["last"])]["production"][7][other["sound"]["cold"]] += 10.0
    with pytest.raises(ReportRefused, match="not its run's"):
        RV.check_rendered(text, lang, other, "validation", RV.status_sentence(environment(lang), ctx, lang))
    row = RV.lag_rows(ctx["sound"], lang)[2]  # the lags' sum
    assert row in text.splitlines()
    altered = text.replace(row, row.replace(" | ", " | 9", 1))
    with pytest.raises(ReportRefused, match="not its run's"):
        RV.check_rendered(altered, lang, ctx, "validation", RV.status_sentence(environment(lang), ctx, lang))


@pytest.mark.parametrize("lang", LANGS)
def test_a_soundness_finding_printed_without_its_label_is_refused(lang):
    ctx = RP.context()
    assert ctx["sound"]["findings"]
    text = RP.render(ctx, lang)
    label = RV.SAY[lang]["soundness_label"]
    finding = next(line for line in text.splitlines() if line.startswith(f"- {RV.SAY[lang]['finding']}"))
    status = RV.status_sentence(environment(lang), ctx, lang)
    for altered in (text.replace(finding, finding.replace(f" ({label})", "")),
                    text.replace(f"({label})", "", 1),  # a measure's heading
                    text.replace(finding + "\n", "")):  # a finding of the run not stated
        assert altered != text
        with pytest.raises(ReportRefused):
            RV.check_rendered(altered, lang, ctx, "validation", status)
    with pytest.raises(ReportRefused, match="no soundness section"):
        RV.check_rendered(text.replace(RV.SOUNDNESS_HEADING, "### 3.2 "), lang, ctx, "validation", status)


@pytest.mark.parametrize("lang", LANGS)
def test_a_soundness_run_without_findings_says_so(lang):
    ctx = RP.context()
    sound = copy.deepcopy(ctx["sound"])
    for year in sound["shape"]:  # every check passed: no step breaks its side, every cold slope is positive, no flag
        sound["shape"][year] = {k: [] for k in SN.KINDS}
        for k in SN.KINDS:
            for r in sound["slopes"][year][k]:
                r.update({c: abs(x) + 1.0 for c, x in r.items() if c.startswith("cold ") and x is not None})
    sound["stability"] |= {"flagged": [], "frozen_flagged": []}
    ctx["sound"] = sound | {"findings": [], "breaks_by_hour": {y: {k: {} for k in SN.KINDS} for y in sound["shape"]}}
    assert SN.failed(ctx["sound"]) == {}
    text = RP.render(ctx, lang)
    assert RV.SAY[lang]["no_finding"] in RV.soundness_section(text)
