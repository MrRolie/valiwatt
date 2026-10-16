"""Live boundaries: the real endpoints, the real no-time-zone trap, the claim that two ECCC routes agree, the
portal's bound on Hydro-Québec's export, and the claim that the IEM archive holds NOAA's NBS values."""

import io
import time

import pandas as pd
import pytest
import requests

from valiwatt.sources import eccc, hydroquebec, nbm
from valiwatt.sources.http import USER_AGENT, FetchRefused

pytestmark = pytest.mark.network
MONTREAL_STATION_ID, MONTREAL_CLIMATE_ID = 51157, "7025251"


def bulk(params):
    r = requests.get(eccc.BULK_URL, params=params, timeout=120, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    return r.content.decode("utf-8-sig")


def test_eccc_month_in_utc_passes_verification():
    text = bulk(eccc.month_params(MONTREAL_STATION_ID, 2019, 3))
    rows, _ = eccc.verify_month(text, MONTREAL_CLIMATE_ID, 2019, 3)
    assert rows == 744


def test_eccc_request_without_time_zone_is_refused():
    params = eccc.month_params(MONTREAL_STATION_ID, 2019, 3)
    params["time"] = ""
    with pytest.raises(FetchRefused):
        eccc.verify_month(bulk(params), MONTREAL_CLIMATE_ID, 2019, 3)


def test_bulk_csv_and_geomet_agree_on_a_sample_day():
    text = bulk(eccc.month_params(MONTREAL_STATION_ID, 2019, 3))
    df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    csv_t = df.set_index("Date/Time (UTC)")["Temp (°C)"]
    r = requests.get("https://api.weather.gc.ca/collections/climate-hourly/items",
                     params={"f": "json", "CLIMATE_IDENTIFIER": MONTREAL_CLIMATE_ID, "limit": 100,
                             "datetime": "2019-03-10T00:00:00Z/2019-03-10T23:00:00Z"},
                     timeout=120, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    feats = r.json()["features"]
    assert len(feats) >= 20
    for f in feats:
        p = f["properties"]
        key = pd.Timestamp(p["UTC_DATE"]).strftime("%Y-%m-%d %H:%M")
        if p["TEMP"] is None:
            assert csv_t[key] == ""
        else:
            assert float(csv_t[key]) == pytest.approx(p["TEMP"]), key


def test_hydroquebec_metadata_and_export_verify():
    """The bounded export, through 2024 only (docs/plan-v2.md, section 11.3.1): never the unbounded one, which would
    hold any 2025 record Hydro-Québec has published."""
    meta = requests.get(hydroquebec.METADATA_URL, timeout=60, headers={"User-Agent": USER_AGENT}).json()
    default = meta["metas"]["default"]
    assert default["license"] == "CC BY-NC 4.0"
    bound = hydroquebec.bound_params(2024)
    count = requests.get(hydroquebec.RECORDS_URL, params={"limit": 0, **bound}, timeout=60,
                         headers={"User-Agent": USER_AGENT}).json()["total_count"]
    export = requests.get(hydroquebec.EXPORT_URL, params=hydroquebec.EXPORT_PARAMS | bound, timeout=300,
                          headers={"User-Agent": USER_AGENT})
    assert hydroquebec.verify_export(export.text, int(count), through=2024) == int(count) == 52_608


def test_the_portal_s_filter_returns_exactly_the_records_up_to_its_bound():
    where = {"where": "date <= '2019-01-01T08:00:00Z'"}
    count = requests.get(hydroquebec.RECORDS_URL, params={"limit": 0, **where}, timeout=60,
                         headers={"User-Agent": USER_AGENT}).json()["total_count"]
    export = requests.get(hydroquebec.EXPORT_URL, params=hydroquebec.EXPORT_PARAMS | where, timeout=60,
                          headers={"User-Agent": USER_AGENT})
    labels = sorted(r.split(",")[0] for r in export.content.decode("utf-8-sig").splitlines()[1:])
    assert count == 3 and labels == ["2019-01-01T06:00:00+00:00", "2019-01-01T07:00:00+00:00",
                                     "2019-01-01T08:00:00+00:00"]


def iem(params) -> str:
    time.sleep(5)  # the archive's rate limiter answers a burst with a line of text
    r = requests.get(nbm.URL, params=params, timeout=300, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    return r.content.decode("utf-8-sig")


def test_iem_nbs_month_for_cyul_verifies():
    params = nbm.year_params(2023) | {"sts": "2023-02-01T00:00Z", "ets": "2023-03-01T00:00Z"}
    counts = nbm.verify_year(iem(params), 2023)
    assert counts["runs"] == 28 * 4 and counts["first_runtime"] == "2023-02-01 01:00:00"
    assert counts["last_runtime"] == "2023-02-28 19:00:00"


NBSTX = "https://noaa-nbm-grib2-pds.s3.amazonaws.com/blend.20230202/19/text/blend_nbstx.t19z"


def noaa_cyul_tmp(url: str) -> dict[int, int]:
    """CYUL's TMP row of NOAA's NBS text bulletin, by forecast hour (read until CYUL's block is complete)."""
    rows = {}
    with requests.get(url, stream=True, timeout=300, headers={"User-Agent": USER_AGENT}) as r:
        r.raise_for_status()
        inside = False
        for raw in r.iter_lines():
            line = raw.decode("ascii", "replace")
            if " NBS GUIDANCE " in line:
                if inside:
                    break
                inside = line.split()[0] == "CYUL"
            elif inside and line[:5].strip() in ("FHR", "TMP"):
                rows[line[:5].strip()] = [int(line[i:i + 3]) for i in range(5, len(line.rstrip()), 3)]
    assert set(rows) == {"FHR", "TMP"}, "no CYUL block in the bulletin"
    return dict(zip(rows["FHR"], rows["TMP"]))


def test_iem_and_noaa_agree_on_one_run():
    runtime = pd.Timestamp("2023-02-02T19:00Z")
    text = iem(nbm.year_params(2023) | {"sts": "2023-02-02T19:00Z", "ets": "2023-02-02T19:00Z"})
    nbm.verify_year(text, 2023)
    d = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    assert set(d["runtime"]) == {"2023-02-02 19:00:00"}
    archive = {int((pd.Timestamp(f, tz="UTC") - runtime) / pd.Timedelta(hours=1)): int(t)
               for f, t in zip(d["ftime"], d["tmp"]) if t != ""}
    noaa = noaa_cyul_tmp(NBSTX)
    common = sorted(set(archive) & set(noaa))
    assert len(common) >= 20
    assert {h: archive[h] for h in common} == {h: noaa[h] for h in common}
