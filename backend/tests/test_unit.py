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


@pytest.mark.parametrize("title,text,expected", [
    ("Just a moment...", "# Are you a robot? Please confirm you are a human", True),
    ("Verifying your browser | OpenReview", "Complete the check below to continue", True),
    ("请稍候…", "正在验证您是否是真人。这可能需要几秒钟时间。", True),
    ("Self-supervised learning of molecules", "A long article about CAPTCHA design. " * 200, False),
    ("Example Domain", "This domain is for use in documentation examples.", False),
])
def test_challenge_detection(title, text, expected):
    from webkit_api.page import looks_like_challenge
    assert looks_like_challenge(title, text) is expected


def test_to_markdown_fit_drops_site_chrome():
    from webkit_api.page import to_markdown
    html = """<html><head><title>Knowledge graph</title></head><body>
      <header><nav>Main menu Home About</nav></header>
      <a class="skip-link" href="#c">Skip to main content</a>
      <main id="c"><h1>Knowledge graph</h1>
        <p>A knowledge graph is a knowledge base that uses a graph-structured data model to represent
        and operate on data. It stores interlinked descriptions of entities, objects, events and concepts.</p>
        <p>Knowledge graphs are used in search engines, question answering and recommender systems,
        where they connect facts across many sources and support reasoning over relations.</p>
        <p>See <a href="https://arxiv.org/abs/2002.00388">the survey</a> for details.</p></main>
      <footer>Privacy policy | Terms</footer></body></html>"""
    fit, cleaned, links, meta = to_markdown("https://example.org/wiki/kg", html, fit=True)
    assert "graph-structured data model" in fit
    assert "Main menu" not in fit and "Privacy policy" not in fit and "Skip to main content" not in fit
    full, *_ = to_markdown("https://example.org/wiki/kg", html, fit=False)
    assert "Privacy policy" in full
    assert any(l.href == "https://arxiv.org/abs/2002.00388" for l in links.external)


def test_wall_response_detection():
    class R:
        def __init__(self, status, headers=None):
            self.status, self.headers = status, headers or {}
    assert serp._is_wall_response(R(405, {"x-amzn-waf-action": "captcha"}), "")
    assert serp._is_wall_response(R(403), "<title>Just a moment...</title>")
    assert not serp._is_wall_response(R(404), "captcha")
    assert not serp._is_wall_response(R(200), "captcha")


def test_decode_handles_io_read_and_fetch_bodies():
    from webkit_api.download import _decode
    import base64
    assert _decode({"data": base64.b64encode(b"%PDF-1").decode(), "base64Encoded": True}) == b"%PDF-1"
    assert _decode({"body": base64.b64encode(b"%PDF-2").decode(), "base64Encoded": True}) == b"%PDF-2"
    assert _decode({"body": "plain", "base64Encoded": False}) == b"plain"


# ─── walls: a person can pass vs. this server is refused ──────────────────────

DATADOME = ("<html><head><title>researchgate.net</title></head><body><script>var dd={'rt':'c','cid':'x',"
            "'t':'%s','host':'geo.captcha-delivery.com'}</script></body></html>")


@pytest.mark.parametrize("text,html,expected", [
    ("", DATADOME % "bv", "blocked"),        # DataDome ban: no CAPTCHA is offered
    ("", DATADOME % "fe", "human"),          # DataDome CAPTCHA a person can solve
    ("Attention Required! | Cloudflare Sorry, you have been blocked", "", "blocked"),
    ("Just a moment... Verify you are human", "", "human"),
    ("请稍候… 正在进行安全验证", "", "human"),
    ("Deep Residual Learning for Image Recognition. Abstract: deeper networks…", "", None),
])
def test_wall_kind(text, html, expected):
    from webkit_api.download import wall_kind
    assert wall_kind(text, html) == expected


def test_page_wall_kind_reads_markup_of_empty_pages():
    from webkit_api.page import page_wall_kind
    assert page_wall_kind("researchgate.net", "", DATADOME % "bv") == "blocked"
    assert page_wall_kind("Article", "x" * 5000) is None  # a long page is content


def test_body_wall_kind_ignores_full_pages_with_captcha_scripts():
    from webkit_api.download import body_wall_kind
    article = ("<html><head><title>Paper</title><script src='https://www.google.com/recaptcha/api.js'></script>"
               "</head><body>" + "<p>text</p>" * 20000 + "</body></html>").encode()
    assert body_wall_kind("text/html", article) is None
    small = b"<html><head><title>Just a moment...</title><script>var captcha=1</script></head><body></body></html>"
    assert body_wall_kind("text/html", small) == "human"
    assert body_wall_kind("application/pdf", b"%PDF-1.7 captcha") is None


def test_error_builders_say_what_to_do():
    h = errors.human_required("https://x/y", "challenge")
    b = errors.blocked("https://x/y", "refused")
    assert (h.error, h.status, b.error) == ("human_required", 409, "blocked")
    assert "Do not retry" in h.hint and "not accessible" in h.hint and "Do not retry" in b.hint
    assert errors.ERROR_STATUS["not_a_file"] == 422


# ─── paper page -> PDF ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("html,expected", [
    ('<meta name="citation_pdf_url" content="https://arxiv.org/pdf/2403.01092">', "https://arxiv.org/pdf/2403.01092"),
    ("<meta content='/content/pdf/10.1007/x.pdf' name='citation_pdf_url'/>",
     "https://link.springer.com/content/pdf/10.1007/x.pdf"),
    ('<meta name="citation_pdf_url" content="https://openreview.net/pdf?id=A&amp;b=1">',
     "https://openreview.net/pdf?id=A&b=1"),
    ('<link rel="alternate" type="application/pdf" href="/doi/pdf/10.1/x">', "https://link.springer.com/doi/pdf/10.1/x"),
    ("<meta name='citation_title' content='No PDF here'>", None),
])
def test_pdf_link(html, expected):
    from webkit_api.page import pdf_link
    assert pdf_link(f"<html><head>{html}</head></html>", "https://link.springer.com/article/x") == expected


def test_to_markdown_fit_drops_skip_links_banners_and_hidden_text():
    from webkit_api.page import to_markdown
    body = ("<a class='c-skip-link' href='#main'>Skip to main content</a>"
            "<div id='siteNotice'>The internet's changing. An important update for readers.</div>"
            "<div hidden><span>在新窗口中打开</span><span>打开外部网站</span></div>"
            "<main><h1>Knowledge graph</h1>" + "<p>A knowledge graph is a knowledge base that uses a graph "
            "structured data model to represent entities and their relations in a domain.</p>" * 6 + "</main>")
    text = to_markdown("https://en.wikipedia.org/wiki/K", f"<html><body>{body}</body></html>", True)[0]
    assert "knowledge base" in text
    for junk in ("Skip to", "internet's changing", "新窗口"):
        assert junk not in text


def test_google_asks_for_the_plain_web_tab():
    url = serp.google_url("V*: guided search", 10, "year")
    assert "udm=14" in url and "tbs=qdr:y" in url and "num=10" in url


def _fetched(url, ctype, body):
    async def nothing():
        if False:
            yield b""
    return Fetched(url, 200, {"content-type": ctype, "content-length": str(len(body))}, "stream", body, nothing())


@pytest.fixture
def fake_files(monkeypatch):
    from webkit_api import download, page
    files = {"https://dl.acm.org/doi/10.1/x": ("text/html", b"<html>landing</html>"),
             "https://dl.acm.org/doi/pdf/10.1/x": ("application/pdf", b"%PDF-1.7 paper"),
             "https://blog.example/post": ("text/html", b"<html>post</html>")}
    links = {"https://dl.acm.org/doi/10.1/x": "https://dl.acm.org/doi/pdf/10.1/x"}

    async def fetch(url, limit):
        return _fetched(url, *files[url])

    async def find_pdf_url(url, timeout=60):
        return links.get(url)

    monkeypatch.setattr(download, "fetch", fetch)
    monkeypatch.setattr(page, "find_pdf_url", find_pdf_url)
    return files


def test_download_resolves_a_paper_page_to_its_pdf(fake_files):
    r = client.get("/v2/download", params={"url": "https://dl.acm.org/doi/10.1/x"})
    assert r.status_code == 200 and r.content == b"%PDF-1.7 paper"
    assert "dl.acm.org/doi/10.1/x" in r.headers["X-Webkit-Resolved-From"]


def test_download_of_a_plain_web_page_is_not_a_file(fake_files):
    r = client.get("/v2/download", params={"url": "https://blog.example/post"})
    assert r.status_code == 422 and r.json()["error"] == "not_a_file" and "webkit read" in r.json()["hint"]
    r = client.get("/v2/download", params={"url": "https://blog.example/post", "allow_html": "true"})
    assert r.status_code == 200 and r.content == b"<html>post</html>"


def test_read_pdf_without_a_pdf_link_is_not_found(monkeypatch):
    from webkit_api import page

    async def no(*a, **k):
        return None

    async def not_pdf(url):
        return False

    monkeypatch.setattr(page, "_is_pdf", not_pdf)
    monkeypatch.setattr(page, "find_pdf_url", no)
    r = client.post("/v2/page", json={"url": "https://blog.example/post", "pdf": True})
    assert r.status_code == 404 and r.json()["error"] == "not_found"
