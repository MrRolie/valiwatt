"""Study window and station panel."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass

import pandas as pd

from valiwatt.paths import CONFIG

# Hydro-Québec labels each hour at its end, in UTC: the first hour of 2019 (00:00-01:00 EST) is 2019-01-01T06:00Z and
# the last hour of 2024 is 2025-01-01T05:00Z.
DEMAND_FIRST = pd.Timestamp("2019-01-01T06:00Z")


def data_last(year: int) -> pd.Timestamp:
    """The hour-ending UTC label of `year`'s last hour (23:00-24:00 EST on 31 December): the end of a data window that
    ends with that year."""
    return pd.Timestamp(f"{int(year) + 1}-01-01T05:00Z")


DEMAND_LAST = data_last(2024)

# The data window ends with the last complete year fetched (docs/plan-v2.md, section 11.4): 2024 by default; 2025 only
# behind the v2 freeze (section 11.3), once Hydro-Québec publishes it. The design window below never moves.
THROUGH_DEFAULT = 2024
THROUGH_YEARS = (2024, 2025)
HELD_OUT_YEAR = 2025
# Each replayed year's evaluation label. EVALUATION[2025] is the label of a clean test of record only; a post-hoc test
# of record is "post_hoc" and every other 2025 entry "seen_by_developers" (docs/plan-v2.md, sections 10.5, 11.3.5).
EVALUATION = {2023: "rehearsal", 2024: "seen_by_developers", 2025: "held_out"}
# The label of every 2020-2023 validation result: in sample for the choice of the model family and its input.
IN_SAMPLE_LABEL = "in_sample_for_choice"

# The freezes. "years": the replayed years each freeze gates; "heading": its departures section in AGENTS.md; "end":
# the tag that ends its checked range (None: open). On the v3 branch v2's range is closed at `v3-fork`
# (docs/plan-v3.md, section 10.1), and v3's freeze is listed under its own tag, which no `rules-frozen*` pattern
# matches (section 10.2), so v2's freeze check never sees it.
FREEZE_TAG = "rules-frozen-v2"
FREEZES = {"rules-frozen": {"heading": "## Departures from the frozen monitoring rules", "end": "v1",
                            "years": (2024,)},
           "rules-frozen-v2": {"heading": "## Departures from the v2 frozen rules", "end": "v3-fork",
                               "years": (2024, 2025)},
           "v3-rules-frozen": {"heading": "## Departures from the v3 frozen rules", "end": None,
                               "years": (2024, 2025)}}
# Every v2 command that computes on real data refuses unless this tag exists and is an ancestor of the head
# (docs/plan-v2.md, section 10.4).
FIRST_RUN_TAG = "v2-first-run"

# v3 (docs/plan-v3.md, sections 3.1, 6, 10.2): its design window ends with 2024; every v3 command that computes on
# real data refuses unless V3_FIRST_RUN_TAG exists and is an ancestor of the head; the thresholds of each replayed
# year come from these validation years.
V3_DESIGN_LAST = data_last(2024)
V3_FIRST_RUN_TAG = "v3-first-run"
V3_THRESHOLD_YEARS = {2024: (2021, 2023), 2025: (2022, 2024)}
# v3's other tags and records (sections 10.2 and 10.3): its freeze; the freeze tag every v3 run made before that tag
# exists carries; the end of v2's checked range on the v3 branch; the pre-registration; v3's own replay ledger; and
# the freeze record, written once before any 2025 record reaches the machine.
V3_FREEZE_TAG = "v3-rules-frozen"
V3_PRE_FREEZE_TAG = "v3-pre-freeze"
V3_FORK_TAG = "v3-fork"
V3_PLAN_TAG = "plan-v3"
V3_LEDGER = "docs/replay-ledger-v3.json"
V3_FREEZE_RECORD = "docs/freeze-v3.json"

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


def fetch_months(through: int = THROUGH_DEFAULT) -> list[tuple[int, int]]:
    """UTC calendar months covering the demand window that ends with `through` (ECCC's UTC export is per UTC month)."""
    months = pd.period_range(DEMAND_FIRST.tz_convert(None), data_last(through).tz_convert(None), freq="M")
    return [(p.year, p.month) for p in months]
