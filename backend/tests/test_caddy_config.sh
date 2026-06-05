#!/bin/bash
# backend/tests/test_caddy_config.sh — validates Caddyfile syntax via `caddy validate`.
set -e
command -v docker >/dev/null 2>&1 || { echo "SKIP: docker not available"; exit 0; }
docker version >/dev/null 2>&1 || { echo "SKIP: docker daemon not reachable"; exit 0; }
HERE="$(cd "$(dirname "$0")/.." && pwd)"
export WEB_KIT_API_KEY="testkey123"
export WEB_KIT_NOVNC_HASH='$2a$14$abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ012345'  # dummy bcrypt for validate
docker run --rm -e WEB_KIT_API_KEY -e WEB_KIT_NOVNC_HASH \
  -v "$HERE/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2.8 \
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
echo "PASS: Caddyfile valid"
