"""Semantic Scholar Graph API: the first path of the semantic_scholar engine.

Used only when WEBKIT_S2_API_KEY is set; serp.semantic_scholar falls back to the
website on any error raised here. A key allows 1 request/second across all
endpoints, so every call goes through one process-wide pacer.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

import httpx

from . import settings
from .errors import WebkitError

logger = logging.getLogger("webkit.s2api")

FIELDS = ("title,url,abstract,tldr,venue,year,publicationDate,authors,citationCount,"
          "openAccessPdf,fieldsOfStudy,externalIds")
_RETRY_STATUS = (429, 500, 502, 503, 504)

_client: httpx.AsyncClient | None = None
_pace_lock = asyncio.Lock()
_last_end = 0.0


def enabled() -> bool:
    return bool(settings.S2_API_KEY)


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=settings.S2_API_URL, timeout=settings.S2_API_TIMEOUT,
                                    headers={"x-api-key": settings.S2_API_KEY})
    return _client


@asynccontextmanager
async def _turn():
    """One request at a time, process-wide, each starting at least S2_API_INTERVAL_MS after
    the previous one finished: S2 still answered 429 to requests whose starts were 1.1 s
    apart (2026-11-22). A caller that waits longer than S2_API_QUEUE_TIMEOUT for its turn
    gives up, so the engine falls back to the website."""
    global _last_end
    try:
        await asyncio.wait_for(_pace_lock.acquire(), timeout=settings.S2_API_QUEUE_TIMEOUT)
    except asyncio.TimeoutError:
        raise WebkitError("timeout", "semantic_scholar API: request queue is full") from None
    try:
        wait = _last_end + settings.S2_API_INTERVAL_MS / 1000 - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        yield
    finally:
        _last_end = time.monotonic()
        _pace_lock.release()


async def _get(path: str, params: dict) -> httpx.Response:
    """GET with retries on 429/5xx. S2 answers 429 (TooManyRequestsException) even after
    45 s without a request from us (2026-11-22), so a 429 is retried after a growing
    back-off rather than taken as our own rate being too high."""
    retries = max(settings.S2_API_RETRIES, 0)
    for attempt in range(retries + 1):
        async with _turn():
            try:
                resp = await client().get(path, params=params)
            except httpx.TimeoutException:
                raise WebkitError("timeout", "semantic_scholar API did not answer") from None
            except httpx.HTTPError as e:
                raise WebkitError("network", f"semantic_scholar API: {type(e).__name__}: {e}") from None
        if resp.status_code in _RETRY_STATUS and attempt < retries:
            logger.warning("semantic_scholar API: HTTP %d, retry %d/%d", resp.status_code, attempt + 1, retries)
            await asyncio.sleep(settings.S2_API_INTERVAL_MS / 1000 * (attempt + 1))
            continue
        return resp


async def search(q: str, limit: int) -> list[dict]:
    resp = await _get("/paper/search", {"query": q, "limit": min(max(limit, 1), 100), "fields": FIELDS})
    if resp.status_code in (401, 403):
        raise WebkitError("upstream_http", f"semantic_scholar API rejected the key (HTTP {resp.status_code})")
    if resp.status_code >= 400:
        raise WebkitError("upstream_http", f"semantic_scholar API returned HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError:
        raise WebkitError("unexpected_page", "semantic_scholar API returned non-JSON") from None
    if not isinstance(data, dict) or not isinstance(data.get("data", []), list):
        raise WebkitError("unexpected_page", "semantic_scholar API returned an unexpected body")
    return parse(data, limit)


def parse(data: dict, limit: int) -> list[dict]:
    """Graph API /paper/search body -> engine results, same shape as the website parser."""
    from .serp import _result  # serp imports this module

    out = []
    for p in data.get("data") or []:
        title, url = (p.get("title") or "").strip(), (p.get("url") or "").strip()
        if not title or not url:
            continue
        ids = p.get("externalIds") or {}
        snippet = ((p.get("tldr") or {}).get("text") or p.get("abstract") or "")
        citations = p.get("citationCount")
        out.append(_result(
            title, url, snippet, p.get("publicationDate") or str(p.get("year") or ""),
            authors=[a.get("name") for a in p.get("authors") or [] if a.get("name")],
            venue=p.get("venue") or "",
            fields=", ".join(p.get("fieldsOfStudy") or []),
            citations="" if citations is None else str(citations),
            pdf=(p.get("openAccessPdf") or {}).get("url") or "",
            doi=ids.get("DOI") or "",
            arxiv=ids.get("ArXiv") or "",
        ))
        if len(out) >= limit:
            break
    return out
