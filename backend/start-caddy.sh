#!/bin/bash
# Launch Caddy. Fail-closed: refuse to start if WEB_KIT_API_KEY is empty,
# so the gateway never comes up without authentication.
set -e

if [ -z "${WEB_KIT_API_KEY:-}" ]; then
    echo "start-caddy: WEB_KIT_API_KEY is empty — refusing to start gateway" >&2
    exit 1
fi

if [ -z "${WEB_KIT_NOVNC_HASH:-}" ]; then
    echo "start-caddy: WEB_KIT_NOVNC_HASH is empty — refusing to start gateway" >&2
    exit 1
fi

if [ "${CADDY_DRYRUN:-}" = "1" ]; then
    echo "DRYRUN-would-exec-caddy"
    exit 0
fi

exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
