"""The standards page quotes SR 11-7 and the NIST AI RMF verbatim; the PDF checker must fire on an altered quote."""

import pytest
import requests

from valiwatt.licences import STANDARDS, parse_quotes, pdf_contains, pdf_text
from valiwatt.sources.http import USER_AGENT

PDFS = ("https://www.federalreserve.gov/boarddocs/srletters/2011/sr1107a1.pdf",
        "https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf")


def test_pdf_checker_ignores_extraction_noise_but_not_words():
    extracted = "should be imple-\nmentation of \u201cCore\u201d  functions"
    assert pdf_contains(extracted, 'should be implementation of "Core" functions')
    assert not pdf_contains(extracted, "should be implementation of the Core functions")
    assert not pdf_contains(extracted, "should be implemented of Core functions")


def test_every_quote_cites_one_of_the_two_documents():
    quotes = parse_quotes(STANDARDS.read_text(encoding="utf-8"))
    assert {q.url for q in quotes} == set(PDFS)
    assert len(quotes) >= 40


@pytest.mark.network
def test_every_quote_is_still_in_its_pdf():
    quotes = parse_quotes(STANDARDS.read_text(encoding="utf-8"))
    texts, missing = {}, []
    for q in quotes:
        if q.url not in texts:
            r = requests.get(q.url, timeout=120, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            assert r.content[:5] == b"%PDF-", f"{q.url} did not return a PDF"
            texts[q.url] = pdf_text(r.content)
        if not pdf_contains(texts[q.url], q.text):
            missing.append(f"line {q.line}: {q.text[:80]}")
    assert len(texts) == 2 and all(len(t) > 50_000 for t in texts.values())
    assert not missing, "\n".join(missing)
