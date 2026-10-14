"""The licence page quotes its sources verbatim; the checker itself must fire on an altered quote."""

import pytest
import requests

from valiwatt.licences import DOC, contains, page_text, parse_quotes
from valiwatt.sources.http import USER_AGENT


def test_parser_ties_each_quote_to_its_source():
    md = "Source: <https://a.example/x>\n\n> first line\n> second line\n\ntext\n\n> another\n"
    q = parse_quotes(md)
    assert [(x.url, x.text) for x in q] == [("https://a.example/x", "first line second line"),
                                           ("https://a.example/x", "another")]


def test_quote_without_source_is_an_error():
    with pytest.raises(ValueError):
        parse_quotes("> orphan\n")


def test_checker_fires_on_an_altered_quote():
    page = page_text("<p>You are <strong>free</strong> to copy.</p><script>x</script>")
    assert contains(page, "You are free to copy.")
    assert not contains(page, "You are free to copy and sell.")


def test_doc_has_quotes_for_every_source_section():
    quotes = parse_quotes(DOC.read_text(encoding="utf-8"))
    urls = {q.url for q in quotes}
    assert any("attachment1_e" in u for u in urls) and any("open-government-licence-canada" in u for u in urls)
    assert any("hydroquebec" in u for u in urls) and any("by-nc/4.0/legalcode" in u for u in urls)


@pytest.mark.network
def test_every_quote_is_still_on_its_page():
    quotes = parse_quotes(DOC.read_text(encoding="utf-8"))
    pages, missing = {}, []
    for q in quotes:
        if q.url not in pages:
            r = requests.get(q.url, timeout=60, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            pages[q.url] = page_text(r.text, r.headers.get("content-type", ""))
        if not contains(pages[q.url], q.text):
            missing.append(f"line {q.line}: {q.text[:80]}")
    assert not missing, "\n".join(missing)
