#!/bin/bash
# Business-level watchdog.
#
# v2 (2026-05): probe is internal-only.
#
# Why the change: the v1 probe hit /google?q=test every 60 seconds. Over a few
# hours that became 200+ Google queries from the same IP, which caused Google
# to rate-limit us. Watchdog then saw failures, restarted Chrome, and probed
# again — same IP, same rate limit, infinite loop.
#
# Lesson: a probe must not make the resource it's monitoring worse. v2 watches
# the *internal* surface (CDP socket + search-proxy /health). Real upstream
# health is monitored only as a low-frequency sanity check.

set -u

PROBE_INTERVAL="${PROBE_INTERVAL:-300}"        # 5 min — internal probes
SANITY_INTERVAL="${SANITY_INTERVAL:-1800}"     # 30 min — real-search sanity
FAIL_THRESHOLD="${FAIL_THRESHOLD:-3}"

# Wait for the stack to come up.
sleep 90

now_ms() { date +%s%3N; }
log_json() {
    echo "{\"ts\":\"$(date -u +%FT%TZ)\",$1}"
}

# Returns 0 when *all* internal checks pass; 1 otherwise.
probe_internal() {
    local t0 reason=""
    t0=$(now_ms)

    # 1. CDP must answer.
    if ! curl -fs --max-time 5 http://localhost:9222/json/version >/dev/null; then
        reason="cdp_unreachable"
        log_json "\"probe\":\"internal\",\"ms\":$(( $(now_ms) - t0 )),\"ok\":false,\"reason\":\"${reason}\""
        return 1
    fi

    # 2. search-proxy must report healthy + chrome connected.
    local resp
    resp=$(curl -fs --max-time 5 http://localhost:3100/health 2>/dev/null || true)
    if [ -z "$resp" ]; then
        log_json "\"probe\":\"internal\",\"ms\":$(( $(now_ms) - t0 )),\"ok\":false,\"reason\":\"search_proxy_unreachable\""
        return 1
    fi
    if ! echo "$resp" | grep -q '"chrome_cdp":true'; then
        log_json "\"probe\":\"internal\",\"ms\":$(( $(now_ms) - t0 )),\"ok\":false,\"reason\":\"chrome_cdp_disconnected\""
        return 1
    fi
    # status:degraded means it knows it's broken — flag but don't immediately fail
    if echo "$resp" | grep -q '"status":"degraded"'; then
        log_json "\"probe\":\"internal\",\"ms\":$(( $(now_ms) - t0 )),\"ok\":false,\"reason\":\"degraded\""
        return 1
    fi

    log_json "\"probe\":\"internal\",\"ms\":$(( $(now_ms) - t0 )),\"ok\":true"
    return 0
}

# Low-frequency real-upstream check. Picks DDG (more lenient than Google).
# Failure here is *informational* — we do NOT restart on it because the
# fix for upstream rate-limit is "wait", not "restart Chrome".
probe_upstream_sanity() {
    local t0 resp
    t0=$(now_ms)
    if resp=$(curl -fs --max-time 15 \
                "http://localhost:3100/ddg?q=health-check&limit=1" 2>/dev/null) \
       && echo "$resp" | grep -qE '"results":\s*\[\s*\{'; then
        log_json "\"probe\":\"upstream_sanity\",\"ms\":$(( $(now_ms) - t0 )),\"ok\":true"
    else
        log_json "\"probe\":\"upstream_sanity\",\"ms\":$(( $(now_ms) - t0 )),\"ok\":false,\"note\":\"informational_only\""
    fi
}

last_sanity=0
fails=0

while true; do
    if probe_internal; then
        fails=0
    else
        fails=$((fails + 1))
    fi

    # Run upstream sanity at most every $SANITY_INTERVAL seconds.
    now=$(date +%s)
    if [ $(( now - last_sanity )) -ge "$SANITY_INTERVAL" ]; then
        probe_upstream_sanity
        last_sanity=$now
    fi

    if [ "$fails" -ge "$FAIL_THRESHOLD" ]; then
        # Internal failure — process really is broken. Restart is appropriate.
        log_json "\"event\":\"watchdog_restart\",\"fails\":${fails}"
        supervisorctl -c /etc/supervisor/supervisord.conf restart chrome search-proxy >&2 || true
        fails=0
        sleep 30
    fi

    sleep "$PROBE_INTERVAL"
done
