"""Named browser targets: another machine's Chrome profile, reached through that
machine's webkit-executor and driven there by opencli. Inactive: the executor was
removed (2026-11-22) when the CLI moved to local mode; without WEBKIT_TARGETS this
module does nothing.

A request's `--via` is an ordered, comma-separated chain of browsers, e.g. 'local,hnas':
'hnas' is this server's own Chrome, a target name ('lab') or a target with one of its
opencli profiles ('lab/main') is that machine's Chrome, and 'local' stands for the
target named after the caller's host (dropped when there is none). Each browser is
tried in order until one works; the chain, not this code, decides the fallback.
Executor and opencli failures are mapped onto web-kit's error classes here, so
engines and the page reader see the same classes whichever browser did the work.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from . import settings
from .errors import WebkitError

logger = logging.getLogger("webkit.targets")

HNAS = "hnas"
LOCAL = "local"
_EMPTY_CODES = frozenset({"EMPTY_RESULT", "NOT_FOUND"})

_clients: dict[str, httpx.AsyncClient] = {}
_health: dict[str, dict] = {}


def names() -> list[str]:
    return list(settings.TARGETS)


def plan(via: str | None, client: str | None) -> list[tuple[str, str | None]]:
    """The --via chain as [(browser, profile)], in order, repeats dropped. 'local' becomes
    the caller's own target; a chain left empty is an error, not a silent default."""
    raw = (via or settings.DEFAULT_VIA).strip()
    chain: list[tuple[str, str | None]] = []
    for part in (p.strip() for p in raw.split(",")):
        if not part:
            continue
        name, _, profile = part.partition("/")
        if name == LOCAL:
            name = local_name(client) or ""
            if not name:
                continue
        elif name != HNAS and name not in settings.TARGETS:
            raise WebkitError("invalid_request", f"unknown browser '{name}' in --via '{raw}'",
                              hint="--via takes a comma-separated list of: " + ", ".join([LOCAL, HNAS, *names()]))
        item = (name, None if name == HNAS else (profile or None))
        if item not in chain:
            chain.append(item)
    if not chain:
        raise WebkitError("invalid_request", f"no usable browser in --via '{raw}'",
                          hint=f"this machine ({client or 'unknown host'}) has no browser target; "
                               "add one that exists, e.g. --via local,hnas")
    return chain


def unavailable_error(name: str) -> WebkitError:
    state = _health.get(name, {})
    return WebkitError("browser_unavailable", f"target {name} is unavailable: {state.get('error') or 'down'}"
                       f" (checked {state.get('checked_at', '?')})",
                       hint="`webkit status` shows targets; put another browser after it in --via")


def local_name(client: str | None) -> str | None:
    """The target registered for the CLI's machine: its name equals the host name the
    CLI sends (X-Webkit-Client), compared case-insensitively."""
    host = (client or "").strip().lower().split(".", 1)[0]
    return next((n for n in names() if n.lower() == host), None) if host else None


def available(name: str) -> bool:
    """Unknown (not probed yet) counts as available: the call itself will tell."""
    return _health.get(name, {}).get("available", True) is not False


def _client(name: str) -> httpx.AsyncClient:
    if name not in _clients:
        _clients[name] = httpx.AsyncClient(base_url=settings.TARGETS[name], timeout=settings.TARGET_TIMEOUT,
                                           headers={"X-Executor-Key": settings.TARGET_KEYS.get(name, "")})
    return _clients[name]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _mark(name: str, ok: bool, error: str | None = None) -> None:
    state = _health.setdefault(name, {})
    state["available"] = ok
    state["error"] = None if ok else error
    state["checked_at"] = _now()


async def _post(name: str, path: str, body: dict) -> dict:
    try:
        resp = await _client(name).post(path, json=body)
    except httpx.TimeoutException:
        raise WebkitError("timeout", f"target {name} did not answer within {settings.TARGET_TIMEOUT}s") from None
    except httpx.HTTPError as e:
        _mark(name, False, f"unreachable: {type(e).__name__}")
        raise WebkitError("browser_unavailable", f"target {name} is unreachable ({type(e).__name__})",
                          hint="its machine or webkit-executor is down; `webkit status` lists targets") from None
    try:
        data = resp.json()
    except ValueError:
        raise WebkitError("internal", f"target {name} answered non-JSON (HTTP {resp.status_code})") from None
    if resp.status_code == 200:
        return data
    msg = f"target {name}: {data.get('error')}: {data.get('message')}"
    if resp.status_code == 401:
        raise WebkitError("internal", f"target {name} rejected this server's key (WEBKIT_TARGET_KEYS)")
    if resp.status_code in (503, 504):  # busy / opencli timeout: never "busy", which aborts a whole search
        raise WebkitError("timeout", msg)
    if resp.status_code == 403:
        raise WebkitError("invalid_request", msg, hint="targets open only public http(s) URLs")
    raise WebkitError("internal", f"{msg} (HTTP {resp.status_code})")


def _opencli_error(name: str, what: str, exit_code, env: dict | None) -> WebkitError | None:
    """WebkitError for a failed opencli envelope; None when opencli only says 'no results'
    (its adapters say that for CAPTCHA pages too, so callers verify before trusting it)."""
    err = (env or {}).get("error") or {}
    code = err.get("code") or ""
    detail = " ".join(str(err.get("message") or "").split())[:200] or f"opencli exit {exit_code}"
    msg = f"target {name}: {what}: {detail}"
    if code in _EMPTY_CODES or exit_code == 66:
        return None
    if exit_code == 69 or code in ("BROWSER_CONNECT", "ADAPTER_LOAD"):
        _mark(name, False, detail)
        return WebkitError("browser_unavailable", msg,
                           hint="the target's Chrome or its OpenCLI extension is not connected")
    if exit_code == 75 or code in ("TIMEOUT", "SESSION_BUSY"):
        return WebkitError("timeout", msg)
    if exit_code == 77 or code in ("AUTH_REQUIRED", "LOGIN_WALL"):
        return WebkitError("human_required", msg, hint="log in to the site in the target's Chrome, then retry")
    return WebkitError("upstream_http", msg)


async def run(name: str, profile: str | None, command: str, query: str, limit: int,
              options: dict | None = None) -> list[dict] | None:
    """Rows of an allowlisted opencli read command on target `name`; None when opencli
    reports no results (unverified)."""
    body = {"command": command, "query": query, "limit": limit, "options": options or {}}
    if profile:
        body["profile"] = profile
    data = await _post(name, "/run", body)
    env = data.get("envelope")
    if env is None:
        raise WebkitError("upstream_http", f"target {name}: {command}: opencli printed no JSON "
                          f"(exit {data.get('exit')}): {str(data.get('stderr', ''))[:200]}")
    if env.get("ok"):
        rows = env.get("data")
        if not isinstance(rows, list):
            raise WebkitError("unexpected_page", f"target {name}: {command}: unexpected payload shape")
        _mark(name, True)
        return [r for r in rows if isinstance(r, dict)]
    err = _opencli_error(name, command, data.get("exit"), env)
    if err is None:
        _mark(name, True)
        return None
    raise err


async def page(name: str, profile: str | None, url: str, wait_selector: str | None = None,
               settle_ms: int | None = None, wait_timeout_ms: int | None = None) -> dict:
    """The rendered page from target `name`: {html, title, final_url, text, wait_ok,
    status, via}. opencli exposes no HTTP status, so status is 0."""
    body: dict = {"url": url}
    for key, value in (("profile", profile), ("wait_selector", wait_selector), ("settle_ms", settle_ms),
                       ("wait_timeout_ms", wait_timeout_ms)):
        if value:
            body[key] = value
    data = await _post(name, "/page", body)
    if not data.get("ok"):
        raise (_opencli_error(name, f"page {data.get('step')}", data.get("exit"), data.get("envelope"))
               or WebkitError("upstream_http", f"target {name}: page {data.get('step')} returned nothing"))
    _mark(name, True)
    return {"html": data.get("html") or "", "title": data.get("title") or "",
            "final_url": data.get("final_url") or url, "text": data.get("text") or "",
            "wait_ok": data.get("wait_ok"), "status": 0, "via": name}


async def probe(name: str) -> dict:
    try:
        resp = await _client(name).get("/health", timeout=30)
        body = resp.json() if resp.status_code == 200 else {}
        if resp.status_code != 200:
            state = {"available": False, "error": f"health check answered HTTP {resp.status_code}"}
        else:
            connected = bool(body.get("extension_connected"))
            state = {"available": connected, "extension_connected": connected, "opencli": body.get("opencli"),
                     "profiles": [p.get("alias") or p.get("context") for p in body.get("profiles", [])
                                  if p.get("connected")],
                     "enabled_profiles": body.get("enabled_profiles", []),
                     "error": body.get("error") or (None if connected else "the OpenCLI extension is not connected")}
    except Exception as e:  # noqa: BLE001 - any failure means unavailable
        state = {"available": False, "error": f"unreachable: {type(e).__name__}"}
    state["checked_at"] = _now()
    _health[name] = {**_health.get(name, {}), **state}
    return _health[name]


async def probe_loop() -> None:
    while True:
        for name in names():
            await probe(name)
        await asyncio.sleep(settings.TARGET_PROBE_INTERVAL)


def snapshot() -> dict:
    return {name: {"url": settings.TARGETS[name], "key_configured": bool(settings.TARGET_KEYS.get(name)),
                   **_health.get(name, {"available": None})} for name in names()}
