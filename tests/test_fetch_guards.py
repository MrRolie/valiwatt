"""The fetchers refuse responses they cannot verify, including ECCC's no-time-zone trap; every Hydro-Québec request
is bounded by the portal; and nothing reaches past 2024 before the v2 freeze (docs/plan-v2.md, section 11.3.1)."""

import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pandas as pd
import pytest

from valiwatt.config import data_last, stations
from valiwatt.monitor import run as mrun
from valiwatt.sources import eccc, http, hydroquebec, nbm
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


# Every fetch below runs with HTTP stubbed, in a scratch data/raw, from a scratch repository's tree.

def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


class Portal:
    """Hydro-Québec's portal, stubbed: hourly records from `first` to `last`; the date filter honoured unless
    `honour` is False. Every request is recorded."""

    def __init__(self, first="2024-12-30T00:00Z", last="2026-01-01T10:00Z", honour=True):
        self.labels = pd.date_range(pd.Timestamp(first), pd.Timestamp(last), freq="h")
        self.honour = honour
        self.calls = []

    def served(self, params):
        m = re.fullmatch(r"date <= '(.+)'", params.get("where", ""))
        return self.labels if m is None or not self.honour else self.labels[self.labels <= pd.Timestamp(m.group(1))]

    def get(self, url, params=None, **kw):
        params = dict(params or {})
        self.calls.append((url, params))
        full = f"{url}?{urlencode(params)}" if params else url
        if url == hydroquebec.METADATA_URL:
            body = json.dumps({"metas": {"default": {"records_count": len(self.labels), "license": "CC BY-NC 4.0",
                                                     "modified": "2026-06-05T16:53:14+00:00", "publisher": "test"}}})
        elif url == hydroquebec.RECORDS_URL:
            body = json.dumps({"total_count": len(self.served(params)), "results": []})
        elif url == hydroquebec.EXPORT_URL:
            body = "﻿date,moyenne_mw\n" + "".join(f"{t.isoformat()},20000.5\n" for t in self.served(params))
        else:
            raise AssertionError(f"unexpected request {url}")
        return SimpleNamespace(json=lambda: json.loads(body.lstrip("﻿")), content=body.encode("utf-8"), text=body,
                               url=full)

    def bounds(self, url):
        """The `where` of every request to `url` (None for one without it)."""
        return [p.get("where") for u, p in self.calls if u == url]


def never(url, params=None, **kw):
    raise AssertionError(f"a request was made: {url} {params}")


@pytest.fixture
def raw(tmp_path, monkeypatch):
    """A scratch data/raw for the three sources, a clean scratch repository as the tree, the freeze passing."""
    root = tmp_path / "raw"
    hq = root / "hydroquebec"
    monkeypatch.setattr(hydroquebec, "RAW_HQ", hq)
    monkeypatch.setattr(hydroquebec, "CSV_PATH", hq / f"{hydroquebec.DATASET}.csv")
    monkeypatch.setattr(hydroquebec, "METADATA_PATH", hq / "metadata.json")
    monkeypatch.setattr(hydroquebec, "MANIFEST_PATH", hq / "manifest.json")
    monkeypatch.setattr(eccc, "RAW_ECCC", root / "eccc")
    monkeypatch.setattr(nbm, "RAW_NBM", root / "nbm")
    monkeypatch.setattr(nbm, "MANIFEST_PATH", root / "nbm" / "manifest.json")
    for module in (hydroquebec, eccc, nbm):
        monkeypatch.setattr(module, "rel", lambda p: Path(p).name)
        monkeypatch.setattr(module, "get", never)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README").write_text("scratch\n")
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-qm", "scratch")
    monkeypatch.setattr(http, "REPO_ROOT", repo)
    monkeypatch.setattr(mrun, "check_freeze", lambda: {"freeze_tag": "rules-frozen-v2", "freeze_commit": "f" * 40})
    return SimpleNamespace(root=root, repo=repo)


def portal(monkeypatch, **kw) -> Portal:
    p = Portal(**kw)
    monkeypatch.setattr(hydroquebec, "get", p.get)
    return p


def snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def weather_fetches_2025(force=False):
    st = stations()[0]
    return {"eccc": lambda: eccc.fetch_station(st, force=force, log=lambda *_: None, through=2025),
            "nbm": lambda: nbm.fetch(2025, force=force)}


def test_the_bound_is_the_last_hour_of_the_window():
    assert hydroquebec.bound_params(2024) == {"where": "date <= '2025-01-01T05:00:00Z'"}
    assert hydroquebec.bound_params(2025) == {"where": "date <= '2026-01-01T05:00:00Z'"}
    assert hydroquebec.csv_path(2024) == hydroquebec.CSV_PATH and hydroquebec.manifest_path() == hydroquebec.MANIFEST_PATH
    assert hydroquebec.csv_path(2025).parent == hydroquebec.CSV_PATH.parent
    assert hydroquebec.csv_path(2025) != hydroquebec.CSV_PATH and hydroquebec.manifest_path(2025) != hydroquebec.MANIFEST_PATH


def test_a_2025_fetch_before_the_freeze_is_refused(raw, monkeypatch):
    p = portal(monkeypatch)

    def not_frozen():
        raise mrun.FreezeError("rule files differ from `rules-frozen-v2`")

    monkeypatch.setattr(mrun, "check_freeze", not_frozen)
    for name, fetch in {"hq": lambda: hydroquebec.fetch(through=2025), **weather_fetches_2025()}.items():
        with pytest.raises(FetchRefused, match="frozen rules"):
            fetch()
    assert p.calls == [] and not raw.root.exists()
    hydroquebec.fetch()  # through 2024: no freeze needed (control)
    assert p.bounds(hydroquebec.EXPORT_URL) == ["date <= '2025-01-01T05:00:00Z'"]


def test_a_2025_fetch_from_a_dirty_tree_is_refused(raw, monkeypatch):
    p = portal(monkeypatch)
    (raw.repo / "notes.txt").write_text("uncommitted\n")
    for name, fetch in {"hq": lambda: hydroquebec.fetch(through=2025), **weather_fetches_2025()}.items():
        with pytest.raises(FetchRefused, match="clean tree"):
            fetch()
    (raw.repo / "notes.txt").unlink()
    (raw.repo / "README").write_text("changed\n")
    with pytest.raises(FetchRefused, match="clean tree"):
        hydroquebec.fetch(through=2025)
    assert p.calls == [] and not raw.root.exists()
    git(raw.repo, "checkout", "-q", "README")
    hydroquebec.fetch(through=2025)  # clean again (control)
    assert p.bounds(hydroquebec.EXPORT_URL) == ["date <= '2026-01-01T05:00:00Z'"]


def test_a_partial_2025_export_is_refused_and_writes_nothing(raw, monkeypatch):
    portal(monkeypatch)
    hydroquebec.fetch()
    before = snapshot(raw.root)
    portal(monkeypatch, last="2025-06-30T05:00Z")
    with pytest.raises(FetchRefused, match="whole year"):
        hydroquebec.fetch(through=2025)
    assert snapshot(raw.root) == before
    portal(monkeypatch, last="2026-01-01T04:00Z")  # one hour short
    with pytest.raises(FetchRefused, match="whole year"):
        hydroquebec.fetch(through=2025)
    assert snapshot(raw.root) == before


def test_every_hydroquebec_request_is_bounded_by_the_portal(raw, monkeypatch):
    for through in (2024, 2025):
        bound = f"date <= '{data_last(through):%Y-%m-%dT%H:%M:%SZ}'"
        assert hydroquebec.bound_params(through) == {"where": bound}
        for path in ("no file on disk", "force"):
            if path == "no file on disk":
                assert not hydroquebec.csv_path(through).exists()
            p = portal(monkeypatch)
            m = hydroquebec.fetch(force=path == "force", through=through)
            assert p.bounds(hydroquebec.RECORDS_URL) == [bound], (through, path)  # the count
            assert p.bounds(hydroquebec.EXPORT_URL) == [bound], (through, path)  # the export
            assert all(u == hydroquebec.METADATA_URL for u, q in p.calls
                       if u not in (hydroquebec.RECORDS_URL, hydroquebec.EXPORT_URL))
            assert m["where"] == bound and urlencode({"where": bound}) in m["export_url"]
            assert urlencode({"where": bound}) in m["count_url"] and m["through"] == through
            last = hydroquebec.load_raw(hydroquebec.csv_path(through))["ts_utc"].max()
            assert last == data_last(through), (through, path)
    # The 2025 vintage went to its own files: the 2024 export is still the one fetched through 2024.
    assert hydroquebec.load_raw(hydroquebec.csv_path(2024))["ts_utc"].max() == data_last(2024)


def test_a_response_holding_a_record_past_its_bound_is_refused_and_writes_nothing(raw, monkeypatch):
    portal(monkeypatch, honour=False)  # a portal that ignores the filter, on the export and on the count
    with pytest.raises(FetchRefused, match="labelled after 2025-01-01T05:00:00"):
        hydroquebec.fetch()
    assert not hydroquebec.RAW_HQ.exists()
    portal(monkeypatch)
    hydroquebec.fetch()
    before = snapshot(raw.root)
    portal(monkeypatch, honour=False)
    with pytest.raises(FetchRefused, match="labelled after"):
        hydroquebec.fetch(force=True)
    assert snapshot(raw.root) == before


def test_a_2024_export_ending_at_the_window_s_last_hour_is_written(raw, monkeypatch):
    p = portal(monkeypatch)  # the portal holds records of 2025 and 2026 too; the filter keeps them out
    m = hydroquebec.fetch()
    records = hydroquebec.load_raw(hydroquebec.CSV_PATH)
    assert records["ts_utc"].max() == data_last(2024) and len(records) == m["rows"] == m["records_count"]
    assert m["rows"] == int((p.labels <= data_last(2024)).sum()) < len(p.labels)
    assert hydroquebec.MANIFEST_PATH.exists() and hydroquebec.METADATA_PATH.exists()
    assert json.loads(hydroquebec.MANIFEST_PATH.read_text())["sha256"] == http.sha256_file(hydroquebec.CSV_PATH)


def test_a_plain_fetch_never_asks_the_portal_again_for_a_verified_file(raw, monkeypatch):
    portal(monkeypatch)
    first = hydroquebec.fetch()
    p = portal(monkeypatch)
    assert hydroquebec.fetch() == first and p.calls == []
    vintage = hydroquebec.fetch(through=2025)
    p = portal(monkeypatch)
    assert hydroquebec.fetch(through=2025) == vintage and p.calls == []  # a second `--through 2025` asks nothing
    assert json.loads(hydroquebec.MANIFEST_PATH.read_text()) == first  # the 2024 export was left as it was


def test_a_forced_2025_fetch_of_the_weather_files_is_refused(raw, monkeypatch):
    portal(monkeypatch)
    hydroquebec.fetch()
    for f in (raw.root / "eccc" / "7025251" / "2024-12.csv", nbm.year_path(2024)):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("a file v2's first 2024 replay read\n")
    before = snapshot(raw.root)
    for name, fetch in weather_fetches_2025(force=True).items():  # `get` is `never`: any request fails the test
        with pytest.raises(FetchRefused, match="--force is refused"):
            fetch()
    assert snapshot(raw.root) == before
    p = portal(monkeypatch)
    hydroquebec.fetch(force=True, through=2025)  # the Hydro-Québec 2025 vintage, in its own file, may be forced
    assert p.bounds(hydroquebec.EXPORT_URL) == ["date <= '2026-01-01T05:00:00Z'"]
    assert {k: v for k, v in snapshot(raw.root).items() if k in before} == before
