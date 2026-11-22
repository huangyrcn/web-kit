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
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

from . import browser, s2api, settings, targets
from .download import HUMAN_MARKERS, wall_kind
from .errors import WebkitError, blocked, classify

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
            if (is_captcha is not None and is_captcha(page, content)) or _is_wall_response(resp, content):
                last = WebkitError(
                    "captcha", f"{engine} served a CAPTCHA/challenge page",
                    hint="this engine wants a person; another engine or profile, or WebSearch, may still answer",
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


def _is_wall_response(resp, content: str) -> bool:
    """An error status that is really an anti-bot interstitial (AWS WAF answers 405)."""
    if resp is None or resp.status not in (403, 405, 429, 503):
        return False
    if resp.headers.get("x-amzn-waf-action"):
        return True
    cl = content[:20000].lower()
    return "awswaf" in cl or any(m in cl for m in HUMAN_MARKERS)


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


def google_url(q: str, limit: int, time_range: str | None = None, lang: str | None = None) -> str:
    # udm=14 is Google's plain "Web" tab: for some queries the default page is an
    # AI Mode answer with no result links at all.
    url = f"https://www.google.com/search?q={quote_plus(q)}&num={limit}&hl=en&udm=14"
    if time_range:
        url += f"&tbs={_GOOGLE_TBS[time_range]}"
    if lang:
        url += f"&lr=lang_{quote_plus(lang)}"
    return url


async def google(q: str, limit: int, time_range: str | None = None, lang: str | None = None) -> list[dict]:
    url = google_url(q, limit, time_range, lang)

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


# Every Semantic Scholar page embeds the AWS WAF token script, so "awswaf" and
# "challenge" are in normal result pages too and must not count as a wall. The
# silent WAF challenge (HTTP 202, x-amzn-waf-action: challenge) clears itself in
# Chrome and reloads into the results (observed ~6-12 s on 2026-11-13); only an
# interactive CAPTCHA needs a person (its 405 form is caught by _is_wall_response).
_S2_WALL_MARKERS = ("checking if the site connection is secure", "enable javascript and cookies to continue",
                    "cf-challenge", "captcha", "human verification")
S2_RESULTS_TIMEOUT_MS = 30000
# After the WAF challenge the page loads its results over XHR (/api/1/search). When
# that call is refused, the app renders this block ("Our servers are having a bit of
# trouble. Error: 403") instead of result rows, so wait for either one.
_S2_ROWS = ".cl-paper-row"
_S2_ERROR_BLOCK = '[data-test-id="error-message-block"]'


def s2_is_blocked(page, content: str) -> bool:
    cl = content.lower()
    return any(m in cl for m in _S2_WALL_MARKERS)


def s2_error_page(url: str, text: str) -> WebkitError:
    """S2's in-app error page -> blocked (403: this server is refused) or upstream_http."""
    m = re.search(r"error:\s*(\d{3})", text or "", re.I)
    code = int(m.group(1)) if m else None
    message = f"semantic_scholar website served its error page (HTTP {code or '?'} from its search API)"
    return blocked(url, message) if code == 403 else WebkitError("upstream_http", message)


async def semantic_scholar(q: str, limit: int, time_range: str | None = None, lang: str | None = None) -> list[dict]:
    """The Graph API first when a key is configured; the website on any API failure."""
    if not s2api.enabled():
        return await _s2_website(q, limit)
    try:
        return await s2api.search(q, limit)
    except Exception as e:  # noqa: BLE001 - any API failure falls back to the website
        api_err = classify(e)
    logger.warning("semantic_scholar: API failed (%s: %s); trying the website", api_err.error, api_err.message)
    try:
        return await _s2_website(q, limit)
    except Exception as e:  # noqa: BLE001 - report both paths
        err = classify(e)
        err.message = f"{err.message}; API path: {api_err.error}: {api_err.message}"
        raise err from None


async def _s2_website(q: str, limit: int) -> list[dict]:
    url = f"https://www.semanticscholar.org/search?q={quote_plus(q)}&sort=relevance"

    async def parse(page) -> list[dict]:
        try:
            found = await _wait_results(page, f"{_S2_ROWS}, {_S2_ERROR_BLOCK}", "semantic_scholar",
                                        ("no results", "did not match"), timeout=S2_RESULTS_TIMEOUT_MS)
        except WebkitError:
            try:
                content = await page.content()
            except Exception:  # noqa: BLE001 - still navigating: keep the original error
                content = ""
            if s2_is_blocked(page, content):
                raise WebkitError(
                    "captcha", "semantic_scholar served a CAPTCHA/challenge page",
                    hint="this engine wants a person; another engine or profile, or WebSearch, may still answer",
                ) from None
            raise
        if not found:
            return []
        if not await page.locator(_S2_ROWS).count():  # the error block won the race
            raise s2_error_page(page.url, await page.locator(_S2_ERROR_BLOCK).first.inner_text())
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


# ─── named browser targets: the same engines on another machine's Chrome ──────
#
# Google, Scholar and DuckDuckGo run there as opencli's own adapters (maintained
# upstream); their rows are mapped onto this module's result shape. opencli reports a
# CAPTCHA page as "no results", so an empty answer is checked against the live page
# before it is trusted. Semantic Scholar's website is read there and parsed here with
# the same selectors as the in-page extractor above.

def _rows(rows: list[dict], limit: int, make) -> list[dict]:
    out, seen = [], set()
    for row in rows:
        title, url = str(row.get("title") or "").strip(), str(row.get("url") or "").strip()
        if not title or not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        out.append(make(title, url, row))
        if len(out) >= limit:
            break
    return out


def map_google_rows(rows: list[dict], limit: int) -> list[dict]:
    rows = [r for r in rows if r.get("type", "result") == "result"]
    return _rows(rows, limit, lambda t, u, r: _result(t, u, r.get("snippet", "")))


def map_scholar_rows(rows: list[dict], limit: int) -> list[dict]:
    def make(title, url, r):
        byline = " - ".join(x for x in (str(r.get("authors") or "").strip(), str(r.get("source") or "").strip()) if x)
        return _result(title, url, "", str(r.get("year") or ""), byline=byline, citations=str(r.get("cited") or ""))
    return _rows(rows, limit, make)


def map_duckduckgo_rows(rows: list[dict], limit: int) -> list[dict]:
    rows = [r for r in rows if r.get("resultType", "web") == "web"]
    return _rows(rows, limit, lambda t, u, r: _result(t, u, r.get("snippet", "")))


TARGET_ENGINES: dict[str, dict] = {
    "google": {"command": "google search", "map": map_google_rows, "time": {}, "lang": True,
               "url": lambda q, t: google_url(q, 10)},
    "google_scholar": {"command": "google-scholar search", "map": map_scholar_rows, "time": {},
                       "url": lambda q, t: f"https://scholar.google.com/scholar?q={quote_plus(q)}&hl=zh-CN"},
    "duckduckgo": {"command": "duckduckgo search", "map": map_duckduckgo_rows,
                   "time": {"day": "d", "week": "w", "month": "m", "year": "y"},
                   "url": lambda q, t: f"https://duckduckgo.com/?q={quote_plus(q)}"},
}
TARGET_CAPABLE = frozenset(TARGET_ENGINES) | {"semantic_scholar"}


def target_time_support(engine: str) -> list[str]:
    return list(TARGET_ENGINES[engine]["time"]) if engine in TARGET_ENGINES else []


def page_wall_error(engine: str, target: str, url: str, page: dict) -> WebkitError | None:
    """A CAPTCHA / block page that a target's browser landed on, or None for content."""
    text, html = page.get("text") or "", page.get("html") or ""
    cl = (text + " " + html[:20000]).lower()
    if "/sorry/" in (page.get("final_url") or "") or "unusual traffic" in cl or "g-recaptcha" in cl:
        kind = "human"
    else:
        kind = wall_kind((page.get("title") or "") + " " + text[:2000], html)
    if kind == "blocked":
        return blocked(url, f"{engine} on target {target}: the site refuses that browser")
    if kind == "human":
        return WebkitError("captcha", f"{engine} on target {target} served a CAPTCHA/challenge page",
                           hint="a person can pass it in that machine's Chrome; another engine or profile may answer")
    return None


async def via_target(engine: str, target: str, profile: str | None, q: str, limit: int,
                     time_range: str | None = None, lang: str | None = None, api_first: bool = True) -> list[dict]:
    """Run `engine` on browser target `target`. For semantic_scholar the Graph API still
    comes first unless api_first is False (it already failed on this server)."""
    if engine == "semantic_scholar":
        api_err = None
        if api_first and s2api.enabled():
            try:
                return await s2api.search(q, limit)
            except Exception as e:  # noqa: BLE001 - fall back to the website on the target
                api_err = classify(e)
        try:
            return await s2_target_website(target, profile, q, limit)
        except Exception as e:  # noqa: BLE001
            err = classify(e)
            if api_err:
                err.message = f"{err.message}; API path: {api_err.error}: {api_err.message}"
            raise err from None
    spec = TARGET_ENGINES[engine]
    options: dict = {}
    if time_range:
        if time_range not in spec["time"]:
            raise WebkitError("invalid_request", f"{engine} on a target has no '{time_range}' filter")
        options["time"] = spec["time"][time_range]
    if lang and spec.get("lang"):
        options["lang"] = lang
    rows = await targets.run(target, profile, spec["command"], q, limit, options)
    if rows:
        results = spec["map"](rows, limit)
        if not results:
            raise WebkitError("unexpected_page", f"{engine} on target {target}: {len(rows)} rows, none with a title and URL")
        return results
    url = spec["url"](q, time_range)
    page = await targets.page(target, profile, url, settle_ms=1000)
    err = page_wall_error(engine, target, url, page)
    if err:
        raise err
    return []


def _by_class(node, name: str) -> list:
    return node.xpath(f'.//*[contains(concat(" ", normalize-space(@class), " "), " {name} ")]')


def _first_text(node, *names: str) -> str:
    for name in names:
        for el in _by_class(node, name):
            text = " ".join(el.text_content().split())
            if text:
                return text
    return ""


def parse_s2_html(html: str, url: str, limit: int) -> list[dict]:
    """Semantic Scholar search results from a rendered page (same fields as _S2_EXTRACT_JS)."""
    from lxml import html as lxml_html

    doc = lxml_html.fromstring(html or "<html></html>")
    rows = _by_class(doc, "cl-paper-row")
    if not rows:
        block = doc.xpath('//*[@data-test-id="error-message-block"]')
        if block:
            raise s2_error_page(url, " ".join(block[0].text_content().split()))
        text = " ".join(doc.text_content().split()).lower()
        if "no results" in text or "did not match" in text:
            return []
        if s2_is_blocked(None, html):
            raise WebkitError("captcha", "semantic_scholar served a CAPTCHA/challenge page")
        raise WebkitError("unexpected_page", f"semantic_scholar: result markup not found at {url[:120]}")
    results, seen = [], set()
    for row in rows:
        link = next((a for a in row.iter("a") if "/paper/" in (a.get("href") or "")
                     and not (a.get("href") or "").endswith("#citing-papers")), None)
        title = _first_text(row, "cl-paper-title") or (" ".join(link.text_content().split()) if link is not None else "")
        if link is None or not title:
            continue
        href = urljoin("https://www.semanticscholar.org/", link.get("href")).split("#", 1)[0]
        if href in seen:
            continue
        seen.add(href)
        snippet = _first_text(row, "tldr-abstract-replacement")
        snippet = re.sub(r"\s*Expand$", "", re.sub(r"^TLDR\s*", "", snippet, flags=re.I), flags=re.I).strip()
        citations = re.sub(r"[^\d]", "", _first_text(row, "cl-paper-stats__citation-pdp-link",
                                                     "cl-paper-stats__v2-citations"))
        pdf_el = next(iter(_by_class(row, "cl-paper-view-paper") + _by_class(row, "cl-paper-action__reader-link")), None)
        authors = [" ".join(a.text_content().split()) for a in _by_class(row, "cl-paper-authors__author-box")]
        results.append(_result(
            title, href, snippet, _first_text(row, "cl-paper-pubdates"),
            authors=[a for a in authors if a], venue=_first_text(row, "cl-paper-venue"),
            fields=_first_text(row, "cl-paper-fos"), citations=citations,
            pdf=urljoin(href, pdf_el.get("href")) if pdf_el is not None and pdf_el.get("href") else "",
        ))
        if len(results) >= limit:
            break
    return results


async def s2_target_website(target: str, profile: str | None, q: str, limit: int) -> list[dict]:
    url = f"https://www.semanticscholar.org/search?q={quote_plus(q)}&sort=relevance"
    page = await targets.page(target, profile, url, wait_selector=f"{_S2_ROWS}, {_S2_ERROR_BLOCK}",
                              wait_timeout_ms=S2_RESULTS_TIMEOUT_MS)
    return parse_s2_html(page["html"], page["final_url"], limit)
