"""/v2/page (read one URL) and /v2/crawl (bounded multi-page crawl).

HTML goes through crawl4ai attached to the shared, logged-in Chrome over CDP.
PDFs are detected first and returned as extracted text, so research agents can
`read` an arXiv/ACL PDF the same way they read a web page.
"""

from __future__ import annotations

import asyncio
import html as html_lib
import io
import logging
import re
import time
from typing import Literal
from urllib.parse import urljoin, urlparse

from fastapi import APIRouter
from pydantic import BaseModel, Field

from . import browser, settings
from .download import fetch_bytes, wall_kind
from .errors import WebkitError, blocked, classify, human_required

logger = logging.getLogger("webkit.page")
router = APIRouter()

Format = Literal["fit", "md", "html", "links", "json"]


class PageRequest(BaseModel):
    url: str
    format: Format = "fit"
    wait_for: str | None = Field(None, description="CSS selector, or milliseconds to wait")
    scroll: bool = False
    cache: bool = False
    pdf: bool = Field(False, description="read the paper PDF this page links (citation_pdf_url)")
    max_chars: int = Field(0, ge=0)
    timeout: int = Field(settings.PAGE_TIMEOUT, ge=5, le=300)


class CrawlRequest(BaseModel):
    url: str
    strategy: Literal["bfs", "dfs"] = "bfs"
    max_depth: int = Field(2, ge=1, le=5)
    max_pages: int = Field(10, ge=1)
    include: list[str] = []
    exclude: list[str] = []
    format: Literal["fit", "md"] = "fit"
    timeout: int = Field(180, ge=10)


def _check_url(url: str) -> None:
    if urlparse(url).scheme not in ("http", "https"):
        raise WebkitError("invalid_request", "url must be http(s)")


# ─── PDF ───────────────────────────────────────────────────────────────────────

async def _is_pdf(url: str) -> bool:
    path = urlparse(url).path.lower()
    if path.endswith(".pdf") or "arxiv.org/pdf/" in url:
        return True
    try:
        ctx = await browser.context()
        resp = await ctx.request.head(url, timeout=8000, max_redirects=5)
        return "application/pdf" in (resp.headers.get("content-type") or "")
    except Exception:  # noqa: BLE001 - HEAD is a hint only
        return False


def pdf_to_text(data: bytes) -> tuple[str, str, int]:
    """(title, markdown-ish text, page count). Each page gets a marker line."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    title = ""
    try:
        title = (reader.metadata.title or "").strip() if reader.metadata else ""
    except Exception:  # noqa: BLE001
        pass
    parts = []
    for i, pg in enumerate(reader.pages, 1):
        try:
            text = pg.extract_text() or ""
        except Exception:  # noqa: BLE001 - one bad page must not sink the document
            text = ""
        parts.append(f"<!-- page {i} -->\n{text.strip()}")
    body = "\n\n".join(parts)
    return title, (f"# {title}\n\n{body}" if title else body), len(reader.pages)


_PDF_META = re.compile(r"<meta\b[^>]*\bname\s*=\s*[\"']citation_pdf_url[\"'][^>]*>", re.I)
_PDF_ALT = re.compile(r"<link\b[^>]*\btype\s*=\s*[\"']application/pdf[\"'][^>]*>", re.I)
_CONTENT = re.compile(r"\b(?:content|href)\s*=\s*[\"']([^\"']+)[\"']", re.I)


def pdf_link(html: str, base: str) -> str | None:
    """The paper PDF a landing page declares: Highwire/Google Scholar `citation_pdf_url`
    (publishers, arXiv, OpenReview, PMC), else <link type="application/pdf">."""
    for rx in (_PDF_META, _PDF_ALT):
        for tag in rx.finditer(html):
            m = _CONTENT.search(tag.group(0))
            if m and m.group(1).strip():
                return urljoin(base, html_lib.unescape(m.group(1).strip()))
    return None


async def find_pdf_url(url: str, timeout: int = settings.PAGE_TIMEOUT) -> str | None:
    """Render `url` and return the PDF it links (itself, if it is a PDF)."""
    try:
        rendered = await render(url, None, False, timeout)
    except _IsPdf:
        return url
    return pdf_link(rendered["html"], rendered["final_url"])


async def _read_pdf(req: PageRequest, url: str | None = None) -> dict | None:
    data, fetched = await fetch_bytes(url or req.url, settings.PDF_MAX_BYTES)
    if data[:5] != b"%PDF-":
        return None  # not actually a PDF: fall back to the HTML path
    title, text, pages = await asyncio.to_thread(pdf_to_text, data)
    if req.format == "html":
        raise WebkitError("invalid_request", "format=html is not available for PDFs; use fit/md/json")
    out = {"url": req.url, "final_url": fetched.url, "title": title, "kind": "pdf", "pages": pages}
    if url and url != req.url:
        out["pdf_url"] = url
    if req.format == "links":
        out.update(format="links", links=[])
        return out
    if req.format == "json":
        out.update(format="json", content=text, bytes=len(data))
    else:
        out.update(format=req.format, content=text)
    return out


# ─── HTML via crawl4ai ─────────────────────────────────────────────────────────

def _browser_config():
    from crawl4ai import BrowserConfig

    return BrowserConfig(browser_mode="custom", cdp_url=settings.CDP_URL, verbose=False)


# "fit" = main content. Pruning alone keeps site chrome at the top of many pages
# (Wikipedia menus, arXiv search box, blog TOCs); dropping structural chrome first
# fixes that. remove_overlay_elements is off: it removed article leads in tests.
_FIT_EXCLUDED_TAGS = ["nav", "header", "footer", "aside", "form", "noscript"]
_FIT_EXCLUDED_SELECTOR = (
    "[role=navigation],[role=banner],[role=contentinfo],[role=search],[aria-label=breadcrumb],"
    ".sr-only,.is-sr-only,.visually-hidden,.screen-reader-text,.skip-link,"
    ".navbox,.mw-jump-link,.vector-header-container,#mw-navigation,#toc,.toc,"
    "[id*=cookie],[class*=cookie],[id*=consent],[class*=consent],[aria-label*=cookie],#onetrust-consent-sdk,"
    ".cc-banner,.cc-window,"
    # skip links ("Skip to main content"), wiki banners, and hidden a11y strings
    # (e.g. Atypon's "opens in a new window" labels)
    "a[class*=skip],[class*=skipnav],[class*=skip-link],#siteNotice,#centralNotice,[hidden]"
)


def _run_config(fmt_fit: bool, cache: bool, wait_for: str | None, scroll: bool, timeout: int, **extra):
    from crawl4ai import CacheMode, CrawlerRunConfig
    from crawl4ai.content_filter_strategy import PruningContentFilter
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

    kwargs = dict(
        cache_mode=CacheMode.ENABLED if cache else CacheMode.BYPASS,
        markdown_generator=DefaultMarkdownGenerator(
            content_filter=PruningContentFilter() if fmt_fit else None),
        scan_full_page=scroll,
        page_timeout=timeout * 1000,
        verbose=False,
    )
    if fmt_fit:
        kwargs.update(excluded_tags=_FIT_EXCLUDED_TAGS, excluded_selector=_FIT_EXCLUDED_SELECTOR)
    if wait_for:
        if wait_for.isdigit():
            kwargs["delay_before_return_html"] = int(wait_for) / 1000
        else:
            kwargs["wait_for"] = wait_for if wait_for.startswith(("css:", "js:")) else f"css:{wait_for}"
    kwargs.update(extra)
    return CrawlerRunConfig(**kwargs)


def _markdown(result, fit: bool) -> str:
    md = result.markdown
    if md is None:
        return ""
    if isinstance(md, str):
        return md
    if fit and getattr(md, "fit_markdown", None):
        return md.fit_markdown
    return getattr(md, "raw_markdown", "") or str(md)


# ─── HTML: rendered by the shared (patchright) Chrome, converted offline ──────────
#
# Rendering goes through the same patchright browser as the search engines, so a
# page read does not leak vanilla-Playwright automation signals that would undo a
# profile's standing with Cloudflare-style checks. crawl4ai is used only for
# HTML -> Markdown (scraping + pruning), never to drive the browser here.

CHALLENGE_WAIT = 30  # seconds a real browser gets to clear an interstitial on its own
ERROR_PAGE_WAIT = 8  # a short 4xx/5xx page may turn into a challenge once its script runs
_SCROLL_JS = """async () => {
  for (let i = 0; i < 20; i++) {
    const before = document.scrollingElement.scrollHeight;
    window.scrollTo(0, before);
    await new Promise(r => setTimeout(r, 400));
    if (document.scrollingElement.scrollHeight === before) break;
  }
  window.scrollTo(0, 0);
}"""
_cache: dict[tuple, tuple[float, dict]] = {}
CACHE_TTL = 600


def page_wall_kind(title: str, text: str, html: str = "") -> str | None:
    """'blocked' / 'human' for an interstitial page, None for content. DataDome walls
    keep their text in an iframe, so near-empty pages are checked by markup too."""
    if "captcha-delivery.com" in html[:20000].lower():
        return wall_kind("", html)
    if len(text) > 4000:
        return None
    return wall_kind(title + " " + text[:2000])


def looks_like_challenge(title: str, text: str) -> bool:
    """A short page whose title/text is an anti-bot interstitial a person can pass."""
    return page_wall_kind(title, text) == "human"


async def _page_state(page) -> str:
    """'blocked', 'challenge', 'settling' (near-empty: JS checks such as NCBI's redirect
    within seconds) or 'ready'."""
    try:
        title = await page.title()
        text = await page.evaluate("() => document.body ? document.body.innerText.slice(0, 4500) : ''")
        html = await page.content() if len(text.strip()) < 200 else ""
    except Exception:  # noqa: BLE001 - mid-navigation
        return "settling"
    kind = page_wall_kind(title, text, html)
    if kind == "blocked":
        return "blocked"
    if kind == "human":
        return "challenge"
    return "settling" if len(text.strip()) < 200 else "ready"


async def _is_short(page) -> bool:
    try:
        return await page.evaluate("() => document.body ? document.body.innerText.length < 1500 : true")
    except Exception:  # noqa: BLE001 - mid-navigation
        return True


class _IsPdf(Exception):
    pass


async def render(url: str, wait_for: str | None, scroll: bool, timeout: int) -> dict:
    """Open `url` in the shared Chrome and return the settled DOM."""
    async with browser.page_slot():
        page = await (await browser.context()).new_page()
        statuses: list[int] = []

        def on_response(resp):
            try:
                if resp.request.resource_type == "document" and resp.frame == page.main_frame:
                    statuses.append(resp.status)
            except Exception:  # noqa: BLE001
                pass

        page.on("response", on_response)
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            if resp is not None and "application/pdf" in (resp.headers.get("content-type") or ""):
                raise _IsPdf()
            t0 = time.monotonic()
            reloaded = False
            while True:
                state = await _page_state(page)
                waited = time.monotonic() - t0
                status = statuses[-1] if statuses else (resp.status if resp is not None else 0)
                if state == "ready" and status >= 400 and waited < ERROR_PAGE_WAIT and await _is_short(page):
                    state = "settling"  # e.g. ResearchGate: "Temporarily Unavailable" -> "Just a moment..."
                if state in ("ready", "blocked") or (state == "settling" and waited > 10) or waited > CHALLENGE_WAIT:
                    break
                await page.wait_for_timeout(1000)
                # Some checks ("Cookies must be enabled ... reload this page") set a cookie
                # and only let a reload through; give the site's own redirect time first.
                if state == "challenge" and not reloaded and waited > 6 and "reload" in (await page.evaluate(
                        "() => document.body ? document.body.innerText.slice(0, 400).toLowerCase() : ''")):
                    reloaded = True
                    await page.reload(wait_until="domcontentloaded", timeout=timeout * 1000)
            final = await _page_state(page)
            if final == "blocked":
                raise blocked(url, "the site refuses this server (block page instead of content)")
            if final == "challenge":
                raise human_required(url, "the site shows an anti-bot / verification page that the browser did not pass")
            try:
                await page.wait_for_load_state("load", timeout=10000)
            except Exception:  # noqa: BLE001 - slow trackers must not fail a read
                pass
            if wait_for:
                if wait_for.isdigit():
                    await page.wait_for_timeout(int(wait_for))
                else:
                    await page.wait_for_selector(wait_for.removeprefix("css:"), timeout=timeout * 1000)
            if scroll:
                await page.evaluate(_SCROLL_JS)
            status = statuses[-1] if statuses else (resp.status if resp is not None else 0)
            return {"html": await page.content(), "title": await page.title(), "final_url": page.url,
                    "status": status}
        except (WebkitError, _IsPdf):
            raise
        except Exception as e:  # noqa: BLE001
            raise classify(e) from None
        finally:
            await browser.safe_close(page)


def to_markdown(url: str, html: str, fit: bool):
    """(markdown, cleaned_html, links, metadata) using crawl4ai's scraper and generator."""
    from crawl4ai.content_filter_strategy import PruningContentFilter
    from crawl4ai.content_scraping_strategy import LXMLWebScrapingStrategy
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

    kwargs = {"excluded_tags": _FIT_EXCLUDED_TAGS, "excluded_selector": _FIT_EXCLUDED_SELECTOR} if fit else {}
    scraped = LXMLWebScrapingStrategy().scrap(url, html, **kwargs)
    md = DefaultMarkdownGenerator(content_filter=PruningContentFilter() if fit else None).generate_markdown(
        input_html=scraped.cleaned_html, base_url=url, citations=False)
    text = (md.fit_markdown if fit and md.fit_markdown else md.raw_markdown) or ""
    return text, scraped.cleaned_html, scraped.links, scraped.metadata or {}


def _truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars and len(text) > max_chars:
        return text[:max_chars], True
    return text, False


def _finish(out: dict, max_chars: int) -> dict:
    if "content" in out:
        out["content"], out["truncated"] = _truncate(out["content"], max_chars)
        out["chars"] = len(out["content"])
    return out


@router.post("/v2/page")
async def page(req: PageRequest):
    _check_url(req.url)
    key = (req.url, req.format, req.wait_for, req.scroll, req.pdf)
    if req.cache and key in _cache and _cache[key][0] > time.time():
        return _finish(dict(_cache[key][1]), req.max_chars)

    out = None
    if req.pdf and req.format != "html" and not await _is_pdf(req.url):
        pdf_url = await find_pdf_url(req.url, req.timeout)
        if not pdf_url:
            raise WebkitError("not_found", "this page links no PDF (no citation_pdf_url)", url=req.url,
                              hint="read the page itself (without --pdf), or find the PDF link with `-f links`")
        out = await _read_pdf(req, pdf_url)
        if out is None:
            raise WebkitError("not_a_file", f"the page's PDF link did not return a PDF: {pdf_url}", url=req.url,
                              pdf_url=pdf_url, hint="the publisher may need a login or show a viewer; "
                              "try `webkit download URL`, or read the page itself")
    elif req.format != "html" and await _is_pdf(req.url):
        out = await _read_pdf(req)
    if out is None:
        try:
            rendered = await render(req.url, req.wait_for, req.scroll, req.timeout)
        except _IsPdf:
            out = await _read_pdf(req)
            if out is None:
                raise WebkitError("internal", "server announced a PDF but did not deliver one") from None
        else:
            if rendered["status"] >= 400:
                raise WebkitError("upstream_http", f"site returned HTTP {rendered['status']}",
                                  status_code=rendered["status"])
            text, cleaned, links, meta = await asyncio.to_thread(
                to_markdown, rendered["final_url"], rendered["html"], req.format == "fit")
            out = {"url": req.url, "final_url": rendered["final_url"],
                   "title": rendered["title"] or meta.get("title") or "", "kind": "html", "format": req.format}
            if req.format == "links":
                out["links"] = [{"href": l.href, "text": (l.text or "").strip(), "scope": scope}
                                for scope in ("internal", "external")
                                for l in getattr(links, scope, []) if l.href]
            elif req.format == "html":
                out["content"] = cleaned
            elif req.format == "json":
                out["content"] = text
                out["metadata"] = {k: v for k, v in meta.items() if isinstance(v, (str, int, float))}
            else:
                out["content"] = text
    if len(_cache) > 256:  # keep the in-process cache small
        for k in [k for k, (exp, _) in _cache.items() if exp < time.time()] or list(_cache)[:128]:
            _cache.pop(k, None)
    _cache[key] = (time.time() + CACHE_TTL, dict(out))
    return _finish(out, req.max_chars)


@router.post("/v2/crawl")
async def crawl(req: CrawlRequest):
    _check_url(req.url)
    if req.max_pages > settings.CRAWL_MAX_PAGES:
        raise WebkitError("invalid_request", f"max_pages is capped at {settings.CRAWL_MAX_PAGES}")
    if req.timeout > settings.CRAWL_TIMEOUT:
        raise WebkitError("invalid_request", f"timeout is capped at {settings.CRAWL_TIMEOUT}s")

    from crawl4ai import AsyncWebCrawler
    from crawl4ai.deep_crawling import BFSDeepCrawlStrategy, DFSDeepCrawlStrategy
    from crawl4ai.deep_crawling.filters import FilterChain, URLPatternFilter

    filters = []
    if req.include:
        filters.append(URLPatternFilter(patterns=req.include))
    if req.exclude:
        filters.append(URLPatternFilter(patterns=req.exclude, reverse=True))
    strategy_cls = BFSDeepCrawlStrategy if req.strategy == "bfs" else DFSDeepCrawlStrategy
    strategy = strategy_cls(max_depth=req.max_depth, filter_chain=FilterChain(filters),
                            max_pages=req.max_pages, include_external=False)
    config = _run_config(req.format == "fit", False, None, False, min(req.timeout, settings.PAGE_TIMEOUT),
                         deep_crawl_strategy=strategy, stream=False, semaphore_count=2)

    async with browser.page_slot():
        try:
            async with AsyncWebCrawler(config=_browser_config()) as crawler:
                results = await asyncio.wait_for(crawler.arun(url=req.url, config=config), timeout=req.timeout)
        except asyncio.TimeoutError:
            raise WebkitError("timeout", f"crawl exceeded {req.timeout}s",
                              hint="lower --max-pages / --max-depth or raise --timeout") from None
        except Exception as e:  # noqa: BLE001
            raise classify(e) from None

    pages = []
    for r in results if isinstance(results, list) else [results]:
        item = {"url": r.url, "depth": (r.metadata or {}).get("depth"),
                "title": (r.metadata or {}).get("title") or ""}
        if r.success and not (r.status_code and r.status_code >= 400):
            item["content"] = _markdown(r, req.format == "fit")
            item["chars"] = len(item["content"])
        else:
            item["error"] = classify(RuntimeError(r.error_message or f"HTTP {r.status_code}")).error
            item["message"] = (r.error_message or f"HTTP {r.status_code}")[:300]
        pages.append(item)
    return {"url": req.url, "strategy": req.strategy, "count": len(pages), "pages": pages}
