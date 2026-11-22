#!/bin/bash
# noVNC on :6080, HTTP basic auth (user webkit, password = the admin key).
set -e
if [ -z "${WEBKIT_ADMIN_KEY:-}" ]; then
    echo "start-novnc: WEBKIT_ADMIN_KEY is not set" >&2
    exit 1
fi
exec /usr/bin/websockify --web /usr/share/novnc \
    --auth-plugin BasicHTTPAuth --auth-source "webkit:${WEBKIT_ADMIN_KEY}" \
    6080 127.0.0.1:5900
