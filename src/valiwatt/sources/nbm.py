"""NOAA's National Blend of Models (NBM) NBS station guidance for CYUL (Montréal-Trudeau), from the Iowa Environmental
Mesonet (IEM) archive of Iowa State University (docs/plan-v2.md, section 3.1).

One CSV per year of run time, requested from IEM's `mos.py` with `station=CYUL`, `model=NBS`, `sts` and `ets`. As
measured live on 2026-10-16, the archive keeps a run when `sts <= runtime <= ets`, both ends included, so the year's
request (`year_params`) runs from 00:00 UTC on 1 January to 01:00 UTC on 1 January of the next year: the next year's
01:00 run is in both files, as in the files of the research run (a fresh 2023 request was byte-identical to them), and
the load drops those identical points and counts them. The header varies with the year (columns that are empty for a
whole response are left out), so the columns are found by name. A refusal from the archive's rate limiter comes as
HTTP 200 with a line of plain text; it has no header and is refused like any response without the required columns.

Points are three-hourly; `tmp` is the 2 m temperature in whole degrees Fahrenheit, blank when the run has none; `wsp`
the wind speed in whole knots, blank when the run has none (docs/plan-v3.md, section 3.1: a header without it, or a
value neither a whole number nor blank, is refused; v3 converts it to km/h as knots x 1.852, forecast/issue.py). The
forecast is read only while the primary station of `config/stations.toml` is the ECCC station at the same airport
(climate ID 7025251): the pairing. IEM declares the data public domain; NOAA's attribution and no-endorsement terms
apply (AGENTS.md). Nothing here is committed: the files go to the gitignored `data/raw/nbm/`.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json

import numpy as np
import pandas as pd

from valiwatt.config import THROUGH_DEFAULT, stations
from valiwatt.forecast.issue import MIDNIGHT, IssueSpec, day_edge, day_origin
from valiwatt.paths import RAW_NBM, rel
from valiwatt.sources.http import FetchRefused, get, require_fetchable, sha256_bytes, sha256_file, utc_now, write_json

STATION, MODEL, PAIRED_CLIMATE_ID = "CYUL", "NBS", "7025251"
URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/mos.py"
FIRST_YEAR = 2019
# A run is available this long after its run time: an assumption, since the archive records no arrival time
# (docs/plan-v2.md, section 3.2). The sensitivity of section 3.5 uses the longer margin.
AVAILABILITY_LAG = pd.Timedelta(hours=2)
SENSITIVITY_LAG = pd.Timedelta(hours=6)
# A day takes the freshest covering run among this many freshest available runs.
MAX_RUNS_BACK = 12
# Plan section 9.2: a run whose points reach 75 h after its run time can feed three consecutive days. A lead at or
# above it is a finding against the section's two-day argument and a trigger for a section 18 entry. v2's origin;
# reuse_lead_h gives it for any issue time (87 at noon, docs/plan-v3.md section 9.2).
REUSE_LEAD_H = 75
REQUIRED = ("runtime", "ftime", "model", "tmp", "wsp", "station")
WHOLE = r"-?\d+"  # a wind speed in whole knots, as the archive writes it
MANIFEST_PATH = RAW_NBM / "manifest.json"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
# Seconds between two yearly requests: the archive answers a burst with its rate limiter.
REQUEST_DELAY = 10.0


def reuse_lead_h(issue: IssueSpec = MIDNIGHT) -> int:
    """The lead from the latest run compliant at day D's origin (its run time AVAILABILITY_LAG before it) to the point a
    run must hold to cover a third day, D+2's last bracket point (D+3 06:00 UTC): a run reaching it can feed three
    consecutive days (plan-v2 and plan-v3, section 9.2). 75 at MIDNIGHT (REUSE_LEAD_H), 87 at NOON."""
    day = pd.Timestamp("2021-01-04")  # any day: the clocks are fixed offsets in UTC
    third = day_edge(day + pd.Timedelta(days=2)) + pd.Timedelta(hours=25)
    return int((third - (day_origin(day, issue=issue) - AVAILABILITY_LAG)) / pd.Timedelta(hours=1))


def year_path(year: int):
    return RAW_NBM / f"CYUL-NBS-{int(year)}.csv"


def year_params(year: int) -> dict:
    """The request for the runs of `year`: 00:00 UTC on 1 January to 01:00 UTC on 1 January of the next year, both
    included by the archive."""
    year = int(year)
    return {"station": STATION, "model": MODEL, "sts": f"{year}-01-01T00:00Z", "ets": f"{year + 1}-01-01T01:00Z",
            "format": "csv"}


def year_bounds(year: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    """The run times the archive returns for `year_params(year)`: from `sts` to `ets`, both included."""
    p = year_params(year)
    return pd.Timestamp(p["sts"]), pd.Timestamp(p["ets"])


def _parse_year(text: str, year: int) -> tuple[pd.DataFrame, dict]:
    """The one reading of a year's response: `verify_year`'s checks, then the rows they passed (runtime and ftime as
    naive UTC, tmp_f in °F and wsp_kt in knots, each NaN when blank, in the file's order) and the counts the manifest
    records. The load reads these same rows, so it never holds a row the checks did not see."""
    rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
    if not rows or not any(rows):
        raise FetchRefused("empty response")
    header = rows[0]
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise FetchRefused(f"missing columns {missing}; the response starts {text[:80]!r}")
    body = rows[1:]
    if not body:
        raise FetchRefused(f"no row in the response for {year}")
    bad = [i for i, r in enumerate(body, start=2) if len(r) != len(header)]
    if bad:
        raise FetchRefused(f"{len(bad)} rows do not have {len(header)} fields (first at line {bad[0]})")
    col = {c: header.index(c) for c in REQUIRED}
    stations_ = {r[col["station"]] for r in body}
    if stations_ != {STATION}:
        raise FetchRefused(f"stations {sorted(stations_)} != expected {STATION}")
    models = {r[col["model"]] for r in body}
    if models != {MODEL}:
        raise FetchRefused(f"models {sorted(models)} != expected {MODEL}")
    tmp = pd.Series([r[col["tmp"]].strip() for r in body], dtype=str)
    values = pd.to_numeric(tmp.where(tmp != ""), errors="coerce")
    not_numbers = (tmp != "") & ~values.between(-1e9, 1e9)
    if not_numbers.any():
        first = int(not_numbers.to_numpy().argmax())
        raise FetchRefused(f"{int(not_numbers.sum())} temperatures are neither a number nor blank "
                           f"(first {tmp.iloc[first]!r} at line {first + 2})")
    wsp = pd.Series([r[col["wsp"]].strip() for r in body], dtype=str)
    not_whole = (wsp != "") & ~wsp.str.fullmatch(WHOLE)
    if not_whole.any():
        first = int(not_whole.to_numpy().argmax())
        raise FetchRefused(f"{int(not_whole.sum())} wind speeds are neither a whole number nor blank "
                           f"(first {wsp.iloc[first]!r} at line {first + 2})")
    knots = pd.to_numeric(wsp.where(wsp != ""), errors="raise").astype(float)
    times = {}
    for c in ("runtime", "ftime"):
        raw = pd.Series([r[col[c]] for r in body], dtype=str)
        # pandas reads a blank, "NaT" or "nan" as a missing time without raising, and no bound below can see one: a
        # point without its run time or its valid time has no availability, so the file is refused, naming the rows.
        parsed = pd.to_datetime(raw, format=TIME_FORMAT, errors="coerce")
        undated = parsed.isna().to_numpy()
        if undated.any():
            lines = [int(i) + 2 for i in np.flatnonzero(undated)]
            raise FetchRefused(f"{len(lines)} rows have a {c} that is missing or does not parse as {TIME_FORMAT!r} "
                               f"(lines {lines[:10]}{' ...' if len(lines) > 10 else ''}; first "
                               f"{raw[undated].iloc[0]!r})")
        times[c] = parsed
    runtime = times["runtime"]
    sts, ets = (t.tz_convert(None) for t in year_bounds(year))
    outside = (runtime < sts) | (runtime > ets)
    if outside.any():
        raise FetchRefused(f"{int(outside.sum())} rows have a run time outside {sts}..{ets}, the request for {year} "
                           f"(first {runtime[outside].iloc[0]})")
    counts = {"rows": len(body), "runs": int(runtime.nunique()), "first_runtime": runtime.min().strftime(TIME_FORMAT),
              "last_runtime": runtime.max().strftime(TIME_FORMAT), "blank_tmp": int((tmp == "").sum()),
              "blank_wsp": int((wsp == "").sum())}
    return pd.DataFrame({"runtime": runtime, "ftime": times["ftime"], "tmp_f": values.astype(float),
                         "wsp_kt": knots}), counts


def verify_year(text: str, year: int) -> dict:
    """Refuse a response that is not NBS guidance for CYUL with run times inside the year's request.

    Refused: a missing required column (any of REQUIRED, found by name), no row, a row of another length than the
    header, a station other than CYUL or a model other than NBS, a temperature that is neither a number nor blank, a
    wind speed that is neither a whole number (WHOLE) nor blank, a run or valid time that is missing (blank, "NaT",
    "nan") or does not parse, naming its lines, and a run time outside the request's bounds as the archive applies them.
    Returns the counts the manifest records."""
    return _parse_year(text, year)[1]


def manifest() -> dict:
    if not MANIFEST_PATH.exists():
        raise FetchRefused("no NBM manifest: run `valiwatt fetch` first")
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _previous() -> dict:
    return manifest() if MANIFEST_PATH.exists() else {"years": {}}


def fetch_year(year: int, force: bool = False) -> dict:
    """One year's file, verified before it is written; a file that still matches the manifest is not fetched again
    unless `force`. Returns the year's manifest entry."""
    key, path = str(int(year)), year_path(year)
    previous = _previous()
    prev = previous["years"].get(key)
    if path.exists() and prev and not force and sha256_file(path) == prev["sha256"]:
        return prev
    resp = get(URL, params=year_params(year), timeout=300, delay=REQUEST_DELAY)
    counts = verify_year(resp.content.decode("utf-8-sig"), year)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(resp.content)
    entry = {"url": resp.url, "params": year_params(year), "retrieved_utc": utc_now(), "file": rel(path),
             "sha256": sha256_bytes(resp.content), **counts}
    years = {**previous["years"], key: entry}
    write_json(MANIFEST_PATH, {
        "source": "NOAA National Blend of Models, NBS station guidance, via the Iowa Environmental Mesonet",
        "station": STATION, "model": MODEL, "url": URL, "directory": rel(RAW_NBM), "years": years})
    return entry


def fetch(through: int = THROUGH_DEFAULT, force: bool = False) -> dict:
    """Every year of run time from FIRST_YEAR to `through`. Past 2024, behind the v2 freeze only, and `force` is
    refused before any request: it would download again the 2019-2024 files v2's first 2024 replay read (a file is
    fetched again by removing it)."""
    require_fetchable(through)
    if force and int(through) > THROUGH_DEFAULT:
        raise FetchRefused(f"--force is refused through {through}: it would download again the 2019-2024 NBM files "
                           "v2's first 2024 replay read; remove a year's file to fetch it again")
    for year in range(FIRST_YEAR, int(through) + 1):
        fetch_year(year, force=force)
    return manifest()


def for_years(m: dict, years) -> dict:
    """The manifest restricted to `years`: a refusal names a listed year it lacks."""
    keys = [str(int(y)) for y in years]
    missing = [k for k in keys if k not in m.get("years", {})]
    if missing:
        raise FetchRefused(f"the NBM manifest lists no file for {missing}: run `valiwatt fetch` first")
    return {**m, "years": {k: m["years"][k] for k in keys}}


def manifest_sha256(m: dict) -> str:
    """One digest of a manifest (each year's entry holds its file's SHA-256), so a later step can tell that the files
    are the ones a data-quality run checked."""
    return hashlib.sha256(json.dumps(m, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def verify_files(years) -> dict:
    """Every listed year's file still matches the manifest, else FetchRefused. Opens only the listed years' files.
    Returns the manifest restricted to them."""
    m = for_years(manifest(), years)
    for key, entry in m["years"].items():
        path = year_path(int(key))
        if not path.exists():
            raise FetchRefused(f"NBM file for {key} is missing: run `valiwatt fetch`")
        if sha256_file(path) != entry["sha256"]:
            raise FetchRefused(f"NBM file for {key} no longer matches its manifest; re-fetch")
    return m


def require_pairing() -> None:
    primary = next(s for s in stations() if s.primary)
    if primary.climate_id != PAIRED_CLIMATE_ID:
        raise FetchRefused(f"the forecast is for {STATION}, paired with ECCC station {PAIRED_CLIMATE_ID}; the primary "
                           f"station of config/stations.toml is {primary.climate_id}")


def load_runs(years) -> pd.DataFrame:
    """The runs of an explicit list of years (never a pattern: a file of a year not listed is never opened).

    Columns runtime, ftime (UTC), tmp_f (°F) and wsp_kt (knots), each NaN when blank, sorted by run and valid time.
    Identical duplicate points (same run, valid time, temperature and wind: the files overlap at their boundary) are
    dropped and counted in `.attrs["identical_duplicates"]`; two values for one run and valid time, in either field,
    refuse the load. Refused unless the
    primary station is the one the forecast is paired with."""
    require_pairing()
    years = [int(y) for y in years]
    frames = [_parse_year(year_path(year).read_bytes().decode("utf-8-sig"), year)[0] for year in years]
    if not frames:
        raise FetchRefused("no year listed: the forecast's years are given explicitly")
    d = pd.concat(frames, ignore_index=True)
    out = pd.DataFrame({"runtime": d["runtime"].dt.tz_localize("UTC"), "ftime": d["ftime"].dt.tz_localize("UTC"),
                        "tmp_f": d["tmp_f"].astype(float), "wsp_kt": d["wsp_kt"].astype(float)})
    identical = out.duplicated(["runtime", "ftime", "tmp_f", "wsp_kt"])
    out = out[~identical]
    conflict = out.duplicated(["runtime", "ftime"], keep=False)
    if conflict.any():
        first = out[conflict].iloc[0]
        raise FetchRefused(f"{int(conflict.sum())} points hold two values for one run and valid time (first run "
                           f"{first['runtime']}, valid {first['ftime']})")
    out = out.sort_values(["runtime", "ftime"], kind="stable").reset_index(drop=True)
    out.attrs["identical_duplicates"] = int(identical.sum())
    out.attrs["years"] = years
    return out
