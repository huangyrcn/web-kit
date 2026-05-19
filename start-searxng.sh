#!/bin/bash
set -e

# Fallback to default settings if user didn't bind-mount any
if [ ! -f /etc/searxng/settings.yml ]; then
    mkdir -p /etc/searxng
    cp /etc/searxng-default/settings.yml /etc/searxng/settings.yml
fi

# Wait briefly for search-proxy (engines reference http://localhost:3100)
for i in $(seq 1 15); do
    if curl -fs --max-time 1 http://localhost:3100/health >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

cd /usr/local/searxng

# SearxNG reads settings via SEARXNG_SETTINGS_PATH (set in Dockerfile)
exec granian \
    --interface wsgi \
    --host 0.0.0.0 \
    --port 8080 \
    --no-ws \
    searx.webapp:application

