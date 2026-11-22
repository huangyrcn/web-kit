"""webkit-serve end to end: the real server (serve.py) answered by local mode with a fake
opencli and fake HTTP, driven by the real CLI in remote mode. Offline."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from webkit import cli, local, serve

KEY, ADMIN = "k" * 64, "a" * 64
WEB = "# Title\n> 原文链接: {url}\n\n---\n\n{body}\n"


def ok(data) -> str:
    return json.dumps({"ok": True, "data": data})


@pytest.fixture
def opencli(monkeypatch):
    """'site command' -> reply (stdout string or (exit code, stdout, stderr))."""
    answers: dict = {}

    def fake(cfg, args, timeout=local.OPENCLI_TIMEOUT, profile=True):
        reply = answers.get(" ".join(args[:2]))
        if callable(reply):
            reply = reply(args)
        if reply is None:
            return 0, ok([]), ""
        return reply if isinstance(reply, tuple) else (0, reply, "")

    monkeypatch.setattr(local, "_run_opencli", fake)
    answers["--version"] = (0, "2.0.3\n", "")
    answers["profile list"] = ok({"profiles": [{"contextId": "c1", "connected": True, "default": False}]})
    answers["plugin list"] = ok([{"name": "webkit", "commands": ["rxiv"]}])
    return answers


@pytest.fixture
def web(monkeypatch):
    pages: dict = {}

    def fake_fetch(url, max_bytes, accept="*/*"):
        if url not in pages:
            raise local.err("not_found", f"{url} answered HTTP 404")
        ctype, body = pages[url]
        return url, ctype, body, {"Content-Type": ctype}

    monkeypatch.setattr(local, "fetch", fake_fetch)
    return pages


def start(**env):
    app = serve.App({"WEBKIT_API_KEY": KEY, "WEBKIT_ADMIN_KEY": ADMIN, "WEBKIT_CHROME": "true", **env})
    srv = serve.make_server("127.0.0.1", 0, app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return app, srv, f"http://127.0.0.1:{srv.server_address[1]}"


@pytest.fixture
def server(monkeypatch, tmp_path, opencli):
    app, srv, url = start()
    monkeypatch.setenv("WEBKIT_BACKEND", "remote")
    monkeypatch.setenv("WEBKIT_URL", url)
    monkeypatch.setenv("WEBKIT_API_KEY", KEY)
    monkeypatch.setenv("WEBKIT_ADMIN_KEY", ADMIN)
    monkeypatch.setenv("WEBKIT_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("WEBKIT_DOWNLOAD_DIR", str(tmp_path / "dl"))
    monkeypatch.setenv("WEBKIT_CACHE_DIR", str(tmp_path / "cache"))
    yield app, url
    srv.shutdown()


def run(*argv) -> int:
    with pytest.raises(SystemExit) as e:
        cli.main(list(argv))
    return e.value.code


def last_error(capsys) -> dict:
    return json.loads(capsys.readouterr().err.strip().splitlines()[-1])


def test_keys_are_required_and_must_differ():
    with pytest.raises(SystemExit):
        serve.App({"WEBKIT_API_KEY": KEY})
    with pytest.raises(SystemExit):
        serve.App({"WEBKIT_API_KEY": KEY, "WEBKIT_ADMIN_KEY": KEY})


def test_every_route_needs_its_key_and_healthz_tells_nothing_else(server, monkeypatch, capsys):
    _, url = server
    with urllib.request.urlopen(url + "/healthz") as r:
        assert json.load(r) == {"ok": True}
    req = urllib.request.Request(url + "/v2/admin/ping", headers={"X-Admin-Key": KEY})  # the API key is not admin
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 401
    monkeypatch.setenv("WEBKIT_API_KEY", "wrong")
    assert run("search", "x") == 4


def test_search_through_the_server(server, opencli, capsys):
    opencli["google search"] = ok([{"type": "result", "title": "A", "url": "https://a.org", "snippet": "s"}])
    assert run("search", "graph") == 0
    assert capsys.readouterr().out.splitlines()[:2] == ["# engines google=ok:1", "1. A"]
    opencli["google search"] = (69, json.dumps({"ok": False, "error": {"code": "BROWSER_CONNECT", "message": "x"}}), "")
    assert run("search", "graph") == 3
    assert last_error(capsys)["error"] == "engines_failed"


def test_read_through_the_server_keeps_local_modes_answers(server, opencli, capsys):
    opencli["web read"] = (0, WEB.format(url="https://e.org/p", body="Hello [a](https://e.org/a)"), "")
    assert run("read", "https://e.org/p") == 0
    assert capsys.readouterr().out == "Hello [a][1]\n\n[1]: https://e.org/a\n"
    assert run("read", "https://arxiv.org/pdf/1706.03762") == 3
    assert last_error(capsys)["error"] == "not_a_page"
    opencli["web read"] = (0, WEB.format(url="https://e.org/p", body="Just a moment... Checking your browser"), "")
    assert run("read", "https://e.org/p") == 6  # 409 human_required
    opencli["web read"] = (0, WEB.format(url="https://e.org/p", body="You have been blocked. Error 1020"), "")
    assert run("read", "https://e.org/p") == 8  # 502 blocked, never 403 (the CLI reads 403 as a bad key)


def test_download_streams_the_file(server, web, tmp_path, capsys):
    web["https://arxiv.org/pdf/2403.01092"] = ("application/pdf", b"%PDF-1.4 tiny")
    assert run("download", "arXiv:2403.01092") == 0
    assert (tmp_path / "dl" / "2403.01092.pdf").read_bytes() == b"%PDF-1.4 tiny"
    assert "the PDF linked from the page" in capsys.readouterr().out
    web["https://blog.org/post"] = ("text/html", b"<html>post</html>")
    assert run("download", "https://blog.org/post") == 3
    assert last_error(capsys)["error"] == "not_a_file"


def test_status_and_doctor_through_the_server(server, capsys):
    assert run("status") == 0
    out = capsys.readouterr().out
    assert "server http://127.0.0.1:" in out and "✓" not in out and "browser: OpenCLI extension connected" in out
    assert run("doctor") == 0
    out = capsys.readouterr().out
    assert "✓ server reachable, key accepted" in out and "✓ admin key accepted" in out and "✗" not in out


def test_busy_when_every_slot_is_taken(monkeypatch, tmp_path, opencli, capsys):
    app, srv, url = start(WEBKIT_MAX_INFLIGHT="1", WEBKIT_QUEUE_WAIT="0.2")
    monkeypatch.setenv("WEBKIT_BACKEND", "remote")
    monkeypatch.setenv("WEBKIT_URL", url)
    monkeypatch.setenv("WEBKIT_API_KEY", KEY)
    monkeypatch.setenv("WEBKIT_CONFIG", str(tmp_path / "config.toml"))
    release = threading.Event()

    def slow(args):
        release.wait(5)
        return ok([{"type": "result", "title": "A", "url": "https://a.org"}])
    opencli["google search"] = slow
    first = threading.Thread(target=lambda: urllib.request.urlopen(
        urllib.request.Request(url + "/v2/search?q=x", headers={"X-API-Key": KEY})).read())
    first.start()
    time.sleep(0.2)
    try:
        assert run("search", "y") == 7
        assert last_error(capsys)["error"] == "busy"
    finally:
        release.set()
        first.join()
        srv.shutdown()


def test_admin_open_hands_the_url_to_chrome(server, monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(serve.subprocess, "Popen", lambda argv, **kw: opened.append(argv))
    app, url = server
    req = urllib.request.Request(url + "/v2/admin/open?url=https%3A%2F%2Fscholar.google.com%2F",
                                 headers={"X-Admin-Key": ADMIN})
    with urllib.request.urlopen(req) as r:
        assert json.load(r) == {"opened": "https://scholar.google.com/"}
    assert opened == [["true", "--user-data-dir=/data/chrome-profile", "https://scholar.google.com/"]]
    bad = urllib.request.Request(url + "/v2/admin/open?url=file%3A%2F%2F%2Fetc%2Fpasswd", headers={"X-Admin-Key": ADMIN})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(bad)
    assert e.value.code == 400 and not opened[1:]
