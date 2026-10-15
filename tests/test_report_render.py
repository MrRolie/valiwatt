"""The validation report rendered IN FULL, in the report's own environment (its filters, StrictUndefined), from runs
built so that every value differs from every other: each value must land in its own place. Also the departure's
labels, the 2024 false-alarm and blind-spot measures beside 2023's, the weeks set aside from the thresholds, the
interval wording, the repair paragraph's bounds sentence and the data-quality section 3.2 with no misplaced
record, in both languages."""

import re

import numpy as np
import pandas as pd
import pytest

from valiwatt.breaks import BREAKS, GROUPS
from valiwatt.report import validation as RV
from valiwatt.report.render import ReportRefused, environment, fmt_num

LANGS = ("fr", "en")
P_YEAR, R_YEAR = 2023, 2024
SEASONS = ("winter", "spring", "summer", "fall")
PHI = (5 ** 0.5 - 1) / 2


class Distinct:
    """Values that never repeat: the k-th is lo + (hi - lo) * frac(k * phi), rounded to 6 decimals, and a value
    already given is skipped."""

    def __init__(self):
        self.k, self.seen = 0, set()

    def __call__(self, lo=0.01, hi=0.09):
        while True:
            self.k += 1
            x = round(lo + (hi - lo) * ((self.k * PHI) % 1), 6)
            if x not in self.seen:
                self.seen.add(x)
                return x


def ci(g, lo, hi):
    m = g(lo, hi)
    return {"mean": m, "ci95_low": round(m - g(0.002, 0.01), 6), "ci95_high": round(m + g(0.002, 0.01), 6)}


def group_rows(g, keys, value="ape", lo=0.02, hi=0.08):
    rows = []
    for k in keys:
        a, b = g(lo, hi), g(-0.03, 0.03)
        rows.append({"key": str(k), "ape": a, "ape_low": round(a - g(0.001, 0.005), 6),
                     "ape_high": round(a + g(0.001, 0.005), 6), "rel_bias": b,
                     "rel_bias_low": round(b - g(0.001, 0.005), 6), "rel_bias_high": round(b + g(0.001, 0.005), 6),
                     "hours": int(g(1000, 9000))})
    return rows


def cov_rows(g, keys):
    rows = []
    for k in keys:
        m = g(0.84, 0.95)
        rows.append({"key": str(k), "covered": m, "covered_low": round(m - g(0.01, 0.03), 6),
                     "covered_high": round(m + g(0.01, 0.03), 6)})
    return rows


def thresholds(g, first, last, excluded):
    return {"performance": {s: {"mape_high": g(0.04, 0.09), "bias_low": g(-0.05, -0.01), "bias_high": g(0.02, 0.06),
                                "coverage_low": g(0.55, 0.85), "weeks": int(g(30, 40))} for s in SEASONS},
            "drift": {"temperature_high": g(1.0, 1.5), "forecast_high": g(0.8, 1.2), "reference_half_window_weeks": 2},
            "quantiles": {"mape_high": 0.95, "bias_low": 0.025, "bias_high": 0.975, "coverage_low": 0.05,
                          "drift_high": 0.95},
            "first_year": first, "last_year": last, "week_days": 7, "min_week_hours": 120,
            "excluded_weeks": [{"week_start": f"{last}-0{i + 1}-0{i + 2}", "reason": "failed_day"}
                               for i in range(excluded)]}


def week(g, start, flags=(), broad=()):
    keys = ("mape_high", "bias_high", "bias_low", "coverage_low")
    return {"week_start": start, "evaluated": True,
            "stats": {"mape": g(0.03, 0.09), "rel_bias": g(-0.04, 0.04), "covered": g(0.6, 0.95), "hours": 168},
            "flags": {k: k in flags for k in keys}, "broad": {k: k in broad for k in keys},
            "trimmed": {k: g(0.01, 0.06) for k in keys},
            "thresholds": {k: g(0.02, 0.07) for k in keys}}


def power(g, year, offset):
    starts = [f"{year}-01-0{1 + offset}", f"{year}-03-0{4 + offset}"]
    bias = [{"start": s, "season": "winter", "bias": b, "weeks": None if (b == 0.02 and i == 0) else int(g(1, 8))}
            for i, s in enumerate(starts) for b in (-0.04, -0.02, 0.02, 0.04)]
    half = [{"start": s, "season": "winter", "half_width": f, "weeks": int(g(1, 8)) if f == 0.5 or i else None}
            for i, s in enumerate(starts) for f in (0.9, 0.7, 0.5)]
    return {"bias": bias, "half_width": half}


def sensitivity(g, year, base):
    """One year's measures: the design (lengths, factors, shifts, noise) is the code's, every count its own."""
    rows = []
    for days in (1, 3, 5, 7):
        for factor in (0.7, 0.85, 1.15, 1.3):
            starts = base + len(rows)
            rows.append({"days": days, "factor": factor, "starts": starts,
                         "retrain": 0 if days == 1 else int(g(1, 60)), "retrain_clean": int(g(0, 9)),
                         "recalibrate": 0 if days == 1 else int(g(0, 5)),
                         "kept_from_retrain_by_echo_exclusion": int(g(0, 9))})
    seasons = [f"{year}-01-0{2 if year == P_YEAR else 1}", f"{year}-03-06", f"{year}-06-05", f"{year}-09-04"]
    shifts = []
    for i, s in enumerate(seasons):
        for f in (-0.1, -0.05, 0.05, 0.1):
            up_missed = f == 0.1 and i < (3 if year == P_YEAR else 1)
            down_missed = f == -0.1 and i == 0 and year == P_YEAR
            shifts.append({"start": s, "season": SEASONS[i], "shift": f,
                           "weeks": None if up_missed or down_missed else int(g(1, 10))})
    echo = [{"sd": sd, "runs": base + 10 * i, "retrain_with": base + 10 * i + 1, "retrain_without": base + 10 * i + 2,
             "suppressed": base + 10 * i + 3, "delayed": base + 10 * i + 4} for i, sd in enumerate((0.06, 0.10, 0.15))]
    return {"episodes": {"rows": rows, "first_start": f"{year}-01-0{3 if year == P_YEAR else 8}"},
            "level_shifts": shifts, "echo_exclusion_cost": {"rows": echo, "horizon_weeks": 8}}


def replay(g, year, base, decisions, near):
    weeks = [week(g, f"{year}-02-05", ("mape_high",), ()), week(g, f"{year}-02-12", ("mape_high",), ()),
             week(g, f"{year}-04-01", ("mape_high",), ()), week(g, f"{year}-04-08", ("bias_high",), ())]
    pair = weeks[:2] if near == "trim" else weeks[2:]
    recs = {}
    for d in decisions:
        if d["recommendation"]:
            recs[d["recommendation"]] = recs.get(d["recommendation"], 0) + 1
    return {
        "year": year, "summary": {
            "mape": ci(g, 0.03, 0.06), "bias": ci(g, -0.01, 0.01), "coverage": ci(g, 0.85, 0.93),
            "days": 366 if year == R_YEAR else 365, "hours_issued": int(g(8700, 8760)),
            "hours_scored": int(g(8600, 8700)), "days_failed": int(g(1, 5)), "weeks_evaluated": 51, "weeks": 53,
            "recommendations": recs,
            "flag_weeks": {k: base // 100 + i for i, k in enumerate(("mape_high", "bias_high", "bias_low",
                                                                      "coverage_low"))},
            "retrain_under_two_week_rule_alone": [{"week_start": pair[1]["week_start"],
                                                   "weeks": [{k: w[k] for k in ("week_start", "stats", "flags",
                                                                                "trimmed")} for w in pair]}]},
        "weeks": weeks, "decisions": decisions,
        "days": [{"day": f"{year}-01-01T00:00:00", "not_predicted": {f"{year}-01-01T06:00": "lag24, temp_persistence"}},
                 {"day": f"{year}-12-31T00:00:00", "not_predicted": {}}],
        "model": {"sha256": f"{base:064x}"}, "power": power(g, year, base % 3),
        "sensitivity": sensitivity(g, year, base),
        "evidently_checks": [{"week_start": f"{year}-03-04", "name": "temperature"}],
    }


def oos_frame(persistence_shift=0.03, observed_shift=0.0, seed=0):
    """Daily out-of-sample rows over the spring of 2020 and of 2021: the persistence forecast low by
    `persistence_shift` of demand in spring 2020 only; the observed-weather forecast off by `observed_shift`."""
    rng = np.random.default_rng(seed)
    days = pd.DatetimeIndex(list(pd.date_range("2020-03-01", "2020-06-30")) + list(pd.date_range("2021-03-01",
                                                                                                 "2021-06-30")))
    target = 20_000.0 * (1 + rng.normal(0, 0.01, len(days)))
    in2020 = days.year == 2020
    bias_p = np.where(in2020, persistence_shift, 0.0) + rng.normal(0, 0.004, len(days))
    bias_o = np.where(in2020, observed_shift, 0.0) + rng.normal(0, 0.03, len(days))
    return pd.DataFrame({"target": target, "pred_persistence": target * (1 - bias_p),
                         "pred_observed": target * (1 - bias_o), "baseline": target * (1 - rng.normal(0, 0.05,
                                                                                                         len(days))),
                         "day": days, "hour": 0, "fold": days.strftime("%Y-%m")})


def payloads(seed=0):
    g = Distinct()
    for _ in range(seed):
        g()
    P = {}
    for name in ("persistence", "observed", "baseline", "panel_persistence"):
        o = ci(g, 0.03, 0.08)
        P[name] = {"overall": {**o, "hours": int(g(30000, 36000)), "bias": {"mean": g(-0.01, 0.01)}},
                   "season": group_rows(g, SEASONS), "year": group_rows(g, range(2020, 2024)),
                   "hour": group_rows(g, range(24)), "decile": group_rows(g, range(1, 11))}
    C = {}
    for name in ("relative", "relative_by_season", "absolute", "observed_relative"):
        o = ci(g, 0.88, 0.92)
        C[name] = {"overall": o, "season": cov_rows(g, SEASONS),
                   "hour_group": cov_rows(g, ("night", "morning", "day", "evening", "late")),
                   "decile": cov_rows(g, range(1, 11)), "mean_half_width_pct": g(7, 12)}

    def case(separated):
        return {"months": [3, 4, 5, 6], "year": 2020, "bias_shift_year": ci(g, -0.03, -0.01),
                "bias_other_years": ci(g, -0.01, 0.01), "separated": separated,
                "monthly": [{"key": f"2020-0{m}", "ape": g(0.03, 0.06), "rel_bias": g(-0.03, 0.0)} for m in (3, 4, 5, 6)]}

    peak = {"hours": [{"utc": f"2023-02-0{d}T23:00:00+00:00", "target": g(41000, 42000), "temp_c": g(-28, -24),
                       "pred_persistence": g(31000, 33000), "pred_observed": g(36000, 38000),
                       "lower": g(28000, 29000), "upper": g(33000, 34000)} for d in (3, 4)],
            "covered": 0, "evaluated": 2, "busiest_in_named_month": True, "named_month": "2023-02"}
    v = {
        "folds": {"first": "2020-01", "last": "2023-12", "count": 48, "embargo_days": 7},
        "conformal": {"alpha": 0.1, "calibration_folds": 12, "first_fold": "2021-01"},
        "config": {"base_c": 18.0, "n_boot": 1000,
                   "hour_blocks": {"night": [0, 5], "morning": [6, 9], "day": [10, 15], "evening": [16, 20],
                                   "late": [21, 23]}},
        "performance": P, "coverage": C,
        "compare": {k: {**ci(g, -0.04, -0.01), "difference": g(-0.04, -0.01), "separated": True}
                    for k in ("persistence_vs_baseline", "observed_vs_persistence", "panel_vs_primary")},
        "cases": {"shift_2020": case(False), "shift_2020_observed": case(True), "peak": peak},
        "thresholds": {str(P_YEAR): thresholds(g, 2021, 2022, 1), str(R_YEAR): thresholds(g, 2021, 2023, 4)},
    }

    def coefficients():
        rows = [{"feature": f"cell:{h}", "frozen": g(10000, 20000), "no_lag": g(10000, 20000)} for h in range(3)]
        rows += [{"feature": f"{k}:{b}", "frozen": g(5, 60), "no_lag": g(70, 300)}
                 for k in ("heating", "cooling") for b in ("night", "morning", "day", "evening", "late")]
        rows += [{"feature": "lag24", "frozen": g(0.5, 0.7), "no_lag": None},
                 {"feature": "lag168", "frozen": g(0.1, 0.3), "no_lag": None}]
        return rows

    frozen = {}
    for year in (P_YEAR, R_YEAR):
        frozen[year] = {"run_id": f"{year:032x}",
                        "params": {"train_last_day": f"{year - 1}-12-24", "q": str(g(0.08, 0.1)),
                                   "calibration_first_fold": f"{year - 1}-01", "calibration_last_fold": f"{year - 1}-12",
                                   "calibration_scores": str(int(g(8000, 8700)))},
                        "tags": {"coefficient_sha256": f"{year * 7:064x}"}, "coefficients": coefficients()}
    p_decisions = [{"week_start": f"{P_YEAR}-05-01", "recommendation": "retrain", "rule": 2,
                    "fired": [{"rule": 2, "evidence": [{"week_start": f"{P_YEAR}-04-24",
                                                        "stats": {"mape": 0.071, "rel_bias": 0.031, "covered": 0.71}}]}]}]
    r_decisions = [{"week_start": f"{R_YEAR}-11-04T00:00:00", "recommendation": "retrain", "rule": 2,
                    "fired": [{"rule": 2, "evidence": [{"week_start": f"{R_YEAR}-10-28",
                                                        "stats": {"mape": 0.0623, "rel_bias": 0.0124,
                                                                  "covered": 0.664}}]}]},
                   {"week_start": f"{R_YEAR}-12-09T00:00:00", "recommendation": "investigate_data", "rule": 1,
                    "fired": [{"rule": 1, "evidence": {"failed_days": [f"{R_YEAR}-12-10"]}}]},
                   {"week_start": f"{R_YEAR}-12-16T00:00:00", "recommendation": None, "rule": None, "fired": []}]
    p = replay(g, P_YEAR, 301, p_decisions, "different")
    p["refitted"] = {"hours": int(g(8000, 8700)),
                     "mape": {"frozen": {"mean": g(0.03, 0.06)}, "refitted": {"mean": g(0.03, 0.06)}},
                     "bias": {"frozen": {"mean": g(-0.01, 0.01)}, "refitted": {"mean": g(-0.01, 0.01)}},
                     "coverage": {"frozen": {"mean": g(0.85, 0.93)}, "refitted": {"mean": g(0.85, 0.93)}},
                     "flag_weeks": {k: {f: int(g(0, 9)) for f in ("mape_high", "bias_high", "bias_low", "coverage_low")}
                                    for k in ("frozen", "refitted")},
                     "recommendations": {"frozen": {"retrain": 1}, "refitted": {"retrain": 2}},
                     "frozen_minus_refitted_ape": {**ci(g, -0.003, 0.003), "difference": g(-0.003, 0.003),
                                                   "separated": False}}
    r = replay(g, R_YEAR, 402, r_decisions, "trim")
    dq = {"sources": {"eccc": [{"centre": "Montréal", "primary": True, "first_retrieved_utc": "2026-10-13T10:00:00Z"},
                               {"centre": "Québec", "primary": False, "first_retrieved_utc": "2026-10-12T10:00:00Z"}],
                      "hydroquebec": {"retrieved_utc": "2026-10-11T09:00:00Z"}},
          "checks": {"demand_window": {"kind": "gate"}, "demand_bounds": {"kind": "gate"},
                     "demand_misplaced": {"kind": "repair", "evidence": [
                         {"moved_to_utc": "2022-01-01T05:00:00+00:00", "duplicated_utc": "2023-01-01T05:00:00+00:00"}]}}}
    b = {"breaks": [{"group": x.group, "test": x.test, "fr": x.fr, "en": x.en, "control": x.control,
                     "outcome": "passed"} for x in BREAKS], "groups": {k: list(v) for k, v in GROUPS.items()}}
    return v, dq, p, r, b, frozen


FIG = {k: f"figures/validation/{k}.png" for k in (
    "validation_monthly_mape", "validation_coverage_by_season", "validation_peak_hours", "pseudo_replay_2023_weekly",
    "replay_2024_weekly", "replay_2024_drift")}
FREEZE = {"freeze_commit": "f" * 40, "freeze_tag": "rules-frozen"}
EARLIER = {"id": "1" * 32, "freeze_commit": "e" * 40, "recorded": False,
           "summary": {"mape": {"mean": 0.04231}, "coverage": {"mean": 0.8943}, "recommendations": {"recalibrate": 1}},
           "recommendations": [{"week_start": "2024-06-17", "recommendation": "recalibrate"}],
           "model_sha256": f"{402:064x}", "flag_weeks": {"mape_high": 11, "bias_high": 12, "bias_low": 13,
                                                        "coverage_low": 14}}
NOT_IN_LEDGER = {"model_sha256": None, "flag_weeks": None}  # what `departure` gives a replay shown from the ledger

_BASE = {}


def context(departures=(), oos=None, **override) -> dict:
    """A context as `validation.context` builds it, from the payloads above through the real `derive`."""
    if oos is None and "derived" in _BASE:
        derived = _BASE["derived"]
    else:
        v, dq, p, r, b, frozen = payloads()
        derived = RV.derive(v, dq, p, r, b, frozen, oos_frame() if oos is None else oos)
        if oos is None:
            _BASE["derived"] = derived
    v, dq, p, r, b, frozen = payloads()
    departures = [dict(d) for d in departures]
    ctx = {"v": v, "dq": dq, "p": p, "r": r, "b": b, "frozen": frozen, **derived,
           "departures": departures, "departure_record": RV.departure_record(departures),
           "runs": {k: {"id": f"{i + 1:032x}", "when": f"2026-10-14 0{i}:00 UTC"} for i, k in enumerate(
               ("dq", "validation", "frozen_2023", "frozen_2024", "pseudo_replay", "replay", "breaks"))},
           "code_sha": "c" * 64, "rules_sha": "d" * 64, "freeze": FREEZE, "commit": "a" * 40, "dirty": False}
    ctx.update(override)
    return ctx


ATTRIBUTION = ["Données : Hydro-Québec.", "Built with Claude Code under the repository owner's direction."]


def render(ctx: dict, lang: str) -> str:
    return environment(lang).get_template(f"validation.{lang}.md.j2").render(
        **ctx, fig=FIG, lang=lang, attribution=ATTRIBUTION)


def section(text: str, number: int) -> str:
    start = re.search(rf"^## {number}\. ", text, re.M).start()
    nxt = re.search(r"^## \d+\. ", text[start + 4:], re.M)
    return text[start:start + 4 + nxt.start()] if nxt else text[start:]


def card_row(text: str, label: str) -> str:
    return next(line for line in text.splitlines() if line.startswith(f"| **{label}"))


def pct(x, d, lang):
    return fmt_num(100 * x, d, lang) + (" %" if lang == "fr" else "%")


# --- the full render, every value in its own place -------------------------------------------------------------------

@pytest.mark.parametrize("lang", LANGS)
def test_the_whole_report_renders_with_every_value_defined(lang):
    text = render(context(), lang)
    assert "None" not in text and "{{" not in text and "{%" not in text
    for n in range(1, 12):
        assert re.search(rf"^## {n}\. ", text, re.M), n


def test_derive_reads_each_forecast_from_its_own_column():
    """The observed-weather spring difference and monthly error come from pred_observed, the persistence ones from
    pred_persistence (the frame makes them differ in value and in verdict)."""
    oos = oos_frame()
    d = context()
    sd = d["shift_diff"]
    assert sd["persistence"]["verdict"] == "confirmed" and sd["observed"]["verdict"] == "not_confirmed"
    for key, col in (("persistence", "pred_persistence"), ("observed", "pred_observed")):
        e = (oos["target"] - oos[col]) / oos["target"]
        spring = pd.to_datetime(oos["day"]).dt.month.isin([3, 4, 5, 6])
        want = e[spring & (oos["day"].dt.year == 2020)].mean() - e[spring & (oos["day"].dt.year != 2020)].mean()
        assert sd[key]["difference"] == pytest.approx(want)
        mape = ((oos["target"] - oos[col]).abs() / oos["target"]).groupby(oos["fold"]).mean()
        assert d["monthly"][key].to_dict() == pytest.approx(mape.to_dict())


VERDICT = {"fr": ("selon la règle de la section 1, l'écart est confirmé",
                  "selon la règle de la section 1, l'écart n'est pas confirmé"),
           "en": ("under section 1's rule, the gap is confirmed", "under section 1's rule, the gap is not confirmed")}
OBSERVED = {"fr": "Avec la météo observée", "en": "With observed weather"}


@pytest.mark.parametrize("lang", LANGS)
def test_each_spring_verdict_is_read_from_its_own_forecast(lang):
    ctx = context()
    text = render(ctx, lang)
    spring = next(p for p in section(text, 3).split("\n\n") if p.startswith("**Printemps" if lang == "fr" else
                                                                             "**Spring"))
    persistence, observed = spring.split(OBSERVED[lang])
    confirmed, not_confirmed = VERDICT[lang]
    assert confirmed in persistence and not_confirmed not in persistence
    assert not_confirmed in observed and confirmed not in observed
    dsh, dso = ctx["shift_diff"]["persistence"], ctx["shift_diff"]["observed"]
    for d, part in ((dsh, persistence), (dso, observed)):
        assert fmt_num(100 * d["mc_se_low"], d["digits"], lang) in part
    card = card_row(text, "Cas tests" if lang == "fr" else "Test cases")
    assert ("confirme l'écart" if lang == "fr" else "confirms the gap") in card
    assert ("ne confirme pas l'écart" if lang == "fr" else "does not confirm the gap") not in card


@pytest.mark.parametrize("lang", LANGS)
def test_the_in_brief_misses_put_each_year_in_its_place(lang):
    ctx = context()
    text = render(ctx, lang)
    line = next(x for x in text.splitlines() if x.startswith("- " + ("Le rapport mesure aussi" if lang == "fr" else
                                                                         "The report also measures")))
    rise, gap = line.split(" ; sur " if lang == "fr" else "; on ")[0], line.split(" ; sur " if lang == "fr" else
                                                                                   "; on ")[-1]
    assert rise.startswith(("- Le rapport mesure aussi ce que le moniteur pouvait manquer : sur " if lang == "fr" else
                            "- The report also measures what the monitor could miss: on ") + f"{P_YEAR}, ")
    assert gap.startswith(f"{R_YEAR}, ")
    lv, lv24 = ctx["level"], ctx["level24"]
    assert (lv["up_n"] - lv["up_missed"], lv24["up_n"] - lv24["up_missed"]) == (1, 3)
    assert (f"à 1 des 4 départs, et sur {R_YEAR} (hors échantillon, mesure ajoutée après les résultats), à 3 des 4" if lang == "fr" else
            f"at 1 of 4 starts, and on {R_YEAR} (out of sample, a measurement added after the results), at 3 of 4") \
        in rise


Y_LABEL = {"fr": ("** (l'année de conception des règles) :", "** (hors échantillon, mesure ajoutée après les résultats) :"),
           "en": ("** (the year the rules were designed on):", "** (out of sample, a measurement added after the results):")}


def tables_after(text: str, label: str) -> list[str]:
    """Each table that follows a year's label, up to the next label or paragraph."""
    out = []
    for part in text.split(label)[1:]:
        out.append(part.split("\n\n**")[0].split("\n\n" + "**")[0])
    return out


@pytest.mark.parametrize("lang", LANGS)
def test_the_2023_and_2024_measures_each_sit_under_their_own_label(lang):
    ctx = context()
    s6 = section(render(ctx, lang), 6)
    l23, l24 = (f"**{y}{x}" for y, x in zip((P_YEAR, R_YEAR), Y_LABEL[lang]))
    under23, under24 = tables_after(s6, l23), tables_after(s6, l24)
    assert len(under23) == len(under24) == 3  # episodes, level shifts, echo-exclusion cost
    ep23, ep24 = ctx["p"]["sensitivity"]["episodes"]["rows"], ctx["r"]["sensitivity"]["episodes"]["rows"]
    for row in ep23:
        assert f"| {row['starts']} |" in under23[0] and f"| {row['starts']} |" not in under24[0]
    for row in ep24:
        assert f"| {row['starts']} |" in under24[0] and f"| {row['starts']} |" not in under23[0]
    assert f"{P_YEAR}-01-02" in under23[1] and f"{R_YEAR}-01-01" in under24[1]
    for rows, mine, other in ((ctx["p"], under23[2], under24[2]), (ctx["r"], under24[2], under23[2])):
        for row in rows["sensitivity"]["echo_exclusion_cost"]["rows"]:
            cells = f"| {row['runs']} | {row['retrain_with']} | {row['retrain_without']} |"
            assert cells in mine and cells not in other
    first = {"fr": f"dès le {P_YEAR}-01-03 en {P_YEAR} et le {R_YEAR}-01-08 en {R_YEAR}",
             "en": f"from {P_YEAR}-01-03 on in {P_YEAR} and from {R_YEAR}-01-08 on in {R_YEAR}"}[lang]
    assert first in s6
    assert ("ne sont donc pas mesurés sur une année" if lang == "fr" else "not measured on a year") in s6
    assert ("aussi mesurés sur 2024, hors échantillon" if lang == "fr" else "also measured on 2024, out of sample") in s6


@pytest.mark.parametrize("lang", LANGS)
def test_the_limits_give_each_years_measures(lang):
    ctx = context()
    s8 = section(render(ctx, lang), 8)
    E23, E24 = ctx["episode_limits"]["pseudo"], ctx["episode_limits"]["replay"]
    assert E23["share"] != E24["share"]
    episodes = next(x for x in s8.splitlines() if x.startswith("- **" + ("Épisodes" if lang == "fr" else "Episodes")))
    one, two = episodes.split((" ; sur " if lang == "fr" else "; on ") + str(R_YEAR))
    assert pct(E23["share"], 1, lang) in one and pct(E24["share"], 1, lang) in two
    assert pct(E24["share"], 1, lang) not in one and pct(E23["share"], 1, lang) not in two
    echo = next(x for x in s8.splitlines() if x.startswith("- **" + ("Exclusion" if lang == "fr" else "Echo")))
    for E, year in ((E23, P_YEAR), (E24, R_YEAR)):
        e = E["echo"]
        assert (f"sur {year} dans {e['suppressed']} essais sur {e['runs']} et le retarde dans {e['delayed']}"
                if lang == "fr" else
                f"on {year} in {e['suppressed']} of {e['runs']} runs and delays it in {e['delayed']}") in echo
    assert E23["echo"]["delayed"] != E24["echo"]["delayed"]
    level = next(x for x in s8.splitlines() if x.startswith("- **" + ("Changement" if lang == "fr" else "Shift")))
    lv, lv24 = ctx["level"], ctx["level24"]
    assert (lv["up_missed"], lv24["up_missed"]) == (3, 1)
    assert (lv["down_caught"], lv["down_n"], lv24["down_caught"], lv24["down_n"]) == (3, 4, 4, 4)
    for year, caught in ((P_YEAR, 3), (R_YEAR, 4)):
        if (lv if year == P_YEAR else lv24)["up_missed"]:
            fall = (f"une baisse de 10\u00a0% l'a donné à {caught} sur 4." if lang == "fr" else
                    f"a 10% fall did at {caught} of 4.")
            clause = level[level.index(f"Sur {year}," if lang == "fr" else f"On {year},"):]
            assert clause[:clause.index(".") + 1].endswith(fall)
    assert (f"Sur {P_YEAR}, une hausse soutenue de 10 % de la demande n'a pas donné « réentraîner » en 10 "
            f"semaines à 3 départs sur 4" if lang == "fr" else
            f"On {P_YEAR}, a sustained 10% rise in demand did not yield \"retrain\" within 10 weeks at 3 of 4 starts") \
        in level
    assert (f"Sur {R_YEAR}, une hausse soutenue de 10 % de la demande n'a pas donné « réentraîner » en 10 "
            f"semaines à 1 départ sur 4" if lang == "fr" else
            f"On {R_YEAR}, a sustained 10% rise in demand did not yield \"retrain\" within 10 weeks at 1 of 4 starts") \
        in level
    assert ("Sur 2023 et 2024, une baisse a donné « réentraîner » plus souvent qu'une hausse" if lang == "fr" else
            "On 2023 and 2024, a fall yielded \"retrain\" more often than a rise") in level
    for label in ("(mesuré sur 2023, l'année de conception des règles, et sur 2024, hors échantillon, mesure ajoutée "
                  "après les résultats)",) if lang == "fr" else (
            "(measured on 2023, the year the rules were designed on, and on 2024, out of sample, a measurement added "
            "after the results)",):
        assert s8.count(label) == 3


@pytest.mark.parametrize("lang", LANGS)
def test_the_model_card_names_the_year_of_each_detection_figure(lang):
    text = render(context(), lang)
    row = card_row(text, ("Rejeu de " if lang == "fr" else "") + str(R_YEAR))
    assert row.count(("Sur " if lang == "fr" else "On ") + str(R_YEAR) + ",") == 2
    assert str(P_YEAR) not in row


@pytest.mark.parametrize("lang", LANGS)
def test_the_synthetic_level_shift_is_set_against_both_years(lang):
    ctx = context()
    s9 = section(render(ctx, lang), 9)
    lv24 = ctx["level24"]
    assert (f"Sur les vraies heures de {P_YEAR}, une hausse" if lang == "fr" else
            f"On the real {P_YEAR} hours, a 10% rise") in s9
    assert (f"Sur les vraies heures de {R_YEAR} (hors échantillon, mesure ajoutée après les résultats), la même "
            f"hausse n'a pas donné « réentraîner » en 10 semaines à {lv24['up_missed']} des {lv24['up_n']} départs"
            if lang == "fr" else
            f"On the real {R_YEAR} hours (out of sample, a measurement added after the results), the same rise did "
            f"not yield \"retrain\" within 10 weeks at {lv24['up_missed']} of {lv24['up_n']} starts") in s9


# --- the 2024 measures: refused when absent or made differently ------------------------------------------------------

def test_a_2024_replay_without_its_sensitivity_measures_is_refused():
    v, dq, p, r, b, frozen = payloads()
    del r["sensitivity"]
    with pytest.raises(ReportRefused, match="no false-alarm and blind-spot measures"):
        RV.derive(v, dq, p, r, b, frozen, oos_frame())
    v, dq, p, r, b, frozen = payloads()
    del r["sensitivity"]["echo_exclusion_cost"]
    with pytest.raises(ReportRefused, match="no false-alarm and blind-spot measures"):
        RV.derive(v, dq, p, r, b, frozen, oos_frame())


def test_2024_measures_made_with_another_design_are_refused():
    for change in ("episodes", "shifts", "sds", "horizon"):
        v, dq, p, r, b, frozen = payloads()
        S = r["sensitivity"]
        if change == "episodes":
            S["episodes"]["rows"][-1]["days"] = 9
        elif change == "shifts":
            S["level_shifts"][0]["shift"] = -0.2
        elif change == "sds":
            S["echo_exclusion_cost"]["rows"][0]["sd"] = 0.2
        else:
            S["echo_exclusion_cost"]["horizon_weeks"] = 6
        with pytest.raises(ReportRefused, match=f"different designs \\({change}\\)"):
            RV.derive(v, dq, p, r, b, frozen, oos_frame())


def test_episode_limits_read_each_years_rows():
    rows = [{"days": 1, "factor": 0.85, "starts": 100, "retrain": 2, "retrain_clean": 1, "recalibrate": 1},
            {"days": 3, "factor": 1.3, "starts": 100, "retrain": 30, "retrain_clean": 4, "recalibrate": 0},
            {"days": 5, "factor": 0.85, "starts": 50, "retrain": 10, "retrain_clean": 0, "recalibrate": 0}]
    S = {"episodes": {"rows": rows}, "echo_exclusion_cost": {"rows": [
        {"suppressed": 2, "runs": 60, "delayed": 1}, {"suppressed": 3, "runs": 60, "delayed": 4}]}}
    E = RV.episode_limits(S, [0.85, 1.15])
    assert E["worst"] is rows[1] and E["share"] == 0.3 and E["mild"] == 0.2 and E["one_day_alarms"] == 3
    assert E["one_day"] == {"retrain": 2, "retrain_clean": 1, "recalibrate": 1, "cases": 100}
    assert E["recal_worst"] is None and E["recal_share"] == 0.0  # the one-day row's recalibrate is not an episode's
    assert E["echo"] == {"suppressed": 5, "runs": 120, "delayed": 5}
    none = RV.episode_limits({"episodes": {"rows": [{**rows[0], "retrain": 0, "recalibrate": 0}]},
                              "echo_exclusion_cost": {"rows": []}}, [0.85])
    assert none["worst"] is None and none["one_day_alarms"] == 0
    # A one-day shock that retrains more often than any episode is an alarm, not the worst episode of several days.
    hot = RV.episode_limits({**S, "episodes": {"rows": [*rows, {"days": 1, "factor": 1.3, "starts": 10, "retrain": 9,
                                                                 "retrain_clean": 2, "recalibrate": 0}]}}, [0.85, 1.15])
    assert hot["worst"] is rows[1] and hot["share"] == 0.3 and hot["one_day_alarms"] == 12
    assert hot["one_day"] == {"retrain": 11, "retrain_clean": 3, "recalibrate": 1, "cases": 110}
    # The episode of several days that most often recalibrates, by its share of starts (3 of 50 beats 4 of 100).
    recal = RV.episode_limits({**S, "episodes": {"rows": [{**rows[0], "recalibrate": 9}, {**rows[1], "recalibrate": 4},
                                                           {**rows[2], "recalibrate": 3}]}}, [0.85])
    assert recal["recal_worst"]["days"] == 5 and recal["recal_share"] == 0.06


@pytest.mark.parametrize("lang", LANGS)
def test_every_limit_branch_renders_a_true_clause(lang):
    m = environment(lang).get_template(f"sentences.{lang}.md.j2").module
    quiet = {"recal_worst": None, "recal_share": 0.0}
    loud = {"recal_worst": {"days": 7, "factor": 1.15}, "recal_share": 0.0174}
    worst = {"worst": {"days": 5, "factor": 1.3}, "share": 0.123, "mild": 0.021}
    retrain = ("sur 2024, « réentraîner » jusqu'à 12,3\u00a0% des départs (5 jours à × 1,30) et au plus 2,1\u00a0% pour "
               "un épisode de ±15\u00a0%, " if lang == "fr" else
               "on 2024, \"retrain\" at up to 12.3% of starts (5 days at × 1.30) and at most 2.1% for an episode of "
               "±15%, ")
    recal = ("« recalibrer » jusqu'à 1,7\u00a0% des départs (7 jours à × 1,15)" if lang == "fr" else
             "\"recalibrate\" at up to 1.7% of starts (7 days at × 1.15)")
    no_recal = "aucun « recalibrer »" if lang == "fr" else "no \"recalibrate\""
    assert str(m.episode_limit({**worst, **quiet}, 2024, 0.15)) == retrain + no_recal
    assert str(m.episode_limit({**worst, **loud}, 2024, 0.15)) == retrain + recal
    none = {"worst": None, "share": 0, "mild": 0}
    no_retrain = "sur 2024, aucun « réentraîner », " if lang == "fr" else "on 2024, no \"retrain\", "
    assert str(m.episode_limit({**none, **quiet}, 2024, 0.15)) == no_retrain + no_recal
    assert str(m.episode_limit({**none, **loud}, 2024, 0.15)) == no_retrain + recal
    absorbs = "La persistance absorbe" if lang == "fr" else "Persistence absorbs"
    for missed, caught in ((0, 4), (1, 1), (2, 1), (2, 3)):
        level = {"size": 0.1, "up_n": 4, "up_missed": missed, "down_n": 4, "down_caught": caught,
                 "months": [1, 3, 6, 9], "missed_months": [1, 3, 6][:missed]}
        text = str(m.level_limit(level, 2024, 10))
        assert text.startswith("Sur 2024, " if lang == "fr" else "On 2024, ") and text.endswith(".")
        assert ("à chaque départ" in text if lang == "fr" else "at every start" in text) == (missed == 0)
        if missed:  # only the starts whose rise was missed are named
            named = {1: ("janvier", "January"), 2: ("janvier et mars", "January and March")}[missed]
            assert (f"à {missed} départ{'s' if missed > 1 else ''} sur 4 (départ{'s' if missed > 1 else ''} de "
                    f"{named[0]})" if lang == "fr" else
                    f"at {missed} of 4 starts (the {named[1]} start{'s' if missed > 1 else ''})") in text
            assert ("juin" not in text) if lang == "fr" else ("June" not in text)
        quiet = {"size": 0.1, "up_n": 4, "up_missed": 0, "down_n": 4, "down_caught": 4, "months": [1, 3],
                 "missed_months": []}
        for pair in (((2023, level), (2024, quiet)), ((2023, quiet), (2024, level))):
            why = str(m.level_why(list(pair)))
            fall = missed > 0 and caught > 4 - missed
            assert why.startswith(absorbs) == (missed > 0)
            year = [y for y, x in pair if x is level][0]
            assert (f"Sur {year}, une baisse" in why if lang == "fr" else f"On {year}, a fall" in why) == fall
            assert why == "" or why.endswith(".")
        note = str(m.level_shift_note_replay(level, 2024, 10))
        assert note.endswith("(section 8).")
        assert (f"à {missed} des 4 départs" in note if lang == "fr" else f"at {missed} of 4 starts" in note) == (
            missed > 0)


@pytest.mark.parametrize("lang", LANGS)
def test_a_one_day_alarm_names_the_year_it_came_from(lang):
    ctx = context()
    quiet = section(render(ctx, lang), 6)
    assert ("Aucun choc d'un seul jour n'a donné d'alerte, ni sur 2023 ni sur 2024" if lang == "fr" else
            "No one-day shock gave an alarm, on 2023 or on 2024") in quiet
    one = {"retrain": 3, "retrain_clean": 0, "recalibrate": 14, "cases": 1400}
    limits = {**ctx["episode_limits"], "replay": {**ctx["episode_limits"]["replay"], "one_day_alarms": 17,
                                                  "one_day": one}}
    loud = section(render({**ctx, "episode_limits": limits}, lang), 6)
    assert ("**Alertes des chocs d'un seul jour** : sur 2024, 3 « réentraîner » (dont 0 sans aide) et 14 "
            "« recalibrer », sur 1\u202f400 cas (départs × facteurs)." if lang == "fr" else
            "**Alarms from one-day shocks**: on 2024, 3 \"retrain\" (0 of them clean) and 14 \"recalibrate\", out of "
            "1,400 cases (starts × factors).") in loud
    assert str(P_YEAR) + ", " not in loud[loud.index("**Al"):loud.index(".", loud.index("**Al"))]


@pytest.mark.parametrize("lang", LANGS)
def test_level_shift_starts_are_named_per_year_when_they_differ(lang):
    ctx = context()
    same = section(render(ctx, lang), 6)
    assert ("première semaine jugée de janvier, mars, juin et septembre, pendant" if lang == "fr" else
            "first judged week of January, March, June and September, for") in same
    other = section(render({**ctx, "level24": {**ctx["level24"], "months": [1, 4, 6, 9]}}, lang), 6)
    want = ("de janvier, mars, juin et septembre en 2023 et de janvier, avril, juin et septembre en 2024" if lang == "fr"
            else "of January, March, June and September in 2023 and of January, April, June and September in 2024")
    assert other.count(want) == 2  # the level-shift paragraph and the echo-exclusion cost paragraph


# --- section 5: the weeks set aside -----------------------------------------------------------------------------------

SET_ASIDE = {
    "fr": {(0, 0): "Pour ces raisons, 0 semaine de 2021–2023 a été écartée (0 de 2021–2022 pour le rejeu de 2023).",
           (1, 0): "Pour ces raisons, 1 semaine de 2021–2023 a été écartée (0 de 2021–2022 pour le rejeu de 2023) ; "
                   "l'exécution de validation consigne chacune avec sa raison.",
           (0, 2): "Pour ces raisons, 0 semaine de 2021–2023 a été écartée (2 de 2021–2022 pour le rejeu de 2023) ; "
                   "l'exécution de validation consigne chacune avec sa raison.",
           (1, 2): "Pour ces raisons, 1 semaine de 2021–2023 a été écartée (2 de 2021–2022 pour le rejeu de 2023) ; "
                   "l'exécution de validation consigne chacune avec sa raison.",
           (4, 4): "Pour ces raisons, 4 semaines de 2021–2023 ont été écartées (4 de 2021–2022 pour le rejeu de 2023) ; "
                   "l'exécution de validation consigne chacune avec sa raison."},
    "en": {(0, 0): "On these grounds 0 weeks of 2021–2023 were set aside (0 of 2021–2022 for the 2023 replay).",
           (1, 0): "On these grounds 1 week of 2021–2023 was set aside (0 of 2021–2022 for the 2023 replay); the "
                   "validation run lists each with its reason.",
           (0, 2): "On these grounds 0 weeks of 2021–2023 were set aside (2 of 2021–2022 for the 2023 replay); the "
                   "validation run lists each with its reason.",
           (1, 2): "On these grounds 1 week of 2021–2023 was set aside (2 of 2021–2022 for the 2023 replay); the "
                   "validation run lists each with its reason.",
           (4, 4): "On these grounds 4 weeks of 2021–2023 were set aside (4 of 2021–2022 for the 2023 replay); the "
                   "validation run lists each with its reason."},
}


@pytest.mark.parametrize("lang", LANGS)
def test_the_set_aside_sentence_has_a_true_branch_for_every_count(lang):
    m = environment(lang).get_template(f"sentences.{lang}.md.j2").module
    for (n24, n23), want in SET_ASIDE[lang].items():
        assert str(m.set_aside(n24, n23, (2021, 2023), (2021, 2022), 2023)) == want


@pytest.mark.parametrize("lang", LANGS)
def test_section_5_reads_the_weeks_set_aside_from_each_threshold_set(lang):
    ctx = context()
    s5 = section(render(ctx, lang), 5)
    assert (SET_ASIDE[lang][(4, 4)].replace("(4 de", "(1 de").replace("(4 of", "(1 of")) in s5
    assert ("les mesures de la section 6 sur 2023 les utilisent, celles sur 2024 les seuils du tableau précédent" if
            lang == "fr" else "the section 6 measurements on 2023 use them, those on 2024 the thresholds of the table "
            "above") in s5


# --- item 2: the interval's calibration as the code makes it ---------------------------------------------------------

INTERVAL = {"fr": ("des n erreurs hors échantillon des 12 mois précédents déjà notées quand le modèle du mois est ajusté "
                   "(section 8)", "sur les erreurs des 12 mois qui précèdent l'année rejouée, déjà notées quand il est "
                   "ajusté", "et déjà notées quand le modèle figé est ajusté (section 8)"),
            "en": ("of the n out-of-sample errors of the previous 12 months that were already scored when the month's "
                   "model is fitted (section 8)", "over the errors of the 12 months before the replayed year that were "
                   "already scored when it is fitted", "and already scored when the frozen model is fitted (section 8)")}


@pytest.mark.parametrize("lang", LANGS)
def test_the_interval_sentences_say_only_scored_errors_calibrate(lang):
    text = render(context(), lang)
    method, frozen, card = INTERVAL[lang]
    s1 = section(text, 1)
    assert method in s1 and frozen in s1
    assert card in card_row(text, "Intervalle" if lang == "fr" else "Interval")


# --- item 4: the departure ----------------------------------------------------------------------------------------------

POST_HOC = {"fr": ("**A posteriori.** Le rejeu de 2024 a tourné à nouveau, après une dérogation aux règles gelées : les "
                   "règles ont été changées après que les résultats de 2024 eurent été vus.",
                   "section « Departures from the frozen monitoring rules », qui en donne la liste"),
            "en": ("**Post hoc.** The 2024 replay ran again, after a departure from the frozen rules: the rules were "
                   "changed after the 2024 results had been seen.",
                   "section \"Departures from the frozen monitoring rules\", which lists it")}
ITEM_WORDS = {"fr": ("destination est sans ambiguïté", "hors des bornes physiques", "comme le rejeu voit son horloge",
                     "terme de 168 heures", "semaines que le rejeu juge", "dans le rejeu lui-même",
                     "registre des rejeux du dépôt", "aussi mesurés sur 2024, hors échantillon"),
              "en": ("destination is unambiguous", "outside the physical bounds", "as the replay sees its clock",
                     "168-hour term", "weeks the replay judges", "inside the replay itself",
                     "repository's replay ledger", "also measured on 2024, out of sample")}


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("recorded", [False, True])
def test_a_departure_replay_is_labelled_post_hoc_with_the_earlier_replay_beside_it(lang, recorded, monkeypatch):
    monkeypatch.setitem(RV.DEPARTURE, "date", "2031-02-03")  # the date is read from DEPARTURE, never typed
    ctx = context(departures=[{**EARLIER, "recorded": recorded, **(NOT_IN_LEDGER if recorded else {})}])
    text = render(ctx, lang)
    s7 = section(text, 7)
    opening, where = POST_HOC[lang]
    assert s7.count(opening) == 1 and where in s7 and "`AGENTS.md`" in s7
    assert ("les règles gelées d'abord sont celles du commit `eeeeeeeeeeee`, les règles après la dérogation celles "
            "du commit `ffffffffffff`" if lang == "fr" else "the rules as first frozen are those of commit "
            "`eeeeeeeeeeee`, the rules after the departure those of commit `ffffffffffff`") in s7
    assert ("Rejeu d'avant, règles du commit `eeeeeeeeeeee`" if lang == "fr" else
            "Earlier replay, rules of commit `eeeeeeeeeeee`") in s7
    table = s7[s7.index("| | " + ("Rejeu d'avant" if lang == "fr" else "Earlier replay")):].split("\n\n")[0]
    rows = {line.split(" | ")[0][2:]: line for line in table.splitlines()[2:]}
    mape, cover, recs = (rows[k] for k in (("EAMP", "Couverture", "Recommandations") if lang == "fr" else
                                          ("MAPE", "Coverage", "Recommendations")))
    r = ctx["r"]["summary"]
    assert mape.split(" | ")[1:] == [pct(0.04231, 2, lang), pct(r["mape"]["mean"], 2, lang) + " |"]
    assert cover.split(" | ")[1:] == [pct(0.8943, 1, lang), pct(r["coverage"]["mean"], 1, lang) + " |"]
    assert recs.split(" | ")[1:] == (
        ["semaine du 2024-06-17 : recalibrer les intervalles",
         "semaine du 2024-11-04 : réentraîner ; semaine du 2024-12-09 : examiner les données |"] if lang == "fr" else
        ["week of 2024-06-17: recalibrate the intervals",
         "week of 2024-11-04: retrain; week of 2024-12-09: investigate the data |"])
    items = [line[2:] for line in s7.splitlines() if line.startswith("- ")]
    assert len(items) == len(RV.DEPARTURE["changes"])
    for words, item in zip(ITEM_WORDS[lang], items, strict=True):
        assert words in item and item.endswith(".") and item.count(". ") == 0
    assert ("La dérogation du 2031-02-03 porte sur ces points" if lang == "fr" else
            "The departure of 2031-02-03 covers these items") in s7
    assert (LEDGER_LABEL[lang] in s7) == recorded
    card = card_row(text, ("Rejeu de " if lang == "fr" else "") + str(R_YEAR))
    assert ("**a posteriori** : rejoué à nouveau après une dérogation aux règles gelées, décidée après que les "
            "résultats de 2024 eurent été vus et consignée dans `AGENTS.md`" if lang == "fr" else
            "**post hoc**: replayed again after a departure from the frozen rules, which was decided after the 2024 "
            "results had been seen and is recorded in `AGENTS.md`") in card
    assert where in card and DEPARTED_CLAUSES[lang]["card"] in card
    s10 = section(text, 10)
    assert ("ce contrôle est le rejeu d'avant" if lang == "fr" else "that back-test is the earlier replay") in s10
    assert DEPARTED_CLAUSES[lang]["process"] in s10 and DEPARTED_CLAUSES[lang]["backtest"] in s10


DEPARTED_CLAUSES = {
    "fr": {"card": "; le rejeu d'avant est montré à côté (section 7).",
           "process": "; l'étiquette a été déplacée une fois, par une dérogation consignée dans `AGENTS.md`, et le "
                      "rejeu d'avant est montré à côté (section 7). Chaque garde a son bris.",
           "backtest": "Depuis la dérogation, ce contrôle est le rejeu d'avant, sur les règles gelées avant 2024"},
    "en": {"card": "; the earlier replay is shown beside it (section 7).",
           "process": "; the tag was moved once, by a departure recorded in `AGENTS.md`, and the earlier replay is "
                      "shown beside the new one (section 7). Each guard has its break.",
           "backtest": "Since the departure, that back-test is the earlier replay, on the rules frozen before 2024"}}


LEDGER_LABEL = {"fr": "**Consigné dans le registre, non rejoué ici.**", "en": "**Recorded in the ledger, not re-run here.**"}


@pytest.mark.parametrize("lang", LANGS)
def test_a_plain_replay_carries_no_departure_label(lang):
    text = render(context(), lang)
    assert POST_HOC[lang][0] not in text and "Departures from the frozen monitoring rules" not in text
    assert all(clause not in text for clause in DEPARTED_CLAUSES[lang].values())
    assert ("a posteriori" if lang == "fr" else "post hoc") not in text.lower().replace("contrôle a posteriori", "")
    m = environment(lang).get_template(f"sentences.{lang}.md.j2").module
    for key in RV.DEPARTURE["changes"]:
        assert str(m.departure_item(key, R_YEAR, [24, 168])) not in text


def test_agents_md_records_the_departure_the_report_describes():
    """One list, two homes: the report names the departure's items from DEPARTURE, AGENTS.md records them. Once the
    departures section names a commit, its entry carries DEPARTURE's date and exactly its items, in order; before
    that, it lists none."""
    from valiwatt.monitor.run import recorded_departures
    from valiwatt.report.lineage import COMMIT

    section = recorded_departures()
    items = re.findall(r"^\s*- ([A-Z][0-9]+):", section, re.M)
    if COMMIT.findall(section):
        assert RV.DEPARTURE["date"] in section
        assert tuple(items) == RV.DEPARTURE["changes"]
    else:
        assert items == []


def test_a_replay_departing_from_replays_under_two_freeze_commits_is_refused():
    assert RV.departure_record([]) is None
    one = RV.departure_record([EARLIER, {**EARLIER, "id": "2" * 32}])
    assert one == {"date": "2026-10-14", "changes": list(RV.DEPARTURE["changes"]), "from_commit": "e" * 40}
    with pytest.raises(ReportRefused, match="describes one departure"):
        RV.departure_record([EARLIER, {**EARLIER, "id": "2" * 32, "freeze_commit": "9" * 40}])
    with pytest.raises(ReportRefused, match="describes one departure"):
        RV.departure_record([{**EARLIER, "freeze_commit": ""}])


@pytest.mark.parametrize("lang", LANGS)
def test_every_departure_item_has_its_sentence_and_an_unknown_one_fails_the_render(lang):
    import jinja2

    m = environment(lang).get_template(f"sentences.{lang}.md.j2").module
    said = [str(m.departure_item(k, 2024, [24, 168])) for k in RV.DEPARTURE["changes"]]
    assert len(set(said)) == len(said) and all(s.endswith(".") and s[0].isupper() for s in said)
    with pytest.raises(jinja2.UndefinedError):
        str(m.departure_item("C9", 2024, [24, 168]))


def test_a_departed_replay_from_the_store_brings_its_recommendations(monkeypatch):
    class Run:
        class info:
            run_id = "3" * 32

        class data:
            tags = {"freeze_commit": "e" * 40}

    flags = {"mape_high": 5, "bias_high": 1, "bias_low": 2, "coverage_low": 6}
    results = {"summary": {"mape": {"mean": 0.04}, "flag_weeks": flags}, "model": {"sha256": "5" * 64},
               "decisions": [{"week_start": "2024-03-04T00:00:00", "recommendation": "retrain"},
                             {"week_start": "2024-03-11T00:00:00", "recommendation": None}]}
    monkeypatch.setattr(RV, "_json", lambda c, run, path: results)
    d = RV.departure(None, Run)
    assert d["recommendations"] == [{"week_start": "2024-03-04", "recommendation": "retrain"}]
    assert d["recorded"] is False and d["freeze_commit"] == "e" * 40
    assert d["model_sha256"] == "5" * 64 and d["flag_weeks"] == flags  # what section 7 sets side by side


# --- item 5: the repair paragraph states what the bounds rule does ---------------------------------------------------

@pytest.mark.parametrize("lang", LANGS)
def test_the_repair_paragraph_says_no_usable_hour_is_out_of_bounds(lang):
    e = {"duplicated_utc": "2023-01-01T05:00:00+00:00", "moved_to_utc": "2022-01-01T05:00:00+00:00",
         "moved_mw": 18000.5, "kept_mw": 21000.25, "cost_mw": 300.0, "swapped_cost_mw": 9000.0, "cost_ratio": 30.0,
         "runner_up": None, "destinations": 1, "destination_ratio": None}
    text = str(environment(lang).get_template(f"repair.{lang}.md.j2").module.misplaced(e, 2.0, 4321.0, 65432.0))
    assert ("dans les bornes physiques (4 321 à 65 432 MW)" if lang == "fr" else
            "inside the physical bounds (4,321 to 65,432 MW)") in text
    assert text.rstrip().endswith(
        "Enfin, aucune heure dont la valeur sort de ces bornes n'est rendue utilisable, quelle que soit la voie (le "
        "contrôle sur toute la fenêtre ou la série que le rejeu reconstruit à son horloge) : une dernière passe la "
        "marque hors bornes." if lang == "fr" else
        "Finally, no hour whose value lies outside those bounds is returned as usable, whatever the path (the check "
        "over the whole window or the series the replay rebuilds at its clock): a last pass marks it out of bounds.")


@pytest.mark.parametrize("lang", LANGS)
def test_section_3_2_states_the_move_rule_and_the_bounds_pass_when_nothing_was_misplaced(lang):
    """Section 3.2 of the data-quality template, rendered as written with no misplaced record: the rule and the bounds
    pass are still stated, with the run's numbers; with one record, the event paragraph carries them once."""
    env = environment(lang)
    src = env.loader.get_source(env, f"dq.{lang}.md.j2")[0]
    head = src.splitlines()[0]
    assert head.startswith("{% from 'repair.") and "move_rule" in head and "bounds_pass" in head
    block = head + "\n" + src[src.index("### 3.2"):src.index("### 3.3")]
    c = {"demand_misplaced": {"evidence": [], "metrics": {"min_ratio": 2.5}},
         "demand_bounds": {"metrics": {"low_mw": 4321.0, "high_mw": 65432.0}}}
    text = env.from_string(block).render(c=c)
    m = env.get_template(f"repair.{lang}.md.j2").module
    rule, bounds = str(m.move_rule(2.5, 4321.0, 65432.0)), str(m.bounds_pass())
    assert rule in text and bounds in text and text.index(rule) < text.index(bounds)
    assert ("Aucun enregistrement déplacé dans la fenêtre. Une heure qui porte deux valeurs" if lang == "fr" else
            "No misplaced record in the window. An hour that carries two values") in text
    assert fmt_num(2.5, 1, lang) in rule and fmt_num(4321.0, 0, lang) in rule and fmt_num(65432.0, 0, lang) in rule
    e = {"duplicated_utc": "2023-01-01T05:00:00+00:00", "moved_to_utc": "2022-01-01T05:00:00+00:00",
         "moved_mw": 18000.5, "kept_mw": 21000.25, "cost_mw": 300.0, "swapped_cost_mw": 9000.0, "cost_ratio": 30.0,
         "runner_up": None, "destinations": 1, "destination_ratio": None}
    one = env.from_string(block).render(c={**c, "demand_misplaced": {**c["demand_misplaced"], "evidence": [e]}})
    assert one.count(rule) == 1 and one.count(bounds) == 1 and "Aucun enregistrement" not in one \
        and "No misplaced" not in one
