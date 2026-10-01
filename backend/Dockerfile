# syntax=docker/dockerfile:1.7
#
# web-kit v2 backend: one container, one API port.
# Chrome (logged-in profile) + webkit-api (FastAPI) + SearXNG (API engines only)
# + noVNC for manual logins, behind a Caddy auth gateway.

FROM caddy:2.10.2 AS caddy

FROM mcr.microsoft.com/playwright/python:v1.52.0-noble

# Pinned upstreams. Bump deliberately; the values are reported by /v2/version.
ARG SEARXNG_REF=a9d99003344ac3879ec45c56c661283482a92a39
# Chrome is pinned too: Cloudflare binds a pass (cf_clearance) to the browser, and an
# unplanned upgrade (149 -> 154) voided every pass the profile held. Google keeps only
# recent builds in its pool, so a stale pin fails the build loudly instead of drifting.
ARG CHROME_VERSION=154.0.8037.92-1

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    SEARXNG_SETTINGS_PATH=/etc/searxng/settings.yml \
    WEBKIT_SEARXNG_REF=${SEARXNG_REF}

# dpkg fsyncs every file it unpacks; on HDD-backed ZFS that turns a 3 s
# download into a 9 min install. An image layer needs no crash safety.
RUN echo force-unsafe-io > /etc/dpkg/dpkg.cfg.d/docker-unsafe-io \
    && apt-get update && apt-get install -y --no-install-recommends \
        xvfb x11-utils x11vnc fluxbox novnc websockify \
        curl wget gnupg ca-certificates git supervisor tini python3-venv \
        pulseaudio \
    && wget -q -O /tmp/google-chrome.deb \
       "https://dl.google.com/linux/chrome/deb/pool/main/g/google-chrome-stable/google-chrome-stable_${CHROME_VERSION}_amd64.deb" \
    && apt-get install -y --no-install-recommends /tmp/google-chrome.deb \
    && rm -f /tmp/google-chrome.deb /etc/apt/sources.list.d/google-chrome.list \
    && rm -rf /var/lib/apt/lists/*

# Caddy from its official image (its apt repo key has expired before).
COPY --from=caddy /usr/bin/caddy /usr/bin/caddy

# SearXNG at a pinned commit, in its own venv (its pins must not fight crawl4ai's).
RUN python3 -m venv /opt/searxng \
    && git init -q /tmp/searxng && cd /tmp/searxng \
    && git remote add origin https://github.com/searxng/searxng.git \
    && git fetch -q --depth 1 origin "${SEARXNG_REF}" && git checkout -q FETCH_HEAD \
    && /opt/searxng/bin/pip install --no-cache-dir -r requirements.txt granian \
    && mkdir -p /usr/local/searxng /etc/searxng-default \
    && cp -r searx utils /usr/local/searxng/ \
    && cp searx/settings.yml /etc/searxng-default/settings.yml \
    && /opt/searxng/bin/python -m compileall -q /usr/local/searxng/searx \
    && cd / && rm -rf /tmp/searxng

# webkit-api in its own venv. Chrome is driven over CDP, so no Playwright
# browser downloads are needed.
COPY app/requirements.txt /tmp/webkit-requirements.txt
RUN python3 -m venv /opt/webkit \
    && /opt/webkit/bin/pip install --no-cache-dir -r /tmp/webkit-requirements.txt \
    && rm /tmp/webkit-requirements.txt
COPY app/webkit_api /opt/webkit-app/webkit_api

COPY Caddyfile /etc/caddy/Caddyfile
COPY supervisord.conf /etc/supervisor/supervisord.conf
COPY start-xvfb.sh start-pulse.sh start-chrome.sh start-searxng.sh start-vnc.sh start-caddy.sh watchdog.sh healthcheck.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/*.sh \
    && google-chrome-stable --version > /etc/webkit-chrome-version

# Last on purpose: a different tag must not invalidate the cached layers above.
ARG IMAGE_TAG=dev
ENV WEBKIT_IMAGE_TAG=${IMAGE_TAG}

EXPOSE 8082 6080 9223
VOLUME /data/chrome-profile

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD /usr/local/bin/healthcheck.sh

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf"]
