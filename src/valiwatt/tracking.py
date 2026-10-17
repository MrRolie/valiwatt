"""MLflow tracking in a local, gitignored store (mlruns/). Every figure in a report comes from a run logged here."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from contextlib import contextmanager

from valiwatt.paths import MLRUNS, REPO_ROOT

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

# The MLflow environment variables a tracked run may start under: this package's own, and the tracking URI, which
# mlflow.set_tracking_uri sets from the one client() gives it and which never decides where a run goes. Any other
# (MLFLOW_RUN_ID, MLFLOW_RUN_CONTEXT, MLFLOW_EXPERIMENT_ID, MLFLOW_ENABLE_SYSTEM_METRICS_LOGGING, ...) can change
# which run mlflow.start_run enters, what it adds to it, or whether entering it fails once it exists.
MLFLOW_ENV_ALLOWED = ("MLFLOW_DISABLE_AGENT_HINT", "MLFLOW_TRACKING_URI")


def ambient_mlflow_env() -> list[str]:
    """The MLflow environment variables set that a tracked run may not start under (none: it may start)."""
    return sorted(k for k in os.environ if k.startswith("MLFLOW_") and k not in MLFLOW_ENV_ALLOWED)


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
    """A fresh run of `experiment`, created with its tags (the git state and `tags`) in the one call that creates it:
    the tags are computed first, so a failure computing them creates no run, and a run never exists without them (a
    replay start the store holds is always seen by `kind`). The run is created here and entered by its ID, so an
    ambient run ID (MLFLOW_RUN_ID, which mlflow.start_run would otherwise resume, in that run's own experiment and
    with its own start time) never makes a start another run. Values are stored as text, as mlflow.set_tags stores
    them, beside the context tags mlflow.start_run adds (user, source).

    Refused before any run exists: an MLflow environment variable other than MLFLOW_ENV_ALLOWED; a run already active
    in this process; an experiment other than `experiment` selected with mlflow.set_experiment. Each could make the
    run another one, or refuse entering the fresh run once created. Should entering it fail all the same, the run is
    ended FAILED with its error's class (tag `error`), never left RUNNING."""
    import mlflow
    from mlflow.tracking import fluent
    from mlflow.tracking.context import registry

    if ambient := ambient_mlflow_env():
        raise RuntimeError(f"MLflow environment variables {ambient} are set: a tracked run starts under none but "
                           f"{list(MLFLOW_ENV_ALLOWED)}; unset them and run again")
    c = client()
    exp = experiment_id(experiment)
    if (active := mlflow.active_run()) is not None:
        raise RuntimeError(f"run {active.info.run_id} is active: a tracked run is never started inside another")
    selected = getattr(fluent, "_active_experiment_id", None)  # what mlflow.start_run checks a run_id against
    if selected is not None and selected != exp:
        raise RuntimeError(f"experiment {selected} is selected (mlflow.set_experiment), not {experiment!r} ({exp})")
    all_tags = {k: str(v) for k, v in {**git_state(), **(tags or {})}.items()}
    # The run's name among its own tags before the context's are resolved, as mlflow.start_run puts it: an ambient
    # context naming the run otherwise (MLFLOW_RUN_CONTEXT) never overrides it, nor collides with it.
    all_tags["mlflow.runName"] = run_name
    created = c.create_run(exp, tags=registry.resolve_tags(all_tags), run_name=run_name)
    try:
        entered = mlflow.start_run(run_id=created.info.run_id)
    except BaseException as e:
        c.set_tag(created.info.run_id, "error", type(e).__name__)
        c.set_terminated(created.info.run_id, "FAILED")
        raise
    with entered as run:
        if (run.info.run_id, run.info.experiment_id) != (created.info.run_id, exp):
            raise RuntimeError(f"run {run.info.run_id} of experiment {run.info.experiment_id} started in place of the "
                               f"fresh run {created.info.run_id} of {experiment!r}")
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
