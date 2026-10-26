"""Small HTTP helper: retries with backoff, a polite delay, content hashing, and the gate on what may be fetched."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

from valiwatt import __version__
from valiwatt.config import THROUGH_DEFAULT, THROUGH_YEARS
from valiwatt.paths import REPO_ROOT

USER_AGENT = f"valiwatt/{__version__} (open-source research; data fetched for local analysis only)"


class FetchRefused(RuntimeError):
    """A response arrived but failed verification, or the fetch is not allowed; nothing was stored."""


def require_fetchable(through: int) -> None:
    """May a fetch whose data window ends with `through` run (docs/plan-v2.md, section 11.3.1)?

    Through 2024 it may. Past 2024 only behind the v2 freeze: the rule files equal the freeze tag's
    (`valiwatt.monitor.run.check_freeze` passes) and the working tree holds no uncommitted change
    (`git status --porcelain` is empty); otherwise it refuses, before any request."""
    through = int(through)
    if through <= THROUGH_DEFAULT:
        return
    if through not in THROUGH_YEARS:
        raise FetchRefused(f"no data window ends with {through}; the windows end with {list(THROUGH_YEARS)}")
    from valiwatt.monitor.run import check_freeze  # monitor imports the sources

    try:
        check_freeze()
    except Exception as exc:
        raise FetchRefused(f"a fetch through {through} runs only behind the frozen rules: {exc}") from exc
    status = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True, text=True,
                            check=False)
    if status.returncode != 0:
        raise FetchRefused(f"a fetch through {through} cannot read the tree's state: {status.stderr.strip()}")
    if status.stdout.strip():
        raise FetchRefused(f"a fetch through {through} runs only from a clean tree; `git status --porcelain` lists "
                           f"{len(status.stdout.splitlines())} uncommitted changes")


def require_fetchable_v3(through: int) -> None:
    """May v3's fetch, whose data window ends with `through`, run (docs/plan-v3.md, section 3.1)? Through 2024 it may;
    past 2024 never, whatever the freeze or the tree, before any request: the 2025 files are byte copies of v2's own
    2025 fetch, made after v2's 2025 test of record (section 11.3). v2's gate, `require_fetchable`, is unchanged and
    still runs inside each source's fetch."""
    through = int(through)
    if through > THROUGH_DEFAULT:
        raise FetchRefused(f"v3 fetches nothing past {THROUGH_DEFAULT} (docs/plan-v3.md, section 3.1): the {through} "
                           "files are byte copies of v2's own fetch, made after v2's test of record; no request made")


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
