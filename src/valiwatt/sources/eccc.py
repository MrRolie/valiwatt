"""ECCC hourly station observations from climate.weather.gc.ca (bulk CSV, one station-month per request).

The export is requested in UTC (`time=UTC`). A request without an explicit time zone returns rows whose value columns
are empty and whose date header is blank, so every response is verified before it is stored. An hour with no
observation at all comes as a row of only its first 9 fields (station identity and time); those are kept as missing
observations and counted. Temperature at a UTC hour is the observation at that instant, unlike Hydro-Québec's demand,
which is the mean over the hour ending then.

The window ends with 2024 by default; with 2025 (fetched only behind the v2 freeze, docs/plan-v2.md, section 11.3.1)
the months of 2025 are fetched beside the others, and `force` is refused: it would download again the 2019-2024
months v2's first 2024 replay read. A month is fetched again by removing its file.
"""

from __future__ import annotations

import calendar
import csv
import io
import json

import pandas as pd

from valiwatt.config import THROUGH_DEFAULT, Station, fetch_months
from valiwatt.paths import RAW_ECCC, rel
from valiwatt.sources.http import FetchRefused, get, require_fetchable, sha256_bytes, sha256_file, utc_now, write_json

BULK_URL = "https://climate.weather.gc.ca/climate_data/bulk_data_e.html"
DATE_COL = "Date/Time (UTC)"
# Station identity and time: longitude, latitude, name, climate ID, date/time, year, month, day, time.
IDENTITY_FIELDS = 9
COLUMNS = {
    DATE_COL: "ts_utc",
    "Temp (°C)": "temp_c",
    "Temp Flag": "temp_flag",
    "Dew Point Temp (°C)": "dew_point_c",
    "Rel Hum (%)": "rel_hum_pct",
    "Wind Spd (km/h)": "wind_kmh",
    "Climate ID": "climate_id",
}


def month_params(station_id: int, year: int, month: int) -> dict:
    return {
        "format": "csv",
        "stationID": station_id,
        "Year": year,
        "Month": month,
        "Day": 1,
        "time": "UTC",
        "timeframe": 1,
        "submit": "Download Data",
    }


def month_path(station: Station, year: int, month: int):
    return RAW_ECCC / station.climate_id / f"{year:04d}-{month:02d}.csv"


def verify_month(text: str, climate_id: str, year: int, month: int) -> tuple[int, int]:
    """Refuse a station-month that is not a complete UTC hourly table for this station and month.

    Returns (rows, rows with no observation)."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    if not rows:
        raise FetchRefused("empty response")
    header = rows[0]
    if DATE_COL not in header:
        raise FetchRefused(f"no {DATE_COL!r} column (time zone not applied); header starts {header[:6]}")
    missing = [c for c in COLUMNS if c not in header]
    if missing:
        raise FetchRefused(f"missing columns {missing}")
    body = rows[1:]
    malformed = [i for i, r in enumerate(body, start=2) if len(r) not in (len(header), IDENTITY_FIELDS)]
    if malformed:
        raise FetchRefused(f"{len(malformed)} rows have neither {len(header)} nor {IDENTITY_FIELDS} fields "
                           f"(first at line {malformed[0]})")
    if all(len(r) == IDENTITY_FIELDS for r in body):
        raise FetchRefused("no row carries observations (identity fields only)")
    no_obs = sum(len(r) == IDENTITY_FIELDS for r in body)
    hours = calendar.monthrange(year, month)[1] * 24
    if len(body) != hours:
        raise FetchRefused(f"{len(body)} rows; a UTC month of {year}-{month:02d} has {hours} hours")
    ci, di = header.index("Climate ID"), header.index(DATE_COL)
    ids = {r[ci] for r in body}
    if ids != {climate_id}:
        raise FetchRefused(f"climate IDs {sorted(ids)} != expected {climate_id}")
    first = pd.Timestamp(body[0][di], tz="UTC")
    if (first.year, first.month, first.day, first.hour) != (year, month, 1, 0):
        raise FetchRefused(f"first row {body[0][di]} is not {year}-{month:02d}-01 00:00 UTC")
    return len(body), no_obs


def fetch_station(station: Station, force: bool = False, log=print, through: int = THROUGH_DEFAULT) -> dict:
    """The station's months of the window through `through`; a month whose file still matches the manifest is not
    fetched again unless `force`. The manifest keeps the entries of months outside the window (a 2025 month fetched
    earlier), so a 2024 fetch never forgets them."""
    require_fetchable(through)
    if force and int(through) > THROUGH_DEFAULT:
        raise FetchRefused(f"--force is refused through {through}: it would download again the 2019-2024 ECCC months "
                           "v2's first 2024 replay read; remove a month's file to fetch it again")
    manifest_path = RAW_ECCC / station.climate_id / "manifest.json"
    previous = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"months": {}}
    window = fetch_months(through)
    keys = {f"{y:04d}-{m:02d}" for y, m in window}
    months = {k: v for k, v in previous["months"].items() if k not in keys}
    for year, month in window:
        key = f"{year:04d}-{month:02d}"
        path = month_path(station, year, month)
        prev = previous["months"].get(key)
        if path.exists() and prev and not force and sha256_file(path) == prev["sha256"]:
            months[key] = prev
            continue
        resp = get(BULK_URL, params=month_params(station.station_id, year, month))
        n, no_obs = verify_month(resp.content.decode("utf-8-sig"), station.climate_id, year, month)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resp.content)
        months[key] = {
            "url": resp.url,
            "retrieved_utc": utc_now(),
            "sha256": sha256_bytes(resp.content),
            "rows": n,
            "rows_without_observation": no_obs,
        }
        log(f"  {station.name} {key}: {n} rows, {no_obs} without observation")
    manifest = {
        "source": "Environment and Climate Change Canada, hourly climate observations (climate.weather.gc.ca)",
        "station": station.name,
        "climate_id": station.climate_id,
        "station_id": station.station_id,
        "directory": rel(RAW_ECCC / station.climate_id),
        "months": months,
    }
    write_json(manifest_path, manifest)
    return manifest


def load_station(station: Station, through: int = THROUGH_DEFAULT) -> pd.DataFrame:
    """Hourly observations for one station over the fetch window through `through`, UTC-indexed, numeric columns as
    floats."""
    frames = []
    for year, month in fetch_months(through):
        text = month_path(station, year, month).read_bytes().decode("utf-8-sig")
        verify_month(text, station.climate_id, year, month)
        df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)[list(COLUMNS)]
        df = df.fillna("").rename(columns=COLUMNS)
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], format="%Y-%m-%d %H:%M").dt.tz_localize("UTC")
    for col in ("temp_c", "dew_point_c", "rel_hum_pct", "wind_kmh"):
        df[col] = pd.to_numeric(df[col].replace("", None), errors="raise")
    df["temp_flag"] = df["temp_flag"].astype(str)
    return df
