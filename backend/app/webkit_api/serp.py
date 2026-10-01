"""Search engines scraped through the logged-in Chrome (the "crawler" engines).

Each engine renders the real results page and extracts results in-page. Parsers
are carried over from web-kit v1, which has run them in production.

Every engine returns a list of {title, url, snippet, published?, extra} or raises
WebkitError; it never silently returns [] for a page it could not read.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import date
from typing import Awaitable, Callable
from urllib.parse import parse_qs, quote_plus, urlparse

from . import browser, settings
from .errors import WebkitError, classify

logger = logging.getLogger("webkit.serp")

_BACKOFFS = [2, 4]


async def _render(
    engine: str,
    url: str,
    parse: Callable[[object], Awaitable[list[dict]]],
    is_captcha: Callable[[object, str], bool] | None = None,
    retries: int = 0,
    goto_timeout: int = 20000,
) -> list[dict]:
    """Open `url` in a fresh page, retry on CAPTCHA/transient errors, parse."""
    async with browser.page_slot():
        return await _render_in_slot(engine, url, parse, is_captcha, retries, goto_timeout)


async def _render_in_slot(engine, url, parse, is_captcha, retries, goto_timeout) -> list[dict]:
    ctx = await browser.context()
    last: WebkitError | None = None
    for attempt in range(retries + 1):
        page = await ctx.new_page()
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=goto_timeout)
            content = await page.content()
            if is_captcha is not None and is_captcha(page, content):
                last = WebkitError(
                    "captcha", f"{engine} served a CAPTCHA/challenge page",
                    hint="solve it once via `webkit browser open <url>` (noVNC); cookies then persist",
                )
                logger.warning("%s: CAPTCHA (attempt %d/%d)", engine, attempt + 1, retries + 1)
            elif resp is not None and resp.status >= 400:
                last = WebkitError("upstream_http", f"{engine} returned HTTP {resp.status}")
            else:
                return await parse(page)
        except WebkitError as e:
            last = e
        except Exception as e:  # noqa: BLE001 - classified below
            last = classify(e)
            logger.warning("%s: %s (attempt %d/%d)", engine, last.message, attempt + 1, retries + 1)
        finally:
            await browser.safe_close(page)
        if attempt < retries and last.error in ("captcha", "network", "timeout"):
            await asyncio.sleep(_BACKOFFS[min(attempt, len(_BACKOFFS) - 1)])
            continue
        break
    raise last


async def _wait_results(page, selector: str, engine: str, no_results_markers: tuple[str, ...] = (),
                        timeout: int = 10000) -> bool:
    """Wait for result markup. Returns False for a legitimate "no results" page;
    raises unexpected_page when neither results nor a no-results notice appear."""
    try:
        await page.wait_for_selector(selector, timeout=timeout)
        return True
    except Exception:  # noqa: BLE001
        text = (await page.content()).lower()
        if any(m in text for m in no_results_markers):
            return False
        raise WebkitError(
            "unexpected_page",
            f"{engine}: result markup not found (layout change or soft block) at {page.url[:120]}",
        ) from None


def _result(title: str, url: str, snippet: str = "", published: str = "", **extra) -> dict:
    out = {"title": title.strip(), "url": url.strip(), "snippet": " ".join((snippet or "").split())}
    if published:
        out["published"] = published.strip()
    extra = {k: v for k, v in extra.items() if v not in ("", None, [], {})}
    if extra:
        out["extra"] = extra
    return out


# ─── Google ────────────────────────────────────────────────────────────────────

def clean_google_url(href: str | None) -> str | None:
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


# Every result-heading link in the results column, whatever container markup Google
# serves (a first-match selector cascade once grabbed only a video block).
_GOOGLE_EXTRACT_JS = r"""
() => {
  // Every result heading link in the results column, whatever its container markup.
  const out = [];
  const seen = new Set();
  const snippetSelectors = ['.VwiC3b', "[data-sncf='1']", "div[style*='webkit-line-clamp']", '.lEBKkf', '.MUxGbd'];
  for (const a of document.querySelectorAll('#rso a:has(h3), #search a:has(h3)')) {
    const title = (a.querySelector('h3').innerText || '').trim();
    const href = a.getAttribute('href');
    if (!title || !href || seen.has(href)) continue;
    seen.add(href);
    const card = a.closest('.MjjYud, .g, [data-hveid]') || a.parentElement;
    let snippet = '';
    for (const sel of snippetSelectors) {
      const s = card && card.querySelector(sel);
      if (s && s.innerText && s.innerText.trim()) { snippet = s.innerText.trim().replace(/\s+/g, ' '); break; }
    }
    out.push({ title, href, snippet });
  }
  return out;
}
"""

def _is_goto(href: str | None) -> bool:
    return bool(href) and (href.startswith("/goto?") or href.startswith("https://www.google.com/goto?"))


async def _resolve_goto_links(page, items: list[dict]) -> None:
    """Newer Google result pages link to /goto?url=<opaque token>; the target is
    only in the redirect. Resolve those concurrently without following it."""
    todo = [i for i in items if _is_goto(i.get("href"))]
    if not todo:
        return
    request = page.context.request

    async def resolve(item: dict) -> None:
        href = item["href"]
        url = href if href.startswith("http") else "https://www.google.com" + href
        try:
            resp = await request.get(url, max_redirects=0, timeout=8000)
            item["href"] = resp.headers.get("location") or None
        except Exception as e:  # noqa: BLE001 - an unresolved link is dropped
            logger.warning("google: could not resolve /goto link: %s", e)
            item["href"] = None

    await asyncio.gather(*(resolve(i) for i in todo))


_GOOGLE_TBS = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}


def google_is_captcha(page, content: str) -> bool:
    # Normal Google HTML contains 'hasCaptchaSupport' in a script var: not a CAPTCHA.
    cl = content.lower()
    return "unusual traffic" in cl or "/sorry/" in page.url or "g-recaptcha" in cl


async def google(q: str, limit: int, time_range: str | None = None, lang: str | None = None) -> list[dict]:
    url = f"https://www.google.com/search?q={quote_plus(q)}&num={limit}&hl=en"
    if time_range:
        url += f"&tbs={_GOOGLE_TBS[time_range]}"
    if lang:
        url += f"&lr=lang_{quote_plus(lang)}"

    async def parse(page) -> list[dict]:
        if not await _wait_results(page, "#search, #rso", "google",
                                   ("did not match any documents", "no results found for")):
            return []
        raw = await page.evaluate(_GOOGLE_EXTRACT_JS) or []
        raw = [i for i in raw if (i.get("title") or "").strip()][: limit * 2]
        await _resolve_goto_links(page, raw)
        results, seen = [], set()
        for item in raw:
            target = clean_google_url(item.get("href"))
            if not target or target in seen:
                continue
            seen.add(target)
            results.append(_result(item["title"], target, item.get("snippet", "")))
            if len(results) >= limit:
                break
        if raw and not results:
            raise WebkitError("unexpected_page",
                              f"google: {len(raw)} result headings found but no target URL could be extracted")
        return results

    return await _render("google", url, parse, google_is_captcha, settings.CAPTCHA_RETRIES)


# ─── DuckDuckGo (HTML endpoint) ────────────────────────────────────────────────

_DDG_DF = {"day": "d", "week": "w", "month": "m", "year": "y"}


async def duckduckgo(q: str, limit: int, time_range: str | None = None, lang: str | None = None) -> list[dict]:
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(q)}"
    if time_range:
        url += f"&df={_DDG_DF[time_range]}"

    async def parse(page) -> list[dict]:
        if not await _wait_results(page, ".results_links, .result", "duckduckgo",
                                   ("no results.", "no  results")):
            return []
        results: list[dict] = []
        for container in await page.query_selector_all(".results_links, .result"):
            if len(results) >= limit:
                break
            title_el = await container.query_selector(".result__a, .result__title a, h2 a")
            if not title_el:
                continue
            title = (await title_el.inner_text()).strip()
            href = await title_el.get_attribute("href") or ""
            target = None
            if "//duckduckgo.com/l/?" in href:
                target = parse_qs(urlparse(href).query).get("uddg", [None])[0]
            elif href.startswith("http"):
                target = href
            if not title or not target or "duckduckgo.com/y.js" in target:
                continue  # ads link through y.js
            if any(r["url"] == target for r in results):
                continue
            snippet_el = await container.query_selector(".result__snippet")
            snippet = (await snippet_el.inner_text()) if snippet_el else ""
            results.append(_result(title, target, snippet))
        return results

    return await _render("duckduckgo", url, parse, None, 1)


# ─── Google Scholar ────────────────────────────────────────────────────────────

_SCHOLAR_EXTRACT_JS = r"""
() => {
  const out = [];
  for (const item of document.querySelectorAll('.gs_r.gs_or.gs_scl')) {
    const titleEl = item.querySelector('.gs_rt a');
    if (!titleEl) continue;
    let title = (titleEl.innerText || '').trim();
    if (!title) continue;
    title = title.replace(/^\[(?:PDF|HTML|CITATION|BOOK)\]\s*/i, '');
    const authorsRaw = (item.querySelector('.gs_a') || {}).innerText || '';
    const yearMatch = authorsRaw.match(/\b((?:19|20)\d{2})\b/);
    const snippetEl = item.querySelector('.gs_rs');
    const pdfEl = item.querySelector('.gs_or_ggsm a');
    let citations = '';
    for (const a of item.querySelectorAll('.gs_fl a')) {
      const m = a.innerText.match(/Cited by (\d+)/);
      if (m) { citations = m[1]; break; }
    }
    out.push({
      title, url: titleEl.href || '',
      byline: authorsRaw.trim(),
      year: yearMatch ? yearMatch[1] : '',
      snippet: snippetEl ? snippetEl.innerText.trim() : '',
      pdf: pdfEl ? pdfEl.href : '',
      citations,
    });
  }
  return out;
}
"""


def scholar_is_captcha(page, content: str) -> bool:
    cl = content.lower()  # 'robot' appears in normal Scholar meta tags: not a CAPTCHA
    return "unusual traffic" in cl or "g-recaptcha" in cl or "/sorry/" in page.url


async def google_scholar(q: str, limit: int, time_range: str | None = None, lang: str | None = None) -> list[dict]:
    url = f"https://scholar.google.com/scholar?q={quote_plus(q)}&hl=en"
    if time_range:  # Scholar only filters by year: "year" means since last calendar year
        url += f"&as_ylo={date.today().year - 1}"

    async def parse(page) -> list[dict]:
        if not await _wait_results(page, ".gs_r.gs_or.gs_scl", "google_scholar",
                                   ("did not match any articles",)):
            return []
        results = []
        for item in (await page.evaluate(_SCHOLAR_EXTRACT_JS) or [])[:limit]:
            if not item.get("url"):
                continue
            results.append(_result(
                item["title"], item["url"], item.get("snippet", ""), item.get("year", ""),
                byline=item.get("byline", ""), citations=item.get("citations", ""), pdf=item.get("pdf", ""),
            ))
        return results

    return await _render("google_scholar", url, parse, scholar_is_captcha, settings.CAPTCHA_RETRIES)


# ─── Semantic Scholar (website) ────────────────────────────────────────────────

_S2_EXTRACT_JS = r"""
() => {
  const text = (el) => (el && (el.innerText || el.textContent) || '').trim().replace(/\s+/g, ' ');
  const out = [];
  for (const row of document.querySelectorAll('.cl-paper-row')) {
    const titleLink = row.querySelector('a[href*="/paper/"]:not([href$="#citing-papers"])');
    const title = text(row.querySelector('.cl-paper-title') || titleLink);
    const url = titleLink ? titleLink.href : '';
    if (!title || !url) continue;
    const authors = Array.from(row.querySelectorAll('.cl-paper-authors__author-box')).map(text).filter(Boolean);
    let snippet = text(row.querySelector('.tldr-abstract-replacement'));
    snippet = snippet.replace(/^TLDR\s*/i, '').replace(/\s*Expand$/i, '').trim();
    const citationEl = row.querySelector('.cl-paper-stats__citation-pdp-link, .cl-paper-stats__v2-citations');
    const sourceLink = row.querySelector('.cl-paper-view-paper[href]');
    const readerLink = row.querySelector('a.cl-paper-action__reader-link[href]');
    out.push({
      title, url, authors, snippet,
      venue: text(row.querySelector('.cl-paper-venue')),
      published: text(row.querySelector('.cl-paper-pubdates')),
      fields: text(row.querySelector('.cl-paper-fos')),
      citations: citationEl ? text(citationEl).replace(/[^\d]/g, '') : '',
      pdf: sourceLink ? sourceLink.href : (readerLink ? readerLink.href : ''),
    });
  }
  return out;
}
"""


def s2_is_blocked(page, content: str) -> bool:
    cl = content.lower()
    return ("checking if the site connection is secure" in cl
            or "enable javascript and cookies to continue" in cl
            or "cf-challenge" in cl or "g-recaptcha" in cl)


async def semantic_scholar(q: str, limit: int, time_range: str | None = None, lang: str | None = None) -> list[dict]:
    url = f"https://www.semanticscholar.org/search?q={quote_plus(q)}&sort=relevance"

    async def parse(page) -> list[dict]:
        if not await _wait_results(page, ".cl-paper-row", "semantic_scholar",
                                   ("no results", "did not match"), timeout=15000):
            return []
        results, seen = [], set()
        for item in await page.evaluate(_S2_EXTRACT_JS) or []:
            link = (item.get("url") or "").split("#", 1)[0]
            if not link or link in seen:
                continue
            seen.add(link)
            results.append(_result(
                item["title"], link, item.get("snippet", ""), item.get("published", ""),
                authors=item.get("authors") or [], venue=item.get("venue", ""), fields=item.get("fields", ""),
                citations=item.get("citations", ""), pdf=item.get("pdf", ""),
            ))
            if len(results) >= limit:
                break
        return results

    return await _render("semantic_scholar", url, parse, s2_is_blocked, 1, goto_timeout=30000)


# ─── arXiv (search page, rendered by Chrome, parsed from HTML) ─────────────────

_ARXIV_RESULT_RE = re.compile(r'<li class="arxiv-result">(.*?)</li>', re.DOTALL)
_ARXIV_ID_RE = re.compile(r"arXiv:(\d+\.\d+)")
_ARXIV_TITLE_RE = re.compile(r'<p class="title[^"]*">\s*(.*?)\s*</p>', re.DOTALL)
_ARXIV_AUTHOR_RE = re.compile(r'<p class="authors">(.*?)</p>', re.DOTALL)
_ARXIV_AUTHOR_LINK_RE = re.compile(r"<a[^>]*>(.*?)</a>")
_TAG_RE = re.compile(r"<[^>]+>")
_ARXIV_CAT_RE = re.compile(r'<span class="tag[^"]*"[^>]*>(.*?)</span>')
_ARXIV_PDF_RE = re.compile(r'href="([^"]*/pdf/[^"]*)"')
_ARXIV_SUBMITTED_RE = re.compile(r"<span[^>]*>Submitted</span>\s*(.*?);\s*")
_ARXIV_COMMENTS_RE = re.compile(r"<span[^>]*>Comments:</span>\s*(.*?)</p>", re.DOTALL)


def parse_arxiv_html(html: str, limit: int) -> list[dict]:
    results: list[dict] = []
    for m in _ARXIV_RESULT_RE.finditer(html):
        if len(results) >= limit:
            break
        block = m.group(1)
        id_m = _ARXIV_ID_RE.search(block)
        title_m = _ARXIV_TITLE_RE.search(block)
        title = _TAG_RE.sub("", title_m.group(1)).strip() if title_m else ""
        if not id_m or not title:
            continue
        author_m = _ARXIV_AUTHOR_RE.search(block)
        authors = _ARXIV_AUTHOR_LINK_RE.findall(author_m.group(1)) if author_m else []
        pdf_m = _ARXIV_PDF_RE.search(block)
        sub_m = _ARXIV_SUBMITTED_RE.search(block)
        com_m = _ARXIV_COMMENTS_RE.search(block)
        abstract = ""
        idx = block.find('class="abstract-full')
        if idx >= 0 and (gt := block.find(">", idx)) >= 0:
            abstract = _TAG_RE.sub("", block[gt + 1:]).strip()
            for marker in ("△ Less", "▽ Less"):
                if (cut := abstract.find(marker)) >= 0:
                    abstract = abstract[:cut].strip()
        results.append(_result(
            title, f"https://arxiv.org/abs/{id_m.group(1)}", abstract,
            sub_m.group(1).strip() if sub_m else "",
            authors=authors, categories=_ARXIV_CAT_RE.findall(block),
            pdf=pdf_m.group(1) if pdf_m else "",
            comments=_TAG_RE.sub("", com_m.group(1)).strip() if com_m else "",
        ))
    return results


_arxiv_lock = asyncio.Lock()
_arxiv_cache: dict[str, tuple[float, list[dict]]] = {}


async def arxiv(q: str, limit: int, time_range: str | None = None, lang: str | None = None) -> list[dict]:
    """arXiv rate-limits bursts from one IP: serialize and cache successful pages."""
    key = q.strip().lower()
    async with _arxiv_lock:
        cached = _arxiv_cache.get(key)
        if cached and cached[0] > time.time():
            return cached[1][:limit]
        url = f"https://arxiv.org/search/?query={quote_plus(q)}&searchtype=all&order=-announced_date_first"

        async def parse(page) -> list[dict]:
            html = await page.content()
            if "arxiv-result" not in html:
                if "sorry, your query" in html.lower() or "no results" in html.lower():
                    return []
                raise WebkitError("unexpected_page", "arxiv: result markup not found")
            return parse_arxiv_html(html, max(limit, 25))

        results = await _render("arxiv", url, parse, None, 0, goto_timeout=30000)
        if results:
            _arxiv_cache[key] = (time.time() + settings.ARXIV_CACHE_SECONDS, results)
        return results[:limit]


ENGINES: dict[str, Callable[..., Awaitable[list[dict]]]] = {
    "google": google,
    "duckduckgo": duckduckgo,
    "google_scholar": google_scholar,
    "semantic_scholar": semantic_scholar,
    "arxiv": arxiv,
}

TIME_SUPPORT = {
    "google": ["day", "week", "month", "year"],
    "duckduckgo": ["day", "week", "month", "year"],
    "google_scholar": ["year"],
    "semantic_scholar": [],
    "arxiv": [],
}
