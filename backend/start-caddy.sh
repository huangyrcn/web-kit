#!/bin/bash
# Fail closed: never bring the gateway up without all three secrets.
set -e
for v in WEBKIT_API_KEY WEBKIT_ADMIN_KEY WEBKIT_NOVNC_HASH; do
    if [ -z "${!v:-}" ]; then
        echo "start-caddy: $v is empty — refusing to start gateway" >&2
        exit 1
    fi
done
if [ "$WEBKIT_API_KEY" = "$WEBKIT_ADMIN_KEY" ]; then
    echo "start-caddy: WEBKIT_API_KEY and WEBKIT_ADMIN_KEY must differ" >&2
    exit 1
fi
exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
