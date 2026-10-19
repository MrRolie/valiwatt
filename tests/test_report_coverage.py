"""Coverage by season in the validation report: both forms carry their week-block 95 % interval into the tables and
the figure, and the sentence that compares them says only what the intervals show, on any data."""

import itertools

import pytest

from valiwatt.report import figures as F
from valiwatt.report import validation as RV
from valiwatt.report.render import ReportRefused, environment

KEYS = ("winter", "spring", "summer", "fall")
NAMES = {"fr": {"winter": "hiver", "spring": "printemps", "summer": "été", "fall": "automne"},
         "en": {k: k for k in KEYS}}


def coverage(pooled: dict, by_season: dict) -> dict:
    def rows(d):
        return [{"group": "season", "key": k, "covered": c, "covered_low": lo, "covered_high": hi, "hours": 1}
                for k, (c, lo, hi) in d.items()]
    return {"relative": {"season": rows(pooled)}, "relative_by_season": {"season": rows(by_season)}}


def test_each_season_carries_both_intervals_and_what_they_show():
    cov = coverage(
        pooled={"winter": (0.83, 0.80, 0.86), "spring": (0.91, 0.89, 0.93), "summer": (0.97, 0.955, 0.985),
                "fall": (0.947, 0.922, 0.971)},
        by_season={"winter": (0.90, 0.87, 0.93), "spring": (0.88, 0.85, 0.899), "summer": (0.90, 0.87, 0.93),
                   "fall": (0.911, 0.879, 0.943)})
    rows = {r["key"]: r for r in RV.season_coverage(cov, 0.9)}
    assert [r for r in rows] == list(KEYS)
    assert rows["winter"]["pooled"] == {"covered": 0.83, "low": 0.80, "high": 0.86}
    assert rows["winter"]["by_season"] == {"covered": 0.90, "low": 0.87, "high": 0.93}
    assert {k: r["contains_nominal"] for k, r in rows.items()} == {
        "winter": True, "spring": False, "summer": True, "fall": True}
    # fall: pooled [92.2, 97.1] and per-season [87.9, 94.3] overlap; winter and summer do not.
    assert {k: r["disjoint"] for k, r in rows.items()} == {
        "winter": True, "spring": False, "summer": True, "fall": False}


def test_touching_intervals_overlap_and_a_bound_at_the_nominal_contains_it():
    cov = coverage(pooled={k: (0.85, 0.80, 0.87) for k in KEYS}, by_season={k: (0.88, 0.87, 0.90) for k in KEYS})
    rows = RV.season_coverage(cov, 0.9)
    assert all(r["contains_nominal"] and not r["disjoint"] for r in rows)


def test_a_season_missing_from_the_run_is_refused():
    cov = coverage(pooled={k: (0.9, 0.88, 0.92) for k in KEYS},
                   by_season={k: (0.9, 0.88, 0.92) for k in KEYS if k != "summer"})
    with pytest.raises(ReportRefused, match="summer"):
        RV.season_coverage(cov, 0.9)


def named(text: str, lang: str) -> set:
    return {k for k, name in NAMES[lang].items() if name in text}


SPLIT = {"fr": {"nominal": ", pas ", "overlap": " ; ils se chevauchent "},
         "en": {"nominal": ", not ", "overlap": "; they overlap "}}
WHOLE = {
    "fr": {"all_in": "dans chaque saison.", "none_in": "ne contient le nominal de 90 % dans aucune saison.",
           "all_apart": "Dans chaque saison, les IC des deux formes ne se chevauchent pas.",
           "none_apart": "Les IC des deux formes se chevauchent dans chaque saison."},
    "en": {"all_in": "nominal in every season.", "none_in": "nominal in no season.",
           "all_apart": "In every season, the CIs of the two forms do not overlap.",
           "none_apart": "The CIs of the two forms overlap in every season."},
}


@pytest.mark.parametrize("lang", ["fr", "en"])
def test_the_season_sentence_puts_each_season_on_the_side_its_intervals_show(lang):
    sentence = environment(lang).get_template(f"season_coverage.{lang}.md.j2").module.season_sentence
    subsets = [set(c) for n in range(5) for c in itertools.combinations(KEYS, n)]
    for inside, apart in itertools.product(subsets, subsets):
        rows = [{"key": k, "contains_nominal": k in inside, "disjoint": k in apart} for k in KEYS]
        text = str(sentence(rows, 0.9))
        assert "\n" not in text and "fix" not in text and "corrige" not in text
        first, second, third = text.split(". ")
        if inside == set(KEYS):
            assert first.endswith(WHOLE[lang]["all_in"][:-1]) and not named(first, lang)
        elif not inside:
            assert first.endswith(WHOLE[lang]["none_in"][:-1]) and not named(first, lang)
        else:
            yes, no = first.split(SPLIT[lang]["nominal"])
            assert (named(yes, lang), named(no, lang)) == (inside, set(KEYS) - inside)
        if apart == set(KEYS):
            assert second == WHOLE[lang]["all_apart"][:-1]
        elif not apart:
            assert second == WHOLE[lang]["none_apart"][:-1]
        else:
            yes, no = second.split(SPLIT[lang]["overlap"])
            assert (named(yes, lang), named(no, lang)) == (apart, set(KEYS) - apart)
        assert third.startswith("Aucun intervalle" if lang == "fr" else "No interval")


@pytest.mark.parametrize("lang", ["fr", "en"])
def test_the_validation_templates_parse_with_the_season_macro(lang):
    env = environment(lang)
    env.get_template(f"validation.{lang}.md.j2")
    assert "season_sentence" in env.get_template(f"season_coverage.{lang}.md.j2").module.__dict__


def test_the_figure_receives_each_season_with_its_interval(monkeypatch):
    seen = {}
    for name in ("monthly_mape", "peak_hours", "partial_effects", "replay_weekly", "replay_drift"):
        monkeypatch.setattr(F, name, lambda *a, **k: None)
    monkeypatch.setattr(F, "coverage_by_season", lambda rows, lang, path, nominal: seen.update(rows, nominal=nominal))
    seasons = RV.season_coverage(coverage(pooled={k: (0.85, 0.82, 0.88) for k in KEYS},
                                          by_season={k: (0.90, 0.87, 0.93) for k in KEYS}), 0.9)
    ctx = {"v": {"cases": {"peak": {"hours": []}}, "config": {"hour_blocks": {}}}, "p": {"weeks": [], "decisions": []},
           "r": {"weeks": [], "decisions": [], "year": 2024, "contrasts": {"label": "seen_by_developers"}},
           "monthly": None, "curves": None, "seasons": seasons, "nominal": 0.9}
    RV.figures(ctx, "en")
    assert seen == {"pooled": {k: (0.85, 0.82, 0.88) for k in KEYS},
                    "by_season": {k: (0.90, 0.87, 0.93) for k in KEYS}, "nominal": 0.9}


def test_the_figure_draws_each_interval_as_an_error_bar_with_the_label_above_it(monkeypatch, tmp_path):
    from matplotlib.collections import LineCollection

    figs = []
    monkeypatch.setattr(F, "_save", lambda fig, path: figs.append(fig))
    rows = {"pooled": {k: (0.80 + 0.02 * i, 0.77 + 0.02 * i, 0.84 + 0.02 * i) for i, k in enumerate(KEYS)},
            "by_season": {k: (0.90, 0.86 + 0.01 * i, 0.99) for i, k in enumerate(KEYS)}}
    F.coverage_by_season(rows, "fr", tmp_path / "x.png", 0.9)
    ax = figs[0].axes[0]
    spans = sorted((round(min(y for _, y in s), 6), round(max(y for _, y in s), 6))
                   for c in ax.collections if isinstance(c, LineCollection) for s in c.get_segments())
    expected = sorted((round(100 * lo, 6), round(100 * hi, 6)) for form in rows.values() for _, lo, hi in form.values())
    assert spans == expected
    tops = sorted(round(100 * hi + 0.6, 6) for form in rows.values() for _, _, hi in form.values())
    assert sorted(round(t.get_position()[1], 6) for t in ax.texts) == tops
    assert ax.get_ylim()[1] >= 99 + 3


def test_a_bound_is_judged_as_the_table_prints_it():
    """A per-season low of 90.004 % prints as 90.0 %: the sentence must count the nominal as inside it, and a pooled
    high that prints equal to the per-season low counts as overlapping."""
    cov = coverage(
        pooled={k: (0.85, 0.82, 0.90004) for k in KEYS},
        by_season={k: (0.92, 0.90004, 0.94) for k in KEYS})
    rows = RV.season_coverage(cov, 0.9)
    assert all(r["contains_nominal"] for r in rows)
    assert not any(r["disjoint"] for r in rows)


@pytest.mark.parametrize("lang", ["fr", "en"])
def test_the_validation_report_prints_both_intervals_and_the_sentence(lang):
    """The validation template itself uses the season rows: both forms' intervals in the season table, the per-season
    interval in the decile table, and the sentence rendered from the intervals instead of a typed claim."""
    env = environment(lang)
    source = env.loader.get_source(env, f"validation.{lang}.md.j2")[0]
    assert "season_sentence(seasons" in source
    assert "ci(row.pooled.low, row.pooled.high, 1)" in source
    assert "ci(row.by_season.low, row.by_season.high, 1)" in source
    assert "ci(ps.covered_low, ps.covered_high, 1)" in source
    assert "fixes the gap" not in source and "corrige l'écart" not in source


@pytest.mark.parametrize("nominal, fr, en", [(0.9, "nominale 90 %", "nominal 90%"), (0.8, "nominale 80 %", "nominal 80%")])
def test_the_figure_draws_the_nominal_line_where_the_runs_put_it(monkeypatch, tmp_path, nominal, fr, en):
    figs = []
    monkeypatch.setattr(F, "_save", lambda fig, path: figs.append(fig))
    rows = {form: {k: (0.85, 0.80, 0.90) for k in KEYS} for form in ("pooled", "by_season")}
    for lang, label in (("fr", fr), ("en", en)):
        F.coverage_by_season(rows, lang, tmp_path / "x.png", nominal)
        ax = figs[-1].axes[0]
        dashed = [line for line in ax.get_lines() if line.get_linestyle() == "--"]
        assert [line.get_ydata()[0] for line in dashed] == [pytest.approx(100 * nominal)]
        assert dashed[0].get_label() == label
