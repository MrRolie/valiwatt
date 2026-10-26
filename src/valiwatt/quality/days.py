"""The day-level data-quality verdict of a noon-issued day (docs/plan-v3.md, sections 2, 5, 9.3 and 12 "The clocks").

v2's verdict (monitor/rules.py `day_problems`, applied by monitor/replay.py `day_verdicts`) cuts each day's raw records
at its origin, which at v2's clock is the day's edge. At noon the origin is twelve hours earlier, and the records
stamped between it and the edge are D-1's: a cut at the origin would judge D on D-1's evening. So a noon day is judged
on the raw records stamped in its own hours only, D 06:00 to D+1 05:00 UTC (forecast/issue.py `day_labels`), whatever
the issue time; the problems are v2's (`day_problems`, unchanged), plus:
- `forecast_missing`, with the noon feed's reason, when no compliant covering run fed the day (the covering rule's
  wind clause included: a run with a wind point outside its bounds does not cover, section 3.2);
- `lag_day_temperature_missing`, when the day's lag day's mean is missing (section 5: such a day has no noon forecast
  and fails its verdict).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from valiwatt.forecast.issue import LAG_DAY_REASON, day_labels

H = pd.Timedelta(hours=1)


def day_problems_noon(hours: pd.DatetimeIndex, raw: pd.DataFrame, raw_t: pd.DataFrame, status: pd.Series,
                      temperature: pd.Series, record: dict | None, lag_day_mean: float | None) -> list[dict]:
    """One noon day's problems (none: the day passes). Pure. The one per-day body of a noon day's verdict: validation's
    `day_verdicts` and the noon replay's scoring (monitor/replay.py `score_day`) both call it, so the two judge a day by
    the same function, never a copy.

    hours: the day's 24 labels (`day_labels`); raw, raw_t: the raw demand and station records stamped in them, after
    the day's edge and up to its last label; status, temperature: the canonical demand status and temperature, read on
    `hours`. The problems are v2's (`day_problems`, with `record` as its forecast record: {"runtime", "reason"}, a
    reason other than "" failing `forecast_missing`; None: v1's criteria), plus `lag_day_temperature_missing` when
    `lag_day_mean` is given and not a finite number (None: not judged)."""
    from valiwatt.monitor.rules import day_problems

    problems = day_problems(hours, raw, raw_t, status, temperature, forecast_record=record)
    if lag_day_mean is not None and not np.isfinite(float(lag_day_mean)):
        problems.append({"kind": LAG_DAY_REASON})
    return problems


def day_verdicts(raw_demand: pd.DataFrame, raw_temperature: pd.DataFrame, first_day, last_day, *,
                 record: pd.DataFrame | None = None, lag_day: pd.Series | None = None) -> dict[pd.Timestamp, list]:
    """The day-level verdict of every local standard day in [first_day, last_day]: day -> its problems (an empty
    list: the day passes). Pure.

    raw_demand, raw_temperature: the raw records (ts_utc, mw) and (ts_utc, temp_c, ...), as v2's `day_verdicts` takes
    them; records after the last day's last hour are never read. Each day reads the records stamped after its edge
    (D 05:00 UTC) and up to its last label, and the canonical status and temperature on its own labels, built once from
    the cut at the last day's last hour (as v2's `day_verdicts` builds them). record: the noon feed's record
    (features.IssuedForecast.record, indexed by day); lag_day: the lag day's mean of each day (indexed by day, NaN when
    missing). A day absent from `record` or `lag_day` when given is refused (KeyError): its verdict cannot be read."""
    from valiwatt.monitor.replay import canonical

    days = pd.date_range(pd.Timestamp(first_day), pd.Timestamp(last_day), freq="D")
    end = day_labels(days[-1])[-1]
    raw, raw_t = (r[r["ts_utc"] <= end].sort_values("ts_utc", kind="stable").reset_index(drop=True)
                  for r in (raw_demand, raw_temperature))
    frame, temp = canonical(raw, raw_t, end, end)
    out = {}
    for d in days:
        hours = day_labels(d)
        cut = [int(r["ts_utc"].searchsorted(x, side="right")) for r in (raw, raw_t) for x in (hours[0] - H, hours[-1])]
        rec = None
        if record is not None:
            row = record.loc[d]
            rec = {"runtime": row["runtime"], "reason": str(row["reason"])}
        out[d] = day_problems_noon(hours, raw.iloc[cut[0]:cut[1]], raw_t.iloc[cut[2]:cut[3]], frame["status"], temp,
                                   rec, None if lag_day is None else lag_day.loc[d])
    return out
