#!/bin/bash
# backend/tests/test_integration.sh
# Integration check for the web-kit Caddy auth gateway. Run from a host that
# can reach the deployed gateway. Requires WEB_KIT_API_KEY in env.
#   GW_HOST=192.168.1.178 WEB_KIT_API_KEY=<key> bash backend/tests/test_integration.sh
set -e
GW="${GW_HOST:-192.168.1.178}"

if [ -z "${WEB_KIT_API_KEY:-}" ]; then
  echo "FAIL: WEB_KIT_API_KEY not set in env"; exit 1
fi

echo "== SearxNG: no key -> 401 =="
code=$(curl -s -o /dev/null -w "%{http_code}" "http://$GW:8082/search?q=test&format=json")
[ "$code" = "401" ] || { echo "FAIL: expected 401, got $code"; exit 1; }

echo "== SearxNG: with key -> 200 =="
code=$(curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: $WEB_KIT_API_KEY" \
  "http://$GW:8082/search?q=test&format=json")
[ "$code" = "200" ] || { echo "FAIL: expected 200, got $code"; exit 1; }

echo "== CDP: no key -> 401 =="
code=$(curl -s -o /dev/null -w "%{http_code}" "http://$GW:9223/json/version")
[ "$code" = "401" ] || { echo "FAIL: CDP expected 401, got $code"; exit 1; }

echo "== CDP: with key -> 200 =="
code=$(curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: $WEB_KIT_API_KEY" \
  "http://$GW:9223/json/version")
[ "$code" = "200" ] || { echo "FAIL: CDP with key expected 200, got $code"; exit 1; }

echo "== noVNC: no auth -> 401 =="
code=$(curl -s -o /dev/null -w "%{http_code}" "http://$GW:6080/")
[ "$code" = "401" ] || { echo "FAIL: noVNC expected 401, got $code"; exit 1; }

echo "== noVNC: with basic auth -> 200 =="
code=$(curl -s -o /dev/null -w "%{http_code}" -u "webkit:$WEB_KIT_API_KEY" "http://$GW:6080/")
[ "$code" = "200" ] || { echo "FAIL: noVNC with auth expected 200, got $code"; exit 1; }

# Regression guard for the browser-fetch page CDP path: crawl4ai runs its OWN
# aiohttp GET to <cdp_url>/json/version before connecting, which bypasses the
# connect_over_cdp monkeypatch. If the key isn't injected on that probe too,
# crawl4ai aborts with "CDP verification failed" and never reaches the WS
# connect. Exercise the real script end-to-end (skipped unless the page script
# + uv are present).
PAGE="$(cd "$(dirname "$0")/../.." && pwd)/skills/browser-fetch/scripts/page"
if [ -x "$PAGE" ] && command -v uv >/dev/null 2>&1; then
  echo "== browser-fetch page: end-to-end crawl via authed CDP =="
  out="$(CDP_URL="http://$GW:9223" "$PAGE" https://example.com 2>&1)" || true
  if echo "$out" | grep -q "CDP verification failed"; then
    echo "FAIL: browser-fetch page CDP verification 401'd — key not injected on crawl4ai's /json/version probe"; exit 1
  fi
  echo "$out" | grep -qi "example domain" || { echo "FAIL: browser-fetch page did not return page content"; echo "$out" | tail -5; exit 1; }
  echo "   browser-fetch page OK (fetched example.com through the gateway)"
else
  echo "== browser-fetch page: SKIP (script or uv not available) =="
fi

echo "PASS: gateway auth enforced on all three ports"
