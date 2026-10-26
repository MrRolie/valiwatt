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


def utc(ts) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def synth_temperature_c(ts: pd.DatetimeIndex, first) -> np.ndarray:
    """synth_obs's temperature (without its noise) at the UTC instants `ts`, its hours counted from `first`."""
    h = np.asarray((pd.DatetimeIndex(ts) - utc(first)) / pd.Timedelta(hours=1), dtype=float)
    return 5 - 15 * np.cos(2 * np.pi * h / (24 * 365.25)) + 4 * np.sin(2 * np.pi * h / 24)


RUN_HOURS = (1, 7, 13, 19)


def synth_wind_kt(ts: pd.DatetimeIndex, first, seed: int = 0) -> np.ndarray:
    """A synthetic wind in whole knots at the UTC instants `ts` (hours counted from `first`): a daily cycle around 10
    knots plus noise from its own generator (seed + 1), never below 0, never above 29 (the archive's largest)."""
    h = np.asarray((pd.DatetimeIndex(ts) - utc(first)) / pd.Timedelta(hours=1), dtype=float)
    noise = np.random.default_rng(seed + 1).normal(0, 3, len(h))
    return np.clip(np.round(10 + 5 * np.sin(2 * np.pi * h / 24) + noise), 0, 29).astype(float)


def synth_runs(first: str, last: str, seed: int = 0, wind: bool = False) -> pd.DataFrame:
    """NBM-shaped synthetic runs, as `sources.nbm.load_runs` gives them: four runs a day at 01, 07, 13 and 19 UTC on
    every UTC day from `first`'s to `last`'s, each with a point every 3 hours of the UTC day (00, 03, ... 21) after its
    run time and out to 72 hours after it; values are synth_obs's temperature (hours counted from `first`) plus noise,
    in whole °F. wind (opt-in, docs/plan-v3.md section 3.1): a fourth column wsp_kt in whole knots (synth_wind_kt),
    from its own generator, so every other column is the same to the bit with or without it."""
    first_day, last_day = (utc(t).tz_convert(None).floor("D") for t in (first, last))
    runtimes = pd.DatetimeIndex([d + pd.Timedelta(hours=h) for d in pd.date_range(first_day, last_day, freq="D")
                                 for h in RUN_HOURS]).as_unit("ns")
    first_point = (runtimes + pd.Timedelta(hours=3)).floor("3h")  # the first 3-hour mark after the run time
    steps = np.arange(24) * np.timedelta64(3, "h")
    rt = np.repeat(runtimes.to_numpy(), len(steps))
    ft = np.repeat(first_point.to_numpy(), len(steps)) + np.tile(steps, len(runtimes))
    keep = (ft - rt) <= np.timedelta64(72, "h")
    rt, ft = pd.DatetimeIndex(rt[keep]).tz_localize("UTC"), pd.DatetimeIndex(ft[keep]).tz_localize("UTC")
    rng = np.random.default_rng(seed)
    c = synth_temperature_c(ft, first) + rng.normal(0, 0.5, len(ft))
    out = pd.DataFrame({"runtime": rt, "ftime": ft, "tmp_f": np.round(c * 9 / 5 + 32).astype(float)})
    if wind:
        out["wsp_kt"] = synth_wind_kt(ft, first, seed)
    out = out.sort_values(["runtime", "ftime"], kind="stable").reset_index(drop=True)
    out.attrs["identical_duplicates"] = 0
    return out


@pytest.fixture
def station():
    return Station(key="test", name="TEST A", climate_id="0000000", station_id=1, centre="Test", primary=True,
                   reason="test", reason_fr="test")
