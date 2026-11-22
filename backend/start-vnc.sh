#!/bin/bash
# The display for a person, on the container's loopback only: noVNC (start-novnc.sh)
# is the way in, behind the admin key.
set -e
for i in $(seq 1 30); do
    xdpyinfo -display :99 >/dev/null 2>&1 && break
    sleep 1
done
exec /usr/bin/x11vnc -display :99 -forever -shared -nopw -localhost -rfbport 5900 -quiet
