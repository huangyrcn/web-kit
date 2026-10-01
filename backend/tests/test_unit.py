"""Offline unit tests: no Chrome, no SearXNG, no network."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from webkit_api import errors, search, serp, stats
from webkit_api.download import Fetched, filename_for, looks_like_human_wall
from webkit_api.main import app
from webkit_api.page import pdf_to_text

client = TestClient(app)  # no `with`: lifespan (CDP connect, egress loop) is not started


# ─── parsers ───────────────────────────────────────────────────────────────────

ARXIV_HTML = """
<ol><li class="arxiv-result">
  <p class="list-title"><a href="https://arxiv.org/abs/2609.31881">arXiv:2609.31881</a>
   <span class="tag is-small is-link">cs.LG</span> <span class="tag is-small is-grey">cs.AI</span></p>
  <p class="title is-5 mathjax">TemporalGraphLLM: <span class="search-hit">Temporal</span> Graph Networks</p>
  <p class="authors"><span>Authors:</span><a href="/a/x">Ada Lovelace</a>, <a href="/a/y">Alan Turing</a></p>
  <p class="abstract mathjax"><span class="abstract-full has-text-grey-dark mathjax">We study graphs.
   △ Less</span></p>
  <p class="is-size-7"><span class="has-text-black-bis">Submitted</span> 29 September, 2026; originally announced.</p>
  <p class="comments"><span class="has-text-black-bis">Comments:</span> Accepted at ICML 2026</p>
  <a href="https://arxiv.org/pdf/2609.31881">pdf</a>
</li></ol>
"""


def test_arxiv_parser_extracts_fields():
    [r] = serp.parse_arxiv_html(ARXIV_HTML, 10)
    assert r["title"] == "TemporalGraphLLM: Temporal Graph Networks"
    assert r["url"] == "https://arxiv.org/abs/2609.31881"
    assert r["snippet"] == "We study graphs."
    assert r["published"] == "29 September, 2026"
    assert r["extra"]["authors"] == ["Ada Lovelace", "Alan Turing"]
    assert r["extra"]["pdf"] == "https://arxiv.org/pdf/2609.31881"
    assert r["extra"]["comments"] == "Accepted at ICML 2026"


@pytest.mark.parametrize("href,expected", [
    ("/url?q=https://example.org/a&sa=U", "https://example.org/a"),
    ("https://www.google.com/url?q=https://maps.google.com/x", None),
    ("https://example.org/b", "https://example.org/b"),
    ("https://support.google.com/x", None),
    (None, None),
])
def test_clean_google_url(href, expected):
    assert serp.clean_google_url(href) == expected


def test_goto_links_detected():
    assert serp._is_goto("/goto?url=CAESYQ")
    assert serp._is_goto("https://www.google.com/goto?url=x")
    assert not serp._is_goto("/url?q=https://a")
    assert not serp._is_goto(None)


# ─── error classification ──────────────────────────────────────────────────────

def test_classify_network_and_timeout():
    assert errors.classify(RuntimeError("Page.goto: net::ERR_CONNECTION_CLOSED at https://x")).error == "network"
    assert errors.classify(asyncio.TimeoutError()).error == "timeout"
    assert errors.classify(ValueError("boom")).error == "internal"


def test_human_wall_detection():
    assert looks_like_human_wall("text/html", b"<div class='g-recaptcha'></div>")
    assert not looks_like_human_wall("application/pdf", b"%PDF-1.7 recaptcha")
    assert not looks_like_human_wall("text/html", b"<h1>Hello</h1>")


def test_filename_for():
    async def none():
        if False:
            yield b""
    f = Fetched("https://x.org/papers/2609.31881", 200, {"content-type": "application/pdf"},
                "stream", b"%PDF-1.7", none())
    assert filename_for(f) == "2609.31881.pdf"
    f.headers["content-disposition"] = "attachment; filename*=UTF-8''r%C3%A9sum%C3%A9.pdf"
    assert filename_for(f) == "résumé.pdf"


# ─── PDF text ──────────────────────────────────────────────────────────────────

def _tiny_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def test_pdf_to_text():
    title, text, pages = pdf_to_text(_tiny_pdf("Hello web-kit"))
    assert pages == 1
    assert "<!-- page 1 -->" in text and "Hello web-kit" in text


# ─── search orchestration ──────────────────────────────────────────────────────

@pytest.fixture
def fake_engines(monkeypatch):
    behaviour: dict = {}

    async def fake_call(name, q, limit, time_range, lang):
        b = behaviour.get(name, [])
        if isinstance(b, Exception):
            raise b
        return [dict(r) for r in b]

    async def fake_time_support(name):
        return ["day", "week", "month", "year"] if name in ("google", "duckduckgo") else []

    monkeypatch.setattr(search, "_call", fake_call)
    monkeypatch.setattr(search, "time_support", fake_time_support)
    stats._engines.clear()
    return behaviour


def test_fallback_reports_every_engine(fake_engines):
    fake_engines["google"] = RuntimeError("net::ERR_CONNECTION_CLOSED")
    fake_engines["duckduckgo"] = [{"title": "A", "url": "https://a"}]
    r = client.get("/v2/search", params={"q": "x"}).json()
    assert [e["status"] for e in r["engines"]] == ["error", "ok", "skipped"]
    assert r["engines"][0]["error"] == "network"
    assert r["fallback_used"] is True
    assert r["results"][0] == {"title": "A", "url": "https://a", "engine": "duckduckgo", "rank": 1}
    assert stats.snapshot()["google"]["last_error"]["class"] == "network"


def test_all_engines_failed_is_an_error(fake_engines):
    for name in search.PROFILES["general"]:
        fake_engines[name] = RuntimeError("net::ERR_CONNECTION_RESET")
    resp = client.get("/v2/search", params={"q": "x"})
    assert resp.status_code == 502
    body = resp.json()
    assert body["error"] == "engines_failed" and body["classes"] == ["network"]
    assert len(body["engines"]) == 3


def test_empty_is_not_an_error(fake_engines):
    resp = client.get("/v2/search", params={"q": "x"})
    assert resp.status_code == 200
    assert resp.json()["results"] == []
    assert [e["status"] for e in resp.json()["engines"]] == ["empty", "empty", "empty"]


def test_time_filter_skips_unsupported_engines(fake_engines):
    fake_engines["bing"] = [{"title": "B", "url": "https://b"}]
    r = client.get("/v2/search", params={"q": "x", "time": "week"}).json()
    statuses = {e["engine"]: e["status"] for e in r["engines"]}
    assert statuses["bing"] == "skipped" and r["results"] == []


def test_validation_errors_are_json(fake_engines):
    assert client.get("/v2/search", params={"q": "x", "engine": "nope"}).json()["error"] == "invalid_request"
    assert client.get("/v2/search", params={"q": "x", "time": "decade"}).status_code == 400
    assert client.get("/v2/search", params={"q": "x", "engine": "arxiv", "time": "day"}).status_code == 400
    assert client.get("/v2/search").json()["error"] == "invalid_request"


def test_busy_propagates(fake_engines):
    fake_engines["google"] = errors.WebkitError("busy", "all pages busy")
    resp = client.get("/v2/search", params={"q": "x"})
    assert resp.status_code == 503 and resp.json()["error"] == "busy"


def test_page_rejects_non_http():
    resp = client.post("/v2/page", json={"url": "file:///etc/passwd"})
    assert resp.status_code == 400 and resp.json()["error"] == "invalid_request"


def test_crawl_caps():
    resp = client.post("/v2/crawl", json={"url": "https://example.org", "max_pages": 999})
    assert resp.status_code == 400 and "capped" in resp.json()["message"]
