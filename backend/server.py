#!/usr/bin/env python3
"""Web search proxy via Chrome CDP.

Reliability features added on top of the original:
  - _ensure_browser(): per-request CDP reconnect when the underlying browser dies
  - asyncio.Semaphore(3): bounded concurrency (matches Chrome page-pool size)
  - in-process failure cache: short-circuits queries that just failed (60s)

Endpoints:
  /google?q=xxx   → Google search
  /ddg?q=xxx      → DuckDuckGo search
  /open?url=xxx   → Open URL for manual interaction via VNC
  /health         → health check
"""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, quote_plus, urlparse

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from patchright.async_api import async_playwright

CDP_URL = os.environ.get("CDP_URL", "http://localhost:9222")
PORT = int(os.environ.get("PORT", "3100"))
MAX_CONCURRENCY = int(os.environ.get("SEARCH_PROXY_CONCURRENCY", "3"))
FAILURE_CACHE_SECONDS = int(os.environ.get("FAILURE_CACHE_SECONDS", "60"))

logger = logging.getLogger("search-proxy")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_pw = None
_browser = None
_browser_lock = asyncio.Lock()
_page_sem = asyncio.Semaphore(MAX_CONCURRENCY)
_failure_cache: dict[str, float] = {}


async def _ensure_browser():
    """Connect (or reconnect) to Chrome over CDP. Idempotent + concurrency-safe."""
    global _pw, _browser
    if _browser is not None and _browser.is_connected():
        return _browser

    async with _browser_lock:
        if _browser is not None and _browser.is_connected():
            return _browser

        if _pw is not None:
            try:
                await _pw.stop()
            except Exception:
                pass
            _pw = None
            _browser = None

        last_err = None
        for attempt in range(10):
            try:
                _pw = await async_playwright().start()
                _browser = await _pw.chromium.connect_over_cdp(CDP_URL)
                logger.info("Connected to Chrome via CDP (attempt %d)", attempt + 1)
                return _browser
            except Exception as e:
                last_err = e
                logger.warning("CDP connect attempt %d failed: %s", attempt + 1, e)
                if _pw is not None:
                    try:
                        await _pw.stop()
                    except Exception:
                        pass
                    _pw = None
                await asyncio.sleep(2)

        raise RuntimeError(f"Failed to connect to Chrome CDP: {last_err}")


async def _get_context(browser):
    """Reuse the first context if present; otherwise create one."""
    if browser.contexts:
        return browser.contexts[0]
    return await browser.new_context(locale="en-US")


def _record_failure(key: str) -> None:
    _failure_cache[key] = time.time() + FAILURE_CACHE_SECONDS


def _check_failure(key: str) -> float | None:
    """Return seconds remaining if `key` is currently quarantined, else None."""
    until = _failure_cache.get(key)
    if until is None:
        return None
    remaining = until - time.time()
    if remaining <= 0:
        _failure_cache.pop(key, None)
        return None
    return remaining


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Best-effort connect at startup, but don't fail boot if Chrome isn't ready yet
    try:
        await _ensure_browser()
    except Exception as e:
        logger.warning("Initial CDP connect failed (will retry on first request): %s", e)
    yield
    if _pw is not None:
        try:
            await _pw.stop()
        except Exception:
            pass


app = FastAPI(lifespan=lifespan)


# ─── Google ────────────────────────────────────────────────────────────────────

def _clean_google_url(href: str) -> str | None:
    if not href:
        return None
    if href.startswith("/url?"):
        parsed = parse_qs(urlparse(href).query)
        if "q" in parsed:
            return parsed["q"][0]
    if href.startswith("http") and "google.com" not in href:
        return href
    return None


async def _parse_google(page, limit: int) -> list[dict]:
    results = []
    try:
        await page.wait_for_selector("#search", timeout=10000)
    except Exception:
        logger.warning("Google: timeout waiting for #search")
        return results

    containers = await page.query_selector_all("#search div[data-hveid]")
    if not containers:
        containers = await page.query_selector_all("#rso .g")
    if not containers:
        containers = await page.query_selector_all("#rso div[data-hveid]")

    for container in containers:
        if len(results) >= limit:
            break
        h3 = await container.query_selector("h3")
        if not h3:
            continue
        title = (await h3.inner_text()).strip()
        if not title:
            continue

        url = None
        anchor = await container.query_selector("a[href]")
        if anchor:
            href = await anchor.get_attribute("href")
            url = _clean_google_url(href)
        if not url:
            parent = await h3.evaluate_handle("el => el.closest('a')")
            if parent:
                href = await parent.evaluate("el => el.href")
                url = _clean_google_url(href)
        if not url:
            continue

        content = ""
        for sel in [".VwiC3b", "[data-sncf='1']", "div[style*='webkit-line-clamp']", "div[role='text']"]:
            snippet_el = await container.query_selector(sel)
            if snippet_el:
                content = (await snippet_el.inner_text()).strip()
                content = " ".join(content.split())
                if content:
                    break

        parsed = urlparse(url)
        if parsed.hostname and "google" in parsed.hostname:
            continue
        if any(r["url"] == url for r in results):
            continue

        results.append({"title": title, "url": url, "content": content})

    if not results:
        anchors = await page.query_selector_all("#search a[href^='http']")
        for a in anchors:
            if len(results) >= limit:
                break
            href = await a.get_attribute("href")
            if not href or "google.com" in href:
                continue
            text = (await a.inner_text()).strip()
            if text and len(text) > 5:
                results.append({"title": text, "url": href, "content": ""})

    return results


@app.get("/google")
async def search_google(q: str = Query(..., min_length=1), limit: int = Query(10, ge=1, le=30)):
    quarantine = _check_failure("google")
    if quarantine is not None:
        return JSONResponse(
            {"error": f"google quarantined for {int(quarantine)}s after recent failure", "results": []},
            status_code=503,
        )

    try:
        browser = await _ensure_browser()
    except Exception as e:
        return JSONResponse({"error": f"chrome unreachable: {e}", "results": []}, status_code=503)

    try:
        await asyncio.wait_for(_page_sem.acquire(), timeout=30)
    except asyncio.TimeoutError:
        return JSONResponse({"error": "queue timeout", "results": []}, status_code=503)

    page = None
    try:
        ctx = await _get_context(browser)
        page = await ctx.new_page()
        url = f"https://www.google.com/search?q={quote_plus(q)}&num={limit}&hl=en"
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)

        page_content = await page.content()
        if "unusual traffic" in page_content.lower() or "captcha" in page_content.lower():
            logger.error("Google CAPTCHA — log in via VNC (port 6080)")
            _record_failure("google")
            return JSONResponse(
                {"error": "Google CAPTCHA. Log in via VNC to refresh session.", "results": []},
                status_code=429,
            )

        results = await _parse_google(page, limit)
        return {"query": q, "engine": "google", "results": results}
    except Exception as e:
        logger.exception("Google search failed")
        _record_failure("google")
        return JSONResponse({"error": str(e), "results": []}, status_code=500)
    finally:
        if page is not None:
            try:
                await page.close()
            except Exception:
                pass
        _page_sem.release()


# ─── DuckDuckGo ────────────────────────────────────────────────────────────────

async def _parse_ddg(page, limit: int) -> list[dict]:
    results = []
    try:
        await page.wait_for_selector(".results_links, .result, article, [data-result]", timeout=10000)
    except Exception:
        logger.warning("DDG: timeout waiting for results")
        return results

    containers = await page.query_selector_all(".results_links")
    if not containers:
        containers = await page.query_selector_all(".result")
    if not containers:
        containers = await page.query_selector_all("article")
    if not containers:
        containers = await page.query_selector_all("[data-result]")

    for container in containers:
        if len(results) >= limit:
            break
        title_el = await container.query_selector(
            ".result__a, .result__title a, h2 a, a[data-testid='result-title-a']"
        )
        if not title_el:
            title_el = await container.query_selector("a")
        if not title_el:
            continue
        title = (await title_el.inner_text()).strip()
        if not title:
            continue

        url = None
        href = await title_el.get_attribute("href")
        if href:
            if "//duckduckgo.com/l/?" in href:
                parsed = parse_qs(urlparse(href).query)
                if "uddg" in parsed:
                    url = parsed["uddg"][0]
            elif href.startswith("http"):
                url = href

        if not url:
            continue

        content = ""
        snippet_el = await container.query_selector(".result__snippet, .result__body, [data-result='snippet']")
        if snippet_el:
            content = (await snippet_el.inner_text()).strip()
            content = " ".join(content.split())

        if any(r["url"] == url for r in results):
            continue
        results.append({"title": title, "url": url, "content": content})

    if not results:
        anchors = await page.query_selector_all("a[href^='http']")
        for a in anchors:
            if len(results) >= limit:
                break
            href = await a.get_attribute("href")
            if not href or "duckduckgo.com" in href:
                continue
            text = (await a.inner_text()).strip()
            if text and len(text) > 5:
                results.append({"title": text, "url": href, "content": ""})

    return results


@app.get("/ddg")
async def search_ddg(q: str = Query(..., min_length=1), limit: int = Query(10, ge=1, le=30)):
    quarantine = _check_failure("ddg")
    if quarantine is not None:
        return JSONResponse(
            {"error": f"ddg quarantined for {int(quarantine)}s after recent failure", "results": []},
            status_code=503,
        )

    try:
        browser = await _ensure_browser()
    except Exception as e:
        return JSONResponse({"error": f"chrome unreachable: {e}", "results": []}, status_code=503)

    try:
        await asyncio.wait_for(_page_sem.acquire(), timeout=30)
    except asyncio.TimeoutError:
        return JSONResponse({"error": "queue timeout", "results": []}, status_code=503)

    page = None
    try:
        ctx = await _get_context(browser)
        page = await ctx.new_page()
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(q)}"
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)

        results = await _parse_ddg(page, limit)
        return {"query": q, "engine": "duckduckgo", "results": results}
    except Exception as e:
        logger.exception("DDG search failed")
        _record_failure("ddg")
        return JSONResponse({"error": str(e), "results": []}, status_code=500)
    finally:
        if page is not None:
            try:
                await page.close()
            except Exception:
                pass
        _page_sem.release()


# ─── Utilities ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    browser_ok = _browser is not None and _browser.is_connected()
    return {
        "status": "healthy" if browser_ok else "degraded",
        "chrome_cdp": browser_ok,
        "concurrency_in_use": MAX_CONCURRENCY - _page_sem._value,  # informational
        "max_concurrency": MAX_CONCURRENCY,
        "quarantined": list(_failure_cache.keys()),
    }


@app.get("/open")
async def open_url(url: str = Query(...)):
    """Open a URL in Chrome for manual interaction via VNC."""
    try:
        browser = await _ensure_browser()
    except Exception as e:
        return JSONResponse({"error": f"chrome unreachable: {e}"}, status_code=503)
    ctx = await _get_context(browser)
    page = await ctx.new_page()
    await page.goto(url, timeout=30000)
    return {"status": "opened", "url": url, "hint": "Use VNC on port 6080 to interact"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
