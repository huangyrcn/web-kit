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


def test_search_read_inline_is_capped():
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
    assert all(c["max_chars"] == cli.INLINE_READ_CHARS for c in calls)


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


def test_skill_render_has_commands(capsys):
    assert run("skill", "show") == 0
    text = capsys.readouterr().out
    assert text.startswith("---\nname: web-kit")
    for cmd in ("webkit search", "webkit read", "webkit crawl", "webkit download", "webkit browser open"):
        assert f"### {cmd}" in text


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
