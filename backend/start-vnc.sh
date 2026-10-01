#!/bin/bash
# x11vnc is only reachable through noVNC behind Caddy basic auth; an extra VNC
# password (WEBKIT_VNC_PASSWORD) is optional defence in depth.
set -e
if [ -n "${WEBKIT_VNC_PASSWORD:-}" ]; then
    PWFILE="$(mktemp)"
    /usr/bin/x11vnc -storepasswd "$WEBKIT_VNC_PASSWORD" "$PWFILE" >/dev/null 2>&1
    exec /usr/bin/x11vnc -display :99 -forever -rfbauth "$PWFILE" -rfbport 5900 -localhost -shared -quiet
fi
exec /usr/bin/x11vnc -display :99 -forever -nopw -rfbport 5900 -localhost -shared -quiet
