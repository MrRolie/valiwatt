"""v3's raw files are byte copies of those v2's first 2024 replay read (docs/plan-v3.md, section 3.1).

Before any v3 run on real data, the Hydro-Québec export, the ECCC files and the NBS files v3 holds, with their
manifests, are checked file by file against the hashes recorded by the data-quality run of v2's first 2024 replay, as
the step-2 read of v2's store logged them in v3's own store (v2store.recorded): the run refuses on any difference.

- The window checked against v2's hashes is v2's 2024 window, whatever window the v3 run reads: the files of
  2019-2024 are never fetched again, and the 2025 files, copied later from v2's 2025 fetch, are not in v2's first 2024
  replay. Those files, and every manifest past 2024 the run's window reads (`files_past_window`), must be byte copies
  all the same (section 11.3 (1): "on byte copies of the 2025 files and their manifests"); their hashes are checked
  against their manifests by the run (quality.run), as v2 checks them.
- Each source is checked as v2's data-quality run recorded it: the Hydro-Québec export by its SHA-256; the ECCC files
  by the one digest of the stations' manifests restricted to the window's months (quality.run.eccc_manifests_sha256,
  each month listing its file's SHA-256), then each month file against its manifest; the NBS files by the digest of the
  NBM manifest restricted to 2019-2024 (the manifest is one file for every year and v2's 2025 fetch rewrites it, so
  its 2019-2024 entries are checked, not the whole file), then each year's file against it. Digests, never manifest
  bytes: a plain fetch rewrites an ECCC manifest with the same content.
- Byte copies, never links: a file or manifest reached through a symbolic link (at any part of its path) or holding a
  hard link is refused, so no write in v2's checkout can reach v3's files.

Not run is never inferred from an absence: only a run told it is on a fresh clone (`valiwatt dq-v3 --fresh-clone`,
section 3.1: neither v2's files nor its store) skips the check, and says so (`byte_copies` `not_run`, which no run of
record may carry: only the runs of record need the byte copies). Without that word, a missing read of v2's store
refuses; with it, a read in v3's store refuses, since that machine is not a fresh clone.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from valiwatt.config import THROUGH_DEFAULT, fetch_months, stations
from valiwatt.sources import eccc, hydroquebec, nbm
from valiwatt.sources.http import FetchRefused, sha256_file

# v2's first 2024 replay read the window ending with 2024, and the NBS files of these years.
WINDOW = THROUGH_DEFAULT
NBM_YEARS = tuple(range(nbm.FIRST_YEAR, WINDOW + 1))


class ByteCopyRefused(FetchRefused):
    """A raw file v3 holds is not a byte copy of the one v2's first 2024 replay read."""


def require_copy(path: Path) -> None:
    """`path` is a regular file of its own: no symbolic link at any part of its path, a single hard link."""
    path = Path(path)
    if not path.exists():
        raise ByteCopyRefused(f"{path} is missing: v3 holds no copy of it")
    # Every raw path hangs off paths.REPO_ROOT, already resolved: a link above the checkout is not seen here, any
    # link below it is.
    absolute = Path(os.path.abspath(path))
    if absolute.resolve() != absolute or path.is_symlink():
        raise ByteCopyRefused(f"{path} is reached through a symbolic link: v3 reads byte copies, never links")
    st = os.stat(absolute)
    if st.st_nlink != 1:
        raise ByteCopyRefused(f"{path} has {st.st_nlink} hard links: v3 reads byte copies, never links")


def files_past_window(through: int) -> list[Path]:
    """Every raw file and manifest past the 2024 window that the window through `through` reads (none through 2024):
    the Hydro-Québec export through `through` and its manifest; each station's month files that the 2024 window lacks,
    and the station's manifest; each NBM year's file past 2024, and the NBM manifest."""
    through = int(through)
    if through <= WINDOW:
        return []
    out = [Path(hydroquebec.csv_path(through)), Path(hydroquebec.manifest_path(through))]
    months = sorted(set(fetch_months(through)) - set(fetch_months(WINDOW)))
    for st in stations():
        out += [Path(eccc.month_path(st, y, m)) for y, m in months]
        out.append(Path(eccc.RAW_ECCC) / st.climate_id / "manifest.json")
    out += [Path(nbm.year_path(y)) for y in range(WINDOW + 1, through + 1)] + [Path(nbm.MANIFEST_PATH)]
    return out


def _eccc_months() -> tuple[str, list[tuple[Path, str]]]:
    """quality.run.eccc_manifests_sha256 over the stations' manifests restricted to the window's months, as
    quality.run._verify_raw returns them, and each month file of the window with the SHA-256 its manifest lists. Each
    manifest must be a byte copy and list every month of the window."""
    from valiwatt.quality.run import eccc_manifests_sha256

    window = {f"{y:04d}-{m:02d}" for y, m in fetch_months(WINDOW)}
    manifests, months_ = [], []
    for st in stations():
        path = eccc.RAW_ECCC / st.climate_id / "manifest.json"
        require_copy(path)
        m = json.loads(path.read_text(encoding="utf-8"))
        months = {k: v for k, v in m.get("months", {}).items() if k in window}
        unlisted = sorted(window - set(months))
        if unlisted:
            raise ByteCopyRefused(f"the ECCC manifest of {st.name} lists no file for {len(unlisted)} month(s) of the "
                                  f"window, first {unlisted[:3]}")
        months_ += [(eccc.month_path(st, *map(int, k.split("-"))), months[k]["sha256"]) for k in sorted(months)]
        manifests.append({**m, "months": months})
    return eccc_manifests_sha256(manifests), months_


def check_byte_copies(anchor: dict | None, fresh_clone: bool = False, through: int = WINDOW) -> dict:
    """The byte-copy check (section 3.1). anchor: v2store.recorded(), the hashes the read of v2's store logged; None
    when there was none. fresh_clone: the run was told it is on a fresh clone (`dq-v3 --fresh-clone`). through: the
    window the run reads; past 2024, every file and manifest past 2024 it reads (`files_past_window`) must be a byte
    copy too (section 11.3 (1)), its hashes checked against its manifests by the run.

    Returns {"status": "not_run"} on a fresh clone without an anchor; else, every file being a byte copy that matches
    the hash v2 recorded, {"status": "passed", the three hashes, v2_dq_run_id, v2_read_run_id, files: how many were
    checked}. Refused (ByteCopyRefused): no anchor without the fresh-clone word, an anchor with it, and, naming the
    source and the file, the first difference or link."""
    if anchor is None:
        if fresh_clone:
            return {"status": "not_run"}
        raise ByteCopyRefused("no read of v2's store in v3's store: run `valiwatt read-v2 PATH` first, or, on a fresh "
                              "clone holding neither v2's files nor its store, `valiwatt dq-v3 --fresh-clone` "
                              "(docs/plan-v3.md, section 3.1); the byte-copy check is never skipped for an absence")
    if fresh_clone:
        raise ByteCopyRefused(f"--fresh-clone, but v3's store holds the read of v2's store {anchor.get('run_id')}: "
                              "this machine is not a fresh clone, and its files are checked against v2's")
    missing = [k for k in ("hq_sha256", "eccc_manifests_sha256", "nbm_manifest_sha256") if not anchor.get(k)]
    if missing:
        raise ByteCopyRefused(f"the read of v2's store {anchor.get('run_id')} recorded no {missing}")
    checked = 0

    hq = hydroquebec.csv_path(WINDOW)
    require_copy(hq)
    require_copy(hydroquebec.manifest_path(WINDOW))
    if sha256_file(hq) != anchor["hq_sha256"]:
        raise ByteCopyRefused(f"the Hydro-Québec export {hq.name} differs from the one v2's first 2024 replay read")
    checked += 2

    digest, months = _eccc_months()
    if digest != anchor["eccc_manifests_sha256"]:
        raise ByteCopyRefused("the ECCC manifests restricted to the 2024 window differ from those v2's first 2024 "
                              "replay read: a month was fetched again, or its file differs")
    for path, sha in months:
        require_copy(path)
        if sha256_file(path) != sha:
            raise ByteCopyRefused(f"the ECCC file {path.parent.name}/{path.name} differs from the one v2's first 2024 "
                                  "replay read")
    checked += len(stations()) + len(months)

    require_copy(nbm.MANIFEST_PATH)
    restricted = nbm.for_years(nbm.manifest(), NBM_YEARS)
    if nbm.manifest_sha256(restricted) != anchor["nbm_manifest_sha256"]:
        raise ByteCopyRefused("the NBM manifest's 2019-2024 entries differ from those v2's first 2024 replay read")
    for key, entry in restricted["years"].items():
        path = nbm.year_path(int(key))
        require_copy(path)
        if sha256_file(path) != entry["sha256"]:
            raise ByteCopyRefused(f"the NBS file {path.name} differs from the one v2's first 2024 replay read")
    checked += 1 + len(restricted["years"])

    past = files_past_window(through)
    for path in past:
        require_copy(path)
    # The stations' manifests and the NBM manifest were checked above, with the 2024 window: counted once.
    seen = {nbm.MANIFEST_PATH, *(eccc.RAW_ECCC / st.climate_id / "manifest.json" for st in stations())}
    checked += len(set(past) - seen)

    return {"status": "passed", **{k: anchor[k] for k in ("hq_sha256", "eccc_manifests_sha256", "nbm_manifest_sha256")},
            "v2_dq_run_id": anchor.get("v2_dq_run_id", ""), "v2_read_run_id": anchor.get("run_id", ""),
            "files": checked}
