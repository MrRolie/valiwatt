"""Outside proof of each issue's time (docs/plan-live.md, Conditions): its sha256, pushed to the public branch
`live-hashes` before the deadline. The hash reveals nothing of the forecast; GitHub's push record dates it."""
from __future__ import annotations

import fcntl
import subprocess

from valiwatt.paths import REPO_ROOT

BRANCH = "live-hashes"
WORKTREE = REPO_ROOT / "data" / "live" / "hashes-worktree"


GIT_TIMEOUT = 90  # seconds: a hung push must not hold the lock past an issue's deadline


def _git(*args, cwd=WORKTREE):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True,
                          timeout=GIT_TIMEOUT).stdout


def commit_hash(body: dict, wait_s: float | None = None) -> None:
    """One push at a time (the midnight issue and the hourly updates run in separate cron steps). The midnight issue
    waits for the lock (wait_s None); an update waits at most wait_s and gives up, so it never delays the issue for
    long, and every git call times out."""
    import time
    with open(WORKTREE.parent / "hashes.lock", "a") as lock:
        if wait_s is None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        else:
            end = time.monotonic() + wait_s
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= end:
                        raise TimeoutError("the hash push lock stayed busy")
                    time.sleep(2)
        _commit_hash(body)


def _commit_hash(body: dict) -> None:
    if not WORKTREE.exists():
        _git("worktree", "add", str(WORKTREE), BRANCH, cwd=REPO_ROOT)
    path = WORKTREE / "hashes.txt"
    with open(path, "a") as fh:
        fh.write(f"{body['day']} {body['sha256']} issued_at={body['issued_at']}\n")
    _git("add", "hashes.txt")
    _git("commit", "-q", "-m", f"Valiwatt Live {body['day']}: {body['sha256']}\n\n"
         "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>")
    _git("push", "-q", "origin", BRANCH)
