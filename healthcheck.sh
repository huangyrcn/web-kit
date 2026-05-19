#!/bin/bash
# Container HEALTHCHECK — all three layers must respond.

curl -fs --max-time 5 http://localhost:9222/json/version >/dev/null || exit 1
curl -fs --max-time 5 http://localhost:3100/health       >/dev/null || exit 2
curl -fs --max-time 5 http://localhost:8080/healthz      >/dev/null || exit 3
exit 0
