"""CLI tests against a fake /v2 server (stdlib only, no backend needed)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from webkit import cli

KEY, ADMIN = "k" * 64, "a" * 64
ROUTES: dict = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, status, body, ctype="application/json", headers=None):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def _route(self, method):
        u = urlparse(self.path)
        admin = u.path.startswith("/v2/admin/")
        if admin and self.headers.get("X-Admin-Key") != ADMIN or not admin and self.headers.get("X-API-Key") != KEY:
            return self._send(401, {"error": "unauthorized", "message": "X-API-Key required"})
        body = None
        if method == "POST":
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        handler = ROUTES.get((method, u.path))
        if handler is None:
            return self._send(404, {"error": "not_found", "message": u.path})
        handler(self, {k: v[0] for k, v in parse_qs(u.query).items()}, body)

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")


@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def env(server, monkeypatch, tmp_path):
    ROUTES.clear()
    monkeypatch.setenv("WEBKIT_URL", server)
    monkeypatch.setenv("WEBKIT_API_KEY", KEY)
    monkeypatch.setenv("WEBKIT_ADMIN_KEY", ADMIN)
    monkeypatch.setenv("WEBKIT_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("WEBKIT_DOWNLOAD_DIR", str(tmp_path / "dl"))
    monkeypatch.setenv("WEBKIT_CACHE_DIR", str(tmp_path / "cache"))
    for v in ("CLAUDE_PLUGIN_OPTION_URL", "CLAUDE_PLUGIN_OPTION_API_KEY"):
        monkeypatch.delenv(v, raising=False)


def run(*argv) -> int:
    with pytest.raises(SystemExit) as e:
        cli.main(list(argv))
    return e.value.code


def search_result(results, engines):
    return {"query": "q", "profile": "general", "engines": engines, "results": results, "fallback_used": False}


def test_search_text_header_and_results(capsys):
    ROUTES[("GET", "/v2/search")] = lambda h, q, b: h._send(200, search_result(
        [{"rank": 1, "title": "LANTERN", "url": "https://acl/559", "snippet": "TKG", "engine": "duckduckgo"}],
        [{"engine": "google", "status": "error", "error": "network"},
         {"engine": "duckduckgo", "status": "ok", "count": 1}, {"engine": "bing", "status": "skipped"}]))
    assert run("search", "lantern") == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "# engines google=error:network duckduckgo=ok:1 bing=skipped"
    assert lines[1:3] == ["1. LANTERN", "   https://acl/559"]


def test_search_urls_only_puts_header_on_stderr(capsys):
    ROUTES[("GET", "/v2/search")] = lambda h, q, b: h._send(200, search_result(
        [{"rank": 1, "title": "A", "url": "https://a", "engine": "google"}],
        [{"engine": "google", "status": "ok", "count": 1}]))
    assert run("search", "--urls", "x") == 0
    captured = capsys.readouterr()
    assert captured.out == "https://a\n" and captured.err.startswith("# engines google=ok:1")


def test_search_passes_params():
    seen = {}

    def h(handler, q, b):
        seen.update(q)
        handler._send(200, search_result([], [{"engine": "arxiv", "status": "empty"}]))
    ROUTES[("GET", "/v2/search")] = h
    assert run("search", "-e", "arxiv", "-n", "3", "--time", "week", "two", "words") == 2  # no results
    assert seen == {"q": "two words", "engine": "arxiv", "limit": "3", "time": "week"}


@pytest.mark.parametrize("status,body,code", [
    (502, {"error": "engines_failed", "message": "every engine failed"}, 3),
    (503, {"error": "busy", "message": "all pages busy"}, 7),
    (409, {"error": "human_required", "message": "captcha"}, 6),
    (400, {"error": "invalid_request", "message": "bad"}, 1),
])
def test_error_classes_map_to_exit_codes(status, body, code, capsys):
    ROUTES[("GET", "/v2/search")] = lambda h, q, b: h._send(status, body)
    assert run("search", "x") == code
    assert json.loads(capsys.readouterr().err.strip().splitlines()[-1])["error"] == body["error"]


def test_auth_and_unreachable(monkeypatch, capsys):
    monkeypatch.setenv("WEBKIT_API_KEY", "wrong")
    assert run("status") == 4
    monkeypatch.setenv("WEBKIT_URL", "http://127.0.0.1:9")
    assert run("status") == 5
    assert "unreachable" in capsys.readouterr().err


def test_read_saves_file_and_dir(tmp_path, capsys):
    def page(h, q, b):
        h._send(200, {"url": b["url"], "title": "Doc Title", "kind": "html", "format": b["format"],
                      "content": f"content of {b['url']}", "chars": 20, "truncated": False})
    ROUTES[("POST", "/v2/page")] = page
    assert run("read", "-o", str(tmp_path / "one.md"), "https://x/a") == 0
    assert (tmp_path / "one.md").read_text() == "content of https://x/a"
    assert run("read", "-o", str(tmp_path / "many"), "https://x/a", "https://x/b") == 0
    assert sorted(p.name for p in (tmp_path / "many").iterdir()) == ["01-doc-title.md", "02-doc-title.md"]
    assert "saved" in capsys.readouterr().out


def test_search_read_saves_pages_and_prints_paths(tmp_path, capsys):
    calls = []

    def page(h, q, b):
        calls.append(b)
        h._send(200, {"url": b["url"], "title": "", "kind": "html", "format": "fit", "content": "x", "chars": 1})
    ROUTES[("GET", "/v2/search")] = lambda h, q, b: h._send(200, search_result(
        [{"rank": i, "title": "t", "url": f"https://r/{i}", "engine": "google"} for i in (1, 2, 3)],
        [{"engine": "google", "status": "ok", "count": 3}]))
    ROUTES[("POST", "/v2/page")] = page
    assert run("search", "--read", "2", "q") == 0
    assert [c["url"] for c in calls] == ["https://r/1", "https://r/2"]
    assert all(c["max_chars"] == 0 for c in calls)  # full text, into files
    saved = [l for l in capsys.readouterr().out.splitlines() if l.startswith("saved ")]
    assert len(saved) == 2 and all(str(tmp_path / "cache" / "pages") in l for l in saved)


def test_download_default_dir_and_length_check(tmp_path, capsys):
    payload = b"%PDF-1.7 fake"
    ROUTES[("GET", "/v2/download")] = lambda h, q, b: h._send(
        200, payload, "application/pdf", {"X-Webkit-Filename": "paper.pdf"})
    assert run("download", "https://x/paper") == 0
    assert (tmp_path / "dl" / "paper.pdf").read_bytes() == payload
    assert "saved" in capsys.readouterr().out


def test_download_human_required_without_wait(capsys):
    ROUTES[("GET", "/v2/download")] = lambda h, q, b: h._send(
        409, {"error": "human_required", "message": "captcha", "hint": "solve it"})
    assert run("download", "https://x/y") == 6
    err = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert err["error"] == "human_required" and err["vnc"].endswith("/vnc.html")


def test_config_set_reads_key_from_stdin(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("WEBKIT_API_KEY")
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("secret-value-123\n"))
    assert run("config", "set", "api-key") == 0
    cfg_file = tmp_path / "config.toml"
    assert 'api_key = "secret-value-123"' in cfg_file.read_text()
    assert oct(cfg_file.stat().st_mode & 0o777) == "0o600"
    assert run("config", "set", "api-key", "on-the-command-line") == 1
    capsys.readouterr()
    assert run("config", "show") == 0
    shown = capsys.readouterr().out
    assert "secr…23" in shown and "secret-value-123" not in shown


def test_usage_error_exit_code_is_1():
    assert run("search") == 1


def test_skill_and_reference(capsys):
    assert run("skill", "show") == 0
    skill = capsys.readouterr().out
    assert skill.startswith("---\nname: web-kit") and "reference.md" in skill and "usage: webkit" not in skill
    assert run("skill", "show", "--reference") == 0
    ref = capsys.readouterr().out
    for cmd in ("webkit search", "webkit read", "webkit crawl", "webkit download"):
        assert f"## {cmd}" in ref


def test_doctor_reports(capsys):
    ROUTES[("GET", "/v2/version")] = lambda h, q, b: h._send(200, {"webkit": "2.0.0", "api": "v2"})
    ROUTES[("GET", "/v2/status")] = lambda h, q, b: h._send(200, {
        "ok": False, "browser": {"connected": True}, "egress_down": ["www.google.com"], "engines": {}})
    ROUTES[("GET", "/v2/admin/ping")] = lambda h, q, b: h._send(200, {"admin": True})
    assert run("doctor") == 3
    out = capsys.readouterr().out
    assert "✗ egress DOWN: www.google.com" in out and "✓ admin key accepted" in out


def test_download_rejects_empty_file(tmp_path, capsys):
    ROUTES[("GET", "/v2/download")] = lambda h, q, b: h._send(200, b"", "application/pdf", {"X-Webkit-Filename": "x.pdf"})
    assert run("download", "https://x/x.pdf") == 3
    assert not (tmp_path / "dl" / "x.pdf").exists()



def test_blocked_maps_to_exit_8(capsys):
    ROUTES[("POST", "/v2/page")] = lambda h, q, b: h._send(
        502, {"error": "blocked", "message": "refused", "hint": "Do not retry"})
    assert run("read", "https://www.researchgate.net/x") == 8
    assert json.loads(capsys.readouterr().err.strip().splitlines()[-1])["error"] == "blocked"


def test_read_targets_cap_and_pdf_flag(tmp_path):
    calls = []

    def page(h, q, b):
        calls.append(b)
        h._send(200, {"url": b["url"], "title": "", "kind": "html", "format": "fit", "content": "x", "chars": 1})
    ROUTES[("POST", "/v2/page")] = page
    assert run("read", "10.1145/3774904.3792101", "doi:10.1016/A.b", "arXiv:2403.01092v2", "https://x/y") == 0
    assert [c["url"] for c in calls] == ["https://doi.org/10.1145/3774904.3792101", "https://doi.org/10.1016/A.b",
                                         "https://arxiv.org/abs/2403.01092v2", "https://x/y"]
    assert all(c["max_chars"] == 0 and "pdf" not in c for c in calls)
    calls.clear()
    assert run("read", "--pdf", "-o", str(tmp_path / "p.md"), "arXiv:2403.01092") == 0
    assert calls[0]["pdf"] is True and calls[0]["max_chars"] == 0


def test_download_reports_resolved_pdf_and_not_a_file(capsys):
    seen = {}

    def dl(h, q, b):
        seen.update(q)
        h._send(200, b"%PDF-1.7 x", "application/pdf",
                {"X-Webkit-Filename": "x.pdf", "X-Webkit-Resolved-From": "https%3A//doi.org/10.1/x",
                 "X-Webkit-Final-Url": "https%3A//pub/x.pdf"})
    ROUTES[("GET", "/v2/download")] = dl
    assert run("download", "10.1145/x") == 0
    assert seen == {"url": "https://doi.org/10.1145/x"}  # allow_html only when asked
    assert "the PDF linked from the page: https://pub/x.pdf" in capsys.readouterr().out
    ROUTES[("GET", "/v2/download")] = lambda h, q, b: h._send(
        422, {"error": "not_a_file", "message": "web page", "hint": "use webkit read"})
    assert run("download", "https://blog/post") == 3


def test_skill_install_for_codex(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert run("skill", "install", "--agent", "codex") == 0
    text = (tmp_path / ".codex/skills/web-kit/SKILL.md").read_text()
    ref = (tmp_path / ".codex/skills/web-kit/reference.md").read_text()
    assert "Codex: web search / open" in text and "## webkit download" in ref and "webkit browser" not in ref


def test_help_states_the_positioning(capsys):
    assert run("--help") == 0
    text = capsys.readouterr().out
    assert "Fallback web access" in text and "8 blocked" in text and "cdp" not in text




# ─── output budget: page text never floods the caller's context ───────────────

BIG = "".join(f"line {i}: " + "lorem ipsum dolor sit amet " * 6 + "\n" for i in range(1300))  # ~200k chars


def _serve_pages(content=BIG, links=0):
    def page(h, q, b):
        body = {"url": b["url"], "title": "Big page", "kind": "html", "format": b["format"]}
        if b["format"] == "links":
            body["links"] = [{"href": f"https://x/{i}", "text": "link text " * 20} for i in range(links)]
        else:
            body["content"] = content
        h._send(200, body)
    ROUTES[("POST", "/v2/page")] = page


def _stdout_len(capsys) -> tuple[str, int]:
    o = capsys.readouterr().out
    return o, len(o)


def test_long_page_is_saved_with_a_preview(tmp_path, capsys):
    _serve_pages()
    assert run("read", "https://x/a") == 0
    o, n = _stdout_len(capsys)
    assert n <= cli.DEFAULT_BUDGET + 50
    [path] = list((tmp_path / "cache" / "pages").iterdir())
    assert path.read_text() == BIG and str(path) in o
    assert "--max-chars 0" not in o and "grep -n" in o


def test_short_page_is_printed(capsys):
    _serve_pages(content="short text")
    assert run("read", "https://x/a") == 0
    assert capsys.readouterr().out == "short text\n"


def test_json_with_output_prints_a_manifest_not_the_text(tmp_path, capsys):
    _serve_pages()
    assert run("read", "--json", "-o", str(tmp_path / "p.md"), "https://x/a") == 0
    o, n = _stdout_len(capsys)
    data = json.loads(o)
    assert n < 1000 and "content" not in data and data["chars"] == len(BIG)
    assert (tmp_path / "p.md").read_text() == BIG


def test_search_read_json_with_output(tmp_path, capsys):
    _serve_pages()
    ROUTES[("GET", "/v2/search")] = lambda h, q, b: h._send(200, search_result(
        [{"rank": i, "title": "t", "url": f"https://r/{i}", "engine": "google"} for i in (1, 2, 3)],
        [{"engine": "google", "status": "ok", "count": 3}]))
    assert run("search", "--read", "3", "--json", "-o", str(tmp_path / "d"), "q") == 0
    o, n = _stdout_len(capsys)
    assert n < cli.DEFAULT_BUDGET and len(json.loads(o)["pages"]) == 3 and "lorem" not in o
    assert all(f.read_text() == BIG for f in (tmp_path / "d").iterdir())


def test_crawl_json_saves_files_and_prints_the_index(tmp_path, capsys):
    ROUTES[("POST", "/v2/crawl")] = lambda h, q, b: h._send(200, {
        "url": b["url"], "count": 10,
        "pages": [{"url": f"https://d/{i}", "depth": 1, "title": f"p{i}", "content": BIG} for i in range(10)]})
    assert run("crawl", "--json", "-o", str(tmp_path / "c"), "https://d/") == 0
    o, n = _stdout_len(capsys)
    assert n < cli.DEFAULT_BUDGET and "lorem" not in o and json.loads(o)["count"] == 10
    assert len(list((tmp_path / "c").glob("*.md"))) == 10


def test_many_urls_share_one_budget(tmp_path, capsys):
    _serve_pages(content="x" * 30000)
    urls = [f"https://x/{i}" for i in range(10)]
    assert run("read", *urls) == 0
    o, n = _stdout_len(capsys)
    assert n <= cli.DEFAULT_BUDGET + 10 * 50
    assert len(list((tmp_path / "cache" / "pages").iterdir())) == 10


def test_links_obey_the_budget(capsys):
    _serve_pages(links=2000)
    assert run("read", "-f", "links", "--max-chars", "100", "https://x/a") == 0
    o, n = _stdout_len(capsys)
    assert n < 100 + cli.NOTE_RESERVE


def test_budget_has_a_hard_ceiling(capsys):
    _serve_pages()
    assert run("read", "--max-chars", "999999", "https://x/a") == 0
    assert len(capsys.readouterr().out) <= cli.MAX_BUDGET + 400


def test_long_listing_spills_to_a_file(tmp_path, capsys):
    ROUTES[("GET", "/v2/search")] = lambda h, q, b: h._send(200, search_result(
        [{"rank": i, "title": "title " * 30, "url": f"https://r/{i}", "snippet": "s" * 200, "engine": "google"}
         for i in range(1, 60)], [{"engine": "google", "status": "ok", "count": 59}]))
    assert run("search", "-n", "59", "q") == 0
    o, n = _stdout_len(capsys)
    assert n < cli.DEFAULT_BUDGET + 400 and "output stopped at" in o
    [spill] = list((tmp_path / "cache" / "output").iterdir())
    assert "https://r/59" in spill.read_text()


def test_error_lines_are_capped(capsys):
    ROUTES[("GET", "/v2/search")] = lambda h, q, b: h._send(
        502, {"error": "engines_failed", "message": "m" * 5000, "hint": "h", "engines": ["e" * 100] * 100})
    assert run("search", "q") == 3
    err = capsys.readouterr().err.strip().splitlines()[-1]
    assert len(err) <= cli.ERR_LINE_MAX and json.loads(err)["error"] == "engines_failed"



def test_search_json_shrinks_to_fit_and_keeps_results(tmp_path, capsys):
    ROUTES[("GET", "/v2/search")] = lambda h, q, b: h._send(200, search_result(
        [{"rank": i, "title": "A fairly long result title " * 2, "url": f"https://site.example/path/{i}",
          "snippet": "snippet text " * 30, "engine": "google"} for i in range(1, 11)],
        [{"engine": "google", "status": "ok", "count": 10}]))
    assert run("search", "--json", "q") == 0
    o, n = _stdout_len(capsys)
    data = json.loads(o)
    assert n <= cli.DEFAULT_BUDGET and len(data["results"]) == 10 and data["results"][9]["url"].endswith("/10")
    full = json.loads(open(data["full_json"]).read())
    assert len(full["results"][0]["snippet"]) > 300  # the file keeps everything


def test_oversized_json_stub_keeps_its_summary_apart(capsys):
    ROUTES[("GET", "/v2/search")] = lambda h, q, b: h._send(200, search_result(
        [{"rank": i, "title": "t" * 300, "url": f"https://r/{i}" + "x" * 300, "engine": "google"} for i in range(1, 40)],
        [{"engine": "google", "status": "ok", "count": 39}]))
    assert run("search", "--json", "-n", "39", "q") == 0
    stub = json.loads(capsys.readouterr().out)
    assert stub["truncated"] is True and stub["summary"] == {"results": 39}
