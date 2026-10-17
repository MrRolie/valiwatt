"""The replay's clock and the only door to the raw records: nothing stamped after the clock can be read.

Raw records are cut at the clock BEFORE any processing (canonical series, spike scores, lags, drift references), so
no derived value is ever computed with a later record in view.
"""

from __future__ import annotations

import numpy as np
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
    """Raw records (one row each, stamped `ts_utc`) behind the clock. The NBM forecast's runs are fed the same way,
    each point stamped at its run's availability (docs/plan-v2.md, section 15: the clock and its raw feeds are reused
    for the forecast)."""

    def __init__(self, records: pd.DataFrame, clock: Clock, name: str):
        self._records = records.sort_values("ts_utc", kind="stable").reset_index(drop=True)
        self._clock = clock
        self.name = name
        self._groups: dict[str, tuple] = {}

    def _visible_count(self) -> int:
        return int(self._records["ts_utc"].searchsorted(self._clock.now, side="right"))

    def visible(self) -> pd.DataFrame:
        """Every record stamped at or before the clock, in arrival order."""
        n = self._visible_count()
        out = self._records.iloc[:n].copy()
        require_before_clock(pd.DatetimeIndex(out["ts_utc"]), self._clock.now, self.name)
        return out

    def _by(self, key: str) -> tuple:
        """The records grouped by `key`, once: each value's first position in arrival order (ascending), the value's
        rank among all values, and the positions of its records."""
        if key not in self._groups:
            codes, values = pd.factorize(self._records[key], sort=True)  # sorted: a code is the value's rank
            if (codes < 0).any():
                raise LookaheadError(f"{self.name}: {int((codes < 0).sum())} records have no {key}")
            order = np.argsort(codes, kind="stable")
            bounds = np.flatnonzero(np.diff(codes[order])) + 1
            positions = np.split(order, bounds)
            ranks = codes[order][np.r_[0, bounds]] if len(order) else np.array([], dtype=int)
            first = np.array([p[0] for p in positions], dtype=np.int64)
            by_first = np.argsort(first, kind="stable")
            self._groups[key] = (first[by_first], ranks[by_first], [positions[i] for i in by_first])
        return self._groups[key]

    def freshest(self, key: str, n: int) -> pd.DataFrame:
        """The visible records of the `n` largest distinct values of `key` among the values that have a visible record
        (the NBM runs by run identity, `runtime`, whatever their age), in arrival order: read through the clock, as
        `visible` reads, never from the whole frame. A record of such a value stamped after the clock stays out."""
        k = self._visible_count()
        first, ranks, positions = self._by(key)
        m = int(np.searchsorted(first, k, side="left"))  # the values with at least one record visible
        chosen = np.arange(m) if m <= n else np.argpartition(ranks[:m], m - n)[m - n:]
        rows = np.sort(np.concatenate([positions[i][positions[i] < k] for i in chosen])) if len(chosen) else \
            np.array([], dtype=np.int64)
        out = self._records.iloc[rows].copy()
        require_before_clock(pd.DatetimeIndex(out["ts_utc"]), self._clock.now, self.name)
        return out

    def window(self, first: pd.Timestamp, last: pd.Timestamp) -> pd.DataFrame:
        """Records stamped in [first, last]; asking for any time after the clock is refused."""
        if pd.Timestamp(last) > self._clock.now:
            raise LookaheadError(f"{self.name}: read up to {last} asked at clock {self._clock.now}")
        v = self.visible()
        return v[(v["ts_utc"] >= first) & (v["ts_utc"] <= last)]
