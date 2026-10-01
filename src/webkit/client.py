"""HTTP client for the /v2 API (stdlib only).

Every failure becomes ApiError(error_class, message, ...) whose exit_code is
the CLI's contract with agents.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request

from . import __version__
from .config import Config

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NO_RESULTS = 2
EXIT_FAILED = 3
EXIT_AUTH = 4
EXIT_UNREACHABLE = 5
EXIT_HUMAN = 6
EXIT_BUSY = 7

_EXIT_BY_CLASS = {
    "invalid_request": EXIT_USAGE,
    "unauthorized": EXIT_AUTH,
    "unreachable": EXIT_UNREACHABLE,
    "client_timeout": EXIT_UNREACHABLE,
    "human_required": EXIT_HUMAN,
    "busy": EXIT_BUSY,
}


class ApiError(Exception):
    def __init__(self, error: str, message: str, status: int = 0, body: dict | None = None):
        super().__init__(message)
        self.error = error
        self.message = message
        self.status = status
        self.body = body or {"error": error, "message": message}

    @property
    def exit_code(self) -> int:
        return _EXIT_BY_CLASS.get(self.error, EXIT_FAILED)


class Client:
    def __init__(self, cfg: Config, timeout: float = 120.0):
        self.cfg = cfg
        self.timeout = timeout

    def _request(self, method: str, path: str, params: dict | None = None, body: dict | None = None,
                 admin: bool = False, timeout: float | None = None):
        if admin and not self.cfg.admin_key:
            raise ApiError("unauthorized", "this command needs the admin key",
                           body={"error": "unauthorized", "message": "admin key not configured",
                                 "hint": "webkit config set admin-key  (reads the key from stdin)"})
        if not admin and not self.cfg.api_key:
            raise ApiError("unauthorized", "no API key configured",
                           body={"error": "unauthorized", "message": "no API key configured",
                                 "hint": "webkit config set api-key  (reads the key from stdin)"})
        url = self.cfg.url + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "", False)})
        headers = {"User-Agent": f"webkit-cli/{__version__}", "Accept": "application/json"}
        headers["X-Admin-Key" if admin else "X-API-Key"] = self.cfg.admin_key if admin else self.cfg.api_key
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            return urllib.request.urlopen(req, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as e:
            raise _from_http_error(e) from None
        except urllib.error.URLError as e:
            reason = e.reason
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise ApiError("client_timeout", f"no response from {self.cfg.url} within {timeout or self.timeout}s") from None
            raise ApiError("unreachable", f"cannot reach {self.cfg.url}: {reason}",
                           body={"error": "unreachable", "message": f"cannot reach {self.cfg.url}: {reason}",
                                 "hint": "check `webkit config show` and that the backend is running"}) from None
        except (socket.timeout, TimeoutError):
            raise ApiError("client_timeout", f"no response from {self.cfg.url} within {timeout or self.timeout}s") from None

    def get_json(self, path: str, params: dict | None = None, admin: bool = False, timeout: float | None = None) -> dict:
        with self._request("GET", path, params=params, admin=admin, timeout=timeout) as resp:
            return json.load(resp)

    def post_json(self, path: str, body: dict, timeout: float | None = None) -> dict:
        with self._request("POST", path, body=body, timeout=timeout) as resp:
            return json.load(resp)

    def stream(self, path: str, params: dict, timeout: float | None = None):
        """Open a streaming GET; caller reads and closes the response."""
        return self._request("GET", path, params=params, timeout=timeout)


def _from_http_error(e: urllib.error.HTTPError) -> ApiError:
    raw = e.read() or b""
    try:
        body = json.loads(raw)
    except ValueError:
        body = {"error": "internal", "message": raw.decode("utf-8", "replace")[:300] or f"HTTP {e.code}"}
    if e.code in (401, 403):
        body.setdefault("hint", "wrong or missing key; see `webkit config show`")
        return ApiError("unauthorized", body.get("message", "unauthorized"), e.code, body)
    return ApiError(body.get("error", "internal"), body.get("message", f"HTTP {e.code}"), e.code, body)
