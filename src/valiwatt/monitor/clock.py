"""The replay's clock and the only door to the raw records: nothing stamped after the clock can be read.

Raw records are cut at the clock BEFORE any processing (canonical series, spike scores, lags, drift references), so
no derived value is ever computed with a later record in view.
"""

from __future__ import annotations

import pandas as pd

from valiwatt.forecast.guards import LookaheadError, require_before_clock


class Clock:
    def __init__(self, start: pd.Timestamp):
        self._now = pd.Timestamp(start)

    @property
    def now(self) -> pd.Timestamp:
        return self._now

    def advance(self, t: pd.Timestamp) -> None:
        t = pd.Timestamp(t)
        if t < self._now:
            raise LookaheadError(f"the clock cannot go back from {self._now} to {t}")
        self._now = t


class RawFeed:
    """Raw records (one row each, stamped `ts_utc`) behind the clock."""

    def __init__(self, records: pd.DataFrame, clock: Clock, name: str):
        self._records = records.sort_values("ts_utc", kind="stable").reset_index(drop=True)
        self._clock = clock
        self.name = name

    def visible(self) -> pd.DataFrame:
        """Every record stamped at or before the clock, in arrival order."""
        n = int(self._records["ts_utc"].searchsorted(self._clock.now, side="right"))
        out = self._records.iloc[:n].copy()
        require_before_clock(pd.DatetimeIndex(out["ts_utc"]), self._clock.now, self.name)
        return out

    def window(self, first: pd.Timestamp, last: pd.Timestamp) -> pd.DataFrame:
        """Records stamped in [first, last]; asking for any time after the clock is refused."""
        if pd.Timestamp(last) > self._clock.now:
            raise LookaheadError(f"{self.name}: read up to {last} asked at clock {self._clock.now}")
        v = self.visible()
        return v[(v["ts_utc"] >= first) & (v["ts_utc"] <= last)]
