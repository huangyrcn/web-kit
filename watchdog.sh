#!/bin/bash
# Business-level watchdog: detects "process alive but search broken" cases.
# Probes the search-proxy /google endpoint; on 3 consecutive failures,
# restarts chrome + search-proxy via supervisorctl.

set -u

PROBE_INTERVAL="${PROBE_INTERVAL:-60}"
FAIL_THRESHOLD="${FAIL_THRESHOLD:-3}"
PROBE_QUERY="${PROBE_QUERY:-test}"

# Wait once for the stack to come up before starting probes.
sleep 90

fails=0
while true; do
    start_ms=$(date +%s%3N)
    if curl -fs --max-time 20 \
            "http://localhost:3100/google?q=${PROBE_QUERY}&limit=1" \
            -o /tmp/probe.json; then
        # Check that we actually got results, not an empty error response
        if grep -q '"results"' /tmp/probe.json && \
           grep -qE '"results":\s*\[\s*\{' /tmp/probe.json; then
            elapsed=$(( $(date +%s%3N) - start_ms ))
            echo "{\"ts\":\"$(date -u +%FT%TZ)\",\"probe\":\"google\",\"ms\":${elapsed},\"ok\":true}"
            fails=0
        else
            elapsed=$(( $(date +%s%3N) - start_ms ))
            echo "{\"ts\":\"$(date -u +%FT%TZ)\",\"probe\":\"google\",\"ms\":${elapsed},\"ok\":false,\"reason\":\"empty_results\"}"
            fails=$((fails+1))
        fi
    else
        elapsed=$(( $(date +%s%3N) - start_ms ))
        echo "{\"ts\":\"$(date -u +%FT%TZ)\",\"probe\":\"google\",\"ms\":${elapsed},\"ok\":false,\"reason\":\"http_error\"}"
        fails=$((fails+1))
    fi

    if [ "$fails" -ge "$FAIL_THRESHOLD" ]; then
        echo "{\"ts\":\"$(date -u +%FT%TZ)\",\"event\":\"watchdog_restart\",\"fails\":${fails}}"
        supervisorctl -c /etc/supervisor/supervisord.conf restart chrome search-proxy >&2 || true
        fails=0
        sleep 30
    fi

    sleep "$PROBE_INTERVAL"
done
