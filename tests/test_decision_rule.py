"""The decision rule of docs/plan-v2.md section 8 (contrasts on the common hours, the cold tail, states and verdicts),
the model card's status (section 8.5) and the evaluation label of a v2 replay (sections 10.5 and 11.3.5)."""

import json

import numpy as np
import pandas as pd
import pytest

from valiwatt import config
from valiwatt.config import LST_OFFSET
from valiwatt.forecast import evaluate
from valiwatt.forecast.evaluate import (
    MIN_COLD_HOURS,
    MIN_COLD_WEEKS,
    STANDINGS,
    STATUSES,
    VERDICTS,
    cold_threshold,
    common_hours,
    contrasts,
    errors,
    evaluation_label,
    paired_difference,
    state,
    status,
    verdict,
)

H = pd.Timedelta(hours=1)
PREDS = ["pred_gam_forecast", "pred_forecast", "pred_persistence"]
COLD_C = -20.0
N_BOOT = 200


def year_frame(weeks: int = 26, seed: int = 0, first: str = "2025-01-06") -> pd.DataFrame:
    """Hours of a replayed year from a Monday: target, day, a mild forecast temperature and the three forecasts, v2
    the closest, v1 as shipped the farthest."""
    idx = pd.date_range(pd.Timestamp(first).tz_localize("UTC") - LST_OFFSET + H, periods=weeks * 168, freq="h")
    rng = np.random.default_rng(seed)
    target = 20_000 + 3_000 * np.sin(2 * np.pi * np.arange(len(idx)) / 24)
    f = pd.DataFrame({"target": target, "day": (idx - H + LST_OFFSET).tz_convert(None).normalize(),
                      "temp_forecast": 10.0}, index=idx)
    for key, err in (("gam_forecast", 0.02), ("forecast", 0.03), ("persistence", 0.04)):
        f[f"pred_{key}"] = target * (1 + rng.normal(0, err, len(idx)))
    return f


def with_cold(f: pd.DataFrame, hours: int, weeks: int) -> pd.DataFrame:
    """`hours` cold forecast hours spread over `weeks` Monday-to-Sunday weeks, from the second week."""
    f = f.copy()
    picks = [f.index[0] + pd.Timedelta(weeks=1 + i % weeks) + (i // weeks) * H for i in range(hours)]
    f.loc[picks, "temp_forecast"] = -25.0
    return f


def bounds(low: float, high: float) -> dict:
    return {"difference": (low + high) / 2, "ci95_low": low, "ci95_high": high, "hours": 1000, "separated": False}


def judged(shipped, family, testable=True, cold_shipped="not_separated", cold_family="not_separated") -> dict:
    st = lambda s: None if s is None else {"state": s}  # noqa: E731
    return {"shipped": st(shipped), "family": st(family),
            "cold": {"testable": testable, "shipped": st(cold_shipped), "family": st(cold_family)}}


OTHER = {"shipped": "family", "family": "shipped"}


# ------------------------------------------------------------------------------------------------------- states


def test_a_contrast_whose_interval_lies_below_zero_is_better():
    assert state(bounds(-0.02, -0.01)) == "better"


def test_a_contrast_whose_interval_lies_above_zero_is_worse():
    assert state(bounds(0.01, 0.02)) == "worse"


def test_a_contrast_whose_interval_holds_zero_is_not_separated():
    assert state(bounds(-0.01, 0.01)) == "not_separated"
    assert state(bounds(-0.01, 0.0)) == "not_separated" and state(bounds(0.0, 0.01)) == "not_separated"


def test_a_bound_printing_as_zero_is_not_separated():
    """The bounds are judged as printed, in percentage points to two decimals: an upper bound of -0.004 pp prints as
    -0.00, zero, and is not separated; -0.006 pp prints as -0.01 and is better. The same on the lower bound."""
    assert f"{100 * -0.00004:.2f}" == "-0.00" and f"{100 * -0.00006:.2f}" == "-0.01"
    assert state(bounds(-0.01, -0.00004)) == "not_separated"
    assert state(bounds(-0.01, -0.00006)) == "better"
    assert state(bounds(0.00004, 0.01)) == "not_separated"
    assert state(bounds(0.00006, 0.01)) == "worse"
    with pytest.raises(ValueError):
        state(bounds(float("nan"), 0.01))


# ----------------------------------------------------------------------------------------------------- verdicts


@pytest.mark.parametrize("x", ["shipped", "family"])
def test_a_better_contrast_whose_cold_tail_is_not_worse_is_better(x):
    for cold in ("better", "not_separated"):
        v = verdict(judged(**{x: "better", OTHER[x]: "worse"}, **{f"cold_{x}": cold, f"cold_{OTHER[x]}": "worse"}))
        assert v[x] == "better" and v[OTHER[x]] == "worse", cold


@pytest.mark.parametrize("x", ["shipped", "family"])
def test_a_better_contrast_whose_cold_tail_is_not_testable_is_better_cold_untested(x):
    for cold in ("worse", "better", None):
        v = verdict(judged(**{x: "better", OTHER[x]: "not_separated"}, testable=False, **{f"cold_{x}": cold}))
        assert v[x] == "better_cold_untested" and v[OTHER[x]] == "not_shown_better", cold


@pytest.mark.parametrize("x", ["shipped", "family"])
def test_a_contrast_not_separated_or_better_with_a_worse_cold_tail_is_not_shown_better(x):
    for testable, cold in ((True, "better"), (True, "worse"), (False, None)):
        v = verdict(judged(**{x: "not_separated", OTHER[x]: "better"}, testable=testable,
                           **{f"cold_{x}": cold, f"cold_{OTHER[x]}": "better"}))
        assert v[x] == "not_shown_better", (testable, cold)
    v = verdict(judged(**{x: "better", OTHER[x]: "better"}, **{f"cold_{x}": "worse", f"cold_{OTHER[x]}": "better"}))
    assert v[x] == "not_shown_better" and v[OTHER[x]] == "better"


@pytest.mark.parametrize("x", ["shipped", "family"])
def test_a_worse_contrast_is_worse(x):
    for testable, cold in ((True, "better"), (False, None)):
        v = verdict(judged(**{x: "worse", OTHER[x]: "better"}, testable=testable,
                           **{f"cold_{x}": cold, f"cold_{OTHER[x]}": "better"}))
        assert v[x] == "worse" and v[OTHER[x]] == ("better" if testable else "better_cold_untested")


def test_a_verdict_on_an_unknown_state_is_refused():
    with pytest.raises(ValueError):
        verdict(judged("separated", "better"))


# -------------------------------------------------------------------------------------------- contrasts and tail


def test_v2_closer_on_every_hour_is_better_on_both_comparisons():
    c = contrasts(with_cold(year_frame(), 168, 6), COLD_C, n_boot=N_BOOT)
    assert (c["shipped"]["state"], c["family"]["state"], c["input"]["state"]) == ("better", "better", "better")
    assert c["cold"]["testable"] and verdict(c) == {"shipped": "better", "family": "better"}
    assert c["hours"] == 26 * 168 and c["weeks"] == 26
    json.dumps(c)  # logged as JSON by the runs


def test_a_contrast_is_scored_on_the_common_hours_only():
    """Hours where some models have a forecast and another lacks one (v1 as shipped forecasting a day no run fed, v2
    and v1 as shipped without v1's form on the forecast), each far off and each cold, change no contrast and no cold
    tail: every contrast, the cold ones included, is scored on the hours with a target and all three forecasts."""
    f = year_frame()
    base = contrasts(f, COLD_C, n_boot=N_BOOT)
    extra = year_frame(weeks=4, seed=1, first="2025-07-07")
    extra["pred_gam_forecast"] = extra["target"] * 1.5
    extra["pred_persistence"] = extra["target"] * 0.4
    extra.loc[extra.index[: 2 * 168], "pred_forecast"] = np.nan  # v2 and v1 as shipped only
    extra.loc[extra.index[2 * 168:], ["pred_gam_forecast", "pred_forecast"]] = np.nan  # v1 as shipped only
    extra["temp_forecast"] = -25.0  # and cold: the cold tail is read on the common hours too
    g = pd.concat([f, extra])
    assert len(common_hours(g, PREDS)) == len(f)
    after = contrasts(g, COLD_C, n_boot=N_BOOT)
    for k in ("shipped", "family", "input"):
        assert after[k] == base[k], k
    assert (after["hours"], after["weeks"], after["cold"]) == (base["hours"], base["weeks"], base["cold"])


def test_the_contrasts_are_errors_hourly_ape_at_the_ruled_bootstrap():
    """The numbers that decide the verdicts (plan section 8.1): each contrast, the cold tail's two included, called
    with its defaults, is paired_difference on the hourly APE of errors() over the common hours, at 1,000 week-block
    resamples and seed 0, exactly. The APE is over the observed demand, |y - f| / y, never over the forecast."""
    assert evaluate.N_BOOT == 1000
    # The cold hours spread over 20 weeks: with few weeks the week-block bootstrap has so few distinct means that 999
    # and 1,000 resamples give the same bounds, and a count off by one on a cold call would pass (checked below).
    f = with_cold(year_frame(seed=4), 168, 20)
    f["hour"] = (f.index - H + LST_OFFSET).hour  # errors() reads them; the contrasts do not
    f["fold"] = f["day"].dt.to_period("M").astype(str)
    # Each pair has hours the three-way set lacks, one cold hour among them.
    f.loc[f.index[[30, 31, 500]], "pred_forecast"] = np.nan
    f.loc[f.index[[700, 2000]], "pred_persistence"] = np.nan
    f.loc[f.index[[3 * 168 + 5]], "pred_gam_forecast"] = np.nan
    hours = common_hours(f, PREDS)
    cold = hours[f.loc[hours, "temp_forecast"].to_numpy() < COLD_C]
    assert (len(hours), len(cold)) == (len(f) - 6, 167)
    c = contrasts(f, COLD_C)

    def ruled(rows, a, b, n_boot=1000):
        return paired_difference(errors(f.loc[rows], f"pred_{a}"), errors(f.loc[rows], f"pred_{b}"), "ape",
                                 n_boot=n_boot, seed=0)

    def bounds_of(d):
        return d["ci95_low"], d["ci95_high"]

    pairs = {"shipped": ("gam_forecast", "persistence"), "family": ("gam_forecast", "forecast"),
             "input": ("forecast", "persistence")}
    for k, (a, b) in pairs.items():
        expected = ruled(hours, a, b)
        assert {key: c[k][key] for key in expected} == expected, k
        assert c[k]["state"] == state(expected), k
        assert bounds_of(ruled(hours, a, b, n_boot=999)) != bounds_of(expected), k  # the frame tells 999 from 1,000
        if k != "input":
            expected = ruled(cold, a, b)
            assert {key: c["cold"][k][key] for key in expected} == expected, ("cold", k)
            assert c["cold"][k]["state"] == state(expected), ("cold", k)
            assert bounds_of(ruled(cold, a, b, n_boot=999)) != bounds_of(expected), ("cold", k)
    # One week of hours at 100 MW: v2 forecasts 80, v1's form on the forecast 90, v1 as shipped 125. Over the demand
    # their APEs are 0.20, 0.10 and 0.25; over the forecast they would be 0.25, 0.11 and 0.20, and the shipped
    # contrast would change sign.
    one = year_frame(weeks=1)
    one["target"], one["pred_gam_forecast"], one["pred_forecast"], one["pred_persistence"] = 100.0, 80.0, 90.0, 125.0
    one["hour"], one["fold"] = 0, "2025-01"
    assert errors(one, "pred_gam_forecast")["ape"].to_numpy() == pytest.approx(0.20, abs=1e-15)
    d = contrasts(one, COLD_C)
    for k, expected in (("shipped", 0.20 - 0.25), ("family", 0.20 - 0.10), ("input", 0.10 - 0.25)):
        assert d[k]["difference"] == pytest.approx(expected, abs=1e-12), k


def test_the_three_contrasts_are_additive():
    c = contrasts(year_frame(seed=3), COLD_C, n_boot=N_BOOT)
    assert c["shipped"]["hours"] == c["family"]["hours"] == c["input"]["hours"] == c["hours"]
    total = c["shipped"]["difference"] - c["family"]["difference"] - c["input"]["difference"]
    assert abs(total) < 1e-12 and c["additive_check"] == total


def test_the_peak_hours_are_the_busiest_of_the_year_with_missing_forecasts_kept():
    """The busiest day has no production forecast and no forecast from v1's form on the forecast (no run fed it): its
    hours stay among the five busiest, NaN with their reason, and quieter hours of the common set do not replace them.
    The paired contrasts still read the common set only."""
    f = year_frame()
    busiest = f["day"] == pd.Timestamp("2025-02-12")
    f.loc[busiest, "target"] += 8_000
    f.loc[busiest, ["pred_gam_forecast", "pred_forecast"]] = np.nan
    f["why_gam_forecast"] = None
    f.loc[busiest, "why_gam_forecast"] = "no run fed the day"  # v1's form on the forecast gives no reason here
    c = contrasts(f, COLD_C, n_boot=N_BOOT)
    top = f["target"].nlargest(5).index
    assert busiest[top].all()
    assert [p["utc"] for p in c["peak"]] == [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in top]
    for p, t in zip(c["peak"], top, strict=True):
        assert p["target"] == f.loc[t, "target"] and p["temp_forecast"] == 10.0
        assert np.isnan(p["pred_gam_forecast"]) and np.isnan(p["pred_forecast"])
        assert p["pred_persistence"] == f.loc[t, "pred_persistence"]
        assert p["missing"] == {"gam_forecast": "no run fed the day", "forecast": "no forecast"}
    quieter = f.loc[common_hours(f, PREDS), "target"].nlargest(5).index
    assert not set(quieter) & {pd.Timestamp(p["utc"]) for p in c["peak"]}
    rest = contrasts(f[~busiest], COLD_C, n_boot=N_BOOT)
    for k in ("shipped", "family", "input"):
        assert c[k] == rest[k], k
    json.dumps(c)


def test_the_cold_tail_needs_120_hours_in_at_least_4_weeks():
    """119 cold hours in 6 weeks and 120 in 3 weeks are each not testable; 120 in 4 weeks is. Cold is strictly below
    cold_c: 119 hours below it and one at it (cold_c is a quantile of the same forecast temperatures, so an hour can
    sit on it) are 119 cold hours, not testable."""
    f = year_frame()
    for hours, weeks, testable in ((119, 6, False), (120, 3, False), (120, 4, True)):
        cold = contrasts(with_cold(f, hours, weeks), COLD_C, n_boot=N_BOOT)["cold"]
        assert (cold["hours"], cold["weeks"], cold["testable"]) == (hours, weeks, testable), (hours, weeks)
        assert cold["threshold_c"] == COLD_C and cold["shipped"]["hours"] == cold["family"]["hours"] == hours
    assert (MIN_COLD_HOURS, MIN_COLD_WEEKS) == (120, 4)
    tie = with_cold(f, 119, 6)
    assert tie.loc[tie.index[-1], "temp_forecast"] == 10.0
    tie.loc[tie.index[-1], "temp_forecast"] = COLD_C
    c = contrasts(tie, COLD_C, n_boot=N_BOOT)
    assert (c["cold"]["hours"], c["cold"]["weeks"], c["cold"]["testable"]) == (119, 6, False)
    assert verdict(c) == {"shipped": "better_cold_untested", "family": "better_cold_untested"}


def test_the_cold_tail_is_not_testable_at_119_hours_and_testable_at_120():
    f = year_frame()
    for hours, testable in ((119, False), (120, True)):
        c = contrasts(with_cold(f, hours, 6), COLD_C, n_boot=N_BOOT)
        assert c["cold"]["testable"] is testable
        expected = "better" if testable else "better_cold_untested"
        assert verdict(c) == {"shipped": expected, "family": expected}
    none = contrasts(f, COLD_C, n_boot=N_BOOT)["cold"]
    assert (none["hours"], none["weeks"], none["testable"], none["shipped"], none["family"]) == (0, 0, False, None, None)


def test_a_better_contrast_with_a_worse_cold_tail_is_not_shown_better():
    f = with_cold(year_frame(), 168, 6)
    cold = f["temp_forecast"] < COLD_C
    f.loc[cold, "pred_gam_forecast"] = f.loc[cold, "target"] * 1.10  # v2 far off on the cold hours only
    c = contrasts(f, COLD_C, n_boot=N_BOOT)
    assert c["shipped"]["state"] == "better" and c["cold"]["testable"] and c["cold"]["shipped"]["state"] == "worse"
    assert verdict(c) == {"shipped": "not_shown_better", "family": "not_shown_better"}


def test_the_cold_threshold_reads_inputs_only():
    f = year_frame()
    f["temp_forecast"] = np.linspace(-30, 30, len(f))
    cold_c = cold_threshold(f["temp_forecast"])
    assert cold_c == pytest.approx(np.quantile(f["temp_forecast"], 0.05))
    g = f.copy()
    g["target"] = g["target"] * 1.5 + 1_000  # every outcome changed
    assert cold_threshold(g["temp_forecast"]) == cold_c
    a, b = contrasts(f, cold_c, n_boot=N_BOOT)["cold"], contrasts(g, cold_c, n_boot=N_BOOT)["cold"]
    assert (a["hours"], a["weeks"], a["threshold_c"]) == (b["hours"], b["weeks"], b["threshold_c"]) and a["hours"] > 0
    blanks = f["temp_forecast"].copy()
    blanks.iloc[:100] = np.nan
    assert cold_threshold(blanks) == pytest.approx(np.quantile(f["temp_forecast"].iloc[100:], 0.05))
    with pytest.raises(ValueError):
        contrasts(f, float("nan"), n_boot=N_BOOT)


# ------------------------------------------------------------------------------------------------------ status


def test_the_status_follows_the_test_of_record():
    """None: no 2025 start, candidate. A test of record that did not finish is unfinished whatever its standing, none
    or one outside STANDINGS included, or any verdict passed with it (a ruled next start passed as the record included,
    until it finishes). A finished post-hoc one is post hoc whatever its verdicts. Only a finished clean one gives a
    held-out status: confirmed when both verdicts are better, input only when the shipped one only, not confirmed
    otherwise. A finished one with a standing outside STANDINGS, or a record that does not say whether it finished, is
    refused."""
    B, U, N, W = VERDICTS[0], VERDICTS[1], VERDICTS[2], VERDICTS[3]

    def rec(finished, standing, shipped=None, family=None):
        v = None if shipped is None else {"shipped": shipped, "family": family}
        return {"finished": finished, "standing": standing, "verdict": v}

    seen = {status(None)}
    assert status(None) == "candidate"
    for standing in STANDINGS:
        assert status(rec(False, standing)) == "unfinished"
        assert status(rec(False, standing, B, B)) == "unfinished"
    assert status(rec(True, "post_hoc", B, B)) == "post_hoc"
    assert status(rec(True, "post_hoc", N, W)) == "post_hoc"
    for shipped in VERDICTS:
        for family in VERDICTS:
            got = status(rec(True, "clean", shipped, family))
            seen |= {got, status(rec(True, "post_hoc", shipped, family)), status(rec(False, "clean"))}
            if shipped in (B, U):
                assert got == ("confirmed" if family in (B, U) else "input_only"), (shipped, family)
            else:
                assert got == "not_confirmed", (shipped, family)
    assert seen == set(STATUSES)
    # Whether it finished is read before the standing: a start that stopped before its standing was logged is
    # unfinished (lane E lists it so), and a finished one without a valid standing is refused.
    for bad in ("", None, "held_out", "seen_by_developers"):
        assert status(rec(False, bad)) == "unfinished"
        assert status(rec(False, bad, B, B)) == "unfinished"
        with pytest.raises(ValueError, match="standing"):
            status(rec(True, bad, B, B))
    assert status({"finished": False, "standing": None, "verdict": None}) == "unfinished"
    assert status({"finished": False}) == "unfinished"  # nothing logged but that it did not finish
    with pytest.raises(ValueError, match="standing"):
        status({"finished": True, "standing": None, "verdict": {"shipped": B, "family": B}})
    for finished in (None, "no", 0):
        with pytest.raises(ValueError, match="finished"):
            status({"finished": finished, "standing": "clean", "verdict": None})
    with pytest.raises(ValueError):
        status(rec(True, "clean"))  # finished and clean, without its verdicts


# ------------------------------------------------------------------------------------------- evaluation label


def test_only_a_clean_test_of_record_is_labelled_held_out(monkeypatch):
    monkeypatch.setattr(config, "EVALUATION", {2023: "rehearsal", 2024: "seen_by_developers", 2025: "held_out"},
                        raising=False)
    S1, S2, S3 = "2026-03-02T14:05:09.123Z", "2026-03-09T10:00:00.000Z", "2026-03-20T08:30:00.500Z"

    def gate(standing, causes=(), start=None):
        ruling = None if start is None else {"start": start, "outcome": standing, "commit": "0" * 12}
        return {"causes": list(causes), "ruling": ruling, "standing": standing}

    def stopped(t, commit="a"):
        return {"started_utc": t, "finished": False, "freeze_commit": commit}

    def done(t, commit="a"):
        return {"started_utc": t, "finished": True, "freeze_commit": commit}

    # The first 2025 start is the test of record: labelled by its standing, whatever the cause.
    assert evaluation_label(2025, [], gate("clean")) == "held_out"
    assert evaluation_label(2025, [], gate("post_hoc")) == "post_hoc"
    assert evaluation_label(2025, [], gate("post_hoc", ["a"], "")) == "post_hoc"  # ruled post hoc on (a)
    assert evaluation_label(2025, [], gate("clean", ["a"], "")) == "held_out"
    # After a stopped start, the next one is the test of record when its gate holds the ruling naming the stopped one.
    assert evaluation_label(2025, [stopped(S1)], gate("clean", ["f"], S1)) == "held_out"
    assert evaluation_label(2025, [stopped(S1)], gate("post_hoc", ["f"], S1)) == "post_hoc"
    assert evaluation_label(2025, [stopped(S2), stopped(S1)], gate("clean", ["a", "f"], S2)) == "held_out"
    for g in (None, gate("clean"), gate("clean", ["a"], ""), gate("clean", ["a"], S1), gate("clean", ["f"], S3),
              {"causes": ["f"], "ruling": None, "standing": "clean"}):
        with pytest.raises(ValueError):
            evaluation_label(2025, [stopped(S1)], g)
    with pytest.raises(ValueError):  # a ruling naming the earlier stopped start, not the latest
        evaluation_label(2025, [stopped(S1), stopped(S2)], gate("clean", ["f"], S1))
    # A test of record whose gate holds no standing, or one outside STANDINGS, is refused.
    for bad in (None, "", "held_out", "seen_by_developers"):
        with pytest.raises(ValueError):
            evaluation_label(2025, [], gate(bad))
        with pytest.raises(ValueError):
            evaluation_label(2025, [stopped(S1)], gate(bad, ["f"], S1))
    with pytest.raises(ValueError):
        evaluation_label(2025, [], {"causes": [], "ruling": None})
    with pytest.raises(ValueError):
        evaluation_label(2025, [], None)
    # After a finished start, under the same or another freeze commit, a start is a rerun.
    for earlier in ([done(S1)], [done(S1, "b")], [done(S1), stopped(S2)], [stopped(S1), done(S2, "b")]):
        for g in (gate("clean"), gate("post_hoc"), gate("clean", ["f"], S2), None):
            assert evaluation_label(2025, earlier, g) == "seen_by_developers", (earlier, g)
    # 2024 is always seen by the developers; 2023 is the rehearsal.
    assert evaluation_label(2024, []) == "seen_by_developers"
    assert evaluation_label(2024, [done(S1)], gate("clean")) == "seen_by_developers"
    assert evaluation_label(2024, [], gate("clean")) == "seen_by_developers"
    assert evaluation_label(2023, []) == "rehearsal"
    with pytest.raises(ValueError):
        evaluation_label(2022, [])
