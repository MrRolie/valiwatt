"""The data-quality report's repair paragraph, rendered from a repair's evidence as the run stores it (JSON), in both
of its branches: the only possible destination, and a destination chosen over another. Its sentence on a refusal says
what the refused hour's evidence names, and every refusal shape build_demand makes is checked against it."""

import json

import numpy as np
import pandas as pd
import pytest

from conftest import synth_demand
from test_demand_gates import H, add_record, drop_record, run, set_mw, value
from valiwatt.quality.demand import BOUNDS_MW, MISPLACED_MIN_RATIO
from valiwatt.report.render import environment, fmt_num

FIRST, LAST = "2021-12-28T00:00Z", "2022-01-10T00:00Z"
S1, S2, T = (pd.Timestamp(x) for x in ("2022-01-02T10:00Z", "2022-01-04T10:00Z", "2022-01-07T10:00Z"))
ONLY = {"fr": "est la seule heure sans enregistrement", "en": "is the only hour without a record"}
OTHER = {"fr": "la suivante en coût est", "en": "the next best is"}
# The destination condition, read as a multiple of the chosen destination's cost (never "N times more").
TIMES = {"fr": "{} fois le coût de la destination choisie", "en": "{} times the chosen destination's cost"}
# What the paragraph says a refused hour's evidence names. The sentence it replaced said "both possible destinations"
# for every refusal, which is false for most of them.
REFUSAL = {
    "fr": "la preuve de ce doublon donne la raison et, quand l'heure porte exactement deux valeurs, toutes deux dans les "
          "bornes, et qu'une destination a été trouvée, nomme la meilleure destination et la suivante en coût s'il y "
          "en a une (les deux quand la destination est ambiguë)",
    "en": "the duplicate's evidence gives the reason and, when the hour holds exactly two values, both inside the "
          "bounds, and a destination was found, names the best destination and the next best if there is one (both, "
          "when the destination is ambiguous)",
}
OLD_REFUSAL = {"fr": "nomme les deux destinations possibles", "en": "names both possible destinations"}


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
        assert TIMES[lang].format(fmt_num(MISPLACED_MIN_RATIO, 1, lang)) in text
        assert "fois plus" not in text and "times more" not in text
        if two:
            assert e["destinations"] == 2 and OTHER[lang] in text and ONLY[lang] not in text
            assert fmt_num(e["destination_ratio"], 1, lang) in text and "2022-01-04 10:00 UTC" in text
            assert fmt_num(e["runner_up"]["cost_mw"], 0, lang) in text
        else:
            assert e["runner_up"] is None and ONLY[lang] in text and OTHER[lang] not in text


@pytest.mark.parametrize("lang", ["fr", "en"])
def test_the_data_quality_templates_parse_with_the_repair_macro(lang):
    environment(lang).get_template(f"dq.{lang}.md.j2")


def refusal_shapes() -> dict:
    """Every way build_demand refuses a duplicate, each as the raw records that make it."""
    clean = synth_demand(FIRST, LAST)
    one_empty = drop_record(clean, S1)
    near = value(clean, T) + 1.0  # fits its own hour: the swap test refuses it
    two = set_mw(set_mw(clean, [S1 - H, S1 + H], 10_000.0), [S2 - H, S2 + H], 10_020.0)
    two = drop_record(drop_record(add_record(set_mw(two, [T - H, T, T + H], 20_000.0), T, 10_010.0), S1), S2)
    return {
        "not_a_pair": (add_record(add_record(clean, T, 21_000.0), T, 22_000.0), 0),
        "blank_value": (add_record(one_empty, T, np.nan), 0),
        "value_out_of_bounds": (add_record(one_empty, T, 4_900.0), 0),
        "no_destination": (add_record(clean, T, near), 0),
        "swap_ratio": (add_record(one_empty, T, near), 1),
        "swap_ratio_two_candidates": (add_record(drop_record(one_empty, S2), T, near), 2),
        "ambiguous_destination": (two, 2),
    }


@pytest.mark.parametrize("lang", ["fr", "en"])
def test_the_refusal_sentence_names_only_the_destinations_each_refusal_records(lang):
    """For each refusal shape, the evidence names what the sentence says: nothing unless the hour holds two in-bounds
    values and a destination was found; then the best and the next best if any; both when the destination is
    ambiguous. The paragraph prints that sentence, not the old one, in both of its branches."""
    for shape, (raw, named) in refusal_shapes().items():
        _, c = run(raw, FIRST, LAST)
        assert not c["demand_duplicates_resolved"].passed and not c["demand_misplaced"].evidence, shape
        ev = json.loads(json.dumps(c["demand_duplicates_resolved"].to_dict()))["evidence"]
        assert len(ev) == 1 and ev[0]["reason"] == shape.removesuffix("_two_candidates"), shape
        got = [d for d in (ev[0]["best_candidate"], ev[0]["runner_up"]) if d is not None]
        assert len(got) == named, shape
        if shape == "ambiguous_destination":
            assert {pd.Timestamp(d["slot"]) for d in got} == {S1, S2}
    for two in (False, True):
        text = paragraph(lang, stored_evidence(two))
        assert REFUSAL[lang] in text and OLD_REFUSAL[lang] not in text
