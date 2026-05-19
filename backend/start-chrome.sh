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

exec google-chrome-stable \
    --no-sandbox \
    --no-first-run \
    --no-default-browser-check \
    --remote-debugging-port=9222 \
    --remote-allow-origins=* \
    --user-data-dir=/data/chrome-profile \
    --disable-gpu \
    --disable-dev-shm-usage \
    --disable-background-networking \
    --disable-client-side-phishing-detection \
    --disable-default-apps \
    --disable-hang-monitor \
    --disable-popup-blocking \
    --disable-sync \
    --disable-translate \
    --disable-features=IsolateOrigins,site-per-process \
    --disable-blink-features=AutomationControlled \
    --metrics-recording-only \
    --window-size=1920,1080 \
    --disable-extensions-except= \
    --disable-plugins \
    "about:blank"
