"""Live boundaries: the real endpoints, the real no-time-zone trap, and the claim that two ECCC routes agree."""

import io

import pandas as pd
import pytest
import requests

from valiwatt.sources import eccc, hydroquebec
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
    meta = requests.get(hydroquebec.METADATA_URL, timeout=60, headers={"User-Agent": USER_AGENT}).json()
    default = meta["metas"]["default"]
    assert default["license"] == "CC BY-NC 4.0"
    export = requests.get(hydroquebec.EXPORT_URL, params=hydroquebec.EXPORT_PARAMS, timeout=300,
                          headers={"User-Agent": USER_AGENT})
    assert hydroquebec.verify_export(export.text, int(default["records_count"])) == int(default["records_count"])
