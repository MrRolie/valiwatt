"""The report templates type no design constant in numerals.

Every number the templates of the validation and data-quality reports write is measured in a run, or is a design
constant read from the runs or from the code (`validation.constants`, `render.design`). Three parts of each template are
scanned: its own text, the quoted strings inside its Jinja (which can be printed), and the numbers its printed
expressions write. The only numerals left in them are those ALLOWED names below, each with the reason it is not a
constant, and, inside the Jinja, the few NAMES and EXPRESSION forms that follow. A numeral typed anywhere else fails
this test, so the reports' opening claim stays as wide as what is checked. Not scanned: the numbers the templates'
logic compares against inside `{% %}` (a rule's number, the one of a plural), the arguments a filter or macro takes
(its decimals), and the break labels section 11 prints, which are hand-written in `valiwatt.breaks`. The story has its
own, stricter test (tests/test_report_story.py). Numbers written out in words are not checked here, and the claim the
reports make is about numerals only.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd
import pytest

from valiwatt.forecast import evaluate
from valiwatt.report.render import TEMPLATES, ReportRefused, bootstrap_quantiles, design

CHECKED = ("validation", "sentences", "season_coverage", "ledger_recorded", "repair", "dq")
REF = r"\d+(?:\.\d+)*(?: \([^)\d]*\))?"  # a section, and what it holds in words
LIST = rf"{REF}(?:(?:, | and | et | or | ou ){REF})*"
# What a numeral may be in these templates, and why it is not a design constant.
ALLOWED = {
    # Names.
    "the version names v1 and v2 (a statement set aside can leave one glued to a word)": r"v[12]\b",
    "the name of the standard SR 11-7": r"\bSR 11-7\b",
    "a NIST AI RMF subcategory, by its number": rf"\b(?:MAP|MEASURE|MANAGE|GOVERN) {LIST}",
    "the SHA-256 hash function": r"\bSHA-256\b",
    "a figure's file name": r"\(figures/[\w/.-]+\.png\)",
    # References.
    "a numbered heading": r"(?m)^#{2,3} \d+(?:\.\d+)?\.?(?= )",
    "a reference to a section of the report or of the plan": rf"(?i)\bsections? {LIST}",
    "a rule of the decision table, by its number": r"(?i)\br(?:ule|ègle)s? \d\b",
    "the decision table's items, numbered after a colon or a semicolon": r"[:;] \(\d\)(?= )",
    "the rank of the freshest forecast, named 0": r"\(0 ?: ",
    # Dates and the calendar.
    "the dates of the owner's rulings": r"\b2026-10-1[57]\b",
    "the 24 hours of a day": r"\b24 h(?:ours|eures)\b",
    "the day before the forecast day, D−1": r"\b[DJ]−1\b",
    "the hour daylight saving repeats, 1 a.m.": r"(?:\b1 a\.m\.|minuit à 1 h|locale 1 h)",
    # Formulas.
    "the interval's form, forecast × (1 ± q)": r"\(1 ±",
    "the conformal rank, ⌈(n + 1) × level⌉": r"n \+ 1",
    "the unit bound of a sum of lag coefficients (a shock fades only below it)":
        r"\b(?:below|less than|sous) 1\b|\bto 1 or more\b",
}


STRING = r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\""
# A quoted string inside the Jinja that is never printed: a key, a run's name, a template's file name, a departure's
# key, a format; or the printed tail of a list of sections, chosen in Jinja (", 8 and 9").
NAMES = rf"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*|[AB]\d|%\.\de|(?:, | and | et ){LIST}"
# In a printed expression, once its strings, the arguments of its calls and its subscripts are set aside: a year or a
# count plus or minus one, one plus a rate (a factor), a count compared with zero or one to choose a plural, the
# hundred that turns a share into a percentage, and the 24 hours of a day.
EXPRESSION = r"[+-] 1\b|\b1 [+-]|[=!<>]=? ?[01]\b|\b100 \*|\b24 \*|/ 24\b"


def _source(path) -> str:
    return re.sub(r"\{#.*?#\}", "", path.read_text(encoding="utf-8"), flags=re.S)


def literal_text(path) -> str:
    """A template's text with its Jinja comments, statements and expressions set aside."""
    text = re.sub(r"\{\{.*?\}\}", "·", _source(path), flags=re.S)
    return re.sub(r"\{%.*?%\}", "", text, flags=re.S)


def jinja_strings(path) -> list[str]:
    """The quoted strings inside a template's Jinja, those that NAMES describes set aside."""
    blocks = re.findall(r"\{\{.*?\}\}|\{%.*?%\}", _source(path), flags=re.S)
    return [s[1:-1] for b in blocks for s in re.findall(STRING, b) if not re.fullmatch(NAMES, s[1:-1])]


def printed_numbers(expression: str) -> list[str]:
    """The numerals a printed expression `{{ ... }}` writes outside its strings, call arguments and subscripts, and
    outside the EXPRESSION forms."""
    e = re.sub(STRING, "''", expression)
    while True:  # innermost first: a call's arguments and a subscript go, a grouping keeps what it holds
        shorter = re.sub(r"(?<![\w\])\]])\(([^()\[\]]*)\)", r" \1 ",
                         re.sub(r"(?<=[\w\])\]])[(\[][^()\[\]]*[)\]]", "", e))
        if shorter == e:
            break
        e = shorter
    e = re.sub(EXPRESSION, "¤", re.sub(r"\b[A-Za-z_]\w*", "x", e))
    return [e for _ in re.findall(r"\d", e)][:1]


def expressions(path) -> list[str]:
    return re.findall(r"\{\{.*?\}\}", _source(path), flags=re.S)


def typed_numerals(text: str) -> list[str]:
    """Each numeral `text` types outside ALLOWED, with its context."""
    for pattern in ALLOWED.values():
        text = re.sub(pattern, "¤", text)
    return [text[max(0, m.start() - 30):m.end() + 20] for m in re.finditer(r"\d", text)]


@pytest.mark.parametrize("doc", CHECKED)
@pytest.mark.parametrize("lang", ("fr", "en"))
def test_the_templates_type_no_design_constant(doc, lang):
    path = TEMPLATES / f"{doc}.{lang}.md.j2"
    assert typed_numerals(literal_text(path)) == []
    assert [s for s in jinja_strings(path) if typed_numerals(s)] == []
    assert [e for e in expressions(path) if printed_numbers(e)] == []


def test_a_constant_typed_inside_the_jinja_is_caught(tmp_path):
    for typed in ("{{ 95 }}", "{{ (95) }}", "{{ (1000)|num(0) }}", "{{ ((a - b) / 18)|num }}", "{{ x * 100 }}",
                  "{{ 'shown on 2024' }}", "{% set y = 'the 2025 test' %}", "{{ 'v2 at 18 °C' if a else b }}",
                  "{{ x ~ ' in 2024' }}"):
        path = tmp_path / "t.md.j2"
        path.write_text(f"A sentence {typed}.", encoding="utf-8")
        assert [s for s in jinja_strings(path) if typed_numerals(s)] or \
            [e for e in expressions(path) if printed_numbers(e)], typed
    for fine in ("{{ x|pct(2) }}", "{{ vy[1] + 1 }}", "{{ 's' if n != 1 }}", "{{ 'lag24' }}", "{{ ', 8 and 9' }}",
                 "{% from 'sentences.en.md.j2' import x %}", "{{ year - 1 }}"):
        path = tmp_path / "t.md.j2"
        path.write_text(f"A sentence {fine}.", encoding="utf-8")
        assert not [s for s in jinja_strings(path) if typed_numerals(s)], fine
        assert not [e for e in expressions(path) if printed_numbers(e)], fine


def test_a_typed_constant_is_caught():
    for typed in ("with a 95% interval", "relative to 18 °C", "shown on 2024", "UTC−5", "threshold 98 %",
                  "the 2023-12-31 point", "06:00 UTC"):
        assert typed_numerals(f"a sentence {typed}.") != [], typed
    assert typed_numerals("sections 4 (the peak hours), 5 and 7; plan section 11.3.5; SR 11-7; v1 and v2") == []
    # A parenthesis after a section holds words only, and a number in brackets is a list's only after a colon.
    assert typed_numerals("see section 4 (a 95% interval, 18 °C).") != []
    assert typed_numerals("the (2) and (9) items.") != []
    assert typed_numerals("one recommendation: (1) a failed day; (2) the same failure.") == []


def test_the_interval_level_is_the_one_the_code_computes():
    """design()["ci_level"] is read from evaluate.mean_with_ci's source; on a fixed sample, the interval that function
    returns is exactly the quantiles of its own bootstrap draws at that level."""
    low, high = design()["ci_quantiles"]
    assert design()["ci_level"] == pytest.approx(high - low)
    rng = np.random.default_rng(3)
    d = pd.DataFrame({"week": np.repeat(np.arange(30), 24), "x": rng.normal(size=720)})
    got = evaluate.mean_with_ci(d, "x", n_boot=400, seed=5)
    g = d.groupby("week")["x"].agg(["sum", "count"])
    W = evaluate._weights(len(g), 400, 5)
    boot = (W @ g["sum"].to_numpy()) / (W @ g["count"].to_numpy())
    assert (got["ci95_low"], got["ci95_high"]) == tuple(float(x) for x in np.quantile(boot, [low, high]))


def test_an_interval_read_from_code_must_be_one_symmetric_quantile_call():
    def lopsided(boot):
        return np.quantile(boot, [0.05, 0.975])

    def none(boot):
        return boot.mean()

    with pytest.raises(ReportRefused, match="symmetric"):
        bootstrap_quantiles(lopsided)
    with pytest.raises(ReportRefused, match="np.quantile"):
        bootstrap_quantiles(none)


def test_the_design_constants_are_the_codes():
    from valiwatt.config import LST_OFFSET
    from valiwatt.forecast import freeze
    from valiwatt.report import soundness

    d = design()
    assert d["ref_c"] == freeze.BASE_C
    assert d["utc_offset_h"] * pd.Timedelta(hours=1) == LST_OFFSET
    assert d["carry_rise_mw"] == soundness.CARRY_RISE_MW
    lag = soundness.lag_reading({2024: {"lag24": 0.25, "lag168": 0.5}}, [])["freezes"]["2024"]
    assert lag["carry_per_1000_mw"] == pytest.approx(0.25 * d["carry_rise_mw"])
