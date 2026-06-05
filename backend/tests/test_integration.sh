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

echo "PASS: gateway auth enforced on all three ports"
