"""/v2/download: fetch a file through the logged-in Chrome and stream it back.

Two strategies, ported from web-kit v1's client-side `file` command:
  1. Network.loadNetworkResource + IO.read: Chrome's own network stack, cookies
     included, streamed in chunks (low memory). Preferred.
  2. Fetch interception + Page.navigate: for resources that only load as a real
     navigation (some CSP / challenge flows). Buffers the body.
A CAPTCHA/login wall surfaces as error class human_required, never as a "file".
"""

from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import os
import re
from dataclasses import dataclass, field
from typing import AsyncIterator, Awaitable, Callable
from urllib.parse import quote, unquote, urlparse

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from . import browser, settings
from .errors import WebkitError, classify

logger = logging.getLogger("webkit.download")
router = APIRouter()

CHUNK = 1 << 20
# Interstitials seen in practice (Cloudflare, ScienceDirect, OpenReview, PubMed/PMC, Google).
HUMAN_MARKERS = ("recaptcha", "g-recaptcha", "verify you are human", "unusual traffic",
                 "checking if the site connection is secure", "cf-challenge", "captcha",
                 "just a moment", "are you a robot", "verifying your browser", "checking your browser",
                 "complete the check below", "attention required", "enable javascript and cookies",
                 "请稍候", "正在验证", "是否是真人")  # Cloudflare in a zh-CN browser


@dataclass
class Fetched:
    url: str
    status: int
    headers: dict
    strategy: str
    first: bytes
    rest: AsyncIterator[bytes]
    _cleanup: list[Callable[[], Awaitable[None]]] = field(default_factory=list)

    @property
    def content_type(self) -> str:
        return (self.headers.get("content-type") or "application/octet-stream").split(";")[0].strip()

    @property
    def length(self) -> int | None:
        try:
            return int(self.headers.get("content-length"))
        except (TypeError, ValueError):
            return None

    async def close(self) -> None:
        for fn in reversed(self._cleanup):
            try:
                await fn()
            except Exception:  # noqa: BLE001
                pass
        self._cleanup.clear()


def _decode(body: dict) -> bytes:
    data = body.get("data", "")
    if body.get("base64Encoded"):
        return base64.b64decode(data)
    try:
        return data.encode("utf-8")
    except UnicodeEncodeError:
        return data.encode("latin-1")


def looks_like_human_wall(content_type: str, head: bytes) -> bool:
    if "html" not in content_type:
        return False
    text = head[:8192].decode("utf-8", errors="ignore").lower()
    return any(m in text for m in HUMAN_MARKERS)


async def fetch(url: str, max_bytes: int) -> Fetched:
    """Open `url` and return its first chunk plus an iterator over the rest.
    The caller must `await fetched.close()`; it releases the browser page slot."""
    if urlparse(url).scheme not in ("http", "https"):
        raise WebkitError("invalid_request", "only http(s) URLs can be downloaded")
    slot = browser.page_slot()
    await slot.__aenter__()
    try:
        try:
            fetched = await _via_stream(url)
        except WebkitError as e:
            if e.error in ("human_required", "too_large", "busy"):
                raise
            logger.info("stream strategy failed for %s (%s); trying navigation", url, e.message)
            fetched = await _via_navigate(url)
    except BaseException:
        await slot.__aexit__(None, None, None)
        raise
    fetched._cleanup.insert(0, lambda: slot.__aexit__(None, None, None))
    if fetched.length is not None and fetched.length > max_bytes:
        await fetched.close()
        raise WebkitError("too_large", f"{fetched.length} bytes exceeds the {max_bytes} byte limit")
    if looks_like_human_wall(fetched.content_type, fetched.first):
        await fetched.close()
        raise WebkitError(
            "human_required", "the site answered with a CAPTCHA / verification page",
            hint=f"solve it once with `webkit browser open '{url}'` (noVNC), then retry",
            url=url,
        )
    return fetched


async def _via_stream(url: str) -> Fetched:
    ctx = await browser.context()
    page = await ctx.new_page()
    cdp = None

    async def cleanup():
        if cdp is not None:
            try:
                await cdp.detach()
            except Exception:  # noqa: BLE001
                pass
        await browser.safe_close(page)

    try:
        cdp = await ctx.new_cdp_session(page)
        tree = await cdp.send("Page.getFrameTree")
        res = (await cdp.send("Network.loadNetworkResource", {
            "frameId": tree["frameTree"]["frame"]["id"],
            "url": url,
            "options": {"disableCache": True, "includeCredentials": True},
        }))["resource"]
        status = int(res.get("httpStatusCode") or 0)
        if not res.get("success") or not res.get("stream"):
            err = res.get("netErrorName") or f"HTTP {status}"
            cls = "network" if res.get("netErrorName") else "upstream_http"
            raise WebkitError(cls, f"loadNetworkResource failed: {err}")
        headers = {k.lower(): v for k, v in (res.get("headers") or {}).items()}
        handle = res["stream"]

        async def read_chunk() -> tuple[bytes, bool]:
            chunk = await cdp.send("IO.read", {"handle": handle, "size": CHUNK})
            return _decode(chunk), bool(chunk.get("eof"))

        first, eof = await read_chunk()

        async def rest() -> AsyncIterator[bytes]:
            nonlocal eof
            while not eof:
                data, eof = await read_chunk()
                if data:
                    yield data
            try:
                await cdp.send("IO.close", {"handle": handle})
            except Exception:  # noqa: BLE001
                pass

        if status >= 400 and not looks_like_human_wall(headers.get("content-type", ""), first):
            raise WebkitError("upstream_http", f"site returned HTTP {status}", status_code=status)
        return Fetched(url, status, headers, "stream", first, rest(), [cleanup])
    except BaseException as e:
        await cleanup()
        if isinstance(e, WebkitError):
            raise
        raise classify(e) from None


async def _via_navigate(url: str) -> Fetched:
    ctx = await browser.context()
    page = await ctx.new_page()
    cdp = await ctx.new_cdp_session(page)
    done: asyncio.Future = asyncio.get_running_loop().create_future()

    async def cleanup():
        try:
            await cdp.detach()
        except Exception:  # noqa: BLE001
            pass
        await browser.safe_close(page)

    async def on_paused(params: dict) -> None:
        req_id = params["requestId"]
        status = int(params.get("responseStatusCode") or 0)
        try:
            if 300 <= status < 400 or params.get("resourceType") != "Document" or done.done():
                await cdp.send("Fetch.continueRequest", {"requestId": req_id})
                return
            body = await cdp.send("Fetch.getResponseBody", {"requestId": req_id})
            headers = {h["name"].lower(): h["value"] for h in params.get("responseHeaders", [])}
            await cdp.send("Fetch.failRequest", {"requestId": req_id, "errorReason": "Aborted"})
            done.set_result((status, headers, _decode(body), params.get("request", {}).get("url", url)))
        except Exception as e:  # noqa: BLE001
            if not done.done():
                done.set_exception(e)

    cdp.on("Fetch.requestPaused", lambda p: asyncio.ensure_future(on_paused(p)))
    try:
        await cdp.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Response"}]})
        await cdp.send("Page.navigate", {"url": url})
        status, headers, body, final = await asyncio.wait_for(done, timeout=90)
    except BaseException as e:
        await cleanup()
        if isinstance(e, WebkitError):
            raise
        raise classify(e) from None
    if status >= 400 and not looks_like_human_wall(headers.get("content-type", ""), body):
        await cleanup()
        raise WebkitError("upstream_http", f"site returned HTTP {status}", status_code=status)
    headers["content-length"] = str(len(body))

    async def empty() -> AsyncIterator[bytes]:
        if False:  # pragma: no cover - generator with nothing left to send
            yield b""

    return Fetched(final, status, headers, "navigate", body, empty(), [cleanup])


async def fetch_bytes(url: str, max_bytes: int) -> tuple[bytes, Fetched]:
    """Whole body in memory (for PDFs read via /v2/page)."""
    fetched = await fetch(url, max_bytes)
    try:
        parts, size = [fetched.first], len(fetched.first)
        async for chunk in fetched.rest:
            size += len(chunk)
            if size > max_bytes:
                raise WebkitError("too_large", f"body exceeds the {max_bytes} byte limit")
            parts.append(chunk)
        return b"".join(parts), fetched
    finally:
        await fetched.close()


def filename_for(fetched: Fetched) -> str:
    cd = fetched.headers.get("content-disposition", "")
    m = re.search(r"filename\*\s*=\s*[^']*''([^;]+)", cd, re.I) or re.search(r'filename\s*=\s*"?([^";]+)"?', cd, re.I)
    name = unquote(m.group(1)).strip() if m else os.path.basename(urlparse(fetched.url).path)
    name = re.sub(r"[/\\\x00-\x1f]", "_", name) or "download"
    if mimetypes.guess_type(name)[0] is None:  # "2609.31881" has no real extension
        ext = ".pdf" if fetched.first[:5] == b"%PDF-" else (mimetypes.guess_extension(fetched.content_type) or "")
        name += ext
    return name[:200]


@router.get("/v2/download")
async def download(url: str = Query(..., min_length=8), max_mb: int = Query(0, ge=0)):
    limit = max_mb * 1024 * 1024 if max_mb else settings.DOWNLOAD_MAX_BYTES
    fetched = await fetch(url, limit)

    async def body() -> AsyncIterator[bytes]:
        sent = len(fetched.first)
        try:
            yield fetched.first
            async for chunk in fetched.rest:
                sent += len(chunk)
                if sent > limit:
                    logger.warning("download of %s exceeded %d bytes; aborting", url, limit)
                    return
                yield chunk
        finally:
            await fetched.close()

    name = filename_for(fetched)
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
        "X-Webkit-Filename": quote(name),
        "X-Webkit-Final-Url": quote(fetched.url, safe=":/?&=%#"),
        "X-Webkit-Strategy": fetched.strategy,
        "X-Webkit-Upstream-Status": str(fetched.status),
    }
    if fetched.length is not None and not fetched.headers.get("content-encoding"):
        headers["Content-Length"] = str(fetched.length)  # IO.read yields decoded bytes
    return StreamingResponse(body(), media_type=fetched.content_type, headers=headers)
