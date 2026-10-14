"""Small HTTP helper: retries with backoff, a polite delay, and content hashing."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

from valiwatt import __version__

USER_AGENT = f"valiwatt/{__version__} (open-source research; data fetched for local analysis only)"


class FetchRefused(RuntimeError):
    """A response arrived but failed verification; nothing was stored."""


def get(url: str, params: dict | None = None, *, retries: int = 4, timeout: int = 120, delay: float = 0.2) -> requests.Response:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": USER_AGENT})
            if resp.status_code == 200:
                time.sleep(delay)
                return resp
            last = RuntimeError(f"HTTP {resp.status_code} for {resp.url}")
        except requests.RequestException as exc:
            last = exc
        time.sleep(2**attempt)
    raise RuntimeError(f"fetch failed after {retries} attempts: {url}") from last


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
