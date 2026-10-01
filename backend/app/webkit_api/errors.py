"""One error shape for every endpoint.

Every failure leaves the API as JSON: {"error": <class>, "message": ..., "hint": ...}
plus optional extra fields. The CLI maps the class to an exit code, so classes
are part of the public contract.
"""

from __future__ import annotations

import asyncio

from fastapi import Request
from fastapi.responses import JSONResponse

# class -> HTTP status
ERROR_STATUS = {
    "invalid_request": 400,
    "not_found": 404,
    "human_required": 409,   # CAPTCHA / login wall: only a person (noVNC) can pass it
    "not_a_file": 422,       # /v2/download got a web page and found no file behind it
    "too_large": 413,
    "engines_failed": 502,   # every engine in the chain errored
    "network": 502,          # TCP/TLS/DNS failure reaching the upstream site
    "upstream_http": 502,    # upstream answered with an HTTP error status
    "blocked": 502,          # the site refuses this server outright (IP / hard block)
    "captcha": 502,
    "unexpected_page": 502,  # page loaded but the expected result markup is missing
    "browser_unavailable": 503,
    "busy": 503,             # all browser pages in use for QUEUE_TIMEOUT seconds
    "timeout": 504,
    "internal": 500,
}


class WebkitError(Exception):
    def __init__(self, error: str, message: str, hint: str = "", **extra):
        super().__init__(message)
        self.error = error
        self.message = message
        self.hint = hint
        self.extra = extra

    @property
    def status(self) -> int:
        return ERROR_STATUS.get(self.error, 500)

    def to_dict(self) -> dict:
        body = {"error": self.error, "message": self.message}
        if self.hint:
            body["hint"] = self.hint
        body.update(self.extra)
        return body


_NETWORK_MARKERS = (
    "net::ERR_CONNECTION", "net::ERR_NAME_NOT_RESOLVED", "net::ERR_SSL",
    "net::ERR_TIMED_OUT", "net::ERR_ADDRESS_UNREACHABLE", "net::ERR_INTERNET_DISCONNECTED",
    "net::ERR_PROXY", "net::ERR_TUNNEL", "net::ERR_EMPTY_RESPONSE", "net::ERR_NETWORK",
    "net::ERR_CERT", "net::ERR_HTTP2",
)


def classify(exc: BaseException) -> WebkitError:
    """Map a browser/network exception to a WebkitError."""
    if isinstance(exc, WebkitError):
        return exc
    text = str(exc)
    name = type(exc).__name__
    if isinstance(exc, asyncio.TimeoutError) or name == "TimeoutError" or "Timeout" in name:
        return WebkitError("timeout", _first_line(text) or "operation timed out")
    if any(m in text for m in _NETWORK_MARKERS):
        return WebkitError(
            "network", _first_line(text),
            hint="the backend host could not reach this site; check its egress (proxy/DNS) via `webkit status`",
        )
    if "Target page, context or browser has been closed" in text or "Browser has been closed" in text:
        return WebkitError("browser_unavailable", _first_line(text))
    return WebkitError("internal", f"{name}: {_first_line(text)}")


def human_required(url: str, message: str) -> WebkitError:
    return WebkitError(
        "human_required", message, url=url,
        hint=("needs a person; you cannot pass it. Do not retry or wait: report the URL as not accessible "
              "and continue. If the user is present they can open it in the backend browser via noVNC "
              "and pass it there; passes can expire within ~30 minutes."),
    )


def blocked(url: str, message: str) -> WebkitError:
    return WebkitError(
        "blocked", message, url=url,
        hint=("the site refuses this server (IP-level block); nobody can pass it from here. Do not retry: "
              "use another source (arXiv, PubMed Central, the publisher's open copy, Semantic Scholar) "
              "or report the URL as not accessible."),
    )


def _first_line(text: str) -> str:
    return (text or "").strip().splitlines()[0][:300] if text else ""


async def webkit_error_handler(_: Request, exc: WebkitError) -> JSONResponse:
    return JSONResponse(exc.to_dict(), status_code=exc.status)
