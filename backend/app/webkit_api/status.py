"""/v2/status, /v2/version, /v2/admin/open and the internal health probe.

The egress probe only completes a TLS handshake with each host, so it shows
"this backend cannot reach google.com" without spending any search quota.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
import time
from importlib.metadata import PackageNotFoundError, version as pkg_version

from fastapi import APIRouter, Query

from . import API_VERSION, __version__, browser, searxng, settings, stats
from .errors import WebkitError, classify

logger = logging.getLogger("webkit.status")
router = APIRouter()

_egress: dict[str, dict] = {}


async def probe_host(host: str, timeout: float = 8.0) -> dict:
    t0 = time.monotonic()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, 443, ssl=ssl.create_default_context(), server_hostname=host),
            timeout=timeout,
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, "ms": int((time.monotonic() - t0) * 1000)}
    except asyncio.TimeoutError:
        return {"ok": False, "error": "timeout", "ms": int(timeout * 1000)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:120]}"}


async def probe_egress() -> None:
    results = await asyncio.gather(*(probe_host(h) for h in settings.EGRESS_HOSTS))
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for host, res in zip(settings.EGRESS_HOSTS, results):
        res["checked_at"] = now
        if _egress.get(host, {}).get("ok", True) != res["ok"]:
            logger.warning("egress %s -> %s", host, "ok" if res["ok"] else res.get("error"))
        _egress[host] = res


async def egress_loop() -> None:
    while True:
        try:
            await probe_egress()
        except Exception as e:  # noqa: BLE001
            logger.warning("egress probe failed: %s", e)
        await asyncio.sleep(settings.EGRESS_PROBE_INTERVAL)


def _pkg(name: str) -> str:
    try:
        return pkg_version(name)
    except PackageNotFoundError:
        return "unknown"


async def version_info() -> dict:
    return {
        "webkit": __version__,
        "api": API_VERSION,
        "image": settings.IMAGE_TAG,
        "chrome": await browser.browser_version(),
        "patchright": _pkg("patchright"),
        "crawl4ai": _pkg("crawl4ai"),
        "searxng_ref": settings.SEARXNG_REF,
    }


@router.get("/v2/version")
async def version():
    return await version_info()


@router.get("/v2/status")
async def status():
    sx_errors = await searxng.stats_errors()
    api_engines = {}
    for cli, sx in searxng.ENGINES.items():
        errs = sx_errors.get(sx) or []
        if errs:
            top = max(errs, key=lambda e: e.get("percentage", 0))
            api_engines[cli] = {"error_rate": top.get("percentage"), "error": top.get("exception_classname")
                                or top.get("log_message")}
    down_hosts = [h for h, r in _egress.items() if not r.get("ok")]
    connected = browser.is_connected()
    return {
        "ok": connected and not down_hosts,
        "version": await version_info(),
        "browser": {"connected": connected, "pages_in_use": browser.slots_in_use(),
                    "max_pages": settings.MAX_PAGES, "waiting": browser.waiting()},
        "egress": _egress,
        "egress_down": down_hosts,
        "engines": stats.snapshot(),
        "searxng": {"healthy": await searxng.healthy(), "engine_errors": api_engines},
    }


@router.post("/v2/status/egress")
async def refresh_egress():
    await probe_egress()
    return {"egress": _egress}


@router.get("/v2/admin/open")
async def admin_open(url: str = Query(..., min_length=8)):
    try:
        await browser.open_manual(url)
    except WebkitError:
        raise
    except Exception as e:  # noqa: BLE001
        raise classify(e) from None
    return {"opened": url, "hint": "connect to noVNC (/vnc.html on the admin VNC port) to interact"}


@router.get("/v2/admin/ping")
async def admin_ping():
    """Lets `webkit doctor` verify the admin key without touching the browser."""
    return {"admin": True}


@router.get("/internal/health")
async def internal_health():
    try:
        await browser.ensure_browser()
    except Exception:  # noqa: BLE001
        pass
    ok = browser.is_connected()
    return {"status": "healthy" if ok else "degraded", "chrome_cdp": ok}
