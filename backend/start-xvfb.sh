#!/bin/bash
# Stale X server lock from a prior crash will make Xvfb exit 1 immediately.
# Container restarts (restart: unless-stopped) reuse the same /tmp, so the
# lock survives across runs even though no Xvfb process exists.
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
exec /usr/bin/Xvfb :99 -screen 0 1920x1080x24
