"""Hydro-Québec open data: "Historique de la demande d'électricité au Québec" (CC BY-NC 4.0).

Hydro-Québec describes the series as informational, not official data. Each value is the average demand over the hour
that ENDS at the timestamp ("établies à la fin du pas de temps"); the API serves timestamps in UTC.
"""

from __future__ import annotations

import csv
import io
import json

import pandas as pd

from valiwatt.paths import RAW_HQ, rel
from valiwatt.sources.http import FetchRefused, get, sha256_bytes, utc_now, write_json

DATASET = "historique-demande-electricite-quebec"
API = "https://donnees.hydroquebec.com/api/explore/v2.1/catalog/datasets"
METADATA_URL = f"{API}/{DATASET}"
EXPORT_URL = f"{API}/{DATASET}/exports/csv"
EXPORT_PARAMS = {"delimiter": ",", "timezone": "UTC"}
HEADER = ["date", "moyenne_mw"]

CSV_PATH = RAW_HQ / f"{DATASET}.csv"
METADATA_PATH = RAW_HQ / "metadata.json"
MANIFEST_PATH = RAW_HQ / "manifest.json"


def verify_export(text: str, expected_records: int) -> int:
    """Refuse an export whose header, row shape or row count is not what the portal's metadata promises."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    if not rows or rows[0] != HEADER:
        raise FetchRefused(f"unexpected header {rows[0] if rows else None!r}; expected {HEADER}")
    body = rows[1:]
    bad = [i for i, r in enumerate(body, start=2) if len(r) != 2]
    if bad:
        raise FetchRefused(f"{len(bad)} rows do not have 2 fields (first at line {bad[0]})")
    if len(body) != expected_records:
        raise FetchRefused(f"export has {len(body)} rows; metadata promises {expected_records}")
    return len(body)


def fetch(force: bool = False) -> dict:
    if MANIFEST_PATH.exists() and CSV_PATH.exists() and not force:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    meta_resp = get(METADATA_URL)
    meta = meta_resp.json()
    default = meta["metas"]["default"]
    expected = int(default["records_count"])
    export = get(EXPORT_URL, params=EXPORT_PARAMS)
    retrieved = utc_now()
    n_rows = verify_export(export.text, expected)

    RAW_HQ.mkdir(parents=True, exist_ok=True)
    CSV_PATH.write_bytes(export.content)
    METADATA_PATH.write_bytes(meta_resp.content)
    manifest = {
        "source": "Hydro-Québec, Historique de la demande d'électricité au Québec",
        "dataset": DATASET,
        "metadata_url": METADATA_URL,
        "export_url": export.url,
        "retrieved_utc": retrieved,
        "file": rel(CSV_PATH),
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
    write_json(MANIFEST_PATH, manifest)
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
