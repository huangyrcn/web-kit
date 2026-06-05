#!/bin/bash
# backend/tests/test_start_caddy.sh
set -e
HERE="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$HERE/start-caddy.sh"

# Case 1: no key -> must exit non-zero, must NOT exec caddy
unset WEB_KIT_API_KEY
if CADDY_DRYRUN=1 bash "$SCRIPT" 2>/dev/null; then
  echo "FAIL: started without key"; exit 1
fi

# Case 2: key set -> must reach the exec point (dry-run prints marker)
export WEB_KIT_API_KEY="testkey123"
out="$(CADDY_DRYRUN=1 bash "$SCRIPT" 2>&1)"
echo "$out" | grep -q "DRYRUN-would-exec-caddy" || { echo "FAIL: did not reach exec"; exit 1; }
echo "PASS: fail-closed behavior correct"
