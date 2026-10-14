"""Public-safety scan: this repository goes public with its history, so nothing private may ever be committed.

`valiwatt safety` reads exactly what a commit would carry: the STAGED content of every path in git's index (not the
working copy), plus every commit message in the history. It fails on local paths, credential shapes, and files that
must never be tracked (data, run outputs, agent run records, the private estate notes).

Private names (people, other repositories, anything ESTATE.local.md says never to publish) cannot be listed in a
tracked file without publishing them. Put one regular expression per line in the gitignored `.private-patterns`; when
that file exists, the scan applies it to staged content and commit messages too.
"""

from __future__ import annotations

import re
import subprocess
from fnmatch import fnmatch
from pathlib import Path

from valiwatt.paths import REPO_ROOT

CONTENT_PATTERNS = {
    "home path": re.compile(r"/home/[A-Za-z0-9._-]+|/Users/[A-Za-z0-9._-]+|[A-Za-z]:\\Users\\"),
    "scratch path": re.compile(r"/tmp/claude-\d"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"),
    "API key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}|\bAKIA[0-9A-Z]{16}\b|\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}

FORBIDDEN_FILES = [
    "data/*", "mlruns/*", "mlartifacts/*", "reports/*", "docs/audits/*", "*.args.json", "ESTATE.local.md", "*.parquet",
    "*.env", ".env*", ".private-patterns",
]

PRIVATE_PATTERNS_FILE = ".private-patterns"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)


def tracked_files(root: Path = REPO_ROOT) -> list[str]:
    """Every path in the index: what the next commit would contain."""
    out = _git(root, "ls-files", "--cached", "-z").stdout.decode()
    return sorted(n for n in out.split("\0") if n)


def private_patterns(root: Path = REPO_ROOT) -> dict:
    path = root / PRIVATE_PATTERNS_FILE
    if not path.exists():
        return {}
    lines = [x.strip() for x in path.read_text(encoding="utf-8").splitlines()]
    return {f"private pattern {i}": re.compile(x, re.IGNORECASE) for i, x in enumerate(lines, 1) if x and not x.startswith("#")}


def scan_text(name: str, text: str, extra: dict | None = None) -> list[str]:
    issues = []
    for label, pattern in {**CONTENT_PATTERNS, **(extra or {})}.items():
        for m in pattern.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            issues.append(f"{name}:{line}: {label}")
    return issues


def scan_names(names: list[str]) -> list[str]:
    return [f"{n}: must never be tracked" for n in names if any(fnmatch(n, pat) for pat in FORBIDDEN_FILES)]


def scan_index(root: Path = REPO_ROOT, extra: dict | None = None) -> list[str]:
    names = tracked_files(root)
    issues = scan_names(names)
    for n in names:
        blob = _git(root, "show", f":{n}")
        if blob.returncode != 0:
            issues.append(f"{n}: in the index but unreadable")
            continue
        if not (root / n).exists():
            issues.append(f"{n}: staged but missing from the working tree")
        try:
            text = blob.stdout.decode("utf-8")
        except UnicodeDecodeError:
            issues.append(f"{n}: binary file tracked")
            continue
        issues += scan_text(n, text, extra)
    return issues


def scan_history(root: Path = REPO_ROOT, extra: dict | None = None) -> list[str]:
    """Commit messages travel with the history when it goes public."""
    log = _git(root, "log", "--all", "--format=%H%x00%B%x01")
    if log.returncode != 0:
        return []  # no commits yet
    issues = []
    for entry in log.stdout.decode("utf-8", "replace").split("\x01"):
        if "\x00" not in entry:
            continue
        sha, body = entry.strip("\n").split("\x00", 1)
        issues += scan_text(f"commit {sha[:12]} message", body, extra)
    return issues


def scan(root: Path = REPO_ROOT) -> list[str]:
    extra = private_patterns(root)
    return scan_index(root, extra) + scan_history(root, extra)


def main() -> int:
    issues = scan()
    for i in issues:
        print(i)
    note = "" if private_patterns() else "; no .private-patterns file, so private names were not checked"
    print("public-safety scan: " + ("FAILED" if issues else "clean")
          + f" ({len(tracked_files())} staged files and the commit history{note})")
    return 1 if issues else 0
