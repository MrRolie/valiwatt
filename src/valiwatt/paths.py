"""Repository-relative locations. Data and run outputs live in gitignored directories."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / "config"
DATA = REPO_ROOT / "data"
RAW = DATA / "raw"
RAW_HQ = RAW / "hydroquebec"
RAW_ECCC = RAW / "eccc"
RAW_NBM = RAW / "nbm"
INTERIM = DATA / "interim"
MLRUNS = REPO_ROOT / "mlruns"
REPORTS = REPO_ROOT / "docs" / "report"  # rendered reports, committed (owner's ruling)


def rel(path: Path) -> str:
    """Path relative to the repository root, for anything written to a manifest, a run or a report."""
    return Path(path).resolve().relative_to(REPO_ROOT).as_posix()
