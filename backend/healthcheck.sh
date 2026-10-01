#!/bin/bash
# Container HEALTHCHECK: Chrome, webkit-api (with a live CDP link) and SearXNG.
curl -fs --max-time 5 http://127.0.0.1:9222/json/version >/dev/null || exit 1
curl -fs --max-time 8 http://127.0.0.1:3100/internal/health | grep -q '"chrome_cdp":true' || exit 2
curl -fs --max-time 5 -H "X-Real-IP: 127.0.0.1" http://127.0.0.1:8080/healthz >/dev/null || exit 3
exit 0
