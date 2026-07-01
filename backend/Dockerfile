# syntax=docker/dockerfile:1.7

FROM mcr.microsoft.com/playwright/python:v1.52.0-noble

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    SEARXNG_SETTINGS_PATH=/etc/searxng/settings.yml

# OS deps: virtual display + VNC + supervisor + Chrome stable + build tools for SearxNG
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb x11vnc fluxbox novnc websockify \
        socat curl wget gnupg ca-certificates git \
        supervisor tini \
        build-essential libxslt-dev zlib1g-dev libffi-dev libssl-dev \
        python3-dev \
    && wget -q -O - https://dl.google.com/linux/linux_signing_key.pub \
       | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
       > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-chrome-stable \
    && curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
       | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/caddy-stable-archive-keyring.gpg] https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-version main" \
       > /etc/apt/sources.list.d/caddy-stable.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends caddy \
    && rm -rf /var/lib/apt/lists/*

# search-proxy deps — patchright is a drop-in for Playwright with anti-detection
# patches at the CDP protocol level (Runtime.enable / Console.enable leak fixes,
# safer default flags). See https://github.com/Kaliiiiiiiiii-Vinyzu/patchright
RUN pip install --no-cache-dir --break-system-packages --ignore-installed \
        fastapi==0.115.12 uvicorn==0.34.2 patchright

# SearxNG: clone source and install into system Python (no venv needed since
# this image is dedicated to one Python app stack). Use --break-system-packages
# because the playwright base is Debian-style PEP 668 protected.
ARG SEARXNG_REF=master
RUN git clone --depth 1 --branch ${SEARXNG_REF} \
        https://github.com/searxng/searxng.git /usr/local/searxng-src \
    && pip install --no-cache-dir --break-system-packages --ignore-installed \
         -r /usr/local/searxng-src/requirements.txt \
    && pip install --no-cache-dir --break-system-packages granian \
    && mkdir -p /usr/local/searxng \
    && cp -r /usr/local/searxng-src/searx /usr/local/searxng/searx \
    && cp -r /usr/local/searxng-src/utils /usr/local/searxng/utils \
    && python3 -m compileall -q /usr/local/searxng/searx \
    && rm -rf /usr/local/searxng-src

# Default settings (used as fallback when /etc/searxng is empty)
RUN mkdir -p /etc/searxng-default \
    && cp /usr/local/searxng/searx/settings.yml /etc/searxng-default/settings.yml

# App code
WORKDIR /app
COPY server.py /app/server.py
COPY searxng-engines/semantic_scholar_api.py /usr/local/searxng/searx/engines/semantic_scholar_api.py
COPY Caddyfile /etc/caddy/Caddyfile
COPY start-xvfb.sh start-chrome.sh start-searxng.sh start-vnc.sh start-caddy.sh watchdog.sh healthcheck.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/start-xvfb.sh \
             /usr/local/bin/start-chrome.sh \
             /usr/local/bin/start-searxng.sh \
             /usr/local/bin/start-vnc.sh \
             /usr/local/bin/start-caddy.sh \
             /usr/local/bin/watchdog.sh \
             /usr/local/bin/healthcheck.sh

COPY supervisord.conf /etc/supervisor/supervisord.conf

EXPOSE 8082 9223 6080
VOLUME /data/chrome-profile

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD /usr/local/bin/healthcheck.sh

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf"]
