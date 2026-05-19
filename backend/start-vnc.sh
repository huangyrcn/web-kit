#!/bin/bash
# Launch x11vnc with optional password (WEB_KIT_VNC_PASSWORD env var).
#
# When WEB_KIT_VNC_PASSWORD is non-empty, x11vnc requires that password.
# When it's empty/unset, x11vnc runs with -nopw — only safe when the
# noVNC port is bound to 127.0.0.1 (the docker-compose default).
set -e

if [ -n "${WEB_KIT_VNC_PASSWORD:-}" ]; then
    PWFILE="$(mktemp)"
    /usr/bin/x11vnc -storepasswd "$WEB_KIT_VNC_PASSWORD" "$PWFILE" >/dev/null 2>&1
    exec /usr/bin/x11vnc -display :99 -forever -rfbauth "$PWFILE" \
                         -rfbport 5900 -shared -quiet
else
    exec /usr/bin/x11vnc -display :99 -forever -nopw \
                         -rfbport 5900 -shared -quiet
fi
