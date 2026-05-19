#!/bin/bash
set -e

# Wait for Xvfb to be ready
for i in $(seq 1 30); do
    if xdpyinfo -display :99 >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

# Clean stale singleton locks (fresh container/profile recreate)
rm -f /data/chrome-profile/SingletonLock \
      /data/chrome-profile/SingletonCookie \
      /data/chrome-profile/SingletonSocket

export DISPLAY=:99

# Minimal flag set:
#   - --no-sandbox: required inside the container (no userns by default)
#   - --user-data-dir: persistent profile (cookies, login state)
#   - --remote-debugging-port + --remote-allow-origins: CDP for search-proxy / web-kit
#   - --disable-blink-features=AutomationControlled: hides navigator.webdriver
#   - --window-size: deterministic viewport for parsers
#
# Per patchright research, the following commonly-added flags are themselves
# detection signals (rare in real users) and are deliberately NOT set:
#   --disable-default-apps, --disable-popup-blocking, --disable-extensions-except=,
#   --disable-plugins, --disable-sync, --disable-translate, --metrics-recording-only,
#   --disable-background-networking, --disable-client-side-phishing-detection,
#   --disable-hang-monitor, --enable-automation, --disable-gpu
#
# We let GPU run via Xvfb's software stack (Mesa) — its WebGL fingerprint blends
# in with real users far better than SwiftShader (which --disable-gpu forces).
exec google-chrome-stable \
    --no-sandbox \
    --no-first-run \
    --no-default-browser-check \
    --remote-debugging-port=9222 \
    --remote-allow-origins=* \
    --user-data-dir=/data/chrome-profile \
    --disable-dev-shm-usage \
    --disable-blink-features=AutomationControlled \
    --window-size=1920,1080 \
    --force-webrtc-ip-handling-policy=disable_non_proxied_udp \
    "about:blank"

