#!/bin/bash
# SearXNG serves only the API-backed engines; webkit-api calls it on localhost.
set -e
if [ ! -f /etc/searxng/settings.yml ]; then
    mkdir -p /etc/searxng
    cp /etc/searxng-default/settings.yml /etc/searxng/settings.yml
fi
cd /usr/local/searxng
exec /opt/searxng/bin/granian --interface wsgi --host 127.0.0.1 --port 8080 --no-ws searx.webapp:application
