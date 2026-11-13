"""Outside proof of each issue's time (docs/plan-live.md, Conditions): its sha256, pushed to the public branch
`live-hashes` before the deadline. The hash reveals nothing of the forecast; GitHub's push record dates it."""
from __future__ import annotations

import subprocess

from valiwatt.paths import REPO_ROOT

BRANCH = "live-hashes"
WORKTREE = REPO_ROOT / "data" / "live" / "hashes-worktree"


def _git(*args, cwd=WORKTREE):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def commit_hash(body: dict) -> None:
    if not WORKTREE.exists():
        _git("worktree", "add", str(WORKTREE), BRANCH, cwd=REPO_ROOT)
    path = WORKTREE / "hashes.txt"
    with open(path, "a") as fh:
        fh.write(f"{body['day']} {body['sha256']} issued_at={body['issued_at']}\n")
    _git("add", "hashes.txt")
    _git("commit", "-q", "-m", f"Valiwatt Live issue {body['day']}: {body['sha256']}\n\n"
         "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>")
    _git("push", "-q", "origin", BRANCH)
