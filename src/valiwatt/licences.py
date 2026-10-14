"""Checks that every quotation in docs/data-licences.md and docs/standards.md still appears on the page or PDF it cites."""

from __future__ import annotations

import html
import io
import json
import re
import unicodedata
from dataclasses import dataclass

from valiwatt.paths import REPO_ROOT

DOC = REPO_ROOT / "docs" / "data-licences.md"
STANDARDS = REPO_ROOT / "docs" / "standards.md"
SOURCE = re.compile(r"^Source: <(https?://[^>]+)>\s*$")


@dataclass
class Quote:
    url: str
    text: str
    line: int


def parse_quotes(markdown: str) -> list[Quote]:
    """Each blockquote paragraph is checked against the page of the nearest `Source:` line above it."""
    quotes, url, buf, start = [], None, [], 0
    lines = markdown.split("\n")
    for i, line in enumerate(lines + [""], start=1):
        m = SOURCE.match(line)
        if m:
            url = m.group(1)
        if line.startswith(">"):
            if not buf:
                start = i
            buf.append(line[1:].strip())
            continue
        if buf:
            text = " ".join(x for x in buf if x)
            if url is None:
                raise ValueError(f"line {start}: quotation with no Source line above it")
            if text:
                quotes.append(Quote(url, text, start))
            buf = []
    return quotes


def page_text(body: str, content_type: str = "") -> str:
    if "json" in content_type or body.lstrip().startswith(("{", "[")):
        strings = []

        def walk(o):
            if isinstance(o, dict):
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
            elif isinstance(o, str):
                strings.append(o)

        walk(json.loads(body))
        body = "\n".join(strings)
    body = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", body)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    return html.unescape(body)


def squash(text: str) -> str:
    """Whitespace removed: markup inserts or drops spaces; every other character must match."""
    return re.sub(r"\s+", "", text)


def contains(page: str, quote: str) -> bool:
    return squash(quote) in squash(page)


def pdf_text(content: bytes) -> str:
    from pypdf import PdfReader

    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages)


def letters(text: str) -> str:
    """Letters and digits only, in order: PDF extraction drops spacing, punctuation and line-break hyphens."""
    return re.sub(r"[^0-9A-Za-z]", "", unicodedata.normalize("NFKC", text))


def pdf_contains(text: str, quote: str) -> bool:
    return letters(quote) in letters(text)
