"""Hydro-Québec open data: "Historique de la demande d'électricité au Québec" (CC BY-NC 4.0).

Hydro-Québec describes the series as informational, not official data. Each value is the average demand over the hour
that ENDS at the timestamp ("établies à la fin du pas de temps"); the API serves timestamps in UTC.

Every request for the records is bounded by the portal itself (docs/plan-v2.md, section 11.3.1): the export and the
count both carry the Explore API's filter on the record's date, at the last hour of the window's last year, so a
response for 2024 cannot hold a record of 2025 (the portal's whole-dataset `records_count` counts every published
record and is not the expected count). A response that holds a record past its bound anyway is refused and nothing is
written. The export comes in no particular order: its bytes can differ between two fetches of the same records.
The 2024 window keeps v1's file names; the 2025 window, fetched only behind the v2 freeze, writes its own files beside
them and never overwrites them.
"""

from __future__ import annotations

import csv
import io
import json

import pandas as pd

from valiwatt.config import THROUGH_DEFAULT, data_last
from valiwatt.paths import RAW_HQ, rel
from valiwatt.sources.http import FetchRefused, get, require_fetchable, sha256_bytes, utc_now, write_json

DATASET = "historique-demande-electricite-quebec"
API = "https://donnees.hydroquebec.com/api/explore/v2.1/catalog/datasets"
METADATA_URL = f"{API}/{DATASET}"
RECORDS_URL = f"{METADATA_URL}/records"
EXPORT_URL = f"{API}/{DATASET}/exports/csv"
EXPORT_PARAMS = {"delimiter": ",", "timezone": "UTC"}
HEADER = ["date", "moyenne_mw"]

CSV_PATH = RAW_HQ / f"{DATASET}.csv"
METADATA_PATH = RAW_HQ / "metadata.json"
MANIFEST_PATH = RAW_HQ / "manifest.json"


def _vintage(path, through: int):
    """v1's file for the 2024 window; for a later window, a file of its own beside it."""
    if int(through) == THROUGH_DEFAULT:
        return path
    return path.with_name(f"{path.stem}-through-{int(through)}{path.suffix}")


def csv_path(through: int = THROUGH_DEFAULT):
    return _vintage(CSV_PATH, through)


def metadata_path(through: int = THROUGH_DEFAULT):
    return _vintage(METADATA_PATH, through)


def manifest_path(through: int = THROUGH_DEFAULT):
    return _vintage(MANIFEST_PATH, through)


def bound_params(through: int) -> dict:
    """The portal's filter on the record's date at the window's last hour: the export and the count both carry it."""
    return {"where": f"date <= '{data_last(through):%Y-%m-%dT%H:%M:%SZ}'"}


def verify_export(text: str, expected_records: int, through: int | None = None) -> int:
    """Refuse an export whose header, row shape or row count is not what the portal's count under the same filter
    promises. With `through`, also refuse a record labelled after the window's last hour (the portal's bound did not
    hold) and, past 2024, an export whose latest record is not that hour (only a whole year)."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    if not rows or rows[0] != HEADER:
        raise FetchRefused(f"unexpected header {rows[0] if rows else None!r}; expected {HEADER}")
    body = rows[1:]
    bad = [i for i, r in enumerate(body, start=2) if len(r) != 2]
    if bad:
        raise FetchRefused(f"{len(bad)} rows do not have 2 fields (first at line {bad[0]})")
    if len(body) != expected_records:
        raise FetchRefused(f"export has {len(body)} rows; the portal's count promises {expected_records}")
    if through is not None:
        last = data_last(through)
        labels = pd.to_datetime(pd.Series([r[0] for r in body], dtype=str), utc=True, format="ISO8601")
        late = labels > last
        if late.any():
            raise FetchRefused(f"{int(late.sum())} records are labelled after {last.isoformat()}, the bound of the "
                               f"window through {through}: the portal's filter did not hold")
        latest = labels.max() if len(labels) else None
        if int(through) > THROUGH_DEFAULT and latest != last:
            raise FetchRefused(f"the export through {through} ends at {latest}, not at {last.isoformat()}: only a "
                               "whole year is fetched")
    return len(body)


def fetch(force: bool = False, through: int = THROUGH_DEFAULT) -> dict:
    """The export of the window through `through`, bounded by the portal. A verified file is never fetched again
    unless `force`. Nothing is written until every check has passed."""
    require_fetchable(through)
    csv_file, meta_file, manifest_file = csv_path(through), metadata_path(through), manifest_path(through)
    if manifest_file.exists() and csv_file.exists() and not force:
        return json.loads(manifest_file.read_text(encoding="utf-8"))
    bound = bound_params(through)
    meta_resp = get(METADATA_URL)
    meta = meta_resp.json()
    default = meta["metas"]["default"]
    count = get(RECORDS_URL, params={"limit": 0, **bound})
    expected = int(count.json()["total_count"])
    export = get(EXPORT_URL, params=EXPORT_PARAMS | bound)
    retrieved = utc_now()
    n_rows = verify_export(export.text, expected, through)

    csv_file.parent.mkdir(parents=True, exist_ok=True)
    csv_file.write_bytes(export.content)
    meta_file.write_bytes(meta_resp.content)
    manifest = {
        "source": "Hydro-Québec, Historique de la demande d'électricité au Québec",
        "dataset": DATASET,
        "metadata_url": METADATA_URL,
        "count_url": count.url,
        "export_url": export.url,
        "where": bound["where"],
        "through": int(through),
        "retrieved_utc": retrieved,
        "file": rel(csv_file),
        "sha256": sha256_bytes(export.content),
        "bytes": len(export.content),
        "rows": n_rows,
        "records_count": expected,
        "dataset_modified": default.get("modified"),
        "data_processed": default.get("data_processed"),
        "license": default.get("license"),
        "license_url": default.get("license_url"),
        "publisher": default.get("publisher"),
        "metadata_sha256": sha256_bytes(meta_resp.content),
    }
    write_json(manifest_file, manifest)
    return manifest


def load_raw(path=CSV_PATH) -> pd.DataFrame:
    """Raw records as served: one row per record, UTC timestamps, blanks as NaN. Order and duplicates are kept."""
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"date": str, "moyenne_mw": str}, keep_default_na=False)
    if list(df.columns) != HEADER:
        raise FetchRefused(f"unexpected columns {list(df.columns)}")
    out = pd.DataFrame(
        {
            "ts_utc": pd.to_datetime(df["date"], utc=True, format="ISO8601"),
            "mw": pd.to_numeric(df["moyenne_mw"].str.strip().replace("", None), errors="raise"),
            "source_line": range(2, len(df) + 2),
        }
    )
    return out


def load_window(through: int = THROUGH_DEFAULT) -> pd.DataFrame:
    """The raw records of the window through `through` (`load_raw` on its file). Records labelled after the window's
    last hour are set aside, never returned, and counted in `.attrs["after_window"]`."""
    raw = load_raw(csv_path(through))
    after = raw["ts_utc"] > data_last(through)
    out = raw[~after].reset_index(drop=True)
    out.attrs["after_window"] = int(after.sum())
    return out
