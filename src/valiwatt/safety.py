"""Public-safety scan: this repository goes public with its history, so nothing private may ever be committed.

`valiwatt safety` reads what the next commit would carry and everything the history already carries:

- the STAGED content of every path in git's index (not the working copy);
- every file reachable from any ref (branches, remote branches, tags, the stash, and anything a rewrite leaves behind,
  such as refs/original/), under every path it was ever committed at, so a file committed once and later deleted or
  untracked is still read;
- every commit message, and every annotated tag's message.

It fails on local paths (the home-directory shorthands included), credential shapes, and files that must never be
tracked (data, run outputs, agent run records, the private estate notes). A binary file cannot be read as text, so it
fails too, with one exception: the report's figures, PNG files that must be well formed (only the chunk types in
PNG_CHUNKS, each with a correct checksum), end at their last chunk, and carry only text (their metadata) that passes
the same scan. A hit in the history names its path and object, or its commit or tag, so the owner can see where it
lives; a ref that exists only in this clone is still scanned, because a push could carry it.

Private names (people, other repositories, anything ESTATE.local.md says never to publish) cannot be listed in a
tracked file without publishing them. Put one regular expression per line in the gitignored `.private-patterns`; when
that file exists, the scan applies it to staged content, to every file in the history and to every message too.
"""

from __future__ import annotations

import re
import subprocess
import zlib
from fnmatch import fnmatch
from functools import partial
from pathlib import Path

from valiwatt.paths import REPO_ROOT

CONTENT_PATTERNS = {
    # The shorthands count only with a path segment after them, so a bare tilde or HOME variable in prose passes.
    "home path": re.compile(r"/home/[A-Za-z0-9._-]+|/Users/[A-Za-z0-9._-]+|[A-Za-z]:\\Users\\"
                            r"|~/[A-Za-z0-9._-]|\$(?:HOME|\{HOME\})/[A-Za-z0-9._-]"),
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
FIGURES = "docs/report/figures/*.png"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# The chunks a figure may carry: the standard ones whose content has a fixed meaning, and the three text chunks the scan
# reads. The committed figures use IHDR, pHYs, tEXt, IDAT and IEND. Anything else (eXIf, iCCP, a private chunk) is
# refused, since its content would pass unread.
PNG_CHUNKS = {b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND", b"tEXt", b"zTXt", b"iTXt", b"pHYs", b"sRGB", b"gAMA", b"cHRM",
              b"sBIT", b"bKGD", b"tIME"}


class ScanError(RuntimeError):
    """git could not answer; the scan refuses to call anything clean."""


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)


def tracked_files(root: Path = REPO_ROOT) -> list[str]:
    """Every path in the index: what the next commit would contain."""
    out = _git(root, "ls-files", "--cached", "-z")
    if out.returncode != 0:
        raise ScanError(f"git ls-files failed: {out.stderr.decode().strip()}")
    return sorted(n for n in out.stdout.decode().split("\0") if n)


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


def png_text(data: bytes) -> str | None:
    """The text a PNG carries (its tEXt, zTXt and iTXt chunks), or None unless the bytes are one well-formed PNG with
    nothing after its IEND chunk, no chunk outside PNG_CHUNKS and a correct checksum on every chunk."""
    if not data.startswith(PNG_SIGNATURE):
        return None
    texts, i = [], len(PNG_SIGNATURE)
    try:
        while i + 8 <= len(data):
            length, kind = int.from_bytes(data[i:i + 4], "big"), data[i + 4:i + 8]
            body = data[i + 8:i + 8 + length]
            if len(body) != length or len(data) < i + 12 + length or kind not in PNG_CHUNKS:
                return None
            if zlib.crc32(kind + body) != int.from_bytes(data[i + 8 + length:i + 12 + length], "big"):
                return None
            if kind == b"tEXt":
                texts.append(body.replace(b"\0", b" ").decode("latin-1"))
            elif kind == b"zTXt":
                key, _, rest = body.partition(b"\0")
                texts.append(key.decode("latin-1") + " " + zlib.decompress(rest[1:]).decode("latin-1"))
            elif kind == b"iTXt":
                key, _, rest = body.partition(b"\0")
                compressed, rest = rest[0], rest[2:]
                lang, _, rest = rest.partition(b"\0")
                translated, _, text = rest.partition(b"\0")
                text = zlib.decompress(text) if compressed else text
                texts.append(" ".join(x.decode("utf-8") for x in (key, lang, translated, text)))
            i += 12 + length
            if kind == b"IEND":
                return "\n".join(texts) if i == len(data) else None
    except (zlib.error, UnicodeDecodeError, IndexError):
        return None
    return None


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
            text = png_text(blob.stdout) if fnmatch(n, FIGURES) else None
            if text is None:
                issues.append(f"{n}: binary file tracked")
                continue
        issues += scan_text(n, text, extra)
    return issues


def _read_objects(root: Path, shas: list[str]) -> dict[str, tuple[str, bytes]]:
    """Each object's type and content, read through one `git cat-file --batch` process rather than one per object."""
    out = subprocess.run(["git", "cat-file", "--batch"], cwd=root, input="".join(f"{s}\n" for s in shas).encode(),
                         capture_output=True, check=False)
    if out.returncode != 0:
        raise ScanError(f"git cat-file failed: {out.stderr.decode().strip()}")
    objects, data, i = {}, out.stdout, 0
    while i < len(data):
        end = data.index(b"\n", i)
        header = data[i:end].decode("utf-8", "replace").split(" ")
        if len(header) != 3:
            raise ScanError(f"git could not read object {header[0]}")
        size = int(header[2])
        objects[header[0]] = (header[1], data[end + 1:end + 1 + size])
        i = end + 2 + size
    if len(objects) != len(set(shas)):
        raise ScanError("git cat-file did not return every object it was asked for")
    return objects


def _tree_files(objects: dict, tree: str, memo: dict) -> list[tuple[str, str]]:
    """(path, blob) for every file under a tree; memo keeps the trees already walked, since most repeat across
    commits."""
    if tree not in memo:
        body, files, i, width = objects[tree][1], [], 0, len(tree) // 2
        while i < len(body):
            space, nul = body.index(b" ", i), body.index(b"\0", i)
            mode, name = body[i:space], body[space + 1:nul].decode("utf-8", "replace")
            sha = body[nul + 1:nul + 1 + width].hex()
            if mode == b"40000":
                files += [(f"{name}/{p}", s) for p, s in _tree_files(objects, sha, memo)]
            elif mode != b"160000":  # a submodule's commit lives in another repository
                files.append((name, sha))
            i = nul + 1 + width
        memo[tree] = files
    return memo[tree]


def _blob_name(sha: str, path: str) -> str:
    return f"{path} (blob {sha[:12]})" if path else f"blob {sha[:12]}"


def _headers_and_message(body: bytes) -> tuple[list[bytes], str]:
    head, _, message = body.partition(b"\n\n")
    return head.split(b"\n"), message.decode("utf-8", "replace")


def scan_history(root: Path = REPO_ROOT, extra: dict | None = None) -> list[str]:
    """What the history carries when it goes public: every object reachable from any ref, so every file under every
    path it was committed at (deleted or not), every commit message and every annotated tag's message."""
    if _git(root, "rev-parse", "--git-dir").returncode != 0:
        raise ScanError(f"{root} is not a git repository")
    if _git(root, "rev-parse", "--is-shallow-repository").stdout.decode().strip() == "true":
        raise ScanError("shallow clone: the history scan needs the full history (fetch-depth: 0)")
    listing = _git(root, "rev-list", "--all", "--objects")
    if listing.returncode != 0:
        raise ScanError(f"git rev-list failed: {listing.stderr.decode().strip()}")
    listed = dict(line.partition(" ")[::2] for line in listing.stdout.decode("utf-8", "replace").splitlines())
    objects = _read_objects(root, list(listed))

    # rev-list names each object once, under the first path it met, so the paths come from walking every root tree:
    # each commit's, each tree a tag points at, and each tree a ref names directly.
    issues, roots = [], {s for s, (kind, _) in objects.items() if kind == "tree" and listed[s] == ""}
    for sha, (kind, body) in objects.items():
        if kind == "commit":
            headers, message = _headers_and_message(body)
            roots.add(headers[0].removeprefix(b"tree ").decode())
            issues += scan_text(f"commit {sha[:12]} message", message, extra)
        elif kind == "tag":
            headers, message = _headers_and_message(body)
            fields = dict(h.decode("utf-8", "replace").partition(" ")[::2] for h in headers if b" " in h)
            if fields.get("type") == "tree":
                roots.add(fields["object"])
            issues += scan_text(f"tag {fields.get('tag', sha[:12])} message", message, extra)
    paths, memo = {}, {}
    for tree in sorted(roots):
        for path, blob in _tree_files(objects, tree, memo):
            paths.setdefault(blob, set()).add(path)

    names = sorted(set().union(*paths.values()))
    issues += [f"{n} (in the history): must never be tracked" for n in names if scan_names([n])]
    for sha, (kind, body) in objects.items():
        if kind != "blob":
            continue
        where = sorted(paths.get(sha) or {listed[sha]})
        name = partial(_blob_name, sha)
        try:
            issues += scan_text(name(where[0]), body.decode("utf-8"), extra)
            continue
        except UnicodeDecodeError:
            pass
        figures = [p for p in where if fnmatch(p, FIGURES)]
        text = png_text(body) if figures else None
        binary = where if text is None else [p for p in where if p not in figures]
        issues += [f"{name(p)}: binary file in the history" for p in binary]
        if text is not None:
            issues += scan_text(name(figures[0]), text, extra)
    return issues


def scan(root: Path = REPO_ROOT) -> list[str]:
    extra = private_patterns(root)
    return scan_index(root, extra) + scan_history(root, extra)


def main(allow_no_private_patterns: bool = False) -> int:
    if not private_patterns() and not allow_no_private_patterns:
        print(f"public-safety scan: REFUSED: no {PRIVATE_PATTERNS_FILE} file, so private names cannot be checked. "
              "Pass --no-private-patterns to scan without them (CI does, and cannot check private names).")
        return 2
    try:
        issues = scan()
    except ScanError as exc:
        print(f"public-safety scan: REFUSED: {exc}")
        return 2
    for i in issues:
        print(i)
    note = "" if private_patterns() else "; private names NOT checked (no .private-patterns)"
    print("public-safety scan: " + ("FAILED" if issues else "clean")
          + f" ({len(tracked_files())} staged files and every object in the history{note})")
    return 1 if issues else 0
