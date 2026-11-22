"""webkit-serve: the /v2 API over local mode, for the web-kit container.

The container runs Chrome with the OpenCLI extension and an opencli daemon; this
server answers every route with the same functions local mode uses on a desktop
(local.py), so `backend = "remote"` gives the same results, errors and shapes as
`backend = "local"`. Standard library only.

  python -m webkit.serve [--host 0.0.0.0] [--port 8082]

Environment:
  WEBKIT_API_KEY      X-API-Key for /v2/* (required)
  WEBKIT_ADMIN_KEY    X-Admin-Key for /v2/admin/* (required, different from the API key)
  WEBKIT_S2_API_KEY   Semantic Scholar API key, passed to opencli (optional)
  WEBKIT_OPENCLI      opencli executable (default: from PATH)
  WEBKIT_MAX_INFLIGHT requests served at once (default 4); others wait, then get `busy`
  WEBKIT_QUEUE_WAIT   seconds a request waits for its turn (default 30)
  WEBKIT_MAX_DOWNLOAD_MB  largest file /v2/download fetches (default 200)
  WEBKIT_CHROME, WEBKIT_CHROME_PROFILE  what /v2/admin/open opens a tab in
  WEBKIT_IMAGE_TAG    reported by /v2/version
  WEBKIT_LOG          "off" stops the events log: one JSON line per request on stdout
                      (docker logs; events.py; `webkit stats -` reads it), except healthy /healthz
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import API_VERSION, __version__, events, local
from .client import ApiError
from .config import Config

# HTTP status per error class (the CLI reads the class from the body; 401/403 always
# mean "wrong key" to it, so nothing else may use them).
STATUS = {
    "invalid_request": 400, "unauthorized": 401, "not_found": 404, "human_required": 409,
    "too_large": 413, "not_a_file": 422, "not_a_page": 422, "internal": 500,
    "busy": 503, "unreachable": 503, "engine_unavailable": 503, "timeout": 504,
}
MAX_BODY = 64 * 1024
COMMANDS = {"/v2/search": "search", "/v2/page": "read", "/v2/download": "download", "/v2/status": "status",
            "/v2/version": "version", "/v2/engines": "engines", "/v2/admin/open": "admin_open",
            "/v2/admin/ping": "admin_ping", "/healthz": "healthz"}


class App:
    def __init__(self, env: dict) -> None:
        self.api_key = env.get("WEBKIT_API_KEY", "")
        self.admin_key = env.get("WEBKIT_ADMIN_KEY", "")
        if not self.api_key or not self.admin_key:
            raise SystemExit("webkit-serve: set WEBKIT_API_KEY and WEBKIT_ADMIN_KEY")
        if hmac.compare_digest(self.api_key, self.admin_key):
            raise SystemExit("webkit-serve: WEBKIT_ADMIN_KEY must differ from WEBKIT_API_KEY")
        self.cfg = Config(backend="local", opencli=env.get("WEBKIT_OPENCLI", ""),
                          s2_api_key=env.get("WEBKIT_S2_API_KEY", ""))
        self.slots = threading.BoundedSemaphore(int(env.get("WEBKIT_MAX_INFLIGHT") or 4))
        self.queue_wait = float(env.get("WEBKIT_QUEUE_WAIT") or 30)
        self.max_download_mb = int(env.get("WEBKIT_MAX_DOWNLOAD_MB") or 200)
        self.chrome = env.get("WEBKIT_CHROME", "google-chrome-stable")
        self.chrome_profile = env.get("WEBKIT_CHROME_PROFILE", "/data/chrome-profile")
        self.image = env.get("WEBKIT_IMAGE_TAG", "")
        self.chrome_version = _chrome_version(self.chrome)
        self.log = events.enabled(env.get("WEBKIT_LOG"))
        self.event_sink = None  # default sys.stdout (tests swap it)
        self._log_lock = threading.Lock()
        local.HUMAN_AT = "in the server's Chrome (noVNC, port 6080)"
        local.OPEN_CHROME = "the server's Chrome is not connected: ask its admin (it restarts on its own)"

    # ─── routes ────────────────────────────────────────────────────────────────
    # Each returns (status, JSON body) or, for a download, a local.Download.

    def search(self, params: dict, body: dict):
        return local.search(self.cfg, params)

    def engines(self, params: dict, body: dict):
        return local.engines()

    def page(self, params: dict, body: dict):
        return local.page(self.cfg, body)

    def download(self, params: dict, body: dict):
        asked = int(params.get("max_mb") or self.max_download_mb)
        return local.download(self.cfg, {**params, "max_mb": min(asked, self.max_download_mb)})

    def status(self, params: dict, body: dict):
        st = local.status(self.cfg)
        if not st.get("extension_connected"):
            st["error"] = ("the OpenCLI extension in the server's Chrome is not connected; the container "
                           "restarts Chrome and opencli on their own: see `docker logs` if it persists")
        return {**st, "mode": "server", "chrome": self.chrome_version, "image": self.image}

    def version(self, params: dict, body: dict):
        return {"webkit": __version__, "api": API_VERSION, "mode": "server", "chrome": self.chrome_version,
                "image": self.image}

    def admin_ping(self, params: dict, body: dict):
        return {"ok": True}

    def admin_open(self, params: dict, body: dict):
        """Open a page in the server's Chrome for a person (noVNC): an existing Chrome takes
        the URL over its profile's singleton socket and this call returns at once."""
        url = str(params.get("url") or "")
        if urlparse(url).scheme not in ("http", "https"):
            raise local.err("invalid_request", "only http(s) URLs can be opened")
        subprocess.Popen([self.chrome, f"--user-data-dir={self.chrome_profile}", url],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={**os.environ, "DISPLAY": ":99"})
        return {"opened": url}

    def emit(self, event: dict) -> None:
        if not self.log:
            return
        text = events.line(event)
        with self._log_lock:
            sink = self.event_sink or sys.stdout
            sink.write(text)
            sink.flush()

    def healthy(self) -> bool:
        try:
            st = local.status(self.cfg)
        except Exception:  # noqa: BLE001 - a health check never raises
            return False
        return bool(st.get("opencli") and st.get("extension_connected"))


ROUTES = {
    ("GET", "/v2/search"): ("api", App.search),
    ("GET", "/v2/engines"): ("api", App.engines),
    ("POST", "/v2/page"): ("api", App.page),
    ("GET", "/v2/download"): ("api", App.download),
    ("GET", "/v2/status"): ("api", App.status),
    ("GET", "/v2/version"): ("api", App.version),
    ("GET", "/v2/admin/ping"): ("admin", App.admin_ping),
    ("GET", "/v2/admin/open"): ("admin", App.admin_open),
}


def _chrome_version(exe: str) -> str:
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.strip().rsplit(" ", 1)[-1] if out.strip() else ""


class Handler(BaseHTTPRequestHandler):
    server_version = f"webkit-serve/{__version__}"
    protocol_version = "HTTP/1.0"  # one request per connection, as the CLI sends them
    app: App  # set by make_server

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def log_message(self, fmt, *args) -> None:  # requests are logged once, in _handle
        pass

    def _handle(self, method: str) -> None:
        t0 = time.monotonic()
        u = urlparse(self.path)
        status = 500
        ev = {"ts": events.now(), "v": __version__, "side": "server", "cmd": COMMANDS.get(u.path, u.path),
              "method": method, "client": self.client_address[0],
              "agent": (self.headers.get("X-Webkit-Agent") or "")[:40],
              "session": (self.headers.get("X-Webkit-Session") or "")[:100]}
        try:
            if method == "GET" and u.path == "/healthz":  # no key, no details: for Docker's healthcheck
                ok = self.app.healthy()
                status = 200 if ok else 503
                self._json(status, {"ok": ok})
                return
            route = ROUTES.get((method, u.path))
            if route is None:
                raise local.err("not_found", f"no route {method} {u.path}")
            kind, fn = route
            self._check_key(kind)
            params = {k: v[-1] for k, v in parse_qs(u.query).items()}
            body = self._body() if method == "POST" else {}
            ev.update(_asked(u.path, params, body))
            if not self.app.slots.acquire(timeout=self.app.queue_wait):
                raise local.err("busy", "the server is busy with other requests", hint="retry in a minute")
            try:
                result = fn(self.app, params, body)
            finally:
                self.app.slots.release()
            status = 200
            _answered(ev, result)
            if isinstance(result, local.Download):
                self._file(result)
            else:
                self._json(200, result)
        except ApiError as e:
            status = STATUS.get(e.error, 502)
            ev["error"] = e.error
            if e.body.get("engines"):
                ev["engines"] = events.engines_report(e.body["engines"])
            self._json(status, e.body)
        except Exception as e:  # noqa: BLE001 - one bad request must not kill the server
            traceback.print_exc(file=sys.stderr)
            status = 500
            ev["error"] = f"crash:{type(e).__name__}"
            self._json(500, {"error": "internal", "message": f"{type(e).__name__}: {e}"[:300]})
        finally:
            ms = int((time.monotonic() - t0) * 1000)
            if ev.get("pages"):
                ev["pages"][0].update(ms=ms, error=ev.get("error"))
            if not (u.path == "/healthz" and status == 200):  # the healthcheck every 30 s is not news
                self.app.emit({**ev, "status": status, "ms": ms})

    def _check_key(self, kind: str) -> None:
        header, want = ("X-Admin-Key", self.app.admin_key) if kind == "admin" else ("X-API-Key", self.app.api_key)
        got = self.headers.get(header) or ""
        if not hmac.compare_digest(got.encode(), want.encode()):
            raise local.err("unauthorized", f"missing or wrong {header}",
                            hint="webkit config set api-key (admin-key for admin commands)")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise local.err("invalid_request", "request body too large")
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            raise local.err("invalid_request", "the request body is not JSON") from None
        if not isinstance(data, dict):
            raise local.err("invalid_request", "the request body must be a JSON object")
        return data

    def _json(self, status: int, data: dict) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _file(self, d: local.Download) -> None:
        self.send_response(200)
        for k, v in d.headers.items():
            self.send_header(k, v)
        self.end_headers()
        while chunk := d.read(1 << 20):
            self.wfile.write(chunk)


def _asked(path: str, params: dict, body: dict) -> dict:
    """What a request asked for, as events-log fields (full query and URL; never keys)."""
    if path == "/v2/search":
        ev = {k: params.get(k) for k in ("q", "profile", "engine", "time", "lang")}
        limit = str(params.get("limit") or "")
        return {**ev, "limit": int(limit) if limit.isdigit() else None}
    if path == "/v2/page":
        return {"pages": [{"url": str(body.get("url") or "")}], "format": body.get("format"),
                "wait_for": body.get("wait_for"), "scroll": body.get("scroll") or None}
    if path == "/v2/download":
        return {"url": params.get("url"), "max_mb": params.get("max_mb"), "allow_html": params.get("allow_html")}
    if path == "/v2/admin/open":
        return {"url": params.get("url")}
    return {}


def _answered(ev: dict, result) -> None:
    """What the answer was, as events-log fields (sizes and counts, never content)."""
    if isinstance(result, local.Download):
        h = result.headers
        final = unquote(h.get("X-Webkit-Final-Url") or "")
        ev.update(bytes=int(h.get("Content-Length") or 0), content_type=h.get("Content-Type"),
                  final_url=final if final != ev.get("url") else None,
                  resolved_from=unquote(h.get("X-Webkit-Resolved-From") or "") or None)
    elif ev.get("cmd") == "search":
        ev.update(engines=events.engines_report(result.get("engines")), results=len(result.get("results") or []))
    elif ev.get("pages"):
        rec = ev["pages"][0]
        final = result.get("final_url")
        rec.update(final_url=final if final != rec["url"] else None,
                   chars=len(result.get("content") or "") if "content" in result else None,
                   links=len(result["links"]) if "links" in result else None)


def make_server(host: str, port: int, app: App) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="webkit-serve", description="the web-kit /v2 API over local mode")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8082)
    args = p.parse_args(argv)
    app = App(dict(os.environ))
    server = make_server(args.host, args.port, app)
    print(f"webkit-serve {__version__} (api {API_VERSION}) on {args.host}:{args.port}, chrome "
          f"{app.chrome_version or '?'}", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
