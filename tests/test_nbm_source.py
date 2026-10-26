"""The NBM fetcher and loader refuse what they cannot verify: another station or model, a temperature that is neither a
number nor blank, a header without the wind speed or a wind speed that is neither a whole number nor blank (plan-v3,
section 3.1), an empty response, a run outside the year's request, a point without its run time or its valid time,
two values for one point, a primary station the forecast is not paired with, a file that no longer matches its
manifest, and a file of a year not listed."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from conftest import synth_runs
from valiwatt.config import Station
from valiwatt.sources import nbm
from valiwatt.sources.http import FetchRefused

# The columns of an IEM response, in its order; the header varies with the year, so only these are relied on.
COLUMNS = ["runtime", "ftime", "model", "tmp", "dpt", "wdr", "wsp", "station", "sky"]
WSP_KT = 12  # the wind written for runs without a wsp_kt column


def iem_text(runs: pd.DataFrame, station: str = "CYUL", model: str = "NBS") -> str:
    """An IEM `mos.py` CSV response holding `runs` (columns runtime, ftime, tmp_f, and wsp_kt when present, else
    WSP_KT knots at every point)."""
    lines = [",".join(COLUMNS)]
    for r in runs.itertuples():
        tmp = "" if pd.isna(r.tmp_f) else str(int(r.tmp_f))
        knots = getattr(r, "wsp_kt", WSP_KT)
        wsp = "" if pd.isna(knots) else str(int(knots))
        lines.append(",".join([r.runtime.strftime(nbm.TIME_FORMAT), r.ftime.strftime(nbm.TIME_FORMAT), model, tmp,
                               "20", "250", wsp, station, "50"]))
    return "\n".join(lines) + "\n"


ARCHIVE = synth_runs("2020-12-25T00:00Z", "2024-01-02T00:00Z")  # one archive: two files agree where they overlap


def year_runs(year: int) -> pd.DataFrame:
    """The archive's runs for a year's request: 1 January 00:00 UTC to 1 January 01:00 UTC of the next year."""
    sts, ets = nbm.year_bounds(year)
    return ARCHIVE[(ARCHIVE["runtime"] >= sts) & (ARCHIVE["runtime"] <= ets)].reset_index(drop=True)


def short_runs(year: int, days: int = 3) -> pd.DataFrame:
    """A few days of runs at the end of `year`, the next year's 01:00 run included (the files' overlap)."""
    runs = year_runs(year)
    return runs[runs["runtime"] >= pd.Timestamp(f"{year}-12-31T00:00Z") - pd.Timedelta(days=days)].reset_index(drop=True)


def test_a_clean_year_verifies_and_the_next_years_first_run_is_inside_its_request():
    counts = nbm.verify_year(iem_text(short_runs(2022)), 2022)
    assert counts["last_runtime"] == "2023-01-01 01:00:00"  # the archive includes `ets`
    assert counts["runs"] == 4 * 4 + 1 and counts["blank_tmp"] == 0 and counts["blank_wsp"] == 0


def test_a_response_for_another_station_or_model_is_refused():
    runs = short_runs(2022)
    nbm.verify_year(iem_text(runs), 2022)
    with pytest.raises(FetchRefused, match="stations"):
        nbm.verify_year(iem_text(runs, station="KBOS"), 2022)
    with pytest.raises(FetchRefused, match="models"):
        nbm.verify_year(iem_text(runs, model="GFS"), 2022)
    mixed = iem_text(runs) + iem_text(runs.head(1), station="CYQB").split("\n", 1)[1]
    with pytest.raises(FetchRefused, match="stations"):
        nbm.verify_year(mixed, 2022)


def test_a_non_numeric_temperature_is_refused():
    runs = short_runs(2022)
    blank = runs.copy()
    blank.loc[5, "tmp_f"] = np.nan
    assert nbm.verify_year(iem_text(blank), 2022)["blank_tmp"] == 1  # a blank is no value, not an error
    lines = iem_text(runs).split("\n")
    for bad in ("M", "NaN", "inf", "12F"):
        fields = lines[3].split(",")
        fields[COLUMNS.index("tmp")] = bad
        text = "\n".join(lines[:3] + [",".join(fields)] + lines[4:])
        with pytest.raises(FetchRefused, match="neither a number nor blank"):
            nbm.verify_year(text, 2022)


def test_an_empty_response_is_refused():
    with pytest.raises(FetchRefused, match="empty"):
        nbm.verify_year("", 2022)
    with pytest.raises(FetchRefused, match="no row"):
        nbm.verify_year(",".join(COLUMNS) + "\n", 2022)
    # The archive's rate limiter answers with HTTP 200 and a line of text, which has none of the columns.
    with pytest.raises(FetchRefused, match="missing columns"):
        nbm.verify_year("Too many requests from your IP address, slow down.", 2022)
    with pytest.raises(FetchRefused, match="missing columns"):
        nbm.verify_year(iem_text(short_runs(2022)).replace("runtime,ftime,model,tmp", "runtime,ftime,model,t2m"), 2022)


def test_a_runtime_outside_the_requested_year_is_refused():
    runs = short_runs(2022)
    nbm.verify_year(iem_text(runs), 2022)  # up to 2023-01-01 01:00, the request's `ets`, included
    with pytest.raises(FetchRefused, match="outside"):
        nbm.verify_year(iem_text(runs), 2023)  # the right file for another year
    later = runs.copy()
    later.loc[later.index[-1], "runtime"] = pd.Timestamp("2023-01-01T07:00Z")  # the next year's second run
    with pytest.raises(FetchRefused, match="outside"):
        nbm.verify_year(iem_text(later), 2022)
    earlier = runs.copy()
    earlier.loc[0, "runtime"] = pd.Timestamp("2021-12-31T19:00Z")
    with pytest.raises(FetchRefused, match="outside"):
        nbm.verify_year(iem_text(earlier), 2022)
    first = runs.copy()
    first.loc[0, "runtime"] = pd.Timestamp("2022-01-01T00:00Z")  # the request's `sts`, included
    nbm.verify_year(iem_text(first), 2022)


def with_field(text: str, line: int, column: str, value: str) -> str:
    """`text` with one field of one line (1-based, the header being line 1) replaced."""
    lines = text.split("\n")
    fields = lines[line - 1].split(",")
    fields[COLUMNS.index(column)] = value
    lines[line - 1] = ",".join(fields)
    return "\n".join(lines)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """data/raw/nbm in a scratch folder, and a stubbed archive that records every request."""
    raw = tmp_path / "raw" / "nbm"
    monkeypatch.setattr(nbm, "RAW_NBM", raw)
    monkeypatch.setattr(nbm, "MANIFEST_PATH", raw / "manifest.json")
    monkeypatch.setattr(nbm, "rel", lambda p: Path(p).name)
    archive = SimpleNamespace(files={}, calls=[])

    def get(url, params=None, **kw):
        archive.calls.append((url, dict(params or {})))
        year = int(params["sts"][:4])
        text = archive.files[year]
        return SimpleNamespace(content=text.encode(), text=text, url=f"{url}?sts={params['sts']}&ets={params['ets']}")

    monkeypatch.setattr(nbm, "get", get)
    return archive


def write_years(store, texts: dict[int, str]) -> None:
    store.files.update(texts)
    for year in texts:
        nbm.fetch_year(year)


def test_two_values_for_one_point_refuse_the_load(store):
    a, b = short_runs(2022), year_runs(2023).head(60)
    overlap = a[a["runtime"] == pd.Timestamp("2023-01-01T01:00Z")]
    assert len(overlap) > 0 and overlap["runtime"].isin(b["runtime"]).all()
    b = b.copy()
    t = overlap.iloc[3]
    b.loc[(b["runtime"] == t["runtime"]) & (b["ftime"] == t["ftime"]), "tmp_f"] = t["tmp_f"] + 1
    write_years(store, {2022: iem_text(a), 2023: iem_text(b)})
    with pytest.raises(FetchRefused, match="two values"):
        nbm.load_runs([2022, 2023])


def test_identical_duplicates_are_dropped_and_counted(store):
    a, b = short_runs(2022), year_runs(2023).head(60)
    overlap = a[a["runtime"] == pd.Timestamp("2023-01-01T01:00Z")]
    write_years(store, {2022: iem_text(a), 2023: iem_text(b)})
    runs = nbm.load_runs([2022, 2023])
    assert runs.attrs["identical_duplicates"] == len(overlap)
    assert not runs.duplicated(["runtime", "ftime"]).any()
    assert len(runs) == len(a) + len(b) - len(overlap)
    assert list(runs.columns) == ["runtime", "ftime", "tmp_f", "wsp_kt"]  # v3 reads the wind too (plan-v3, 3.1)
    assert str(runs["runtime"].dt.tz) == "UTC" and runs["tmp_f"].dtype == float and runs["wsp_kt"].dtype == float
    assert runs.equals(runs.sort_values(["runtime", "ftime"]))


def test_a_primary_station_other_than_7025251_refuses_the_pairing(store, monkeypatch):
    write_years(store, {2022: iem_text(short_runs(2022))})
    nbm.load_runs([2022])

    def panel(climate_id):
        return [Station(key="x", name="X", climate_id=climate_id, station_id=1, centre="X", primary=True,
                        reason="", reason_fr=""),
                Station(key="y", name="Y", climate_id=nbm.PAIRED_CLIMATE_ID, station_id=2, centre="Y",
                        primary=False, reason="", reason_fr="")]

    monkeypatch.setattr(nbm, "stations", lambda: panel(nbm.PAIRED_CLIMATE_ID))
    nbm.load_runs([2022])
    monkeypatch.setattr(nbm, "stations", lambda: panel("7016293"))
    with pytest.raises(FetchRefused, match="paired"):
        nbm.load_runs([2022])


def test_a_file_that_no_longer_matches_its_manifest_is_refused(store):
    write_years(store, {2021: iem_text(short_runs(2021)), 2022: iem_text(short_runs(2022))})
    m = nbm.verify_files([2021, 2022])
    assert set(m["years"]) == {"2021", "2022"} and m["years"]["2022"]["runs"] == 17
    path = nbm.year_path(2022)
    path.write_text(path.read_text().replace(",NBS,", ",NBS,1", 1))
    with pytest.raises(FetchRefused, match="no longer matches"):
        nbm.verify_files([2021, 2022])
    nbm.verify_files([2021])  # the other year still verifies
    path.unlink()
    with pytest.raises(FetchRefused, match="missing"):
        nbm.verify_files([2022])
    with pytest.raises(FetchRefused, match="lists no file"):
        nbm.verify_files([2020])


def test_load_runs_reads_only_the_listed_years(store):
    write_years(store, {2021: iem_text(short_runs(2021)), 2022: iem_text(short_runs(2022))})
    nbm.year_path(2022).write_text("not a forecast file\n")  # any read of it would refuse
    nbm.year_path(2023).write_text("not a forecast file either\n")
    runs = nbm.load_runs([2021])
    assert runs["runtime"].max() == pd.Timestamp("2022-01-01T01:00Z")
    assert runs.attrs["years"] == [2021]
    with pytest.raises(FetchRefused):
        nbm.load_runs([2021, 2022])


def test_a_fetched_year_is_written_with_its_manifest_and_not_fetched_again(store):
    store.files[2022] = iem_text(short_runs(2022))
    entry = nbm.fetch_year(2022)
    assert store.calls == [(nbm.URL, nbm.year_params(2022))]
    assert entry["first_runtime"] == "2022-12-28 01:00:00" and entry["last_runtime"] == "2023-01-01 01:00:00"
    assert nbm.manifest()["years"]["2022"]["sha256"] == entry["sha256"]
    assert nbm.fetch_year(2022) == entry and len(store.calls) == 1  # verified on disk: no request
    nbm.fetch_year(2022, force=True)
    assert len(store.calls) == 2


def test_a_refused_response_writes_nothing(store):
    store.files[2022] = "Too many requests from your IP address, slow down."
    with pytest.raises(FetchRefused):
        nbm.fetch_year(2022)
    assert not nbm.RAW_NBM.exists()


def test_an_undated_point_is_refused_at_verification_and_load(store):
    """pandas reads a blank, "NaT" or "nan" time as missing without raising, and no bound can see a missing time: a
    point without its run time or its valid time has no availability (docs/plan-v2.md, sections 2 and 3.2), so the
    response is refused before it is written, and a file holding one refuses the load, each naming the lines."""
    text = iem_text(short_runs(2022))
    nbm.verify_year(text, 2022)
    for column in ("runtime", "ftime"):
        for bad in ("", "NaT", "nan", "2022-13-01 00:00:00"):
            broken = with_field(with_field(text, 5, column, bad), 9, column, bad)
            with pytest.raises(FetchRefused, match=rf"2 rows have a {column} that is missing or does not parse "
                                                   r".*lines \[5, 9\]"):
                nbm.verify_year(broken, 2022)
    store.files[2022] = with_field(text, 7, "runtime", "")
    with pytest.raises(FetchRefused, match=r"lines \[7\]"):
        nbm.fetch_year(2022)
    assert not nbm.year_path(2022).exists()  # nothing written
    write_years(store, {2022: text})
    assert nbm.load_runs([2022])["runtime"].notna().all()  # control: the dated file loads
    nbm.year_path(2022).write_text(with_field(text, 12, "runtime", ""))  # an undated run on disk
    with pytest.raises(FetchRefused, match=r"1 rows have a runtime that is missing .*lines \[12\]"):
        nbm.load_runs([2022])
    nbm.year_path(2022).write_text(with_field(text, 4, "ftime", "NaT"))
    with pytest.raises(FetchRefused, match=r"1 rows have a ftime that is missing .*lines \[4\]"):
        nbm.load_runs([2022])


# ------------------------------------------------------------------------------------- the wind speed (plan-v3, 3.1)

def test_a_header_without_the_wind_speed_is_refused():
    text = iem_text(short_runs(2022))
    nbm.verify_year(text, 2022)  # control
    with pytest.raises(FetchRefused, match=r"missing columns \['wsp'\]"):
        nbm.verify_year(text.replace(",wsp,", ",gst,", 1), 2022)
    dropped = "\n".join(",".join(f for i, f in enumerate(line.split(",")) if i != COLUMNS.index("wsp")) if line else ""
                        for line in text.split("\n"))
    with pytest.raises(FetchRefused, match=r"missing columns \['wsp'\]"):
        nbm.verify_year(dropped, 2022)


def test_a_wind_speed_that_is_not_a_whole_number_is_refused():
    text = iem_text(short_runs(2022))
    for bad in ("7.5", "7.0", "x", "M", "1e1", "NaN", "inf", "+7", "7 kt"):
        with pytest.raises(FetchRefused, match=r"1 wind speeds are neither a whole number nor blank .*line 4\)"):
            nbm.verify_year(with_field(text, 4, "wsp", bad), 2022)
    for good in ("0", "29", "101", "-3", " 7 "):  # whole numbers: the bounds are the gate's (quality.weather)
        nbm.verify_year(with_field(text, 4, "wsp", good), 2022)
    counts = nbm.verify_year(with_field(with_field(text, 4, "wsp", ""), 6, "wsp", ""), 2022)
    assert counts["blank_wsp"] == 2 and counts["blank_tmp"] == 0  # a blank is no value, not an error


def test_the_loader_returns_the_wind_in_knots_blank_as_nan(store):
    runs = short_runs(2022).assign(wsp_kt=lambda r: (np.arange(len(r)) % 30).astype(float))
    runs.loc[7, "wsp_kt"] = np.nan
    write_years(store, {2022: iem_text(runs)})
    loaded = nbm.load_runs([2022])
    np.testing.assert_array_equal(loaded["wsp_kt"].to_numpy(), runs["wsp_kt"].to_numpy())
    assert loaded["wsp_kt"].isna().sum() == 1 and nbm.manifest()["years"]["2022"]["blank_wsp"] == 1


def test_one_temperature_with_two_winds_at_one_point_refuses_the_load(store):
    a, b = short_runs(2022), year_runs(2023).head(60)
    overlap = a[a["runtime"] == pd.Timestamp("2023-01-01T01:00Z")]
    t = overlap.iloc[3]
    b = b.assign(wsp_kt=float(WSP_KT))
    b.loc[(b["runtime"] == t["runtime"]) & (b["ftime"] == t["ftime"]), "wsp_kt"] = WSP_KT + 1  # same temperature
    write_years(store, {2022: iem_text(a), 2023: iem_text(b)})
    with pytest.raises(FetchRefused, match="two values"):
        nbm.load_runs([2022, 2023])


def test_the_reuse_bound_follows_the_origin():
    """Plan section 9.2: the latest run compliant at the origin, to D+3 06:00 UTC. v2's 75 hours from D 03:00 UTC;
    v3's 87 from D-1 15:00 UTC."""
    from valiwatt.forecast.issue import MIDNIGHT, NOON

    assert nbm.REUSE_LEAD_H == nbm.reuse_lead_h() == nbm.reuse_lead_h(MIDNIGHT) == 75
    assert nbm.reuse_lead_h(NOON) == 87
