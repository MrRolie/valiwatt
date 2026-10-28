"""v3's decision rule (docs/plan-v3.md, section 8): the contrasts on the common set and the horizon on its own, the
subsets (the cold tail and the rate's event windows), the terms and family verdicts, the window ranking with its W
and k, the model card's status and the recommendation."""

import json

import numpy as np
import pandas as pd
import pytest

from valiwatt.config import LOCAL_TZ
from valiwatt.forecast import evaluate
from valiwatt.forecast.evaluate import (
    RECOMMENDATIONS,
    STANDINGS,
    STATUSES_V3,
    TERMS_BETTER,
    TERMS_VERDICTS,
    VERDICTS,
    common_hours,
    contrasts_v3,
    event_window_mask,
    event_windows,
    excluded_days,
    family_verdict,
    judge_v3,
    rank_windows,
    recommendation_v3,
    status_v3,
    terms_verdict,
    verdict_v3,
    winter_days,
    winter_windows,
    windows_k,
)
from valiwatt.forecast.issue import day_edge, lst_day

H = pd.Timedelta(hours=1)
KEYS = ("v3", "v2_noon", "v1_noon", "v2_midnight")
COLD_C = -20.0
N_BOOT = 200


def local(ts: str) -> pd.Timestamp:
    """The hour-ending UTC label of an hour ending at this local clock time."""
    return pd.Timestamp(ts).tz_localize(LOCAL_TZ).tz_convert("UTC")


def winter_frame(first: str = "2023-12-04", weeks: int = 17, seed: int = 0, errors: dict | None = None) -> pd.DataFrame:
    """Hours from a Monday: target, day, a mild noon-feed temperature and the four forecasts, v3 the closest."""
    idx = pd.date_range(day_edge(first) + H, periods=weeks * 168, freq="h")
    rng = np.random.default_rng(seed)
    target = 20_000 + 3_000 * np.sin(2 * np.pi * np.arange(len(idx)) / 24)
    f = pd.DataFrame({"target": target, "day": lst_day(idx), "temp_forecast": 10.0}, index=idx)
    for key, err in ({"v3": 0.02, "v2_noon": 0.03, "v1_noon": 0.04, "v2_midnight": 0.025} | (errors or {})).items():
        f[f"pred_{key}"] = target * (1 + rng.normal(0, err, len(idx)))
    return f


def with_cold(f: pd.DataFrame, hours: int, weeks: int) -> pd.DataFrame:
    """`hours` cold noon-feed hours over `weeks` Monday-to-Sunday weeks from the second week, at hours starting
    before 04:00 local, never in a window."""
    f = f.copy()
    night = f.index[event_windows(f.index)["hour"].to_numpy() < 4]
    week = evaluate.week_of(f.loc[night, "day"]).to_numpy()
    chosen = sorted(set(week))[1:1 + weeks]
    picks = [night[week == chosen[i % weeks]][i // weeks] for i in range(hours)]
    f.loc[picks, "temp_forecast"] = -25.0
    return f


def set_error(f: pd.DataFrame, key: str, err: float, mask, seed: int = 9) -> None:
    rng = np.random.default_rng(seed)
    f.loc[mask, f"pred_{key}"] = f.loc[mask, "target"] * (1 + rng.normal(0, err, int(np.sum(mask))))


def judged(ev, all_="not_separated", cold="not_separated", ev_testable=True, cold_testable=True) -> dict:
    st = lambda s: None if s is None else {"state": s}  # noqa: E731
    return {"subsets": {"all": {}, "cold": {"testable": cold_testable}, "event_windows": {"testable": ev_testable}},
            "terms": {"all": st(all_), "cold": st(cold), "event_windows": st(ev)},
            "family": {"all": st(all_), "cold": st(cold), "event_windows": st(ev)}}


# ------------------------------------------------------------------------------------------- the event windows


def test_the_event_windows_are_the_rate_s_peak_hours_by_local_clock():
    """Section 12: the hours ending 06:00, 11:00, 16:00 and 21:00 local are outside, those ending 07:00, 10:00, 17:00
    and 20:00 inside; the `hour` read is the local hour each hour starts in (6 to 9, 16 to 19)."""
    day = "2024-01-10"
    inside = [local(f"{day} {h:02d}:00") for h in (7, 10, 17, 20)]
    outside = [local(f"{day} {h:02d}:00") for h in (6, 11, 16, 21)]
    ew = event_windows(pd.DatetimeIndex(inside + outside))
    assert list(ew["window"]) == ["morning", "morning", "evening", "evening", None, None, None, None]
    assert list(ew["hour"]) == [6, 9, 16, 19, 5, 10, 15, 20]
    assert list(event_window_mask(pd.DatetimeIndex(inside + outside))) == [True] * 4 + [False] * 4


def test_on_a_march_day_under_daylight_time_the_windows_are_hours_ending_11_to_14_and_21_to_00_utc():
    labels = pd.date_range(local("2024-03-15 01:00"), local("2024-03-16 00:00"), freq="h")
    assert len(labels) == 24
    ew = event_windows(labels)
    inside = ew.index[ew["window"].notna()]
    assert [t.strftime("%d %H") for t in inside] == ["15 11", "15 12", "15 13", "15 14",
                                                     "15 21", "15 22", "15 23", "16 00"]
    assert set(ew.loc[inside, "date"]) == {pd.Timestamp("2024-03-15")}


@pytest.mark.parametrize(("day", "n"), [("2023-12-25", 0), ("2023-12-24", 0), ("2023-12-31", 0), ("2024-01-02", 0),
                                        ("2024-03-29", 0), ("2024-03-31", 8), ("2024-04-01", 0), ("2023-12-01", 8),
                                        ("2023-11-30", 0), ("2024-02-29", 8), ("2023-12-27", 8)])
def test_excluded_days_and_days_outside_the_winter_hold_no_window(day, n):
    """December 25 holds none, nor any excluded day (Good Friday 2024-03-29 included); Easter Sunday is not excluded;
    April and November hold none; December 1, March 31 and a leap day are inside."""
    labels = pd.date_range(day_edge(day) + H, periods=24, freq="h")
    assert int(event_window_mask(labels).sum()) == n


@pytest.mark.parametrize("year, sunday", [(2024, "2024-03-31"), (2025, "2025-04-20"), (2020, "2020-04-12"),
                                          (2023, "2023-04-09"), (2026, "2026-04-05")])
def test_easter_sunday_is_the_gregorian_date(year, sunday):
    """The local anonymous Gregorian computation (no calendar dependency) on known dates."""
    assert evaluate.easter_sunday(year) == pd.Timestamp(sunday)


def test_w_counts_each_movable_excluded_day_by_its_own_date():
    """Section 12 as amended (section 18): a winter whose Good Friday and Easter Monday both fall in April counts
    neither (2024-25: Easter 2025-04-20); 2023-24 counts Good Friday 2024-03-29 and not Easter Monday 2024-04-01."""
    assert excluded_days(2024) == frozenset(pd.Timestamp(d) for d in ("2024-12-24", "2024-12-25", "2024-12-26",
                                                                      "2024-12-31", "2025-01-01", "2025-01-02"))
    assert pd.Timestamp("2024-03-29") in excluded_days(2023) and pd.Timestamp("2024-04-01") not in excluded_days(2023)
    assert len(excluded_days(2023)) == 7 and len(winter_days(2023)) == 122 - 7
    assert {y: winter_windows(y) for y in range(2019, 2026)} == {2019: 232, 2020: 230, 2021: 230, 2022: 230,
                                                                 2023: 230, 2024: 230, 2025: 230}


def test_k_rounds_half_up_and_never_exceeds_w():
    """k = floor(30 w / W + 0.5), at most w: exactly half rounds up (w=1, W=60), just under it down (W=61)."""
    assert windows_k(1, 60) == 1 and windows_k(1, 61) == 0 and windows_k(3, 36) == 3 and windows_k(1, 120) == 0
    assert windows_k(230, 230) == 30 and windows_k(115, 230) == 15 and windows_k(58, 230) == 8
    assert windows_k(2, 2) == 2 and windows_k(0, 230) == 0
    for w in range(0, 233):
        assert windows_k(w, 232) == min(w, int(np.floor(30 * w / 232 + 0.5))), w
    with pytest.raises(ValueError):
        windows_k(1, 0)


# ------------------------------------------------------------------------------------------------- the ranking


def january() -> pd.DataFrame:
    """2024-01, every hour's demand equal: every window ties on demand."""
    f = winter_frame(first="2024-01-01", weeks=5)
    f = f[f["day"] <= "2024-01-31"].copy()
    f["target"] = 20_000.0
    for key in KEYS:
        f[f"pred_{key}"] = 20_000.0
    return f


def test_the_window_ranking_breaks_ties_by_date_then_morning_and_counts_overlap():
    """Equal means are ordered by the earlier date, then the morning first; a model ranking other windows highest
    overlaps the actual k highest in none. January 2024 holds 29 days that are not excluded: w=58, W=230, k=8."""
    f = january()
    later = event_windows(f.index)["date"] >= "2024-01-20"
    f.loc[later.to_numpy(), "pred_v2_noon"] = 21_000.0
    (part,) = rank_windows(f, "2024-01-01", "2024-01-31")
    assert (part["winter"], part["first_day"], part["last_day"]) == ("2023-24", "2024-01-01", "2024-01-31")
    assert (part["W"], part["w"], part["k"], part["ineligible"]) == (230, 58, 8, [])
    assert part["top"]["actual"] == [f"2024-01-{d:02d} {w}" for d in (3, 4, 5, 6) for w in ("morning", "evening")]
    assert part["top"]["v3"] == part["top"]["actual"] and part["top"]["v2_noon"][0] == "2024-01-20 morning"
    assert part["overlap"] == {"v3": 8, "v2_noon": 0, "v1_noon": 8}
    assert "v2_midnight" not in part["overlap"]  # issued after the notice: never ranked
    json.dumps(part)


def test_a_window_missing_one_forecast_hour_is_not_eligible_and_is_listed():
    f = january()
    ew = event_windows(f.index)
    at = lambda d, w: ew.index[(ew["date"] == d) & (ew["window"] == w)]  # noqa: E731
    f.loc[at("2024-01-10", "morning")[1], "pred_v1_noon"] = np.nan
    f.loc[at("2024-01-12", "morning")[3], "target"] = np.nan
    f = f.drop(index=at("2024-01-11", "evening")[:1])
    (part,) = rank_windows(f, "2024-01-01", "2024-01-31")
    assert part["ineligible"] == [{"day": "2024-01-10", "window": "morning", "reason": "no forecast from v1_noon"},
                                  {"day": "2024-01-11", "window": "evening",
                                   "reason": "3 of its 4 hours in the period's data"},
                                  {"day": "2024-01-12", "window": "morning", "reason": "no target"}]
    assert (part["w"], part["k"]) == (55, 7) and "2024-01-10 morning" not in part["top"]["actual"]
    f.loc[at("2024-01-13", "evening")[0], "pred_v2_midnight"] = np.nan  # not ranked: no reason to drop a window
    assert rank_windows(f, "2024-01-01", "2024-01-31")[0]["w"] == 55


def test_each_winter_s_part_of_a_period_is_ranked_on_its_own():
    """2025 holds the end of the winter 2024-25 and the start of 2025-26: two parts, never joined, each with its own
    W; days the period does not hold are not ranked, and windows without data are listed."""
    f = january()
    parts = rank_windows(f.iloc[:0], "2025-01-01", "2025-12-31")
    assert [(p["winter"], p["first_day"], p["last_day"], p["W"], p["w"], p["k"]) for p in parts] == [
        ("2024-25", "2025-01-01", "2025-03-31", 230, 0, 0), ("2025-26", "2025-12-01", "2025-12-31", 230, 0, 0)]
    assert len(parts[0]["ineligible"]) == 2 * (31 + 28 + 31 - 2) and len(parts[1]["ineligible"]) == 2 * (31 - 4)
    assert rank_windows(f, "2024-06-01", "2024-08-31") == []


# ----------------------------------------------------------------------------------------------- the contrasts


def test_the_contrasts_are_scored_on_the_common_noon_set_and_add_up():
    f = with_cold(winter_frame(), 168, 6)
    c = contrasts_v3(f, COLD_C, n_boot=N_BOOT)
    assert c["hours"] == len(f) and c["subsets"]["cold"] == {"hours": 168, "weeks": 6, "testable": True}
    windows = int(event_window_mask(f.index).sum())
    assert c["subsets"]["event_windows"] == {"hours": windows, "weeks": 17, "testable": True} and windows == 8 * 112
    for subset in ("all", "cold", "event_windows"):
        t, fam, whole = (c[k][subset]["difference"] for k in ("terms", "family", "whole"))
        assert abs(t + fam - whole) < 1e-12 and c["additive_check"][subset] == t + fam - whole
        assert c["terms"][subset]["state"] == c["family"][subset]["state"] == "better", subset
    assert verdict_v3(c) == {"terms": "better", "family": "better"}
    pd_hours = common_hours(f, [f"pred_{k}" for k in ("v3", "v2_noon")])
    assert len(pd_hours) == c["hours"]
    assert [p["utc"] for p in c["peak"]] and all(set(p) >= {f"pred_{k}" for k in KEYS} for p in c["peak"])
    json.dumps(c)


def test_the_horizon_is_scored_on_its_own_hours_and_the_others_ignore_it():
    """v2's form at midnight missing on some hours, v1's form at noon on others: the horizon reads the hours with v2's
    two forms, the three judged contrasts the hours with the three noon forecasts, neither set the other's."""
    f = winter_frame()
    base = contrasts_v3(f, COLD_C, n_boot=N_BOOT)
    g = f.copy()
    g.loc[g.index[:168], "pred_v1_noon"] = np.nan
    g.loc[g.index[168:336], "pred_v2_midnight"] = np.nan
    c = contrasts_v3(g, COLD_C, n_boot=N_BOOT)
    assert c["hours"] == len(f) - 168 and c["horizon"]["subsets"]["all"]["hours"] == len(f) - 168
    assert base["horizon"]["subsets"]["all"]["hours"] == len(f)
    assert c["horizon"]["contrast"]["all"]["hours"] == len(f) - 168
    on_both = g.index[168:]
    assert c["terms"]["all"] == contrasts_v3(g.loc[on_both], COLD_C, n_boot=N_BOOT)["terms"]["all"]
    assert c["horizon"]["contrast"]["all"]["state"] == "worse"  # noon (0.03) against midnight (0.025)
    # Section 8.1: the horizon's event windows are the common set's, kept to the horizon's hours (both sets' hours).
    assert c["horizon"]["contrast"]["event_windows"]["hours"] == int(event_window_mask(g.index[336:]).sum())


def test_the_horizon_s_subsets_are_the_common_set_s_intersected_with_its_hours():
    """Section 8.1, "on each subset of section 8.2 intersected with those hours": a cold event-window hour with the
    target, v2's form at noon and at midnight but no v1-noon forecast is outside the common set, so it enters the
    horizon's own set ("all") and neither its cold tail nor its event windows. The control: the same hour with its
    v1-noon forecast enters all three."""
    f = with_cold(winter_frame(), hours=130, weeks=5)
    hour = f.index[event_window_mask(f.index)][200]
    f.loc[hour, "temp_forecast"] = -25.0
    f.loc[hour, "pred_v2_noon"] = f.loc[hour, "target"] * 2  # an error the horizon's estimates would see
    base = contrasts_v3(f, COLD_C, n_boot=N_BOOT)
    g = f.copy()
    g.loc[hour, "pred_v1_noon"] = np.nan
    c = contrasts_v3(g, COLD_C, n_boot=N_BOOT)
    hz, hz0 = c["horizon"], base["horizon"]
    assert hz["subsets"]["all"]["hours"] == hz0["subsets"]["all"]["hours"] == len(f)
    assert hz["contrast"]["all"] == hz0["contrast"]["all"]  # the horizon's own set keeps the hour
    for k in ("cold", "event_windows"):
        assert hz["subsets"][k]["hours"] == hz0["subsets"][k]["hours"] - 1 == c["subsets"][k]["hours"], k
        assert hz["contrast"][k]["hours"] == c["subsets"][k]["hours"], k
        assert hz["contrast"][k]["difference"] < hz0["contrast"][k]["difference"], k  # its error left with it
    on_both = g.index[g.index != hour]
    assert hz["contrast"]["cold"] == contrasts_v3(g.loc[on_both], COLD_C, n_boot=N_BOOT)["horizon"]["contrast"]["cold"]


def test_a_gain_in_the_event_windows_paid_for_by_worse_all_hours_is_not_shown_better():
    f = winter_frame(errors={"v3": 0.05})
    windows = event_window_mask(f.index)
    set_error(f, "v3", 0.005, windows)
    c = contrasts_v3(f, COLD_C, n_boot=N_BOOT)
    assert c["terms"]["event_windows"]["state"] == "better" and c["terms"]["all"]["state"] == "worse"
    assert terms_verdict(c) == "not_shown_better"


def test_a_gain_in_the_event_windows_paid_for_by_a_worse_cold_tail_is_not_shown_better():
    f = with_cold(winter_frame(errors={"v3": 0.03}), 168, 6)
    windows, cold = event_window_mask(f.index), (f["temp_forecast"] < COLD_C).to_numpy()
    set_error(f, "v3", 0.005, windows)
    set_error(f, "v3", 0.10, cold, seed=3)
    c = contrasts_v3(f, COLD_C, n_boot=N_BOOT)
    assert c["subsets"]["cold"]["testable"] and c["terms"]["cold"]["state"] == "worse"
    assert c["terms"]["event_windows"]["state"] == "better" and c["terms"]["all"]["state"] != "worse"
    assert terms_verdict(c) == "not_shown_better"


def test_too_few_event_window_hours_are_not_testable():
    """Two weeks of December hold 112 window hours, fewer than 120: not testable, whatever their contrast; a summer
    holds none, and has no window contrast at all."""
    c = contrasts_v3(winter_frame(weeks=2), COLD_C, n_boot=N_BOOT)
    assert c["subsets"]["event_windows"] == {"hours": 112, "weeks": 2, "testable": False}
    assert c["terms"]["event_windows"]["state"] == "better" and terms_verdict(c) == "not_testable"
    summer = contrasts_v3(winter_frame(first="2024-06-03", weeks=4), COLD_C, n_boot=N_BOOT)
    assert summer["subsets"]["event_windows"]["hours"] == 0 and summer["terms"]["event_windows"] is None
    assert terms_verdict(summer) == "not_testable" and summer["subsets"]["cold"]["testable"] is False
    with pytest.raises(ValueError):
        contrasts_v3(winter_frame(), float("nan"), n_boot=N_BOOT)
    with pytest.raises(ValueError):
        contrasts_v3(winter_frame().assign(pred_v3=np.nan), COLD_C, n_boot=N_BOOT)


# ------------------------------------------------------------------------------------------------- the verdicts


@pytest.mark.parametrize(("case", "expected"), [
    (dict(ev="better", ev_testable=False, all_="better", cold="better"), "not_testable"),
    (dict(ev="worse", ev_testable=False), "not_testable"),
    (dict(ev="worse", all_="better", cold="better"), "worse"),
    (dict(ev="worse", cold_testable=False, cold=None), "worse"),
    (dict(ev="not_separated", all_="better", cold="better"), "not_shown_better"),
    (dict(ev="better", all_="worse", cold="better"), "not_shown_better"),
    (dict(ev="better", all_="better", cold="worse"), "not_shown_better"),
    (dict(ev="better", all_="worse", cold_testable=False, cold=None), "not_shown_better"),
    (dict(ev="better", all_="not_separated", cold="not_separated"), "better"),
    (dict(ev="better", all_="better", cold="better"), "better"),
    (dict(ev="better", all_="not_separated", cold_testable=False, cold="worse"), "better_windows_cold_untested"),
    (dict(ev="better", all_="better", cold_testable=False, cold=None), "better_windows_cold_untested"),
])
def test_each_line_of_the_terms_verdict(case, expected):
    assert terms_verdict(judged(**case)) == expected
    assert expected in TERMS_VERDICTS


def test_the_terms_verdict_refuses_an_unknown_state():
    with pytest.raises(ValueError):
        terms_verdict(judged("separated"))
    with pytest.raises(ValueError):
        terms_verdict(judged("better", all_="separated"))


def test_the_family_verdict_is_v2_s_rule_on_the_family_contrast():
    """All common hours decide, the cold tail guards; the event windows play no part."""
    for all_, cold, testable, expected in (("better", "not_separated", True, "better"),
                                           ("better", "worse", True, "not_shown_better"),
                                           ("better", "worse", False, "better_cold_untested"),
                                           ("not_separated", "better", True, "not_shown_better"),
                                           ("worse", "better", True, "worse")):
        for ev in ("worse", "better", None):
            assert family_verdict(judged(ev, all_=all_, cold=cold, cold_testable=testable)) == expected


def test_judge_v3_returns_the_contrasts_the_verdicts_and_the_ranking():
    f = winter_frame()
    j = judge_v3(f, COLD_C, "2023-12-04", "2024-03-31", n_boot=N_BOOT)
    assert set(j) == {"contrasts", "verdict", "ranking"} and j["verdict"] == verdict_v3(j["contrasts"])
    assert [p["winter"] for p in j["ranking"]] == ["2023-24"] and j["ranking"][0]["w"] == 2 * 112
    json.dumps(j)


# -------------------------------------------------------------------------------- status and recommendation


def test_the_status_and_the_recommendation_follow_v3_s_test_of_record():
    """Section 8.5: candidate before the test; unfinished or post hoc as v2's; from a finished clean test, both,
    terms only, family only or neither, "better in the event windows, the cold tail not testable" and the family's
    "better, the cold tail not testable" counting as better and the terms' "not testable" as not. Only a finished
    clean test recommends: keep v2's form at noon when the terms are not better, review the family when it is not,
    the family first."""
    def rec(finished, standing, terms=None, family=None):
        return {"finished": finished, "standing": standing,
                "verdict": None if terms is None else {"terms": terms, "family": family}}

    assert status_v3(None) == "candidate" and recommendation_v3(None) == []
    seen = {"candidate"}
    for standing in STANDINGS:
        assert status_v3(rec(False, standing)) == "unfinished" and recommendation_v3(rec(False, standing)) == []
        seen.add("unfinished")
    for terms in TERMS_VERDICTS:
        for family in VERDICTS:
            assert status_v3(rec(True, "post_hoc", terms, family)) == "post_hoc"
            assert recommendation_v3(rec(True, "post_hoc", terms, family)) == []
            got = status_v3(rec(True, "clean", terms, family))
            t, fm = terms in TERMS_BETTER, family in ("better", "better_cold_untested")
            assert got == {(True, True): "both_better", (True, False): "terms_only", (False, True): "family_only",
                           (False, False): "neither"}[(t, fm)], (terms, family)
            expected = (["review_family"] if not fm else []) + (["keep_v2_noon"] if not t else [])
            assert recommendation_v3(rec(True, "clean", terms, family)) == expected, (terms, family)
            seen |= {got, "post_hoc"}
    assert seen == set(STATUSES_V3) and RECOMMENDATIONS == ("review_family", "keep_v2_noon")
    assert TERMS_BETTER == ("better", "better_windows_cold_untested")
    for bad in (rec(True, "clean"), rec(True, "clean", "better", "better_windows_cold_untested"),
                rec(True, "clean", "better_cold_untested", "better"), rec(True, None, "better", "better"),
                {"finished": None}):
        with pytest.raises(ValueError):
            status_v3(bad)
