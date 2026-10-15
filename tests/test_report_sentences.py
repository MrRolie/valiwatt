"""The validation report's computed sentences: what derive() reads from the runs, and every branch of the sentence
macros, in both languages, says only what its data show."""

import inspect
import itertools
import re
from decimal import Decimal

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


def test_every_from_needs_every_larger_size_caught_too():
    both = {"2024-01-01", "2024-04-01"}
    L = RV.detection_limits(power({0.02: both, -0.02: both, 0.04: {"2024-01-01"}, -0.04: both, 0.06: both,
                                   -0.06: both}, {0.5: both}))
    assert L["every_from"] == 0.06  # 0.02 is caught everywhere, but 0.04 is not: no size from 0.02 up holds


def test_an_empty_power_table_is_refused():
    with pytest.raises(ReportRefused, match="sustained-gap"):
        RV.detection_limits({"bias": [], "half_width": []})


def test_a_power_table_without_narrowed_interval_rows_is_refused():
    both = {"2024-01-01", "2024-04-01"}
    table = power({0.02: both, -0.02: both}, {})
    assert table["bias"] and not table["half_width"]
    with pytest.raises(ReportRefused, match="narrowed-interval"):
        RV.detection_limits(table)


@pytest.mark.parametrize("lang", LANGS)
def test_the_power_sentence_has_a_true_branch_for_every_table(lang):
    both = {"2024-01-01", "2024-04-01"}
    cases = [
        power({-0.02: set(), 0.02: set(), 0.08: both, -0.08: both}, {0.9: set(), 0.5: both}),
        power({-0.02: set(), 0.02: set(), 0.08: {"2024-01-01"}, -0.08: both}, {0.9: {"2024-01-01"}, 0.5: set()}),
        power({-0.02: both, 0.02: both}, {0.5: {"2024-04-01"}}),
        # not monotone: the smallest size is caught everywhere, the largest is not
        power({-0.02: both, 0.02: both, 0.04: {"2024-01-01"}, -0.04: both}, {0.5: both}),
    ]
    for p in cases:
        L = RV.detection_limits(p)
        text = str(macros(lang).power_limit(L, 2024, 8))
        assert "None" not in text and "\n" not in text
        if L["every_from"] is None:  # even the largest size missed at some start, whatever the smaller ones did
            largest = ("±" + f"{100 * L['sizes'][-1]:.0f}" + ("\u00a0%" if lang == "fr" else "%"))
            assert (f"même le plus grand écart mesuré entre la demande et la prévision, {largest}, n'a pas donné"
                    if lang == "fr" else
                    f"even the largest gap measured between demand and forecast, {largest}, did not yield") in text
            assert ("aucune taille" if lang == "fr" else "no size") not in text
        else:
            assert ("±" + f"{100 * L['every_from']:.0f}") in text
        up, down = L["smallest_by_sign"]["up"], L["smallest_by_sign"]["down"]
        assert f"{up['caught']} {'des' if lang == 'fr' else 'of'} {up['cases']}" in text
        assert f"{down['caught']} {'des' if lang == 'fr' else 'of'} {down['cases']}" in text
        if L["milder_max"] == 0:
            assert ("aucune réduction moindre" if lang == "fr" else "no milder cut") in text


@pytest.mark.parametrize("lang", LANGS)
def test_the_power_sentence_puts_each_count_next_to_its_own_sign(lang):
    both = {"2024-01-01", "2024-04-01"}
    L = RV.detection_limits(power({0.02: set(), -0.02: {"2024-01-01"}, 0.06: both, -0.06: both},
                                  {0.9: {"2024-01-01"}, 0.7: set(), 0.5: both}))
    assert L["smallest_by_sign"] == {"up": {"caught": 0, "cases": 2}, "down": {"caught": 1, "cases": 2}}
    assert (L["strongest_caught"]["caught"], L["milder_max"]) == (2, 1)
    text = str(macros(lang).power_limit(L, 2024, 8))
    if lang == "fr":
        assert "un écart de +2\u00a0%, à 0 des 2 départs, et de −2\u00a0%, à 1 des 2." in text
        assert "× 0,5 de leur demi-largeur ont donné « recalibrer » à 2 des 2 départs" in text
        assert "des réductions moindres (jusqu'à × 0,9), à 1 des 2 départs au plus." in text
    else:
        assert "a gap of +2% did at 0 of 2 starts, and of −2% at 1 of 2." in text
        assert "× 0.5 of their half-width yielded \"recalibrate\" at 2 of 2 starts" in text
        assert "milder cuts (up to × 0.9) at most at 1 of 2 starts." in text


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
    band, threshold = ("hors bande", "hors seuil") if lang == "fr" else ("outside its band", "above its threshold")
    assert band not in listed and listed.count(threshold) == 2
    flagged = dict(base, weeks=[base["weeks"][0], dict(base["weeks"][1], flags={"mape_high": False, "bias_high": False,
                                                                                "bias_low": True})])
    listed = str(m.near_list([flagged]))
    assert listed.count(band) == 1 and listed.count(threshold) == 1
    assert listed.index(band) > listed.index(";" if lang == "fr" else "that week:")


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


def spring_sides(oos):
    e = errors(oos, "pred_persistence")
    months = pd.to_datetime(e["day"]).dt.month.isin([3, 4, 5, 6])
    return e[months & (e["year"] == 2020)], e[months & (e["year"] != 2020)]


def test_the_difference_uses_the_codes_week_block_bootstrap_on_each_side():
    oos = spring_frame()
    a, b = spring_sides(oos)
    for side, seed in ((a, 0), (b, 1)):
        draws = RV._boot_means(side, "rel_bias", 200, seed)
        ref = mean_with_ci(side, "rel_bias", 200, seed)
        assert np.quantile(draws, [0.025, 0.975]).tolist() == [ref["ci95_low"], ref["ci95_high"]]
    d = RV.shift_difference(oos, "pred_persistence", batch_draws=500)
    assert d["difference"] == pytest.approx(a["rel_bias"].mean() - b["rel_bias"].mean())
    assert d["ci95_low"] < d["difference"] < d["ci95_high"]
    assert d["verdict"] == "confirmed"  # a 2 % shift with 3 % hourly noise over a spring of weeks
    assert RV.shift_difference(spring_frame(shift=0.0), "pred_persistence", batch_draws=500)["verdict"] == \
        "not_confirmed"


def test_the_seeds_are_pinned_and_each_side_of_each_batch_has_its_own(monkeypatch):
    assert RV.SHIFT_DIFF_SEEDS == tuple((2 * k, 2 * k + 1) for k in range(RV.SHIFT_DIFF_BATCHES))
    flat = [s for pair in RV.SHIFT_DIFF_SEEDS for s in pair]
    assert len(set(flat)) == len(flat) == 2 * RV.SHIFT_DIFF_BATCHES
    oos = spring_frame()
    a, b = spring_sides(oos)
    for seeds in (RV.SHIFT_DIFF_SEEDS, ((5, 9), (11, 3), (4, 8))):
        got = RV.shift_difference(oos, "pred_persistence", **({"seeds": seeds} if seeds != RV.SHIFT_DIFF_SEEDS else {}),
                                  batch_draws=300)
        batches = [RV._boot_means(a, "rel_bias", 300, sa) - RV._boot_means(b, "rel_bias", 300, sb) for sa, sb in seeds]
        per = np.array([np.quantile(x, [0.025, 0.975]) for x in batches])
        lo, hi = np.quantile(np.concatenate(batches), [0.025, 0.975])
        se = per.std(axis=0, ddof=1) / np.sqrt(len(seeds))
        assert (got["ci95_low"], got["ci95_high"]) == (lo, hi)
        assert (got["mc_se_low"], got["mc_se_high"]) == tuple(se)
        assert (got["draws"], got["batches"], got["batch_draws"]) == (300 * len(seeds), len(seeds), 300)
    # The constants the report prints are the ones the render uses, and the verdict is judged on them: the call that
    # judges it receives the multiple and the digits the result reports, read from the module at call time (a
    # default bound when the function was defined, or a literal at the call, would print one rule and judge another).
    verdict = RV.shift_verdict
    calls = []

    def recording(*args, **kwargs):
        bound = inspect.signature(verdict).bind(*args, **kwargs)
        bound.apply_defaults()
        calls.append(tuple(bound.arguments.values()))
        return verdict(*args, **kwargs)

    def judged(d):
        return verdict(d["ci95_low"], d["ci95_high"], d["mc_se_low"], d["mc_se_high"], d["mc_multiple"], d["digits"])

    def judged_on_what_it_reports(d):
        assert calls == [(d["ci95_low"], d["ci95_high"], d["mc_se_low"], d["mc_se_high"], d["mc_multiple"],
                          d["digits"])]
        assert {k: d[k] for k in ("verdict", "near")} == judged(d)
        calls.clear()

    monkeypatch.setattr(RV, "shift_verdict", recording)
    full = RV.shift_difference(oos, "pred_persistence")
    assert (full["draws"], full["batches"], full["batch_draws"], full["mc_multiple"], full["digits"]) == (
        RV.SHIFT_DIFF_BATCHES * RV.SHIFT_DIFF_BATCH_DRAWS, RV.SHIFT_DIFF_BATCHES, RV.SHIFT_DIFF_BATCH_DRAWS,
        RV.SHIFT_DIFF_MC_MULTIPLE, RV.SHIFT_DIFF_DIGITS)
    judged_on_what_it_reports(full)
    # Constants the render reads at call time, unlike the defaults, tell a verdict judged on the printed rule from one
    # judged on another. A multiple so wide that every bound lies within it leaves the verdict undetermined; at one
    # decimal of a point the spring's errors print as zero, so the same wide multiple of them confirms the gap, and a
    # verdict judged at the default precision would print a rule it did not apply.
    for multiple, digits, expected in ((10**9, RV.SHIFT_DIFF_DIGITS, "undetermined"),
                                       (RV.SHIFT_DIFF_MC_MULTIPLE, 6, full["verdict"]), (10**9, 1, "confirmed")):
        monkeypatch.setattr(RV, "SHIFT_DIFF_MC_MULTIPLE", multiple)
        monkeypatch.setattr(RV, "SHIFT_DIFF_DIGITS", digits)
        got = RV.shift_difference(oos, "pred_persistence", batch_draws=300)
        assert (got["mc_multiple"], got["digits"], got["verdict"]) == (multiple, digits, expected)
        judged_on_what_it_reports(got)


def test_a_spring_difference_whose_sides_share_a_seed_is_refused():
    oos = spring_frame()
    for seeds in (((0, 0), (1, 2)), ((0, 1), (1, 2)), ((0, 1),)):
        with pytest.raises(ReportRefused, match="seed of its own"):
            RV.shift_difference(oos, "pred_persistence", seeds=seeds, batch_draws=50)


def test_a_bound_within_monte_carlo_error_of_zero_leaves_the_verdict_undetermined():
    V = RV.shift_verdict
    assert V(-0.02, -0.001, 1e-4, 1e-4) == {"verdict": "confirmed", "near": None}
    assert V(0.001, 0.02, 1e-4, 1e-4)["verdict"] == "confirmed"
    assert V(-0.02, 0.001, 1e-4, 1e-4)["verdict"] == "not_confirmed"
    assert V(-0.02, -0.00031, 1e-4, 1e-4)["verdict"] == "confirmed"  # 3.1 standard errors from zero
    for hi in (-0.00029, 0.00029):  # 2.9 standard errors from zero, on either side
        got = V(-0.02, hi, 1e-4, 1e-4)
        assert got["verdict"] == "undetermined"
        assert got["near"] == {"bound": "high", "value": hi, "se": 1e-4, "by": "mc"}
    got = V(0.00025, 0.02, 1e-4, 1e-4)
    assert got["verdict"] == "undetermined" and got["near"]["bound"] == "low"
    got = V(-0.02, -0.00004, 1e-4, 1e-6)  # far from zero in standard errors, but prints as 0.00 pt
    assert got["verdict"] == "undetermined" and got["near"]["by"] == "print"
    got = V(-0.0002, 0.0001, 1e-4, 1e-4)  # both near: the nearer is named
    assert got["near"]["bound"] == "high"
    assert V(-0.02, -0.00025, 1e-4, 1e-4, multiple=2)["verdict"] == "confirmed"


def test_a_validation_frame_without_its_spring_is_refused():
    oos = spring_frame()
    oos = oos[~((oos["day"].dt.year == 2020) & oos["day"].dt.month.isin([3, 4, 5, 6]))]
    with pytest.raises(ReportRefused, match="spring"):
        RV.shift_difference(oos, "pred_persistence", batch_draws=50)


# The fixture's constants are unlike the real ones (3, 400 000 draws in 20 batches of 20 000) and its two standard
# errors print unlike each other, so a sentence that prints a constant, or one bound's error for the other, fails.
SPRING_K, SPRING_DIGITS, SPRING_BATCHES, SPRING_BATCH_DRAWS = 4, 4, 7, 17_571
SPRING_CASES = {  # (verdict, by, bound): ci95_low, ci95_high, mc_se_low, mc_se_high
    ("confirmed", None, None): (-0.0251, -0.0002, 0.0000213, 0.0000347),
    ("not_confirmed", None, None): (-0.0251, 0.0050, 0.0000213, 0.0000347),
    ("undetermined", "mc", "high"): (-0.0251, -0.00002, 0.0000213, 0.0000347),
    ("undetermined", "mc", "low"): (0.00003, 0.0251, 0.0000213, 0.0000347),
    ("undetermined", "print", "high"): (-0.0251, -0.00004, 0.0000213, 0.0000062),
}


def spring_d(verdict, by=None, bound=None):
    lo, hi, se_lo, se_hi = SPRING_CASES[(verdict, by, bound)]
    d = {"difference": -0.0127, "ci95_low": lo, "ci95_high": hi, "mc_se_low": se_lo, "mc_se_high": se_hi,
         "draws": SPRING_BATCHES * SPRING_BATCH_DRAWS, "batches": SPRING_BATCHES, "batch_draws": SPRING_BATCH_DRAWS,
         "mc_multiple": SPRING_K, "digits": SPRING_DIGITS,
         **RV.shift_verdict(lo, hi, se_lo, se_hi, SPRING_K, SPRING_DIGITS)}
    # The fixture is what the gate says of its bounds.
    assert d["verdict"] == verdict and (d["near"] is None if by is None else
                                        (d["near"]["by"], d["near"]["bound"]) == (by, bound))
    return d


NB = " "
SPRING = {"fr": {"confirmed": "selon la règle de la section 1, l'écart est confirmé",
                 "not_confirmed": "selon la règle de la section 1, l'écart n'est pas confirmé",
                 "undetermined": "indéterminé à cette résolution",
                 "mc": "n'est pas à plus de 4 erreurs types de Monte-Carlo",
                 "print": "s'arrondit à zéro", "low": "la borne inférieure", "high": "la borne supérieure",
                 "draws": f"122{NB}997 tirages par côté, en 7 lots indépendants de 17{NB}571",
                 "se": "{} pt et {} pt", "far": "chacune à plus de 4 de ses erreurs types de zéro",
                 "bounds": "bornes à cette précision : {} pt et {} pt,",
                 "near": "{}, {} pt, n'est pas à plus de 4 erreurs types de Monte-Carlo ({} pt) de zéro",
                 "card": {"confirmed": "confirme l'écart", "not_confirmed": "ne confirme pas l'écart",
                          "mc": "ne tranche pas à cette résolution : une borne n'est pas à plus de 4 erreurs types",
                          "print": "ne tranche pas à cette résolution : une borne s'arrondit à zéro"},
                 "disjoint": ("écart confirmé par des intervalles disjoints",
                              "écart non confirmé par des intervalles disjoints")},
          "en": {"confirmed": "under section 1's rule, the gap is confirmed",
                 "not_confirmed": "under section 1's rule, the gap is not confirmed",
                 "undetermined": "undetermined at this resolution",
                 "mc": "lies no more than 4 Monte Carlo standard errors",
                 "print": "rounds to zero", "low": "the lower bound", "high": "the upper bound",
                 "draws": "122,997 draws per side, in 7 independent batches of 17,571",
                 "se": "{} pt and {} pt", "far": "each more than 4 of its standard errors from zero",
                 "bounds": "bounds at this precision: {} pt and {} pt,",
                 "near": "{}, {} pt, lies no more than 4 Monte Carlo standard errors ({} pt) from zero",
                 "card": {"confirmed": "confirms the gap", "not_confirmed": "does not confirm the gap",
                          "mc": "does not decide at this resolution: a bound lies no more than 4 Monte Carlo",
                          "print": "does not decide at this resolution: a bound rounds to zero"},
                 "disjoint": ("a gap confirmed by disjoint intervals", "a gap not confirmed by disjoint intervals")}}
# How each case's numbers print, four decimals of a point: the two standard errors, then the bounds.
SPRING_PRINTED = {
    ("confirmed", None, None): (("0,0021", "0,0035"), ("−2,5100", "−0,0200")),
    ("not_confirmed", None, None): (("0,0021", "0,0035"), ("−2,5100", "+0,5000")),
    ("undetermined", "mc", "high"): (("0,0021", "0,0035"), ("−2,5100", "−0,0020")),
    ("undetermined", "mc", "low"): (("0,0021", "0,0035"), ("+0,0030", "+2,5100")),
    ("undetermined", "print", "high"): (("0,0021", "0,0006"), ("−2,5100", "−0,0040")),
}


@pytest.mark.parametrize("lang", LANGS)
def test_every_spring_verdict_branch_renders_its_own_sentence(lang):
    m, W = macros(lang), SPRING[lang]

    def tr(s):
        return s if lang == "fr" else s.replace(",", ".")

    for (verdict, by, bound), separated in itertools.product(SPRING_CASES, (True, False)):
        d = spring_d(verdict, by, bound)
        (se_lo, se_hi), (lo, hi) = (tuple(tr(s) for s in pair) for pair in SPRING_PRINTED[(verdict, by, bound)])
        text = str(m.diff_verdict(d))
        assert "None" not in text and "\n" not in text
        assert W["draws"] in text and W["se"].format(se_lo, se_hi) in text  # each error in its own place
        assert W[verdict] in text
        assert not any(W[o] in text for o in {"confirmed", "not_confirmed", "undetermined"} - {verdict})
        if verdict == "undetermined":
            assert W[by] in text and W[{"mc": "print", "print": "mc"}[by]] not in text and W[bound] in text
            assert W["far"] not in text
            if by == "mc":  # the bound judged, with its own standard error
                assert W["near"].format(W[bound], lo if bound == "low" else hi,
                                        se_lo if bound == "low" else se_hi) in text
            else:  # the bound is not printed beside the rounding claim: the interval shows it at two decimals
                assert (lo if bound == "low" else hi) not in text
        else:
            assert W["bounds"].format(lo, hi) + " " + W["far"] in text
        card = str(m.spring_card({"separated": separated}, d))
        assert "None" not in card and card.startswith(W["disjoint"][0 if separated else 1])
        branch = verdict if verdict != "undetermined" else by
        assert W["card"][branch] in card
        assert not any(phrase in card for key, phrase in W["card"].items() if key != branch)


NUMBER_PT = re.compile(r"([+−]?[0-9][0-9,. ]*) pt")


def numbers_pt(text, lang):
    """Every number the sentence prints in points, as the reader reads it: exact decimals."""
    out = []
    for s in NUMBER_PT.findall(text):
        s = s.replace("−", "-")
        s = s.replace(" ", "").replace(",", ".") if lang == "fr" else s.replace(",", "")
        out.append(Decimal(s))
    return out


@pytest.mark.parametrize("lang", LANGS)
def test_no_spring_verdict_contradicts_its_own_printed_numbers(lang):
    """The rule is judged on the bound and its standard error as the verdict prints them. A bound printed at most k
    printed standard errors from zero is undetermined; one printed farther out is judged, never undetermined; so a
    reader who redoes the comparison on the printed numbers always agrees with the sentence. Swept across the gate,
    at several errors and both multiples, on both sides of zero."""
    m = macros(lang)
    seen = set()
    for k, se_pt in itertools.product((3, 4), (0.00334, 0.0021, 0.00151, 0.0049, 0.00049)):
        se = se_pt / 100
        edge = k * se_pt
        for step, which, sign in itertools.product(range(-80, 81), ("low", "high"), (1, -1)):
            x = sign * (edge + step * 0.00001) / 100
            lo, hi = (x, 0.025) if which == "low" else (-0.025, x)
            se_lo, se_hi = (se, 1e-6) if which == "low" else (1e-6, se)
            v = RV.shift_verdict(lo, hi, se_lo, se_hi, k)
            d = {"draws": 1, "batches": 2, "batch_draws": 1, "mc_multiple": k, "digits": RV.SHIFT_DIFF_DIGITS,
                 "ci95_low": lo, "ci95_high": hi, "mc_se_low": se_lo, "mc_se_high": se_hi, **v}
            nums = numbers_pt(str(m.diff_verdict(d)), lang)
            if v["verdict"] == "undetermined" and v["near"]["by"] == "mc":
                value, near_se = nums[0], nums[1]
                assert abs(value) <= k * near_se, (lang, k, se_pt, x)
                assert near_se == nums[2 if v["near"]["bound"] == "low" else 3]
            elif v["verdict"] == "undetermined":
                assert str(m.pp(v["near"]["value"], 2)).startswith("0")  # the interval prints it as 0.00
            else:
                s_lo, s_hi, p_lo, p_hi = nums
                assert abs(p_lo) > k * s_lo and abs(p_hi) > k * s_hi, (lang, k, se_pt, x)
                assert (p_lo > 0 or p_hi < 0) == (v["verdict"] == "confirmed")
            seen.add(v["verdict"] if v["verdict"] != "undetermined" else v["near"]["by"])
    assert seen == {"confirmed", "not_confirmed", "mc", "print"}  # the sweep crosses the gate both ways

    # The reviewer's case: a bound of -0.0096 pt with an error of 0.00334 pt, undetermined by the draws, used to
    # print as -0.010 pt within 3 errors of 0.003 pt (3 x 0.003 = 0.009 < 0.010).
    V = RV.shift_verdict(-0.025, -0.000096, 1e-6, 0.0000334, 3)
    assert V["verdict"] == "undetermined" and V["near"]["by"] == "mc"
    d = {"draws": 1, "batches": 2, "batch_draws": 1, "mc_multiple": 3, "digits": RV.SHIFT_DIFF_DIGITS,
         "ci95_low": -0.025, "ci95_high": -0.000096, "mc_se_low": 1e-6, "mc_se_high": 0.0000334, **V}
    assert ("la borne supérieure, −0,0096 pt, n'est pas à plus de 3 erreurs types de Monte-Carlo (0,0033 pt) de zéro"
            if lang == "fr" else
            "the upper bound, −0.0096 pt, lies no more than 3 Monte Carlo standard errors (0.0033 pt) from zero"
            ) in str(m.diff_verdict(d))
    # And the symmetric case: -0.0100 pt lies within 3 x 0.00334 pt of zero, but prints more than 3 x 0.0033 pt
    # from it, so it is judged, and the sentence says so with the numbers it prints.
    V = RV.shift_verdict(-0.025, -0.0001, 1e-6, 0.0000334, 3)
    assert V["verdict"] == "confirmed"
    d = {**d, "ci95_high": -0.0001, **V}
    assert ("bornes à cette précision : −2,5000 pt et −0,0100 pt, chacune à plus de 3"
            if lang == "fr" else "bounds at this precision: −2.5000 pt and −0.0100 pt, each more than 3"
            ) in str(m.diff_verdict(d))
    # Exactly at the gate as printed (0.0099 = 3 x 0.0033): not more than 3 errors out, so undetermined.
    assert RV.shift_verdict(-0.025, -0.000099, 1e-6, 0.0000334, 3)["verdict"] == "undetermined"


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


def test_the_synthetic_world_takes_its_drift_alarm_from_the_registry(monkeypatch):
    from replay_world import thresholds

    assert thresholds()["drift"] == {"temperature_high": SYNTHETIC_WORLD["drift_alarm"],
                                     "forecast_high": SYNTHETIC_WORLD["drift_alarm"]}
    monkeypatch.setitem(SYNTHETIC_WORLD, "drift_alarm", SYNTHETIC_WORLD["drift_alarm"] + 1.5)
    assert thresholds()["drift"] == {"temperature_high": SYNTHETIC_WORLD["drift_alarm"],
                                     "forecast_high": SYNTHETIC_WORLD["drift_alarm"]}


@pytest.mark.parametrize("lang", LANGS)
def test_the_breaks_table_has_a_word_for_a_control_that_stayed_quiet(lang):
    word = macros(lang).break_result
    quiet, fired, failed = ("resté silencieux", "déclenché", "ÉCHEC") if lang == "fr" else ("stayed quiet", "fired",
                                                                                           "FAILED")
    assert str(word({"outcome": "passed", "control": True})) == quiet
    assert str(word({"outcome": "passed", "control": False})) == fired
    for control in (True, False):
        for outcome in ("failed", "not_run", "skipped"):
            assert str(word({"outcome": outcome, "control": control})) == failed


@pytest.mark.parametrize("lang", LANGS)
def test_the_level_shift_note_sets_the_synthetic_break_against_the_real_measure(lang):
    m = macros(lang)
    world = {"level_shift": 0.12, "drift_alarm": 1.0}
    for missed in (0, 1, 4):
        level = {"size": 0.10, "up_n": 4, "up_missed": missed, "down_n": 4, "down_caught": 3, "months": [1, 4, 7, 10]}
        text = str(m.level_shift_note("Un changement de niveau", world, level, 2023, 10))
        assert "None" not in text and "\n" not in text
        assert ("1,12, une hausse de 12\u00a0%" if lang == "fr" else "1.12, a 12% rise") in text
        if missed == 0:
            assert ("à chaque départ (section 8)" if lang == "fr" else "at every start (section 8)") in text
            assert ("n'a pas donné" if lang == "fr" else "did not yield") not in text
        else:
            assert (f"à {missed} des 4 départs" if lang == "fr" else f"at {missed} of 4 starts") in text
            if lang == "fr":
                assert ("à aucun départ" in text) == (missed == 4) and ("à chaque départ." in text) == (missed < 4)
            else:
                assert text.endswith("at any start.") == (missed == 4) and text.endswith("at every start.") == (missed < 4)


# --- In brief -------------------------------------------------------------------------------------------------------

def test_both_reports_name_the_summary_block_alike():
    for report in ("validation", "dq"):
        for lang, heading in (("fr", "## En bref\n"), ("en", "## In brief\n")):
            source = environment(lang).loader.get_source(environment(lang), f"{report}.{lang}.md.j2")[0]
            assert source.count(heading) == 1 and "## In short" not in source


@pytest.mark.parametrize("lang", LANGS)
def test_every_in_brief_branch_renders_a_true_sentence(lang):
    m, fr = macros(lang), lang == "fr"

    def ok(text):
        assert "None" not in text and "\n" not in text and text.endswith(".")
        return text

    P = {"persistence": {"overall": {"mean": 0.0312}}, "baseline": {"overall": {"mean": 0.0531}}}
    for separated, diff, phrase in ((True, -0.02, "elle fait mieux" if fr else "it does better"),
                                    (True, 0.02, "elle fait moins bien" if fr else "it does worse"),
                                    (False, -0.02, "l'écart n'est pas confirmé" if fr else "the gap is not confirmed")):
        text = ok(str(m.brief_error(P, {"separated": separated, "difference": diff}, (2020, 2023))))
        assert phrase in text and "2020–2023" in text and ("3,12\u00a0%" in text if fr else "3.12%" in text)

    none = {"under": [], "over": []}
    for overall, phrase in (({"under": ["all"], "over": []}, "moins que le nominal" if fr else "less than nominal"),
                            ({"under": [], "over": ["all"]}, "plus que le nominal" if fr else "more than nominal"),
                            (none, "contient le nominal" if fr else "contains the nominal")):
        for season, decile in ((none, none), ({"under": ["winter"], "over": []}, {"under": ["9", "10"], "over": []})):
            text = ok(str(m.brief_coverage(0.9, 0.884, "2021-01", {"overall": overall, "season": season,
                                                                   "decile": decile})))
            assert phrase in text and ("88,4\u00a0%" in text if fr else "88.4%" in text)
            if season["under"]:
                assert ("l'hiver ; les déciles de charge prévue 9 et 10 (section 4)" if fr else
                        "winter; forecast-load deciles 9 and 10 (section 4)") in text
            else:
                assert ("Aucune saison ni aucun décile" if fr else "No season and no load decile") in text

    def peak(evaluated, covered):
        return {"hours": [{}] * 5, "evaluated": evaluated, "covered": covered, "named_month": "2023-02"}

    for p, in_month, above, phrase in (
            (peak(0, 0), True, 0, "aucun intervalle de prévision" if fr else "no prediction interval"),
            (peak(5, 5), False, 0, "chaque fois (5 heures jugées)" if fr else "every time (5 hours judged)"),
            (peak(5, 0), True, 5, "5 fois sur 5, toujours par le haut" if fr else "5 times out of 5, always above it"),
            (peak(5, 2), True, 1, "3 fois sur 5." if fr else "3 times out of 5.")):
        text = ok(str(m.brief_peak(p, (2019, 2023), in_month, above)))
        assert phrase in text and ("toutes en 2023-02" in text if fr else "all in 2023-02" in text) == in_month
        if p["covered"] == 2:
            assert ("toujours par le haut" if fr else "always above") not in text

    r = {"year": 2024, "summary": {"mape": {"mean": 0.035}, "coverage": {"mean": 0.91}}}
    for n, phrase in ((0, "n'a déclenché aucune recommandation" if fr else "triggered no recommendation"),
                      (1, "a déclenché 1 recommandation" if fr else "triggered 1 recommendation"),
                      (3, "a déclenché 3 recommandations" if fr else "triggered 3 recommendations")):
        for departures in ([], [{"id": "x"}]):
            text = ok(str(m.brief_replay(r, n, departures)))
            assert phrase in text and ("3,50\u00a0%" in text if fr else "3.50%" in text)
            assert (("a posteriori" if fr else "post hoc") in text) == bool(departures)
    assert [str(m.recs(n)) for n in (0, 1, 3)] == (["aucune recommandation", "1 recommandation", "3 recommandations"]
                                                   if fr else ["no recommendation", "1 recommendation",
                                                               "3 recommendations"])

    L = {"smallest": 0.02, "smallest_by_sign": {"up": {"caught": 0, "cases": 4}, "down": {"caught": 1, "cases": 4}}}
    level = {"size": 0.10, "up_n": 4, "up_missed": 3}
    text = ok(str(m.brief_misses(level, L, 2023, 2024, 10)))
    assert ("a donné « réentraîner » en 10 semaines à 1 des 4 départs" if fr else
            "yielded \"retrain\" within 10 weeks at 1 of 4 starts") in text
    assert ("±2\u00a0% entre la demande et la prévision, à 1 des 8 cas" if fr else
            "±2% between demand and forecast did at 1 of 8 cases") in text
