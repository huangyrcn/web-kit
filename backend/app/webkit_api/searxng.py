"""API-backed engines served by the embedded SearXNG (github, pypi, openalex, ...).

SearXNG only aggregates these; the crawler engines in serp.py are called
directly so their outcome (captcha / network / layout) is known exactly.
"""

from __future__ import annotations

import httpx

from . import settings
from .errors import WebkitError

# CLI name -> SearXNG engine name (as configured in settings.yml)
ENGINES = {
    "bing": "bing",
    "openalex": "openalex",
    "pubmed": "pubmed",
    "github": "github",
    "github_code": "github code",
    "gitlab": "gitlab",
    "huggingface": "huggingface",
    "pypi": "pypi",
    "npm": "npm",
    "crates": "crates.io",
    "lib_rs": "lib.rs",
    "pkg_go_dev": "pkg.go.dev",
    "sourcehut": "sourcehut",
    "microsoft_learn": "microsoft learn",
    "nvd": "nvd",
    "hackernews": "hackernews",
    "stackoverflow": "stackoverflow",
    "wikidata": "wikidata",
    "annas_archive": "annas archive",
    "zlibrary": "zlibrary",
}

_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        # SearXNG's bot detection logs an error for every request without a client IP header.
        _client = httpx.AsyncClient(base_url=settings.SEARXNG_URL, timeout=30.0,
                                    headers={"X-Real-IP": "127.0.0.1", "X-Forwarded-For": "127.0.0.1"})
    return _client


async def search(name: str, q: str, limit: int, time_range: str | None = None,
                 lang: str | None = None) -> list[dict]:
    sx_name = ENGINES[name]
    params = {"q": q, "format": "json", "engines": sx_name, "pageno": 1}
    if time_range:
        params["time_range"] = time_range
    if lang:
        params["language"] = lang
    try:
        resp = await client().get("/search", params=params)
    except httpx.TimeoutException:
        raise WebkitError("timeout", f"searxng did not answer for engine {name}") from None
    except httpx.HTTPError as e:
        raise WebkitError("internal", f"searxng unreachable: {e}") from None
    if resp.status_code != 200:
        raise WebkitError("internal", f"searxng returned HTTP {resp.status_code} for engine {name}")
    data = resp.json()
    results = []
    for r in data.get("results", []):
        if not r.get("url") or not r.get("title"):
            continue
        out = {"title": r["title"].strip(), "url": r["url"], "snippet": " ".join((r.get("content") or "").split())}
        if r.get("publishedDate"):
            out["published"] = str(r["publishedDate"])
        results.append(out)
        if len(results) >= limit:
            break
    if not results:
        for engine, reason in data.get("unresponsive_engines", []):
            if engine == sx_name:
                raise _from_searxng_reason(name, reason)
    return results


def _from_searxng_reason(name: str, reason: str) -> WebkitError:
    r = (reason or "").lower()
    if "timeout" in r:
        cls = "timeout"
    elif "captcha" in r:
        cls = "captcha"
    elif "access denied" in r or "too many requests" in r or "suspended" in r:
        cls = "blocked"
    elif "http" in r and "error" in r:
        cls = "upstream_http"
    elif "connect" in r or "ssl" in r or "network" in r:
        cls = "network"
    else:
        cls = "upstream_http"
    return WebkitError(cls, f"{name}: searxng reports '{reason}'")


async def config() -> dict:
    """SearXNG's /config: per-engine time_range/language support."""
    try:
        resp = await client().get("/config")
        resp.raise_for_status()
        return resp.json()
    except Exception:  # noqa: BLE001 - informational
        return {}


async def healthy() -> bool:
    try:
        return (await client().get("/healthz")).status_code == 200
    except Exception:  # noqa: BLE001
        return False


async def stats_errors() -> dict:
    try:
        resp = await client().get("/stats/errors")
        resp.raise_for_status()
        return resp.json()
    except Exception:  # noqa: BLE001
        return {}
