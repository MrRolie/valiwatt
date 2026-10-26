"""v3's first-run guard (docs/plan-v3.md, section 10.2).

Every v3 command that computes on real data (data quality, validation, freezes, the rehearsal, the replay, and the read
of v2's store) refuses unless the `v3-first-run` tag exists and is an ancestor of the head (or the head itself): the
tag marks the commit from which v3 first runs on real data, so every rule change after it is disclosed. v2's guard,
`quality.run.require_first_run`, checks v2's tag for v2's commands and is unchanged; this one is v3's, called first by
each v3 command before it opens any file.
"""

from __future__ import annotations

import subprocess

from valiwatt.config import V3_FIRST_RUN_TAG
from valiwatt.paths import REPO_ROOT


class FirstRunRefused(RuntimeError):
    """A v3 command that computes on real data ran from a commit that does not descend from `v3-first-run`."""


def _git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False)


def require_first_run() -> str:
    """The `v3-first-run` tag exists and is an ancestor of the head (or the head itself), else FirstRunRefused.
    Returns the commit the tag names."""
    tag = _git("rev-parse", "-q", "--verify", f"refs/tags/{V3_FIRST_RUN_TAG}^{{commit}}")
    commit = tag.stdout.strip()
    if tag.returncode != 0 or not commit:
        raise FirstRunRefused(f"no `{V3_FIRST_RUN_TAG}` tag: a v3 command computes on real data only from a commit "
                              "that descends from it (docs/plan-v3.md, section 10.2)")
    if _git("merge-base", "--is-ancestor", commit, "HEAD").returncode != 0:
        raise FirstRunRefused(f"the head does not descend from `{V3_FIRST_RUN_TAG}` ({commit[:12]}): no v3 real-data "
                              "run from it")
    return commit
