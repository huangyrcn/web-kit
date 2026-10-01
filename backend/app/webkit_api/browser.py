"""The one shared Chrome: CDP connection, page slots, and the manual (noVNC) page.

All browser work goes through `page_slot()`, which bounds concurrent pages to
MAX_PAGES and turns a long wait into error class "busy" instead of a hang.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from patchright.async_api import async_playwright

from . import settings
from .errors import WebkitError

logger = logging.getLogger("webkit.browser")

_pw = None
_browser = None
_lock = asyncio.Lock()
_slots = asyncio.Semaphore(settings.MAX_PAGES)
_waiting = 0
_manual_page = None
_manual_lock = asyncio.Lock()


async def ensure_browser():
    """Connect (or reconnect) to Chrome over CDP. Idempotent and concurrency-safe."""
    global _pw, _browser
    if _browser is not None and _browser.is_connected():
        return _browser
    async with _lock:
        if _browser is not None and _browser.is_connected():
            return _browser
        await _stop_playwright()
        last_err = None
        for attempt in range(5):
            try:
                _pw = await async_playwright().start()
                _browser = await _pw.chromium.connect_over_cdp(settings.CDP_URL)
                logger.info("connected to Chrome via CDP (attempt %d)", attempt + 1)
                await _warmup(_browser)
                return _browser
            except Exception as e:  # noqa: BLE001 - retried, then surfaced
                last_err = e
                logger.warning("CDP connect attempt %d failed: %s", attempt + 1, e)
                await _stop_playwright()
                await asyncio.sleep(2)
        raise WebkitError("browser_unavailable", f"cannot connect to Chrome CDP: {last_err}")


async def _stop_playwright():
    global _pw, _browser
    if _pw is not None:
        try:
            await _pw.stop()
        except Exception:  # noqa: BLE001
            pass
    _pw = None
    _browser = None


async def _warmup(browser):
    """A quick round-trip; the first real navigation after a (re)connect can
    otherwise fail with ERR_CONNECTION_RESET while Chrome finishes starting."""
    page = None
    try:
        page = await (await context(browser)).new_page()
        await page.goto("about:blank", timeout=5000)
    except Exception as e:  # noqa: BLE001
        logger.warning("browser warmup failed (non-fatal): %s", e)
    finally:
        if page is not None:
            await safe_close(page)


async def context(browser=None):
    """The persistent profile's default context (carries cookies / logins)."""
    browser = browser or await ensure_browser()
    if browser.contexts:
        return browser.contexts[0]
    return await browser.new_context(locale="en-US")


@asynccontextmanager
async def page_slot():
    """Reserve one of MAX_PAGES browser page slots."""
    global _waiting
    _waiting += 1
    try:
        await asyncio.wait_for(_slots.acquire(), timeout=settings.QUEUE_TIMEOUT)
    except asyncio.TimeoutError:
        raise WebkitError(
            "busy",
            f"all {settings.MAX_PAGES} browser pages busy for {settings.QUEUE_TIMEOUT}s "
            f"({_waiting} requests waiting)",
            hint="retry shortly; many agents are using the backend at once",
            queue=_waiting,
        ) from None
    finally:
        _waiting -= 1
    try:
        yield
    finally:
        _slots.release()


def slots_in_use() -> int:
    return settings.MAX_PAGES - _slots._value  # noqa: SLF001 - informational only


def waiting() -> int:
    return _waiting


def is_connected() -> bool:
    return _browser is not None and _browser.is_connected()


async def browser_version() -> str:
    try:
        return (await ensure_browser()).version
    except Exception:  # noqa: BLE001
        return "unknown"


async def safe_close(page) -> None:
    try:
        await page.close()
    except Exception:  # noqa: BLE001
        pass


async def open_manual(url: str) -> None:
    """Open `url` in a single long-lived page that a human drives via noVNC."""
    global _manual_page
    ctx = await context()
    async with _manual_lock:
        if _manual_page is not None and _manual_page.is_closed():
            _manual_page = None
        if _manual_page is None:
            _manual_page = await ctx.new_page()
        try:
            await _manual_page.goto(url, timeout=30000)
        except Exception:
            await safe_close(_manual_page)
            _manual_page = None
            raise
        await _manual_page.bring_to_front()


async def shutdown() -> None:
    await _stop_playwright()
