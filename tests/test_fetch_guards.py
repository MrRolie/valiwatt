"""The fetchers refuse responses they cannot verify, including ECCC's no-time-zone trap."""

import pytest

from valiwatt.sources.eccc import verify_month
from valiwatt.sources.http import FetchRefused
from valiwatt.sources.hydroquebec import verify_export

HEADER = ('"Longitude (x)","Latitude (y)","Station Name","Climate ID","Date/Time (UTC)","Year","Month","Day",'
          '"Time (UTC)","Flag","Temp (°C)","Temp Flag","Dew Point Temp (°C)","Dew Point Temp Flag","Rel Hum (%)",'
          '"Rel Hum Flag","Precip. Amount (mm)","Precip. Amount Flag","Wind Dir (10s deg)","Wind Dir Flag",'
          '"Wind Spd (km/h)","Wind Spd Flag","Visibility (km)","Visibility Flag","Stn Press (kPa)","Stn Press Flag",'
          '"Hmdx","Hmdx Flag","Wind Chill","Wind Chill Flag","Weather"')


def row(day, hour, climate_id="7025251", observed=True):
    ident = f'"-73.74","45.47","MONTREAL INTL A","{climate_id}","2019-02-{day:02d} {hour:02d}:00","2019","02","{day:02d}","{hour:02d}:00"'
    if not observed:
        return ident
    return ident + ',"","-10.2","","-17.5","","55","","","","23","","11","","24.1","","102.17","","","","-16","","NA"'


def month(climate_id="7025251", unobserved=(), header=HEADER, days=28):
    rows = [row(d, h, climate_id, (d, h) not in unobserved) for d in range(1, days + 1) for h in range(24)]
    return "﻿" + header + "\n" + "\n".join(rows) + "\n"


def test_complete_month_is_accepted_and_counts_unobserved_hours():
    assert verify_month(month(unobserved={(2, 3)}), "7025251", 2019, 2) == (672, 1)


def test_no_time_zone_trap_is_refused():
    # Without `time=UTC` the service returns a blank date header and rows of identity fields only.
    trap_header = HEADER.replace('"Date/Time (UTC)"', '""').replace('"Time (UTC)"', '""')
    with pytest.raises(FetchRefused, match="time zone"):
        verify_month(month(header=trap_header), "7025251", 2019, 2)


def test_rows_with_identity_fields_only_everywhere_are_refused():
    every = {(d, h) for d in range(1, 29) for h in range(24)}
    with pytest.raises(FetchRefused, match="no row carries observations"):
        verify_month(month(unobserved=every), "7025251", 2019, 2)


def test_wrong_station_is_refused():
    with pytest.raises(FetchRefused, match="climate IDs"):
        verify_month(month(climate_id="7016293"), "7025251", 2019, 2)


def test_incomplete_month_is_refused():
    with pytest.raises(FetchRefused, match="rows"):
        verify_month(month(days=27), "7025251", 2019, 2)


def test_malformed_row_is_refused():
    text = month().replace(row(5, 5), '"-73.74","45.47","MONTREAL INTL A"', 1)
    with pytest.raises(FetchRefused, match="neither"):
        verify_month(text, "7025251", 2019, 2)


def test_hydroquebec_export_with_wrong_count_or_header_is_refused():
    good = "﻿date,moyenne_mw\n2019-01-01T06:00:00+00:00,23762.55\n2019-01-01T07:00:00+00:00,\n"
    assert verify_export(good, 2) == 2
    with pytest.raises(FetchRefused, match="promises"):
        verify_export(good, 3)
    with pytest.raises(FetchRefused, match="header"):
        verify_export(good.replace("moyenne_mw", "mw"), 2)


def test_wrong_month_of_the_same_length_is_refused():
    # February 2019 (672 hours) served for a request about February 2019 passes; for February 2018 it must not.
    with pytest.raises(FetchRefused, match="first row"):
        verify_month(month(), "7025251", 2018, 2)


def test_missing_value_columns_are_refused():
    with pytest.raises(FetchRefused, match="missing columns"):
        verify_month(month(header=HEADER.replace('"Temp (°C)"', '"Temp"')), "7025251", 2019, 2)


def test_empty_response_is_refused():
    with pytest.raises(FetchRefused, match="empty"):
        verify_month("", "7025251", 2019, 2)


def test_hydroquebec_row_with_extra_field_is_refused():
    bad = "\ufeffdate,moyenne_mw\n2019-01-01T06:00:00+00:00,23762.55,extra\n"
    with pytest.raises(FetchRefused, match="2 fields"):
        verify_export(bad, 1)


def test_hydroquebec_load_refuses_renamed_columns(tmp_path):
    from valiwatt.sources.hydroquebec import load_raw

    path = tmp_path / "x.csv"
    path.write_text("date,mw\n2019-01-01T06:00:00+00:00,23762.55\n", encoding="utf-8")
    with pytest.raises(FetchRefused, match="columns"):
        load_raw(path)
