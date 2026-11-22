#!/bin/bash
# Chrome on the virtual display, its profile in the volume. No remote debugging port:
# opencli drives it through the OpenCLI extension (force-installed by policy).
set -e
for i in $(seq 1 30); do
    xdpyinfo -display :99 >/dev/null 2>&1 && break
    sleep 1
done
# A crash or a container restart leaves these behind, and Chrome then refuses the profile.
rm -f /data/chrome-profile/SingletonLock /data/chrome-profile/SingletonCookie /data/chrome-profile/SingletonSocket
exec google-chrome-stable \
    --no-sandbox \
    --no-first-run \
    --no-default-browser-check \
    --user-data-dir=/data/chrome-profile \
    --disable-dev-shm-usage \
    --window-size=1920,1080 \
    --force-webrtc-ip-handling-policy=disable_non_proxied_udp \
    "about:blank"
