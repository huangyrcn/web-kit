# syntax=docker/dockerfile:1.7
#
# web-kit server: one container, the same code as local mode.
#   Chrome (OpenCLI extension, force-installed by policy) + the opencli daemon
#   + webkit-serve (the /v2 API over src/webkit/local.py) + noVNC for a person.
# Build context: the repository root (backend/docker-compose.yml sets it).
FROM debian:bookworm-slim

# Pinned upstreams; bump deliberately. A new Chrome voids the Cloudflare passes
# (cf_clearance) the profile holds, and Google keeps only recent builds in its pool,
# so a stale pin fails the build loudly instead of drifting.
ARG CHROME_VERSION=158.0.8127.36-1
ARG NODE_VERSION=24.21.0
ARG NODE_SHA256=6e1db87ef58b8819e5d5402eff1536491b18edd8eb7bee5ef7897876e88dc5ff
ARG OPENCLI_VERSION=2.0.3

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/node/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# dpkg fsyncs every file it unpacks; on HDD-backed ZFS that turns a short install into
# minutes. An image layer needs no crash safety.
RUN echo force-unsafe-io > /etc/dpkg/dpkg.cfg.d/docker-unsafe-io \
    && apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl tini supervisor python3 \
        xvfb x11-utils fluxbox x11vnc novnc websockify \
        fonts-noto-cjk fonts-noto-color-emoji \
    && curl -fsSLo /tmp/chrome.deb \
       "https://dl.google.com/linux/chrome/deb/pool/main/g/google-chrome-stable/google-chrome-stable_${CHROME_VERSION}_amd64.deb" \
    && apt-get install -y --no-install-recommends /tmp/chrome.deb \
    && rm -f /tmp/chrome.deb /etc/apt/sources.list.d/google-chrome.list \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSLo /tmp/node.tgz "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.gz" \
    && echo "${NODE_SHA256}  /tmp/node.tgz" | sha256sum -c - \
    && mkdir -p /opt/node && tar -xzf /tmp/node.tgz -C /opt/node --strip-components=1 && rm /tmp/node.tgz \
    && npm install -g --no-fund --no-audit --loglevel=error "@jackwener/opencli@${OPENCLI_VERSION}"

# The OpenCLI extension: installed and kept updated by Chrome from the Web Store.
# Modes are set here, not taken from the build host (a checkout may be 0700); the policy
# directory first, since COPY --chmod would give the directories it creates the file's mode.
RUN install -d -m 755 /etc/opt/chrome/policies/managed
COPY --chmod=644 backend/chrome-policy.json /etc/opt/chrome/policies/managed/webkit.json
COPY --chmod=644 backend/supervisord.conf /etc/supervisor/webkit.conf
COPY --chmod=755 backend/start-xvfb.sh backend/start-chrome.sh backend/start-vnc.sh backend/start-novnc.sh /usr/local/bin/

# Everything runs as one unprivileged user.
RUN useradd -m -u 1000 -s /bin/bash webkit \
    && mkdir -p /data/chrome-profile /opt/webkit && chown webkit:webkit /data/chrome-profile /opt/webkit \
    && google-chrome-stable --version > /etc/webkit-chrome-version
USER webkit
WORKDIR /home/webkit

# web-kit's own opencli adapters (biorxiv, medrxiv), then the server code.
COPY --chown=webkit:webkit opencli-plugin /opt/webkit/opencli-plugin
RUN opencli plugin install file:///opt/webkit/opencli-plugin
COPY --chown=webkit:webkit src/webkit /opt/webkit/src/webkit

# Last on purpose: a different tag must not invalidate the cached layers above.
ARG IMAGE_TAG=dev
ENV WEBKIT_IMAGE_TAG=${IMAGE_TAG} \
    PYTHONPATH=/opt/webkit/src \
    DISPLAY=:99

EXPOSE 8082 6080
VOLUME /data/chrome-profile
HEALTHCHECK --interval=30s --timeout=15s --start-period=90s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8082/healthz', timeout=12)"
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/webkit.conf"]
