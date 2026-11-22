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

    async def fake_call(name, q, limit, time_range, lang, **kw):
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


def test_s2_wall_ignores_the_embedded_waf_token_script():
    # Normal S2 result pages and the silent 202 challenge both carry these strings.
    normal = '<script src="https://x.token.awswaf.com/x/challenge.js"></script><div class="cl-paper-row">'
    assert not serp.s2_is_blocked(None, normal)
    assert serp.s2_is_blocked(None, '<div id="captcha-container"></div><script src="x.awswaf.com/captcha.js">')
    assert serp.s2_is_blocked(None, "<title>Just a moment...</title> Checking if the site connection is secure")


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


# ─── Semantic Scholar: Graph API first, website second ────────────────────────

S2_API_BODY = {"total": 3, "offset": 0, "data": [
    {"paperId": "62ed", "url": "https://www.semanticscholar.org/paper/62ed",
     "title": "How Powerful are Graph Neural Networks?", "abstract": "Long abstract.",
     "tldr": {"model": "tldr@v2.0.0", "text": "GNNs are  as powerful as WL."},
     "venue": "ICLR", "year": 2018, "publicationDate": "2018-10-01", "citationCount": 10956,
     "authors": [{"authorId": "1", "name": "Keyulu Xu"}, {"authorId": "2", "name": "Weihua Hu"}],
     "openAccessPdf": {"url": "http://arxiv.org/pdf/1810.00826", "status": "GREEN"},
     "fieldsOfStudy": ["Computer Science", "Mathematics"],
     "externalIds": {"ArXiv": "1810.00826", "DOI": "10.48550/arXiv.1810.00826"}},
    {"paperId": "aa", "url": "https://www.semanticscholar.org/paper/aa", "title": "No TLDR",
     "abstract": "Abstract only.", "tldr": None, "year": 2020, "publicationDate": None,
     "citationCount": 0, "authors": [], "openAccessPdf": None, "fieldsOfStudy": None, "externalIds": None},
    {"paperId": "bb", "url": "https://www.semanticscholar.org/paper/bb", "title": ""},
]}


def test_s2_api_body_maps_to_the_website_result_shape():
    from webkit_api import s2api
    first, second = s2api.parse(S2_API_BODY, 10)
    assert first["title"] == "How Powerful are Graph Neural Networks?"
    assert first["snippet"] == "GNNs are as powerful as WL." and first["published"] == "2018-10-01"
    assert first["extra"] == {"authors": ["Keyulu Xu", "Weihua Hu"], "venue": "ICLR",
                              "fields": "Computer Science, Mathematics", "citations": "10956",
                              "pdf": "http://arxiv.org/pdf/1810.00826",
                              "doi": "10.48550/arXiv.1810.00826", "arxiv": "1810.00826"}
    assert second["snippet"] == "Abstract only." and second["published"] == "2020"
    assert second["extra"] == {"citations": "0"}
    assert len(s2api.parse(S2_API_BODY, 1)) == 1


def test_s2_error_page_is_blocked_only_for_403():
    url = "https://www.semanticscholar.org/search?q=x"
    err = serp.s2_error_page(url, "Our servers are having a bit of trouble.\nError: 403\nGo back")
    assert err.error == "blocked" and "Do not retry" in err.hint
    assert serp.s2_error_page(url, "Error: 500").error == "upstream_http"
    assert serp.s2_error_page(url, "something went wrong").error == "upstream_http"


@pytest.fixture
def s2_paths(monkeypatch):
    from webkit_api import s2api
    calls: list[str] = []
    behaviour = {"api": [{"title": "from api"}], "web": [{"title": "from web"}]}

    async def fake(path, q, limit):
        calls.append(path)
        if isinstance(behaviour[path], Exception):
            raise behaviour[path]
        return behaviour[path]

    monkeypatch.setattr(s2api, "search", lambda q, limit: fake("api", q, limit))
    monkeypatch.setattr(serp, "_s2_website", lambda q, limit: fake("web", q, limit))
    monkeypatch.setattr(serp.settings, "S2_API_KEY", "k")
    return calls, behaviour


def test_s2_uses_the_api_and_never_the_website_when_the_api_answers(s2_paths):
    calls, _ = s2_paths
    assert asyncio.run(serp.semantic_scholar("gnn", 5)) == [{"title": "from api"}]
    assert calls == ["api"]


def test_s2_api_empty_answer_is_final(s2_paths):
    calls, behaviour = s2_paths
    behaviour["api"] = []
    assert asyncio.run(serp.semantic_scholar("gnn", 5)) == [] and calls == ["api"]


def test_s2_falls_back_to_the_website_on_api_failure(s2_paths):
    calls, behaviour = s2_paths
    behaviour["api"] = errors.WebkitError("upstream_http", "semantic_scholar API returned HTTP 500")
    assert asyncio.run(serp.semantic_scholar("gnn", 5)) == [{"title": "from web"}]
    assert calls == ["api", "web"]


def test_s2_reports_both_paths_when_both_fail(s2_paths):
    _, behaviour = s2_paths
    behaviour["api"] = errors.WebkitError("timeout", "semantic_scholar API did not answer")
    behaviour["web"] = errors.blocked("https://www.semanticscholar.org/search?q=gnn", "error page (HTTP 403)")
    with pytest.raises(errors.WebkitError) as e:
        asyncio.run(serp.semantic_scholar("gnn", 5))
    assert e.value.error == "blocked" and "API path: timeout" in e.value.message


def test_s2_without_a_key_uses_the_website_only(s2_paths, monkeypatch):
    calls, _ = s2_paths
    monkeypatch.setattr(serp.settings, "S2_API_KEY", "")
    assert asyncio.run(serp.semantic_scholar("gnn", 5)) == [{"title": "from web"}] and calls == ["web"]


@pytest.fixture
def s2_http(monkeypatch):
    """s2api over a fake transport; answers are popped from `replies` in order."""
    import httpx
    from webkit_api import s2api
    replies: list[tuple[int, dict]] = []
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        status, body = replies.pop(0)
        return httpx.Response(status, json=body)

    monkeypatch.setattr(s2api.settings, "S2_API_INTERVAL_MS", 50)
    monkeypatch.setattr(s2api, "_last_end", 0.0)
    monkeypatch.setattr(s2api, "_pace_lock", asyncio.Lock())
    monkeypatch.setattr(s2api, "_client", httpx.AsyncClient(
        base_url="https://api.test/graph/v1", headers={"x-api-key": "k"}, transport=httpx.MockTransport(handler)))
    return replies, seen


def test_s2_api_sends_the_key_and_paces_calls(s2_http):
    import time
    from webkit_api import s2api
    replies, seen = s2_http
    replies += [(200, S2_API_BODY)] * 3

    async def three_at_once():
        t0 = time.monotonic()
        await asyncio.gather(*(s2api.search("gnn", 2) for _ in range(3)))
        return time.monotonic() - t0

    assert asyncio.run(three_at_once()) >= 0.1  # concurrent callers still go one by one, 50 ms apart
    assert all(r.headers["x-api-key"] == "k" for r in seen)
    assert seen[0].url.params["query"] == "gnn" and "tldr" in seen[0].url.params["fields"]


def test_s2_api_retries_429_then_gives_up(s2_http, monkeypatch):
    from webkit_api import s2api
    monkeypatch.setattr(s2api.settings, "S2_API_RETRIES", 3)
    replies, seen = s2_http
    replies += [(429, {}), (429, {}), (503, {}), (200, S2_API_BODY)]
    assert len(asyncio.run(s2api.search("gnn", 10))) == 2 and len(seen) == 4
    replies += [(429, {})] * 4
    with pytest.raises(errors.WebkitError) as e:
        asyncio.run(s2api.search("gnn", 10))
    assert e.value.error == "upstream_http" and "429" in e.value.message and len(seen) == 8


def test_s2_api_rejected_key_is_reported(s2_http):
    from webkit_api import s2api
    replies, _ = s2_http
    replies.append((403, {"message": "Forbidden"}))
    with pytest.raises(errors.WebkitError) as e:
        asyncio.run(s2api.search("gnn", 10))
    assert "rejected the key" in e.value.message


# ─── named browser targets (webkit-executor + opencli on another machine) ─────

@pytest.fixture
def lab(monkeypatch):
    """One target 'lab' served by a fake executor; `answers[path]` is (status, body) or a list."""
    import httpx
    from webkit_api import settings, targets
    answers: dict = {}
    seen: list = []

    def handler(request):
        import json as _json
        seen.append((request.url.path, _json.loads(request.content or b"{}"), request.headers.get("x-executor-key")))
        a = answers[request.url.path]
        status, body = a.pop(0) if isinstance(a, list) else a
        if isinstance(body, Exception):
            raise body
        return httpx.Response(status, json=body)

    monkeypatch.setattr(settings, "TARGETS", {"lab": "http://lab.test"})
    monkeypatch.setattr(settings, "TARGET_KEYS", {"lab": "secret"})
    monkeypatch.setattr(settings, "DEFAULT_VIA", "hnas")
    targets._health.clear()
    monkeypatch.setattr(targets, "_clients", {"lab": httpx.AsyncClient(
        base_url="http://lab.test", headers={"X-Executor-Key": "secret"}, transport=httpx.MockTransport(handler))})
    stats._engines.clear()
    return answers, seen


def _env(data=None, error=None, exit=0):
    return (200, {"exit": exit, "envelope": {"ok": True, "data": data} if error is None
                  else {"ok": False, "error": error}, "stderr": ""})


def test_opencli_failures_map_to_webkit_classes():
    from webkit_api import targets
    def cls(exit_code, code):
        err = targets._opencli_error("lab", "x", exit_code, {"ok": False, "error": {"code": code, "message": "m"}})
        return err.error if err else None
    assert cls(66, "EMPTY_RESULT") is None and cls(1, "NOT_FOUND") is None
    assert cls(69, "BROWSER_CONNECT") == "browser_unavailable"
    assert cls(75, "SESSION_BUSY") == "timeout"  # never "busy": that would abort a whole search chain
    assert cls(77, "AUTH_REQUIRED") == "human_required"
    assert cls(1, "COMMAND_EXEC") == "upstream_http"


def test_target_run_sends_the_key_and_returns_rows(lab):
    from webkit_api import targets
    answers, seen = lab
    answers["/run"] = _env([{"title": "t", "url": "https://x.org"}])
    rows = asyncio.run(targets.run("lab", "main", "google search", "gnn", 5, {"lang": "en"}))
    assert rows == [{"title": "t", "url": "https://x.org"}]
    assert seen[0] == ("/run", {"command": "google search", "query": "gnn", "limit": 5, "options": {"lang": "en"},
                                "profile": "main"}, "secret")


def test_target_unreachable_or_disconnected_marks_it_unavailable(lab):
    import httpx
    from webkit_api import targets
    answers, _ = lab
    answers["/run"] = (200, httpx.ConnectError("refused"))
    with pytest.raises(errors.WebkitError) as e:
        asyncio.run(targets.run("lab", None, "google search", "q", 5))
    assert e.value.error == "browser_unavailable" and not targets.available("lab")
    targets._health.clear()
    answers["/run"] = _env(error={"code": "BROWSER_CONNECT", "message": "extension not connected"}, exit=69)
    with pytest.raises(errors.WebkitError) as e:
        asyncio.run(targets.run("lab", None, "google search", "q", 5))
    assert e.value.error == "browser_unavailable" and not targets.available("lab")


def test_target_key_rejected_and_busy(lab):
    from webkit_api import targets
    answers, _ = lab
    answers["/run"] = [(401, {"error": "unauthorized", "message": "x"}), (503, {"error": "busy", "message": "full"})]
    with pytest.raises(errors.WebkitError) as e:
        asyncio.run(targets.run("lab", None, "google search", "q", 5))
    assert e.value.error == "internal" and "key" in e.value.message
    with pytest.raises(errors.WebkitError) as e:
        asyncio.run(targets.run("lab", None, "google search", "q", 5))
    assert e.value.error == "timeout"


def test_via_chain_is_parsed_in_order():
    from webkit_api import settings, targets
    old = settings.TARGETS
    try:
        settings.TARGETS = {"lab": "http://x", "mac": "http://y"}
        assert targets.plan("local,hnas", "lab") == [("lab", None), ("hnas", None)]
        assert targets.plan("lab/main, hnas, lab/main", None) == [("lab", "main"), ("hnas", None)]
        assert targets.plan("local,hnas", "pc") == [("hnas", None)]  # no target for this machine
        assert targets.plan(None, None) == [(settings.DEFAULT_VIA, None)]
        for bad in ("nowhere", "local"):
            with pytest.raises(errors.WebkitError) as e:
                targets.plan(bad, "pc")
            assert e.value.error == "invalid_request"
    finally:
        settings.TARGETS = old


def test_opencli_rows_map_onto_the_result_shape():
    google = serp.map_google_rows([{"type": "paa", "title": "Q?", "url": "https://q"},
                                   {"type": "result", "title": "A", "url": "https://a", "snippet": "s"},
                                   {"type": "result", "title": "A again", "url": "https://a"}], 10)
    assert google == [{"title": "A", "url": "https://a", "snippet": "s"}]
    scholar = serp.map_scholar_rows([{"title": "GNN", "url": "https://n.org/x", "authors": "G Corso, H Stark…",
                                      "source": "Nature Reviews …", "year": "2024", "cited": "788"}], 10)
    assert scholar == [{"title": "GNN", "url": "https://n.org/x", "snippet": "", "published": "2024",
                        "extra": {"byline": "G Corso, H Stark… - Nature Reviews …", "citations": "788"}}]
    ddg = serp.map_duckduckgo_rows([{"title": "T", "url": "https://t", "snippet": "s", "resultType": "web"},
                                    {"title": "V", "url": "https://v", "resultType": "video"}], 10)
    assert [r["url"] for r in ddg] == ["https://t"]


def test_target_empty_answer_is_checked_against_the_live_page(lab):
    answers, seen = lab
    answers["/run"] = _env(error={"code": "EMPTY_RESULT", "message": "no results"}, exit=66)
    answers["/page"] = (200, {"ok": True, "html": "<html>sorry</html>", "title": "",
                              "final_url": "https://www.google.com/sorry/index", "text": "unusual traffic"})
    with pytest.raises(errors.WebkitError) as e:
        asyncio.run(serp.via_target("google_scholar", "lab", None, "gnn", 5))
    assert e.value.error == "captcha" and seen[-1][0] == "/page"
    answers["/page"] = (200, {"ok": True, "html": "<html>nothing</html>", "title": "Google Scholar",
                              "final_url": "https://scholar.google.com/scholar?q=x", "text": "did not match any articles"})
    assert asyncio.run(serp.via_target("google_scholar", "lab", None, "gnn", 5)) == []


S2_ROWS_HTML = """<html><body>
<div class="cl-paper-row serp-papers__paper-row">
  <a href="/paper/How-Powerful-GNN/62ed"><h2 class="cl-paper-title">How Powerful are GNNs?</h2></a>
  <span class="cl-paper-authors__author-box">Keyulu Xu</span><span class="cl-paper-authors__author-box">W. Hu</span>
  <span class="cl-paper-venue">ICLR</span><span class="cl-paper-pubdates">1 October 2018</span>
  <div class="tldr-abstract-replacement">TLDR This work characterizes GNNs. Expand</div>
  <a class="cl-paper-stats__citation-pdp-link" href="/paper/x#citing-papers">10,956 Citations</a>
  <a class="cl-paper-view-paper" href="https://arxiv.org/pdf/1810.00826">PDF</a>
</div></body></html>"""


def test_s2_website_html_from_a_target_is_parsed_like_the_live_page():
    [r] = serp.parse_s2_html(S2_ROWS_HTML, "https://www.semanticscholar.org/search?q=gnn", 10)
    assert r["title"] == "How Powerful are GNNs?" and r["url"] == "https://www.semanticscholar.org/paper/How-Powerful-GNN/62ed"
    assert r["snippet"] == "This work characterizes GNNs." and r["published"] == "1 October 2018"
    assert r["extra"] == {"authors": ["Keyulu Xu", "W. Hu"], "venue": "ICLR", "citations": "10956",
                          "pdf": "https://arxiv.org/pdf/1810.00826"}
    err_page = '<div data-test-id="error-message-block"><h1>Our servers are having a bit of trouble.</h1>Error: 403</div>'
    with pytest.raises(errors.WebkitError) as e:
        serp.parse_s2_html(err_page, "https://www.semanticscholar.org/search?q=gnn", 10)
    assert e.value.error == "blocked"
    with pytest.raises(errors.WebkitError) as e:
        serp.parse_s2_html("<html><body>loading</body></html>", "https://s2/search", 10)
    assert e.value.error == "unexpected_page"


@pytest.fixture
def routed(monkeypatch, lab):
    """Search routing with fake HNAS engines and a fake target path."""
    behaviour = {"hnas": {}, "lab": {}}
    calls: list = []

    async def fake_call(name, q, limit, time_range, lang, **kw):
        calls.append((name, "hnas", kw.get("skip_s2_api", False)) if kw.get("skip_s2_api") else (name, "hnas"))
        b = behaviour["hnas"].get(name, [])
        if isinstance(b, Exception):
            raise b
        return [dict(r) for r in b]

    async def fake_target(name, target, profile, q, limit, time_range, lang, api_first):
        calls.append((name, target, profile, api_first))
        b = behaviour["lab"].get(name, [])
        if isinstance(b, Exception):
            raise b
        return [dict(r) for r in b]

    async def fake_time_support(name):
        return []

    monkeypatch.setattr(search, "_call", fake_call)
    monkeypatch.setattr(search, "_call_target", fake_target)
    monkeypatch.setattr(search, "time_support", fake_time_support)
    return behaviour, calls


def test_chain_moves_on_when_a_browser_fails(routed):
    behaviour, calls = routed
    behaviour["hnas"]["google_scholar"] = errors.WebkitError("captcha", "unusual traffic")
    behaviour["lab"]["google_scholar"] = [{"title": "T", "url": "https://t"}]
    body = client.get("/v2/search", params={"q": "gnn", "engine": "google_scholar", "via": "hnas,lab"}).json()
    assert calls == [("google_scholar", "hnas"), ("google_scholar", "lab", None, True)]
    assert [(e["via"], e["status"]) for e in body["engines"]] == [("hnas", "error"), ("lab", "ok")]
    assert body["results"][0]["via"] == "lab" and body["fallback_used"] is True
    assert stats.snapshot()["google_scholar@lab"]["calls"] == 1


def test_chain_without_a_next_browser_reports_the_failure(routed):
    behaviour, calls = routed
    behaviour["hnas"]["google_scholar"] = errors.WebkitError("captcha", "x")
    r = client.get("/v2/search", params={"q": "x", "engine": "google_scholar", "via": "hnas"})
    assert r.status_code == 502 and calls == [("google_scholar", "hnas")]
    assert client.get("/v2/search", params={"q": "x", "via": "nowhere"}).status_code == 400


def test_a_target_runs_browser_engines_and_api_engines_stay_here(routed):
    behaviour, calls = routed
    behaviour["lab"]["semantic_scholar"] = errors.WebkitError("blocked", "403")
    behaviour["lab"]["google_scholar"] = []
    behaviour["hnas"]["openalex"] = [{"title": "O", "url": "https://o"}]
    body = client.get("/v2/search", params={"q": "x", "profile": "academic", "via": "lab/main"}).json()
    assert calls == [("semantic_scholar", "lab", "main", True), ("google_scholar", "lab", "main", True),
                     ("openalex", "hnas")]
    assert [e.get("via") for e in body["engines"]] == ["lab", "lab", "hnas", None]


def test_an_unavailable_target_is_reported_not_called(routed):
    from webkit_api import targets
    behaviour, calls = routed
    targets._mark("lab", False, "down")
    behaviour["hnas"]["google_scholar"] = errors.WebkitError("captcha", "x")
    r = client.get("/v2/search", params={"q": "x", "engine": "google_scholar", "via": "hnas,lab"})
    assert r.status_code == 502 and calls == [("google_scholar", "hnas")]
    assert [e["error"] for e in r.json()["engines"]] == ["captcha", "browser_unavailable"]


def test_local_runs_on_the_callers_own_machine_then_the_next_browser(routed):
    behaviour, calls = routed
    behaviour["lab"]["google_scholar"] = errors.WebkitError("captcha", "x")
    behaviour["hnas"]["google_scholar"] = [{"title": "H", "url": "https://h"}]
    body = client.get("/v2/search", params={"q": "x", "engine": "google_scholar", "via": "local,hnas"},
                      headers={"X-Webkit-Client": "LAB.tailnet"}).json()
    assert calls == [("google_scholar", "lab", None, True), ("google_scholar", "hnas")]
    assert [(e["via"], e["status"]) for e in body["engines"]] == [("lab", "error"), ("hnas", "ok")]


def test_s2_asks_the_api_once_per_search(routed):
    behaviour, calls = routed
    behaviour["lab"]["semantic_scholar"] = errors.WebkitError("upstream_http", "API 429; website 500")
    behaviour["hnas"]["semantic_scholar"] = [{"title": "S", "url": "https://s"}]
    client.get("/v2/search", params={"q": "x", "engine": "semantic_scholar", "via": "local,hnas"},
               headers={"X-Webkit-Client": "lab"})
    assert calls == [("semantic_scholar", "lab", None, True), ("semantic_scholar", "hnas", True)]


def test_local_on_a_machine_without_a_target(routed):
    behaviour, calls = routed
    behaviour["hnas"]["google_scholar"] = [{"title": "H", "url": "https://h"}]
    r = client.get("/v2/search", params={"q": "x", "engine": "google_scholar", "via": "local,hnas"},
                   headers={"X-Webkit-Client": "macbook"})
    assert r.status_code == 200 and calls == [("google_scholar", "hnas")]
    r = client.get("/v2/search", params={"q": "x", "engine": "google_scholar", "via": "local"},
                   headers={"X-Webkit-Client": "macbook"})
    assert r.status_code == 400  # an empty chain is an error, never a silent default


def test_a_target_without_the_time_filter_is_skipped(routed):
    behaviour, calls = routed

    async def all_times(name):
        return ["day", "week", "month", "year"]
    search.time_support = all_times
    behaviour["hnas"]["google"] = [{"title": "G", "url": "https://g"}]
    body = client.get("/v2/search", params={"q": "x", "engine": "google", "via": "local,hnas", "time": "week"},
                      headers={"X-Webkit-Client": "lab"}).json()
    assert calls == [("google", "hnas")] and body["engines"][0]["status"] == "skipped"


@pytest.fixture
def pages(monkeypatch, lab):
    from webkit_api import page
    answers, seen = lab
    rendered = {"html": "<html><head><title>H</title></head><body><p>hnas body</p></body></html>",
                "title": "H", "final_url": "https://a.org/", "status": 200}
    hnas = {"result": rendered}

    async def fake_render(url, wait_for, scroll, timeout):
        r = hnas["result"]
        if isinstance(r, Exception):
            raise r
        return dict(r, final_url=url)

    async def not_pdf(url):
        return False

    def fake_markdown(url, html, fit):
        return ("md:" + html, html, None, {})

    monkeypatch.setattr(page, "render", fake_render)
    monkeypatch.setattr(page, "_is_pdf", not_pdf)
    monkeypatch.setattr(page, "to_markdown", fake_markdown)
    answers["/page"] = (200, {"ok": True, "html": "<html>lab body " + "x" * 300 + "</html>", "title": "L",
                              "final_url": "https://a.org/", "text": "lab body " + "x" * 300})
    return hnas, answers, seen


def test_read_moves_to_the_next_browser_when_one_is_walled(pages):
    hnas, _, seen = pages
    hnas["result"] = errors.blocked("https://a.org/", "block page")
    out = client.post("/v2/page", json={"url": "https://a.org/", "via": "hnas,lab"}).json()
    assert out["via"] == "lab" and out["content"].startswith("md:<html>lab body")
    assert out["attempts"] == [{"via": "hnas", "error": "blocked", "message": "block page"}]
    assert seen[-1][1]["url"] == "https://a.org/" and seen[-1][1]["settle_ms"] == 2000


def test_read_treats_an_http_error_as_a_failure_and_stops_at_success(pages):
    hnas, _, seen = pages
    hnas["result"] = dict(hnas["result"], status=403)
    assert client.post("/v2/page", json={"url": "https://a.org/", "via": "hnas,lab"}).json()["via"] == "lab"
    seen.clear()
    hnas["result"] = dict(hnas["result"], status=200)
    out = client.post("/v2/page", json={"url": "https://a.org/", "via": "hnas,lab"}).json()
    assert "via" not in out and "attempts" not in out and seen == []


def test_read_local_first_then_this_server(pages):
    _, answers, seen = pages
    out = client.post("/v2/page", json={"url": "https://a.org/", "via": "local,hnas"},
                      headers={"X-Webkit-Client": "lab"}).json()
    assert out["via"] == "lab" and seen[-1][0] == "/page"
    answers["/page"] = (200, {"ok": False, "step": "open", "exit": 69,
                              "envelope": {"ok": False, "error": {"code": "BROWSER_CONNECT", "message": "x"}}})
    out = client.post("/v2/page", json={"url": "https://a.org/x", "via": "local,hnas"},
                      headers={"X-Webkit-Client": "lab"}).json()
    assert "via" not in out and out["attempts"][0]["error"] == "browser_unavailable"
    from webkit_api import targets
    assert targets.local_name("Lab.tail1234.ts.net") == "lab" and targets.local_name("mac") is None


def test_read_on_a_target_still_refuses_wall_pages(pages):
    _, answers, _ = pages
    answers["/page"] = (200, {"ok": True, "html": "<html>Just a moment...</html>", "title": "Just a moment...",
                              "final_url": "https://a.org/", "text": "Checking if the site connection is secure"})
    r = client.post("/v2/page", json={"url": "https://a.org/", "via": "lab"})
    assert r.status_code == 409 and r.json()["error"] == "human_required"
