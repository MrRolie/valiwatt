"""The data-quality report's repair paragraph, rendered from a repair's evidence as the run stores it (JSON), in both
of its branches: the only possible destination, and a destination chosen over another."""

import json

import pandas as pd
import pytest

from conftest import synth_demand
from test_demand_gates import H, add_record, drop_record, run, set_mw
from valiwatt.quality.demand import BOUNDS_MW, MISPLACED_MIN_RATIO
from valiwatt.report.render import environment, fmt_num

FIRST, LAST = "2021-12-28T00:00Z", "2022-01-10T00:00Z"
S1, S2, T = (pd.Timestamp(x) for x in ("2022-01-02T10:00Z", "2022-01-04T10:00Z", "2022-01-07T10:00Z"))
ONLY = {"fr": "est la seule heure sans enregistrement", "en": "is the only hour without a record"}
OTHER = {"fr": "la meilleure après", "en": "the best after"}


def stored_evidence(two_destinations: bool) -> dict:
    raw = set_mw(synth_demand(FIRST, LAST), [S1 - H, S1 + H], 10_000.0)
    raw = set_mw(set_mw(raw, [S2 - H, S2 + H], 14_000.0), [T - H, T, T + H], 20_000.0)
    raw = add_record(drop_record(raw, S1), T, 10_010.0)
    if two_destinations:
        raw = drop_record(raw, S2)
    _, c = run(raw, FIRST, LAST)
    return json.loads(json.dumps(c["demand_misplaced"].to_dict()))["evidence"][0]


def paragraph(lang: str, e: dict) -> str:
    macro = environment(lang).get_template(f"repair.{lang}.md.j2").module.misplaced
    return str(macro(e, MISPLACED_MIN_RATIO, *BOUNDS_MW))


@pytest.mark.parametrize("lang", ["fr", "en"])
def test_the_repair_paragraph_states_both_conditions_with_the_run_numbers(lang):
    for two in (False, True):
        e = stored_evidence(two)
        text = paragraph(lang, e)
        assert "\n" not in text.strip() and "{" not in text
        for x in (fmt_num(MISPLACED_MIN_RATIO, 1, lang), fmt_num(BOUNDS_MW[0], 0, lang), fmt_num(BOUNDS_MW[1], 0, lang),
                  fmt_num(e["cost_ratio"], 1, lang)):
            assert x in text
        if two:
            assert e["destinations"] == 2 and OTHER[lang] in text and ONLY[lang] not in text
            assert fmt_num(e["destination_ratio"], 1, lang) in text and "2022-01-04 10:00 UTC" in text
            assert fmt_num(e["runner_up"]["cost_mw"], 0, lang) in text
        else:
            assert e["runner_up"] is None and ONLY[lang] in text and OTHER[lang] not in text


@pytest.mark.parametrize("lang", ["fr", "en"])
def test_the_data_quality_templates_parse_with_the_repair_macro(lang):
    environment(lang).get_template(f"dq.{lang}.md.j2")
