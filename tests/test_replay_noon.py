"""v3's replay at noon (docs/plan-v3.md, sections 2, 4.2, 9.3 and 12): each day issued at its noon origin, D-1 17:00
UTC, carrying its own 24 labels and scored at D+1 06:00 UTC; its verdict the one validation computes, on the raw records
stamped in its own hours; a run that became available after the origin never feeding the day; the spike scale cut at
the year's first label whatever the origin; v2's form at midnight issued at D 05:00 UTC inside the same replay; the
comparison models at noon forecasting only v3's usable rows; v3's own gate in place of v2's; and v3's runs invisible
to v2's ledger and starts.

On the synthetic noon world of tests/v3_world.py, two weeks of January 2024."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import v3_world as vw
from valiwatt.config import data_last
from valiwatt.forecast.issue import MIDNIGHT
from valiwatt.monitor import replay as rp
from valiwatt.monitor import run as mrun
from valiwatt.quality import days as qdays

H, DAY = pd.Timedelta(hours=1), pd.Timedelta(days=1)


def utc(text: str) -> pd.Timestamp:
    return pd.Timestamp(text).tz_convert("UTC") if pd.Timestamp(text).tzinfo else pd.Timestamp(text, tz="UTC")


def same(a: pd.Series, b: pd.Series) -> np.ndarray:
    """Equal bit for bit, NaN equal to NaN."""
    a, b = a.to_numpy(dtype=float), b.to_numpy(dtype=float)
    return (a == b) | (np.isnan(a) & np.isnan(b))


def recording(monkeypatch) -> tuple[list, list]:
    """Every issue() and score_day() of a replay, with the clock at the call."""
    issued, scored = [], []
    real_issue, real_score = rp.issue, rp.score_day

    def rec_issue(state, model, day, **kwargs):
        out = real_issue(state, model, day, **kwargs)
        issued.append({"issue": kwargs.get("issue", MIDNIGHT).name, "day": pd.Timestamp(day),
                       "at": state.clock.now, "labels": list(out.index)})
        return out

    def rec_score(state, day, frame, final=False, **kwargs):
        s, rec = real_score(state, day, frame, final=final, **kwargs)
        scored.append({"day": pd.Timestamp(day), "at": state.clock.now, "labels": list(s.index), "final": final})
        return s, rec

    monkeypatch.setattr(rp, "issue", rec_issue)
    monkeypatch.setattr(rp, "score_day", rec_score)
    return issued, scored


# ------------------------------------------------------------------------------------------------------ the clocks


def test_a_noon_day_is_issued_at_noon_carries_its_24_labels_and_is_scored_at_d_plus_1_0600(monkeypatch):
    """Section 2 and section 12, "The clocks": day D is issued at D-1 17:00 UTC; it carries exactly the 24 labels D
    06:00 to D+1 05:00 UTC; it is scored at D+1 06:00 UTC, never at a time derived from D+1's origin; no target is
    blank because of the clock."""
    issued, scored = recording(monkeypatch)
    r = vw.noon_replay(2024)
    days = pd.date_range(*vw.DAYS[2024])
    noon = [x for x in issued if x["issue"] == "noon"]
    assert [x["day"] for x in noon] == list(days)
    for x in noon:
        d = x["day"].date()
        assert x["at"] == utc(f"{(x['day'] - DAY).date()}T17:00"), d
        assert x["labels"] == list(pd.date_range(utc(f"{d}T06:00"), utc(f"{(x['day'] + DAY).date()}T05:00"),
                                                 freq="h")), d
    assert [x["day"] for x in scored] == list(days)
    for x in scored:
        assert x["at"] == utc(f"{(x['day'] + DAY).date()}T06:00") and not x["final"], x["day"]
        assert len(x["labels"]) == 24 and x["labels"][0] == utc(f"{x['day'].date()}T06:00")
    assert r.issue == "noon" and len(r.hourly) == 24 * len(days)
    assert r.hourly["target"].notna().all() and r.hourly["forecast"].notna().all()
    assert (r.forecast_record["origin"] == pd.DatetimeIndex(days).tz_localize("UTC") - 7 * H).all()


def test_the_noon_replay_judges_each_day_as_validation_does():
    """Section 12, "The clocks": a day's data-quality verdict reads only the raw records stamped in its own hours. A
    demand record out of bounds stamped at D-1 18:00 UTC (after D's noon origin, in D-1's hours) fails D-1 alone; a lag
    day with 19 usable hours fails the day it feeds. Every day's problems in the replay are exactly those
    quality.days.day_verdicts gives on the same records, the replay's own forecast record and its lag days' means:
    the one per-day function validation's verdicts call."""
    w = vw.world()
    d = pd.Timestamp("2024-01-09")
    late = utc("2024-01-08T18:00")
    raw = w.raw.copy()
    raw.loc[raw["ts_utc"] == late, "mw"] = 70_000.0
    gap = pd.date_range(utc("2024-01-04T06:00"), periods=5, freq="h")  # 2024-01-04 keeps 19 usable hours
    obs = w.obs[~w.obs["ts_utc"].isin(gap)].reset_index(drop=True)
    r = vw.noon_replay(2024, raw=raw, obs=obs)
    replayed = {x["day"]: x["problems"] for x in r.days}
    first, last = (pd.Timestamp(x) for x in vw.DAYS[2024])
    lag_day = r.hourly.groupby("day")["lagday_observed"].first()
    expected = qdays.day_verdicts(raw, obs, first, last, record=r.forecast_record, lag_day=lag_day)
    assert replayed == expected
    assert [p["kind"] for p in replayed[d - DAY]] == ["demand_out_of_bounds"] and replayed[d] == []
    assert replayed[pd.Timestamp("2024-01-06")] == [{"kind": "lag_day_temperature_missing"}]
    assert replayed[pd.Timestamp("2024-01-04")] == []


# ------------------------------------------------------------------------------------------------- the noon origin


def _late_run(day: pd.Timestamp, runtime: pd.Timestamp) -> pd.DataFrame:
    """A run whose points sit exactly on day D's nine bracket times (so it covers D and no other day), at 100 °F (in
    the bounds, absurd for January), wind 10 knots, available two hours after its run time."""
    edge = utc(f"{day.date()}T05:00")
    ftimes = [edge + h * H for h in range(1, 26, 3)]
    return pd.DataFrame({"runtime": runtime, "ftime": ftimes, "tmp_f": 100.0, "wsp_kt": 10.0,
                         "available_utc": runtime + 2 * H})


@pytest.mark.parametrize("runtime, feeds", [("2024-01-08T15:01", False), ("2024-01-08T14:59", True)],
                         ids=["after_origin", "control"])
def test_the_replay_never_feeds_a_day_a_run_available_after_its_noon_origin(runtime, feeds):
    """Section 12, "The noon origin": a run available at D-1 17:01 UTC (run time 15:01) is never used for D, even with
    absurd values, and the next-freshest run (D-1 13:00, available 15:00) feeds it. Control: the same run available at
    16:59 feeds D."""
    w = vw.world()
    d = pd.Timestamp("2024-01-09")
    rt = utc(runtime)
    runs = pd.concat([w.runs, _late_run(d, rt)], ignore_index=True).sort_values(["runtime", "ftime"],
                                                                                 kind="stable").reset_index(drop=True)
    r = vw.noon_replay(2024, runs=runs, midnight=False, comparators=False)
    fed = r.forecast_record.loc[d, "runtime"]
    hot = r.hourly.loc[r.hourly["day"] == d, "temp_forecast"]
    if feeds:
        assert fed == rt and np.allclose(hot, (100.0 - 32) * 5 / 9)
    else:
        assert fed == utc("2024-01-08T13:00") and hot.max() < 20
        assert r.forecast_record.loc[d, "available_utc"] <= utc("2024-01-08T17:00")
    others = r.forecast_record.drop(index=d)["runtime"]
    assert not (others == rt).any()  # it covers no other day


# ------------------------------------------------------------------------------------------------- the spike scale


def test_the_noon_replay_s_spike_scale_is_cut_at_the_year_s_first_label(monkeypatch):
    """Section 9.3 and section 12, "The spike scale": the replay's spike scale keeps v2's cut at the last label of the
    year before, 2024-01-01 05:00 UTC, on every rebuild of the canonical series, though the first day is issued at
    2023-12-31 17:00 UTC, twelve hours before it."""
    cuts = []
    real = rp.canonical

    def recorder(raw, raw_t, now, scale_last):
        cuts.append(pd.Timestamp(scale_last))
        return real(raw, raw_t, now, scale_last)

    monkeypatch.setattr(rp, "canonical", recorder)
    vw.noon_replay(2024, midnight=False, comparators=False, days=("2024-01-01", "2024-01-03"))
    assert cuts and set(cuts) == {utc("2024-01-01T05:00")} == {data_last(2023)}


# ---------------------------------------------------------------------------------- v2's form at midnight, at noon


def test_v2_s_form_at_midnight_is_issued_at_d_0500_inside_the_noon_replay(monkeypatch):
    """Section 4.2: v2's form at midnight is issued at D 05:00 UTC from the same feeds and the same clock as the noon
    replay; its forecasts, reasons and forecast record are, bit for bit, those of v2's own replay of that model on the
    same days; the noon forecasts are not changed by it."""
    issued, _ = recording(monkeypatch)
    r = vw.noon_replay(2024)
    days = pd.date_range(*vw.DAYS[2024])
    mid = [x for x in issued if x["issue"] == "midnight"]
    assert [x["day"] for x in mid] == list(days)
    assert all(x["at"] == utc(f"{x['day'].date()}T05:00") for x in mid)
    monkeypatch.undo()
    w, fz = vw.world(), vw.frozen(2024)
    v2 = rp.replay(w.raw, w.obs, fz.comparators["v2_midnight"], fz.thresholds, fz.reference, *vw.DAYS[2024],
                   data_last(2024), runs=w.runs, gate=vw.no_gate)
    assert same(r.hourly["pred_v2_midnight"], v2.hourly["forecast"]).all()
    assert (r.hourly["why_v2_midnight"] == v2.hourly["not_predicted_because"]).all()
    assert r.hourly["lower_v2_midnight"].isna().all()  # it issues no interval (section 7)
    pd.testing.assert_frame_equal(r.midnight_record, v2.forecast_record)
    alone = vw.noon_replay(2024, midnight=False)
    assert same(r.hourly["forecast"], alone.hourly["forecast"]).all()
    assert alone.midnight_record is None and "pred_v2_midnight" not in alone.hourly


def test_the_noon_comparators_forecast_only_v3_s_usable_rows():
    """Sections 4.2 and 5: a day whose lag day holds 19 usable hours has no v3 forecast, each hour saying why; v2's and
    v1's forms at noon, scored after the loop on the recorded inputs, forecast none of its hours either, and exactly
    v3's hours elsewhere; v2's form at midnight reads no lag day and forecasts them."""
    w = vw.world()
    gap = pd.date_range(utc("2024-01-04T06:00"), periods=5, freq="h")
    r = vw.noon_replay(2024, obs=w.obs[~w.obs["ts_utc"].isin(gap)].reset_index(drop=True))
    h = r.hourly
    hole = h["day"] == pd.Timestamp("2024-01-06")
    assert h.loc[hole, "forecast"].isna().all() and h.loc[~hole, "forecast"].notna().all()
    assert (h.loc[hole, "not_predicted_because"] == "lagday_observed (lag_day_temperature_missing)").all()
    for key in ("v2_noon", "v1_noon"):
        assert (h[f"pred_{key}"].isna() == h["forecast"].isna()).all(), key
    assert h.loc[hole, "pred_v2_midnight"].notna().all()


# ------------------------------------------------------------------------------------------------------- the gate


def test_a_v3_replay_never_reaches_v2_s_gate(monkeypatch):
    """The replay's gate is the caller's: given, it is called once, before any feed is read, with (last_day, data_end,
    departure, run_id), and v2's gates (held_out_gate for 2024, check_2025_run for 2025) are never called, though the
    replay reads past v2's design window. Control: without a gate, v2's routing is reached."""
    reads, calls = [], []

    def v2_gate(*args, **kwargs):
        raise AssertionError("v2's gate was reached")

    real = rp.RawFeed.visible

    def visible(self, *args, **kwargs):
        reads.append(self.name)
        return real(self, *args, **kwargs)

    def v3_gate(*args):
        calls.append((args, list(reads)))

    monkeypatch.setattr(mrun, "held_out_gate", v2_gate)
    monkeypatch.setattr(mrun, "check_2025_run", v2_gate)
    monkeypatch.setattr(rp.RawFeed, "visible", visible)
    vw.noon_replay(2024, gate=v3_gate, departure=True, run_id="r" * 32, midnight=False, comparators=False,
                   days=("2024-01-01", "2024-01-02"))
    assert calls == [(("2024-01-02", data_last(2024), True, "r" * 32), [])]
    assert reads  # the replay read its feeds after the gate
    with pytest.raises(AssertionError, match="v2's gate was reached"):
        vw.noon_replay(2024, gate=None, midnight=False, comparators=False, days=("2024-01-01", "2024-01-02"))


def test_v3_runs_are_invisible_to_v2_s_ledger_and_starts(tmp_path, monkeypatch):
    """D3: a finished v3 replay of 2025 under v3's tag (kind run_v3.KIND) in the store is no replay to any of v2's
    readers, asked under either tag: v2's ledger, its starts, its moved-tag scan. Control: v3's own reader of starts
    sees it."""
    import records_v3_world as rw
    from valiwatt import records_v3
    from valiwatt.config import V3_FREEZE_TAG
    from valiwatt.monitor import run_v3
    from valiwatt.report import ledger

    s = rw.scratch_store(tmp_path, monkeypatch)
    rid = rw.log_replay_v3(s.repo, 2025, kind=run_v3.KIND)
    assert mrun._replay_runs() == [] and ledger.from_store() == []
    for tag in ("rules-frozen-v2", V3_FREEZE_TAG):
        assert mrun.replay_starts(2025, tag=tag, ledger={"replays": []}) == []
        assert mrun.earlier_freeze_commits(tag) == {}
    assert [x["run_id"] for x in records_v3.replay_starts_v3(2025, ledger={"replays": []})] == [rid]
