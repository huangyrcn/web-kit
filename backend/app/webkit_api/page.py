"""/v2/page (read one URL) and /v2/crawl (bounded multi-page crawl).

HTML goes through crawl4ai attached to the shared, logged-in Chrome over CDP.
PDFs are detected first and returned as extracted text, so research agents can
`read` an arXiv/ACL PDF the same way they read a web page.
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter
from pydantic import BaseModel, Field

from . import browser, settings
from .download import HUMAN_MARKERS, fetch_bytes
from .errors import WebkitError, classify

logger = logging.getLogger("webkit.page")
router = APIRouter()

Format = Literal["fit", "md", "html", "links", "json"]


class PageRequest(BaseModel):
    url: str
    format: Format = "fit"
    wait_for: str | None = Field(None, description="CSS selector, or milliseconds to wait")
    scroll: bool = False
    cache: bool = False
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


async def _read_pdf(req: PageRequest) -> dict | None:
    data, fetched = await fetch_bytes(req.url, settings.PDF_MAX_BYTES)
    if data[:5] != b"%PDF-":
        return None  # not actually a PDF: fall back to the HTML path
    title, text, pages = await asyncio.to_thread(pdf_to_text, data)
    if req.format == "html":
        raise WebkitError("invalid_request", "format=html is not available for PDFs; use fit/md/json")
    out = {"url": req.url, "final_url": fetched.url, "title": title, "kind": "pdf", "pages": pages}
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
    ".navbox,.mw-jump-link,.vector-header-container,#mw-navigation,#toc,.toc"
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


_CHALLENGE_DONE_JS = (
    "js:() => !/just a moment|verifying your browser|checking your browser|are you a robot|attention required|请稍候|正在验证/i"
    ".test(document.title + ' ' + (document.body ? document.body.innerText.slice(0, 400) : ''))"
)


def _is_challenge(result) -> bool:
    """A short page whose title/text is an anti-bot interstitial, or crawl4ai's own block verdict."""
    if (result.error_message or "").startswith("Blocked by anti-bot protection"):
        return True
    title = ((result.metadata or {}).get("title") or "").lower()
    text = _markdown(result, False)
    if len(text) > 4000:
        return False
    head = (title + " " + text[:2000]).lower()
    return any(m in head for m in HUMAN_MARKERS)


def _check_result(result, url: str) -> None:
    if _is_challenge(result):
        raise WebkitError(
            "human_required", "the site shows an anti-bot / verification page that the browser did not pass",
            hint=f"solve it once with `webkit browser open '{url}'` (noVNC), then retry", url=url,
        )
    if not result.success:
        raise classify(RuntimeError(result.error_message or "crawl failed"))
    if result.status_code and result.status_code >= 400:
        raise WebkitError("upstream_http", f"site returned HTTP {result.status_code}",
                          status_code=result.status_code)


def _truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars and len(text) > max_chars:
        return text[:max_chars], True
    return text, False


@router.post("/v2/page")
async def page(req: PageRequest):
    _check_url(req.url)
    if req.format != "html" and await _is_pdf(req.url):
        out = await _read_pdf(req)
        if out is not None:
            if "content" in out:
                out["content"], out["truncated"] = _truncate(out["content"], req.max_chars)
                out["chars"] = len(out["content"])
            return out

    from crawl4ai import AsyncWebCrawler

    config = _run_config(req.format == "fit", req.cache, req.wait_for, req.scroll, req.timeout)
    async with browser.page_slot():
        try:
            async with AsyncWebCrawler(config=_browser_config()) as crawler:
                result = await asyncio.wait_for(crawler.arun(url=req.url, config=config),
                                                timeout=req.timeout + 15)
                # A real browser usually clears Cloudflare-style interstitials on its own
                # within seconds; the first capture can be too early. Retry once, waiting
                # until the title/text no longer looks like a challenge.
                if _is_challenge(result) or (not result.success and "_crawl_web" in (result.error_message or "")):
                    logger.info("challenge or crawl error on %s; retrying with wait", req.url)
                    retry = _run_config(req.format == "fit", False, _CHALLENGE_DONE_JS, req.scroll,
                                        max(req.timeout, 30))
                    result = await asyncio.wait_for(crawler.arun(url=req.url, config=retry),
                                                    timeout=max(req.timeout, 30) + 15)
        except WebkitError:
            raise
        except Exception as e:  # noqa: BLE001
            raise classify(e) from None
    _check_result(result, req.url)

    title = (result.metadata or {}).get("title") or ""
    out = {"url": req.url, "final_url": getattr(result, "redirected_url", None) or result.url,
           "title": title, "kind": "html", "format": req.format}
    if req.format == "links":
        links = []
        for scope in ("internal", "external"):
            for link in (result.links or {}).get(scope, []):
                if link.get("href"):
                    links.append({"href": link["href"], "text": (link.get("text") or "").strip(), "scope": scope})
        out["links"] = links
        return out
    if req.format == "html":
        content = result.cleaned_html or ""
    else:
        content = _markdown(result, req.format == "fit")
    out["content"], out["truncated"] = _truncate(content, req.max_chars)
    out["chars"] = len(out["content"])
    return out


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
