# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

web-kit is a Claude Code skill plugin that gives AI agents two lightweight web primitives:
search (searxng-search) and browser fetch (browser-fetch, with `page` → markdown and
`file` → raw download subcommands). A self-hosted single-container backend (Docker)
powers both.

## Architecture

The repo has two independent halves connected only by environment variables:

```
skills/
├── searxng-search/      (SearxNG metasearch CLI)         ─┐
└── browser-fetch/   (Chrome fetcher: page + file)    ─┘
       ↓ SEARXNG_URL, CDP_URL
backend/          (Docker: SearxNG + Chrome + search-proxy)
```

- **skill scripts** run on the agent's host machine, call the backend over HTTP
- **backend** runs in Docker: supervisord manages 9 processes (Xvfb, fluxbox, x11vnc, novnc, Chrome, socat CDP forward, search-proxy FastAPI, SearxNG, watchdog)

The search-proxy (`backend/server.py`) renders Google/DuckDuckGo SERPs via Chrome CDP using `patchright` (anti-detection Playwright fork). SearxNG proxies to this search-proxy for Google/DDG engines while using direct APIs for everything else.

## Script Details

| Skill | Script | Invocation | Key dependency |
|-------|--------|------------|----------------|
| `searxng-search` | `scripts/searxng-search` | `uv run --script` | `urllib.request` (stdlib) |
| `browser-fetch` (page) | `scripts/page` | `uv run --script` | `crawl4ai` (auto-installed by uv) |
| `browser-fetch` (file) | `scripts/file` | `uv run --script` | `websocket-client` (auto-installed by uv) |

All scripts use PEP 723 inline metadata with `#!/usr/bin/env -S uv run --script` shebangs.
On Unix they can be executed directly; on Windows use `uv run --script <path>`.

The only external dependency is `uv` (for running scripts with auto-installed packages) and the backend service (SEARXNG_URL / CDP_URL).

## Building & Running

### Backend

```bash
cd backend
cp searxng-settings/settings.yml.example searxng-settings/settings.yml
docker compose up -d          # first build ~5-10 min
sleep 90                      # warm-up
```

Smoke tests:
```bash
curl 'http://localhost:8082/search?q=test&format=json' | jq '.results | length'
curl http://localhost:9223/json/version | jq .Browser
```

### Skills (no build step)

All scripts are run via `uv run --script <path>` — uv auto-installs inline dependencies on first use.
No curl, no pip install, no manual setup beyond having `uv` in PATH.

```bash
# Cross-platform invocation (works on Windows/macOS/Linux):
uv run --script skills/searxng-search/scripts/searxng-search "query"
uv run --script skills/browser-fetch/scripts/page -O page.md "https://example.com"
uv run --script skills/browser-fetch/scripts/file https://example.com/file.pdf
```

Both skills are independent — install only the ones you need. All share the same backend env vars.

## Key Design Decisions

- **Single-container backend**: supervisord + 4-layer self-healing (process, application `_ensure_browser()`, container HEALTHCHECK, business-level watchdog.sh)
- **patchright over Playwright**: patches CDP-level leaks (`Runtime.enable`, `Console.enable`) for anti-detection
- **Failure cache**: search-proxy quarantines engines for 15-60s after failures to avoid cascading
- **Chrome profile persistence**: Docker volume `chrome-profile` persists cookies/login state across restarts
- **Security**: ports bound to 127.0.0.1 by default; CDP and noVNC give full browser session access

## Configuration

Required env vars (set on the agent host or in `.claude-plugin/plugin.json` userConfig):
- `SEARXNG_URL` — e.g. `http://localhost:8082`
- `CDP_URL` — e.g. `http://localhost:9223`
- `WEB_KIT_API_KEY` — gateway auth key, sent as the `X-API-Key` header (also the noVNC Basic Auth password). Generate with `openssl rand -hex 32`

Backend tuning via env vars or `docker-compose.yml`:
- `SEARCH_PROXY_CONCURRENCY` (default 3), `FAILURE_CACHE_SECONDS` (default 15s), `PROBE_INTERVAL` (default 300s)
- `WEB_KIT_BIND` (default 127.0.0.1), `WEB_KIT_VNC_PASSWORD` (required when binding to LAN)
