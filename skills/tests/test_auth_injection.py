# skills/tests/test_auth_injection.py
import json, os, pathlib, subprocess, sys, threading, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

SKILLS = pathlib.Path(__file__).resolve().parents[1]
SEARXNG_SEARCH = SKILLS / "searxng-search" / "scripts" / "searxng-search"

def run(script, args, env):
    return subprocess.run([sys.executable, str(script), *args],
                          capture_output=True, text=True, env=env)

def test_searxng_search_no_key_fails_fast():
    env = {**os.environ}; env.pop("WEB_KIT_API_KEY", None)
    env.pop("CLAUDE_PLUGIN_OPTION_WEB_KIT_API_KEY", None)
    env["SEARXNG_URL"] = "http://localhost:8082"
    r = run(SEARXNG_SEARCH, ["test query"], env)
    assert r.returncode != 0, f"expected non-zero exit, got 0; stdout={r.stdout}"
    assert "WEB_KIT_API_KEY" in (r.stderr + r.stdout)


def test_searxng_search_rejects_multiple_engines_before_network():
    env = {**os.environ}
    env["WEB_KIT_API_KEY"] = "testkey"
    env["SEARXNG_URL"] = "http://127.0.0.1:9"
    r = run(SEARXNG_SEARCH, ["test query", "-e", "google_scholar,semantic_scholar"], env)
    assert r.returncode != 0, f"expected non-zero exit, got 0; stdout={r.stdout}"
    assert "multiple_engines_not_supported" in r.stdout
    assert "backend_unreachable" not in r.stdout


def run_fake_searxng(response_body):
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen["path"] = self.path
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response_body).encode())

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    return server, thread, seen


def test_searxng_search_maps_public_engine_alias_to_searxng_name():
    body = {
        "results": [{
            "title": "Scholar result",
            "url": "https://example.com/paper",
            "content": "paper metadata",
            "engine": "google scholar",
            "engines": ["google scholar"],
        }],
        "unresponsive_engines": [],
    }
    server, thread, seen = run_fake_searxng(body)
    env = {**os.environ, "WEB_KIT_API_KEY": "testkey", "SEARXNG_URL": f"http://127.0.0.1:{server.server_port}"}

    r = run(SEARXNG_SEARCH, ["test query", "-e", "google_scholar", "-j"], env)
    thread.join(1)
    server.server_close()

    assert r.returncode == 0, f"expected zero exit; stdout={r.stdout} stderr={r.stderr}"
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(seen["path"]).query)
    assert query["engines"] == ["google scholar"]


def test_searxng_search_rejects_mismatched_engine_results():
    body = {
        "results": [{
            "title": "Fallback result",
            "url": "https://example.com/fallback",
            "content": "not scholar",
            "engine": "bing",
            "engines": ["bing"],
        }],
        "unresponsive_engines": [],
    }
    server, thread, seen = run_fake_searxng(body)
    env = {**os.environ, "WEB_KIT_API_KEY": "testkey", "SEARXNG_URL": f"http://127.0.0.1:{server.server_port}"}

    r = run(SEARXNG_SEARCH, ["test query", "-e", "google_scholar", "-j"], env)
    thread.join(1)
    server.server_close()

    assert r.returncode != 0, f"expected non-zero exit, got 0; stdout={r.stdout}"
    assert "engine_mismatch" in r.stdout
    assert "google scholar" in r.stdout


def test_searxng_search_rejects_unknown_engine_before_network():
    env = {**os.environ}
    env["WEB_KIT_API_KEY"] = "testkey"
    env["SEARXNG_URL"] = "http://127.0.0.1:9"
    r = run(SEARXNG_SEARCH, ["test query", "-e", "google-scholarrr"], env)
    assert r.returncode != 0, f"expected non-zero exit, got 0; stdout={r.stdout}"
    assert "unknown_engine" in r.stdout
    assert "backend_unreachable" not in r.stdout

PAGE = SKILLS / "browser-fetch" / "scripts" / "page"


def run_uv(script, args, env):
    """Run a uv --script file (installs PEP723 deps) instead of plain python."""
    return subprocess.run(["uv", "run", "--script", str(script), *args],
                          capture_output=True, text=True, env=env)


def test_page_no_key_fails_fast():
    env = {**os.environ}
    env.pop("WEB_KIT_API_KEY", None)
    env.pop("CLAUDE_PLUGIN_OPTION_WEB_KIT_API_KEY", None)
    env["CDP_URL"] = "http://localhost:9223"
    r = run_uv(PAGE, ["https://example.com"], env)
    assert r.returncode != 0, f"expected non-zero exit, got 0; stdout={r.stdout}"
    assert "WEB_KIT_API_KEY" in (r.stderr + r.stdout)


FILE = SKILLS / "browser-fetch" / "scripts" / "file"


def test_file_no_key_fails_fast():
    env = {**os.environ}
    env.pop("WEB_KIT_API_KEY", None)
    env.pop("CLAUDE_PLUGIN_OPTION_WEB_KIT_API_KEY", None)
    env["CDP_URL"] = "http://localhost:9223"
    r = run_uv(FILE, ["https://example.com/x.pdf"], env)
    assert r.returncode != 0, f"expected non-zero exit, got 0; stdout={r.stdout}"
    assert "WEB_KIT_API_KEY" in (r.stderr + r.stdout), (
        f"expected WEB_KIT_API_KEY in output; stderr={r.stderr!r} stdout={r.stdout!r}"
    )


if __name__ == "__main__":
    test_searxng_search_no_key_fails_fast()
    print("searxng-search PASS")
    test_searxng_search_rejects_multiple_engines_before_network()
    print("searxng-search multiple engines PASS")
    test_searxng_search_maps_public_engine_alias_to_searxng_name()
    print("searxng-search engine alias PASS")
    test_searxng_search_rejects_mismatched_engine_results()
    print("searxng-search engine mismatch PASS")
    test_searxng_search_rejects_unknown_engine_before_network()
    print("searxng-search unknown engine PASS")
    test_page_no_key_fails_fast()
    print("browser-fetch page PASS")
    test_file_no_key_fails_fast()
    print("browser-fetch file PASS")
