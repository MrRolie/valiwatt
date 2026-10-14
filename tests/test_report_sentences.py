"""The validation report's computed sentences: what derive() reads from the runs, and every branch of the sentence
macros, in both languages, says only what its data show."""

import itertools

import numpy as np
import pandas as pd
import pytest

from valiwatt.breaks import BREAKS, SYNTHETIC_WORLD
from valiwatt.forecast.evaluate import errors, mean_with_ci
from valiwatt.report import validation as RV
from valiwatt.report.render import ReportRefused, environment

LANGS = ("fr", "en")
SEASONS = ("winter", "spring", "summer", "fall")


def macros(lang):
    return environment(lang).get_template(f"sentences.{lang}.md.j2").module


# --- coverage sides -------------------------------------------------------------------------------------------------

def test_a_group_is_under_or_over_covered_only_when_its_printed_interval_excludes_the_nominal():
    rows = [{"key": "a", "covered_low": 0.80, "covered_high": 0.8994},   # prints 89.9: wholly below
            {"key": "b", "covered_low": 0.85, "covered_high": 0.89996},  # prints 90.0: holds the nominal
            {"key": "c", "covered_low": 0.90004, "covered_high": 0.95},  # prints 90.0: holds the nominal
            {"key": "d", "covered_low": 0.9006, "covered_high": 0.95}]   # prints 90.1: wholly above
    assert RV.coverage_sides(rows, 0.9) == {"under": ["a"], "over": ["d"]}


def named(text, lang):
    names = {"fr": {"winter": "l'hiver", "spring": "le printemps", "summer": "l'été", "fall": "l'automne"},
             "en": {k: k for k in SEASONS}}[lang]
    return {k for k, n in names.items() if n in text}


SPLIT = {"fr": ("Sous-couverts", "Sur-couverts", "Aucune saison", "Aucun groupe"),
         "en": ("Under-covered", "Over-covered", "No season", "No group")}


@pytest.mark.parametrize("lang", LANGS)
def test_the_coverage_sentence_puts_each_group_on_the_side_its_interval_shows(lang):
    sentence = macros(lang).coverage_sides
    subsets = [list(c) for n in range(3) for c in itertools.combinations(SEASONS, n)]
    deciles = [[], ["10"], ["7", "8", "9", "10"]]
    under_word, over_word, no_under, no_over = SPLIT[lang]
    for under, over, du, do in itertools.product(subsets, subsets, deciles, deciles):
        if set(under) & set(over) or set(du) & set(do):
            continue
        text = str(sentence({"under": under, "over": over}, {"under": du, "over": do}, 0.9))
        first, second = text.split(". ", 1)
        assert "None" not in text and "\n" not in text
        assert first.startswith(under_word if (under or du) else no_under)
        assert second.startswith(over_word if (over or do) else no_over)
        assert named(first, lang) == set(under) and named(second, lang) == set(over)
        for d in du:
            assert f" {d}" in first or f" {d}." in first
        if not du and not do:
            assert "décile de charge prévue" not in text and "deciles " not in text and "decile 1" not in text


# --- detection limits -----------------------------------------------------------------------------------------------

def power(caught: dict, half: dict, starts=("2024-01-01", "2024-04-01")):
    """caught: {bias: set of starts that catch it}; half: {factor: set of starts that catch it}."""
    rows = {"bias": [], "half_width": []}
    for s in starts:
        for b, who in caught.items():
            rows["bias"].append({"start": s, "season": "winter", "bias": b, "weeks": 3 if s in who else None})
        for f, who in half.items():
            rows["half_width"].append({"start": s, "season": "winter", "half_width": f, "weeks": 2 if s in who else None})
    return rows


def test_detection_limits_read_the_power_table():
    both = {"2024-01-01", "2024-04-01"}
    L = RV.detection_limits(power(
        {-0.04: both, -0.02: {"2024-04-01"}, 0.02: set(), 0.04: both, 0.06: both},
        {0.9: set(), 0.7: {"2024-01-01"}, 0.5: both}))
    assert L["sizes"] == [0.02, 0.04, 0.06]
    assert L["every_from"] == 0.04
    assert L["smallest"] == 0.02
    assert L["smallest_by_sign"] == {"up": {"caught": 0, "cases": 2}, "down": {"caught": 1, "cases": 2}}
    assert L["starts"] == 2 and L["months"] == [1, 4]
    assert (L["strongest"], L["strongest_caught"], L["mildest"], L["milder_max"]) == (
        0.5, {"caught": 2, "cases": 2}, 0.9, 1)


def test_a_gap_missed_at_the_largest_size_leaves_no_size_caught_everywhere():
    L = RV.detection_limits(power({-0.08: {"2024-01-01"}, 0.08: {"2024-01-01", "2024-04-01"}}, {0.5: set()}))
    assert L["every_from"] is None and L["milder_max"] is None


def test_an_empty_power_table_is_refused():
    with pytest.raises(ReportRefused):
        RV.detection_limits({"bias": [], "half_width": []})


@pytest.mark.parametrize("lang", LANGS)
def test_the_power_sentence_has_a_true_branch_for_every_table(lang):
    both = {"2024-01-01", "2024-04-01"}
    cases = [
        power({-0.02: set(), 0.02: set(), 0.08: both, -0.08: both}, {0.9: set(), 0.5: both}),
        power({-0.02: set(), 0.02: set(), 0.08: {"2024-01-01"}, -0.08: both}, {0.9: {"2024-01-01"}, 0.5: set()}),
        power({-0.02: both, 0.02: both}, {0.5: {"2024-04-01"}}),
    ]
    for p in cases:
        L = RV.detection_limits(p)
        text = str(macros(lang).power_limit(L, 2024, 8))
        assert "None" not in text and "\n" not in text
        if L["every_from"] is None:
            assert ("aucune taille" if lang == "fr" else "no size") in text
        else:
            assert ("±" + f"{100 * L['every_from']:.0f}") in text
        up, down = L["smallest_by_sign"]["up"], L["smallest_by_sign"]["down"]
        assert f"{up['caught']} {'des' if lang == 'fr' else 'of'} {up['cases']}" in text
        assert f"{down['caught']} {'des' if lang == 'fr' else 'of'} {down['cases']}" in text
        if L["milder_max"] == 0:
            assert ("aucune réduction moindre" if lang == "fr" else "no milder cut") in text


# --- near misses ----------------------------------------------------------------------------------------------------

def week(start, flags=(), broad=(), bias=0.01, threshold=0.06, evaluated=True):
    keys = ("mape_high", "bias_high", "bias_low", "coverage_low")
    return {"week_start": start, "evaluated": evaluated,
            "flags": {k: k in flags for k in keys}, "broad": {k: k in broad for k in keys},
            "stats": {"mape": 0.07, "rel_bias": bias, "covered": 0.8, "hours": 168},
            "trimmed": {k: 0.05 for k in keys}, "thresholds": {k: threshold for k in keys}}


def alone(a, b):
    return [{"week_start": b["week_start"], "weeks": [{k: w[k] for k in ("week_start", "stats", "flags", "trimmed")}
                                                      for w in (a, b)]}]


def test_a_near_miss_names_what_kept_the_rule_quiet():
    cases = {
        "trim": (week("2024-10-28", ["mape_high"], bias=0.007), week("2024-11-04", ["mape_high"], bias=-0.008)),
        "different": (week("2023-01-30", ["mape_high"], bias=0.05), week("2023-02-06", ["bias_low"], bias=-0.05)),
        "echo": (week("2023-05-01", ["mape_high", "bias_high"], ["mape_high", "bias_high"], bias=0.05),
                 week("2023-05-08", ["mape_high", "bias_low"], ["mape_high", "bias_low"], bias=-0.05)),
    }
    for kept_by, (a, b) in cases.items():
        got = RV.near_misses(alone(a, b), [a, b], [{"week_start": b["week_start"], "recommendation": None}])
        assert got[0]["kept_by"] == kept_by
    a, b = cases["trim"]
    got = RV.near_misses(alone(a, b), [a, b], [])[0]
    assert got["bias_flips"] and not got["bias_flagged"]
    assert got["same"] == [{"failure": "mape_high", "trimmed": [0.05, 0.05], "broad": [False, False],
                            "threshold": 0.06, "thresholds_equal": True}]
    fired = RV.near_misses(alone(a, b), [a, b], [{"week_start": "2024-11-04", "recommendation": "retrain"}])
    assert fired[0]["kept_by"] == "fired"


def test_a_near_miss_week_missing_from_the_replay_is_refused():
    a, b = week("2024-10-28", ["mape_high"]), week("2024-11-04", ["mape_high"])
    with pytest.raises(ReportRefused, match="2024-10-28"):
        RV.near_misses(alone(a, b), [b], [])
    with pytest.raises(ReportRefused, match="2024-10-28"):
        RV.near_misses(alone(a, b), [dict(a, evaluated=False), b], [])


def test_a_failure_broad_in_both_weeks_without_a_retrain_is_refused():
    a = week("2024-10-28", ["mape_high"], ["mape_high"])
    b = week("2024-11-04", ["mape_high"], ["mape_high"])
    with pytest.raises(ReportRefused, match="no retrain"):
        RV.near_misses(alone(a, b), [a, b], [])


@pytest.mark.parametrize("lang", LANGS)
def test_every_near_miss_branch_renders_a_sentence(lang):
    m = macros(lang)
    base = {"week_start": "2024-11-04", "bias_flips": True, "bias_flagged": False,
            "weeks": [{"stats": {"mape": 0.063, "rel_bias": 0.0075},
                       "flags": {"mape_high": True, "bias_high": False, "bias_low": False}},
                      {"stats": {"mape": 0.064, "rel_bias": -0.0086},
                       "flags": {"mape_high": True, "bias_high": False, "bias_low": False}}]}
    same = [{"failure": "mape_high", "trimmed": [0.047, 0.046], "threshold": 0.06, "thresholds_equal": True}]
    for kept_by, broad in itertools.product(("fired", "different", "echo", "trim"), ([False, False], [True, False])):
        item = dict(base, kept_by=kept_by, same=[dict(same[0], broad=broad)])
        text = str(m.near_why([item], 2))
        assert "None" not in text and "\n" not in text and text.endswith(".")
        if kept_by == "trim":
            assert "4,70\u00a0%" in text if lang == "fr" else "4.70%" in text
            assert ("aucune des deux" if lang == "fr" else "neither week") in text if broad == [False, False] else \
                ("l'une des deux" if lang == "fr" else "one of the two") in text
            assert ("l'exclusion de l'écho" if lang == "fr" else "echo exclusion") in text
    listed = str(m.near_list([base]))
    assert "2024-11-04" in listed and ("6,30\u00a0%" in listed if lang == "fr" else "6.30%" in listed)


# --- spring 2020, the interval of the difference -------------------------------------------------------------------

def spring_frame(shift=-0.02, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01T06:00Z", "2023-12-31T05:00Z", freq="h")
    day = (idx - pd.Timedelta(hours=6)).tz_convert(None).normalize()
    pred = np.full(len(idx), 20_000.0)
    bias = np.where((day.year == 2020) & day.month.isin([3, 4, 5, 6]), shift, 0.0)
    target = pred * (1 + bias + rng.normal(0, 0.03, len(idx)))
    fold = pd.Series(day).dt.to_period("M").astype(str).to_numpy()
    return pd.DataFrame({"target": target, "pred_persistence": pred, "day": day, "hour": idx.hour, "fold": fold},
                        index=idx)


def test_the_difference_uses_the_codes_week_block_bootstrap_on_each_side():
    oos = spring_frame()
    e = errors(oos, "pred_persistence")
    months = pd.to_datetime(e["day"]).dt.month.isin([3, 4, 5, 6])
    a, b = e[months & (e["year"] == 2020)], e[months & (e["year"] != 2020)]
    for side, seed in ((a, 0), (b, 1)):
        draws = RV._boot_means(side, "rel_bias", 200, seed)
        ref = mean_with_ci(side, "rel_bias", 200, seed)
        assert np.quantile(draws, [0.025, 0.975]).tolist() == [ref["ci95_low"], ref["ci95_high"]]
    d = RV.shift_difference(oos, "pred_persistence", 200)
    assert d["difference"] == pytest.approx(a["rel_bias"].mean() - b["rel_bias"].mean())
    assert d["ci95_low"] < d["difference"] < d["ci95_high"]
    assert d["separated"]  # a 2 % shift with 3 % hourly noise over a spring of weeks
    assert not RV.shift_difference(spring_frame(shift=0.0), "pred_persistence", 200)["separated"]


def test_a_validation_frame_without_its_spring_is_refused():
    oos = spring_frame()
    oos = oos[~((oos["day"].dt.year == 2020) & oos["day"].dt.month.isin([3, 4, 5, 6]))]
    with pytest.raises(ReportRefused, match="spring"):
        RV.shift_difference(oos, "pred_persistence", 50)


# --- drift references, constants, breaks ---------------------------------------------------------------------------

def test_drift_reference_years_count_the_earlier_years_each_input_has():
    got = RV.drift_reference_years(2021, 2023, 2024, "2020-01")
    assert got == {"temperature": {"calibration": (2, 4), "replay": 5},
                   "forecast": {"calibration": (1, 3), "replay": 4}}


def test_the_breaks_run_must_say_which_breaks_are_controls():
    with pytest.raises(ReportRefused, match="controls"):
        RV.derive({"conformal": {"alpha": 0.1}, "coverage": {}}, {}, {}, {}, {"breaks": [{"en": "x"}]}, {2024: {}},
                  pd.DataFrame())


def test_a_control_is_marked_in_the_registry_exactly_when_its_label_says_so():
    for b in BREAKS:
        assert b.control == b.en.startswith("Control:") == b.fr.startswith("Témoin")
    assert sum(b.control for b in BREAKS) >= 1


def test_the_synthetic_world_uses_the_registry_constants():
    from replay_world import thresholds

    assert thresholds()["drift"] == {"temperature_high": SYNTHETIC_WORLD["drift_alarm"],
                                     "forecast_high": SYNTHETIC_WORLD["drift_alarm"]}


@pytest.mark.parametrize("lang", LANGS)
def test_the_breaks_table_has_a_word_for_a_control_that_stayed_quiet(lang):
    source = environment(lang).loader.get_source(environment(lang), f"validation.{lang}.md.j2")[0]
    assert ("resté silencieux" if lang == "fr" else "stayed quiet") in source
    assert "elif row.control" in source
