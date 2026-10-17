"""MLflow tracking in a local, gitignored store (mlruns/). Every figure in a report comes from a run logged here."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from contextlib import contextmanager

from valiwatt.paths import MLRUNS, REPO_ROOT

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")


def tracking_uri() -> str:
    return f"sqlite:///{MLRUNS / 'mlflow.db'}"


def client():
    import mlflow

    MLRUNS.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(tracking_uri())
    return mlflow.MlflowClient()


def experiment_id(name: str) -> str:
    c = client()
    exp = c.get_experiment_by_name(name)
    if exp:
        return exp.experiment_id
    return c.create_experiment(name, artifact_location=(MLRUNS / "artifacts" / name).as_uri())


CODE_PATHS = ("src", "config", "tests", "pyproject.toml", "uv.lock")


def code_sha256() -> str:
    """Hash of every code and config file that is tracked or untracked-but-not-ignored: what actually ran."""
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", *CODE_PATHS],
                         cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout.split("\n")
    h = hashlib.sha256()
    for name in sorted(n for n in out if n and (REPO_ROOT / n).is_file()):
        h.update(name.encode() + b"\0" + (REPO_ROOT / name).read_bytes() + b"\0")
    return h.hexdigest()


def git_state() -> dict:
    """Commit, and whether the working tree differs from it, untracked files included (ignored files excluded)."""
    def git(*args):
        return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False).stdout.strip()

    commit = git("rev-parse", "HEAD") or "none"
    dirty = bool(git("status", "--porcelain"))
    return {"git_commit": commit, "git_dirty": str(dirty).lower(), "code_sha256": code_sha256()}


def metric_key(name: str) -> str:
    """MLflow keys allow letters, digits, _ - . / and spaces; checks use brackets for the station."""
    return re.sub(r"[^A-Za-z0-9_\-./ ]", ".", name).strip(".").replace("..", ".")


@contextmanager
def start_run(experiment: str, run_name: str, tags: dict | None = None):
    """A run created with its tags (the git state and `tags`) in the one call that creates it: the tags are computed
    first, so a failure computing them creates no run, and a run never exists without them (a replay start the
    store holds is always seen by `kind`). Values are stored as text, as mlflow.set_tags stores them."""
    import mlflow

    client()
    all_tags = {k: str(v) for k, v in {**git_state(), **(tags or {})}.items()}
    with mlflow.start_run(experiment_id=experiment_id(experiment), run_name=run_name, tags=all_tags) as run:
        yield run


def latest_run(experiment: str, filter_string: str = ""):
    c = client()
    exp = c.get_experiment_by_name(experiment)
    if exp is None:
        raise LookupError(f"no MLflow experiment named {experiment!r}: run `valiwatt dq` first")
    runs = c.search_runs([exp.experiment_id], filter_string=filter_string, order_by=["attributes.start_time DESC"],
                         max_results=1)
    if not runs:
        raise LookupError(f"no run in {experiment!r} matches {filter_string!r}")
    return runs[0]
