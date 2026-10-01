#!/bin/bash
# Restart Chrome + webkit-api when they are alive but broken (CDP dead, or the
# API lost its CDP link) for FAIL_THRESHOLD consecutive probes.
#
# Upstream reachability is NOT watched here: a restart cannot fix a lost route
# to google.com. webkit-api reports that itself via /v2/status (egress probe).
set -u
PROBE_INTERVAL="${PROBE_INTERVAL:-300}"
FAIL_THRESHOLD="${FAIL_THRESHOLD:-3}"
log() { echo "{\"ts\":\"$(date -u +%FT%TZ)\",\"watchdog\":$1}"; }

sleep 90
fails=0
while true; do
    reason=""
    if ! curl -fs --max-time 5 http://127.0.0.1:9222/json/version >/dev/null; then
        reason="cdp_unreachable"
    elif ! curl -fs --max-time 10 http://127.0.0.1:3100/internal/health | grep -q '"chrome_cdp":true'; then
        reason="api_cdp_disconnected"
    fi
    if [ -z "$reason" ]; then
        fails=0
    else
        fails=$((fails + 1))
        log "{\"ok\":false,\"reason\":\"$reason\",\"fails\":$fails}"
    fi
    if [ "$fails" -ge "$FAIL_THRESHOLD" ]; then
        log "{\"event\":\"restart\",\"programs\":\"chrome webkit-api\"}"
        supervisorctl -c /etc/supervisor/supervisord.conf restart chrome webkit-api >&2 || true
        fails=0
        sleep 30
    fi
    sleep "$PROBE_INTERVAL"
done
