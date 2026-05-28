#!/usr/bin/env python3
"""Web search proxy via Chrome CDP.

Reliability features added on top of the original:
  - _ensure_browser(): per-request CDP reconnect when the underlying browser dies
  - asyncio.Semaphore(3): bounded concurrency (matches Chrome page-pool size)
  - in-process failure cache: short-circuits queries that just failed (15s)

Endpoints:
  /google?q=xxx   → Google search
  /ddg?q=xxx      → DuckDuckGo search
  /arxiv?q=xxx    → arXiv search (direct HTTP, no Chrome)
  /open?url=xxx   → Open URL for manual interaction via VNC
  /health         → health check
"""

import asyncio
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, quote_plus, urlparse

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from patchright.async_api import async_playwright

CDP_URL = os.environ.get("CDP_URL", "http://localhost:9222")
PORT = int(os.environ.get("PORT", "3100"))
MAX_CONCURRENCY = int(os.environ.get("SEARCH_PROXY_CONCURRENCY", "3"))
FAILURE_CACHE_SECONDS = int(os.environ.get("FAILURE_CACHE_SECONDS", "15"))

logger = logging.getLogger("search-proxy")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_pw = None
_browser = None
_browser_lock = asyncio.Lock()
_page_sem = asyncio.Semaphore(MAX_CONCURRENCY)
_failure_cache: dict[str, float] = {}

# /open uses a single dedicated page that's reused across calls.
# Without this, every /open call leaks a new page target into Chrome.
_manual_page = None
_manual_page_lock = asyncio.Lock()


async def _warmup_browser(browser):
    """After CDP (re)connect, do a quick page round-trip to ensure Chrome is
    responsive. Without this the first real search request can hit
    ERR_CONNECTION_RESET because the browser isn't fully initialised yet."""
    page = None
    try:
        ctx = browser.contexts[0] if browser.contexts else await browser.new_context(locale="en-US")
        page = await ctx.new_page()
        await page.goto("about:blank", timeout=5000)
        logger.info("Browser warmup OK")
    except Exception as e:
        logger.warning("Browser warmup failed (non-fatal): %s", e)
    finally:
        if page is not None:
            try:
                await page.close()
            except Exception:
                pass


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
                await _warmup_browser(_browser)
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

def _clean_google_url(href: str | None) -> str | None:
    if not href:
        return None
    if href.startswith("/url?") or href.startswith("https://www.google.com/url?"):
        parsed = parse_qs(urlparse(href).query)
        if "q" in parsed:
            target = parsed["q"][0]
            host = (urlparse(target).hostname or "") if target.startswith("http") else ""
            if host and "google." not in host and not host.endswith("googleusercontent.com"):
                return target
        return None
    if href.startswith("http"):
        host = urlparse(href).hostname or ""
        if "google." in host or host.endswith("googleusercontent.com"):
            return None
        return href
    return None


_GOOGLE_EXTRACT_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  const containerSelectors = [
    '#search div[data-hveid]',
    '#rso .g',
    '#rso div[data-hveid]',
    '#search a:has(h3)',
    '#rso a:has(h3)',
  ];
  let nodes = [];
  for (const sel of containerSelectors) {
    const found = document.querySelectorAll(sel);
    if (found.length) { nodes = Array.from(found); break; }
  }
  if (!nodes.length) {
    nodes = Array.from(document.querySelectorAll('#search a[href]')).filter(a => a.querySelector('h3'));
  }
  const snippetSelectors = [
    '.VwiC3b', "[data-sncf='1']", "div[style*='webkit-line-clamp']",
    "div[role='text']", '.lEBKkf', '.MUxGbd',
  ];
  for (const node of nodes) {
    const h3 = node.querySelector('h3');
    if (!h3) continue;
    const title = (h3.innerText || '').trim();
    if (!title) continue;
    let href = null;
    if (node.tagName === 'A' && node.href) {
      href = node.href;
    } else {
      const a = node.querySelector('a[href]');
      if (a) href = a.href || a.getAttribute('href');
      if (!href) {
        const closestA = h3.closest('a');
        if (closestA) href = closestA.href || closestA.getAttribute('href');
      }
    }
    if (!href || seen.has(href)) continue;
    seen.add(href);
    let snippet = '';
    for (const sel of snippetSelectors) {
      const s = node.querySelector(sel);
      if (s && s.innerText && s.innerText.trim()) {
        snippet = s.innerText.trim().replace(/\s+/g, ' ');
        break;
      }
    }
    out.push({ title, href, snippet });
  }
  return out;
}
"""


async def _parse_google(page, limit: int) -> list[dict]:
    results: list[dict] = []
    try:
        await page.wait_for_selector("#search, #rso, #main", timeout=10000)
    except Exception:
        logger.warning("Google: timeout waiting for results container")
        return results

    try:
        raw = await page.evaluate(_GOOGLE_EXTRACT_JS)
    except Exception as e:
        logger.warning("Google: structured extraction failed: %s", e)
        raw = []

    seen_urls: set[str] = set()
    for item in raw or []:
        if len(results) >= limit:
            break
        try:
            url = _clean_google_url(item.get("href"))
            if not url or url in seen_urls:
                continue
            title = (item.get("title") or "").strip()
            if not title:
                continue
            seen_urls.add(url)
            results.append({
                "title": title,
                "url": url,
                "content": (item.get("snippet") or "").strip(),
            })
        except Exception as e:
            logger.debug("Google: skipping malformed item: %s", e)
            continue

    if not results:
        try:
            anchors = await page.query_selector_all("#search a[href^='http'], #rso a[href^='http']")
        except Exception:
            anchors = []
        for a in anchors:
            if len(results) >= limit:
                break
            try:
                href = await a.get_attribute("href")
                url = _clean_google_url(href)
                if not url or url in seen_urls:
                    continue
                text = (await a.inner_text()).strip()
                if not text or len(text) <= 5:
                    continue
                seen_urls.add(url)
                results.append({"title": text, "url": url, "content": ""})
            except Exception:
                continue

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

        # Retry once on transient connection errors (e.g. cold-start)
        _transient = ("ERR_CONNECTION_CLOSED", "ERR_CONNECTION_RESET",
                      "ERR_CONNECTION_REFUSED", "ERR_NAME_NOT_RESOLVED")
        for attempt in range(2):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                break
            except Exception as e:
                if attempt == 0 and any(t in str(e) for t in _transient):
                    logger.warning("Google: transient error on attempt 1, retrying: %s", e)
                    await page.close()
                    page = await ctx.new_page()
                    await asyncio.sleep(2)
                    continue
                raise

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

        # Retry once on transient connection errors (e.g. cold-start)
        _transient = ("ERR_CONNECTION_CLOSED", "ERR_CONNECTION_RESET",
                      "ERR_CONNECTION_REFUSED", "ERR_NAME_NOT_RESOLVED")
        for attempt in range(2):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                break
            except Exception as e:
                if attempt == 0 and any(t in str(e) for t in _transient):
                    logger.warning("DDG: transient error on attempt 1, retrying: %s", e)
                    await page.close()
                    page = await ctx.new_page()
                    await asyncio.sleep(2)
                    continue
                raise

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


# ─── arXiv (via Chrome, same pattern as Google/DDG) ───────────────────────────

_ARXIV_RESULT_RE = re.compile(
    r'<li class="arxiv-result">(.*?)</li>',
    re.DOTALL,
)
_ARXIV_ID_RE = re.compile(r'arXiv:(\d+\.\d+)')
_ARXIV_TITLE_RE = re.compile(r'<p class="title[^"]*">\s*(.*?)\s*</p>', re.DOTALL)
_ARXIV_AUTHOR_RE = re.compile(r'<p class="authors">(.*?)</p>', re.DOTALL)
_ARXIV_AUTHOR_LINK_RE = re.compile(r'<a[^>]*>(.*?)</a>')
_ARXIV_TAG_RE = re.compile(r'<[^>]+>')
_ARXIV_CAT_RE = re.compile(
    r'<span class="tag[^"]*"[^>]*>(.*?)</span>'
)
_ARXIV_PDF_RE = re.compile(r'href="([^"]*/pdf/[^"]*)"')
_ARXIV_SUBMITTED_RE = re.compile(
    r'<span[^>]*>Submitted</span>\s*(.*?);\s*'
)


def _parse_arxiv_html(html: str, limit: int) -> list[dict]:
    results: list[dict] = []
    for m in _ARXIV_RESULT_RE.finditer(html):
        if len(results) >= limit:
            break
        block = m.group(1)

        id_m = _ARXIV_ID_RE.search(block)
        if not id_m:
            continue
        arxiv_id = id_m.group(1)
        url = f"https://arxiv.org/abs/{arxiv_id}"

        title_m = _ARXIV_TITLE_RE.search(block)
        title = _ARXIV_TAG_RE.sub("", title_m.group(1)).strip() if title_m else ""
        if not title:
            continue

        # Authors
        authors: list[str] = []
        author_m = _ARXIV_AUTHOR_RE.search(block)
        if author_m:
            authors = _ARXIV_AUTHOR_LINK_RE.findall(author_m.group(1))

        # Categories / tags
        tags = _ARXIV_CAT_RE.findall(block)

        # PDF URL
        pdf_url = ""
        pdf_m = _ARXIV_PDF_RE.search(block)
        if pdf_m:
            pdf_url = pdf_m.group(1)

        # Submitted date
        published_date = ""
        sub_m = _ARXIV_SUBMITTED_RE.search(block)
        if sub_m:
            published_date = sub_m.group(1).strip()

        # Comments (e.g. "accepted by ICML 2026")
        comments = ""
        com_m = re.search(
            r'<span[^>]*>Comments:</span>\s*(.*?)</p>', block, re.DOTALL
        )
        if com_m:
            comments = _ARXIV_TAG_RE.sub("", com_m.group(1)).strip()

        # Abstract: find abstract-full span, take everything after its opening
        # tag until end of block, then strip HTML.
        abstract = ""
        abs_idx = block.find('class="abstract-full')
        if abs_idx >= 0:
            gt = block.find(">", abs_idx)
            if gt >= 0:
                raw = block[gt + 1 :]
                abstract = _ARXIV_TAG_RE.sub("", raw).strip()
                for marker in ("△ Less", "▽ Less"):
                    idx = abstract.find(marker)
                    if idx >= 0:
                        abstract = abstract[:idx].strip()

        results.append({
            "title": title,
            "url": url,
            "content": abstract,
            "authors": authors,
            "publishedDate": published_date,
            "comments": comments,
            "tags": tags,
            "pdf_url": pdf_url,
        })
    return results


@app.get("/arxiv")
async def search_arxiv(q: str = Query(..., min_length=1), limit: int = Query(10, ge=1, le=50)):
    quarantine = _check_failure("arxiv")
    if quarantine is not None:
        return JSONResponse(
            {"error": f"arxiv quarantined for {int(quarantine)}s after recent failure", "results": []},
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
        url = f"https://arxiv.org/search/?query={quote_plus(q)}&searchtype=all"

        _transient = ("ERR_CONNECTION_CLOSED", "ERR_CONNECTION_RESET",
                      "ERR_CONNECTION_REFUSED", "ERR_NAME_NOT_RESOLVED")
        for attempt in range(2):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                break
            except Exception as e:
                if attempt == 0 and any(t in str(e) for t in _transient):
                    logger.warning("arXiv: transient error on attempt 1, retrying: %s", e)
                    await page.close()
                    page = await ctx.new_page()
                    await asyncio.sleep(2)
                    continue
                raise

        html = await page.content()
        results = _parse_arxiv_html(html, limit)
        return {"query": q, "engine": "arxiv", "results": results}
    except Exception as e:
        logger.exception("arXiv search failed")
        _record_failure("arxiv")
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
    """Open a URL in Chrome for manual VNC interaction.

    Reuses a single dedicated "manual" page so repeated /open calls don't leak
    page targets. The page lives across calls (the user is interacting with it
    via VNC), but the count is always 1.
    """
    global _manual_page
    try:
        browser = await _ensure_browser()
    except Exception as e:
        return JSONResponse({"error": f"chrome unreachable: {e}"}, status_code=503)

    ctx = await _get_context(browser)

    async with _manual_page_lock:
        # If we have a stored handle but the page was closed (manually or by
        # Chrome crash), drop it and make a fresh one.
        if _manual_page is not None and _manual_page.is_closed():
            _manual_page = None
        if _manual_page is None:
            _manual_page = await ctx.new_page()
        try:
            await _manual_page.goto(url, timeout=30000)
        except Exception as e:
            try:
                await _manual_page.close()
            except Exception:
                pass
            _manual_page = None
            return JSONResponse({"error": f"goto failed: {e}", "url": url}, status_code=502)

    return {"status": "opened", "url": url, "hint": "Use VNC on port 6080 to interact"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
