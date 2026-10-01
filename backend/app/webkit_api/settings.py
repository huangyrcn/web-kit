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
