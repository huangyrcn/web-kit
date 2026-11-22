"""Runtime configuration, read once from the environment."""

from __future__ import annotations

import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


CDP_URL = os.environ.get("WEBKIT_CDP_URL", "http://127.0.0.1:9222")
SEARXNG_URL = os.environ.get("WEBKIT_SEARXNG_URL", "http://127.0.0.1:8080")
HOST = os.environ.get("WEBKIT_API_HOST", "127.0.0.1")
PORT = _int("WEBKIT_API_PORT", 3100)

# Pages open concurrently in the shared Chrome. Requests beyond this wait up to
# QUEUE_TIMEOUT seconds and then fail with error class "busy".
MAX_PAGES = _int("WEBKIT_MAX_PAGES", 5)
QUEUE_TIMEOUT = _int("WEBKIT_QUEUE_TIMEOUT", 45)

# Google-family engines retry in place when a CAPTCHA interstitial shows up.
CAPTCHA_RETRIES = _int("WEBKIT_CAPTCHA_RETRIES", 2)
ARXIV_CACHE_SECONDS = _int("WEBKIT_ARXIV_CACHE_SECONDS", 900)

# Semantic Scholar Graph API. With a key, the semantic_scholar engine asks the API
# first and falls back to the website on any API failure; without one it uses the
# website only. A key allows 1 request/second on all endpoints: calls run one at a
# time process-wide, each S2_API_INTERVAL_MS after the previous one finished.
S2_API_KEY = os.environ.get("WEBKIT_S2_API_KEY", "").strip()
S2_API_URL = os.environ.get("WEBKIT_S2_API_URL", "https://api.semanticscholar.org/graph/v1")
S2_API_INTERVAL_MS = _int("WEBKIT_S2_API_INTERVAL_MS", 1100)
S2_API_TIMEOUT = _int("WEBKIT_S2_API_TIMEOUT", 20)
S2_API_QUEUE_TIMEOUT = _int("WEBKIT_S2_API_QUEUE_TIMEOUT", 15)
S2_API_RETRIES = _int("WEBKIT_S2_API_RETRIES", 3)  # on 429/5xx, back-off 1, 2, 3 intervals

def _pairs(name: str) -> dict[str, str]:
    """'a=x,b=y' -> {'a': 'x', 'b': 'y'}, keeping order."""
    out: dict[str, str] = {}
    for item in os.environ.get(name, "").split(","):
        key, sep, value = item.strip().partition("=")
        if sep and key.strip() and value.strip():
            out[key.strip()] = value.strip()
    return out


# Named browser targets: another machine's Chrome, driven through a webkit-executor and
# opencli. Inactive: the executor was removed (2026-11-22) when the CLI moved to local
# mode; kept because the deployed backend still carries it. WEBKIT_TARGETS='lab=http://100.x.y.z:8735', WEBKIT_TARGET_KEYS=
# 'lab=<key>'. Which browsers a request uses, and in what order, is its --via chain
# (e.g. 'local,hnas'); WEBKIT_DEFAULT_VIA is the chain for requests that name none.
TARGETS = _pairs("WEBKIT_TARGETS")
TARGET_KEYS = _pairs("WEBKIT_TARGET_KEYS")
DEFAULT_VIA = os.environ.get("WEBKIT_DEFAULT_VIA", "hnas").strip() or "hnas"
TARGET_TIMEOUT = _int("WEBKIT_TARGET_TIMEOUT", 120)
TARGET_PROBE_INTERVAL = _int("WEBKIT_TARGET_PROBE_INTERVAL", 60)

PAGE_TIMEOUT = _int("WEBKIT_PAGE_TIMEOUT", 60)
CRAWL_MAX_PAGES = _int("WEBKIT_CRAWL_MAX_PAGES", 30)
CRAWL_TIMEOUT = _int("WEBKIT_CRAWL_TIMEOUT", 300)
DOWNLOAD_MAX_BYTES = _int("WEBKIT_DOWNLOAD_MAX_MB", 500) * 1024 * 1024
PDF_MAX_BYTES = _int("WEBKIT_PDF_MAX_MB", 60) * 1024 * 1024

EGRESS_PROBE_INTERVAL = _int("WEBKIT_EGRESS_PROBE_INTERVAL", 300)
EGRESS_HOSTS = [
    h.strip()
    for h in os.environ.get(
        "WEBKIT_EGRESS_HOSTS",
        "www.google.com,html.duckduckgo.com,scholar.google.com,"
        "www.semanticscholar.org,arxiv.org,www.bing.com,api.openalex.org,github.com",
    ).split(",")
    if h.strip()
]

# Build provenance, baked into the image by the Dockerfile.
SEARXNG_REF = os.environ.get("WEBKIT_SEARXNG_REF", "unknown")
IMAGE_TAG = os.environ.get("WEBKIT_IMAGE_TAG", "dev")
