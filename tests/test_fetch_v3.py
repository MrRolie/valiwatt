"""v3's fetch refuses any window past 2024 before any request (docs/plan-v3.md, sections 3.1 and 12 "Data"), whatever
v2's gate would say: the 2025 files are byte copies of v2's own fetch; and it is never forced, so nothing of 2019-2024
is fetched again. The HTTP layer is replaced by a counter; no network is reached and no data directory of the checkout
is touched."""

from __future__ import annotations

import pytest

from valiwatt import cli
from valiwatt.sources import eccc, http, hydroquebec, nbm
from valiwatt.sources.http import FetchRefused


class Reached(Exception):
    """The HTTP layer was reached: the fetch went past every gate."""


@pytest.fixture
def requests_seen(tmp_path, monkeypatch):
    """Every request through sources/http.py `get` is counted and stopped; v2's own gate always passes (as behind its
    freeze, from a clean tree), so only v3's gate can refuse; the raw directories are scratch ones."""
    seen = []

    def counted(url, params=None, **kw):
        seen.append(url)
        raise Reached(url)

    monkeypatch.setattr(http.requests, "get", counted)
    for module in (hydroquebec, eccc, nbm):
        monkeypatch.setattr(module, "require_fetchable", lambda through: None)
    monkeypatch.setattr(hydroquebec, "CSV_PATH", tmp_path / "hq" / "export.csv")
    monkeypatch.setattr(hydroquebec, "METADATA_PATH", tmp_path / "hq" / "metadata.json")
    monkeypatch.setattr(hydroquebec, "MANIFEST_PATH", tmp_path / "hq" / "manifest.json")
    monkeypatch.setattr(eccc, "RAW_ECCC", tmp_path / "eccc")
    monkeypatch.setattr(nbm, "RAW_NBM", tmp_path / "nbm")
    monkeypatch.setattr(nbm, "MANIFEST_PATH", tmp_path / "nbm" / "manifest.json")
    monkeypatch.setattr(nbm, "year_path", lambda year: tmp_path / "nbm" / f"{year}.csv")
    return seen, tmp_path


@pytest.mark.parametrize("only", [None, "hq", "eccc", "nbm"])
def test_a_v3_fetch_past_2024_is_refused_before_any_request(requests_seen, only):
    seen, tmp = requests_seen
    argv = ["fetch-v3", "--through", "2025"] + (["--only", only] if only else [])
    with pytest.raises(FetchRefused, match="byte copies of v2's own fetch"):
        cli.main(argv)
    assert seen == []
    assert not any(p.is_file() for p in tmp.rglob("*"))
    with pytest.raises(FetchRefused, match="no request made"):
        http.require_fetchable_v3(2025)
    assert seen == []


@pytest.mark.parametrize("only", ["hq", "eccc", "nbm"])
def test_a_v3_fetch_through_2024_reaches_each_source(requests_seen, only):
    """The control: through 2024 the gate is quiet and each source asks the HTTP layer."""
    seen, _ = requests_seen
    http.require_fetchable_v3(2024)
    with pytest.raises(Reached):
        cli.main(["fetch-v3", "--through", "2024", "--only", only])
    assert len(seen) == 1


def test_a_v3_fetch_is_never_forced(requests_seen, monkeypatch):
    """Section 3.1, "nothing of 2019-2024 is fetched again": `fetch-v3 --force` is refused by the parser before any
    source runs, whatever the window and the source; through 2024 the fetch reaches a source unforced (the control)."""
    seen, tmp = requests_seen
    for through in ("2024", "2025"):
        for only in ([], ["--only", "hq"], ["--only", "nbm"]):
            with pytest.raises(SystemExit) as e:
                cli.main(["fetch-v3", "--through", through, "--force", *only])
            assert e.value.code == 2
    assert seen == [] and not any(p.is_file() for p in tmp.rglob("*"))
    forced = []
    monkeypatch.setattr(hydroquebec, "fetch", lambda force, through: forced.append(force) or {"rows": 0, "sha256": ""})
    assert cli.main(["fetch-v3", "--through", "2024", "--only", "hq"]) == 0
    assert forced == [False]
