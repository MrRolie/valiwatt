"""Study window and station panel."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass

import pandas as pd

from valiwatt.paths import CONFIG

# Hydro-Québec labels each hour at its end, in UTC: the first hour of 2019 (00:00-01:00 EST) is 2019-01-01T06:00Z and
# the last hour of 2024 is 2025-01-01T05:00Z.
DEMAND_FIRST = pd.Timestamp("2019-01-01T06:00Z")
DEMAND_LAST = pd.Timestamp("2025-01-01T05:00Z")

# Design-informing analysis (station representativeness, descriptive figures) uses 2019-2023 only. 2024 is replayed
# later as production with a frozen model, so nothing about 2024 may shape a design choice.
DESIGN_LAST = pd.Timestamp("2024-01-01T05:00Z")

# The residual check of the report fits a weather model on this year, the first of the design window.
RESIDUAL_FIT_YEAR = 2019
# The brief names the 2020 shift as a test case; these are the months the report tabulates for it (the first months of
# the 2020 public-health restrictions). Whether demand actually shifted is read from the run, never assumed.
SHIFT_2020_MONTHS = ("2020-03", "2020-04", "2020-05", "2020-06")
# The brief names the February 2023 cold peak as a test case; the report says whether the run's busiest hour falls
# in this month.
COLD_PEAK_MONTH = "2023-02"

# Local standard time in Québec's populated south (Eastern Standard Time, no daylight saving).
LST_OFFSET = pd.Timedelta(hours=-5)
LOCAL_TZ = "America/Toronto"


@dataclass(frozen=True)
class Station:
    key: str
    name: str
    climate_id: str
    station_id: int
    centre: str
    primary: bool
    reason: str
    reason_fr: str


def stations() -> list[Station]:
    with open(CONFIG / "stations.toml", "rb") as fh:
        raw = tomllib.load(fh)
    panel = [Station(**s) for s in raw["station"]]
    if sum(s.primary for s in panel) != 1:
        raise ValueError("stations.toml must mark exactly one primary station")
    if len({s.key for s in panel}) != len(panel):
        raise ValueError("stations.toml has duplicate keys")
    return panel


def fetch_months() -> list[tuple[int, int]]:
    """UTC calendar months covering the demand window (ECCC's UTC export is per UTC month)."""
    months = pd.period_range(DEMAND_FIRST.tz_convert(None), DEMAND_LAST.tz_convert(None), freq="M")
    return [(p.year, p.month) for p in months]
