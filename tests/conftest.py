import numpy as np
import pandas as pd
import pytest

from valiwatt.config import Station


def synth_demand(first: str, last: str, seed: int = 0, step_at: str | None = None, step_mw: float = 0.0) -> pd.DataFrame:
    """Raw-shaped synthetic demand: a daily cycle, mild noise, and an optional level step."""
    idx = pd.date_range(pd.Timestamp(first), pd.Timestamp(last), freq="h")
    h = np.arange(len(idx))
    rng = np.random.default_rng(seed)
    mw = 20_000 + 3_000 * np.sin(2 * np.pi * h / 24) + rng.normal(0, 40, len(idx))
    if step_at:
        mw = mw + np.where(idx >= pd.Timestamp(step_at), step_mw, 0.0)
    return pd.DataFrame({"ts_utc": idx, "mw": mw, "source_line": np.arange(2, len(idx) + 2)})


def synth_obs(first: str, last: str, seed: int = 0, climate_id: str = "0000000") -> pd.DataFrame:
    """Station-shaped synthetic hourly observations."""
    idx = pd.date_range(pd.Timestamp(first), pd.Timestamp(last), freq="h")
    h = np.arange(len(idx))
    rng = np.random.default_rng(seed)
    temp = 5 - 15 * np.cos(2 * np.pi * h / (24 * 365.25)) + 4 * np.sin(2 * np.pi * h / 24) + rng.normal(0, 0.4, len(idx))
    return pd.DataFrame({
        "ts_utc": idx, "temp_c": np.round(temp, 1), "temp_flag": "", "dew_point_c": np.round(temp - 4, 1),
        "rel_hum_pct": 70.0, "wind_kmh": 10.0, "climate_id": climate_id,
    })


@pytest.fixture
def station():
    return Station(key="test", name="TEST A", climate_id="0000000", station_id=1, centre="Test", primary=True,
                   reason="test", reason_fr="test")
