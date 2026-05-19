# web-kit-backend

A reliable, single-container search + browser backend for the [web-kit](./client/) skill suite.
Bundles SearxNG, a Chrome+Playwright search proxy, and a Chrome DevTools Protocol (CDP) endpoint
behind one `docker compose up`.

## Why

Self-hosted SearxNG instances often need a headless browser to bypass JavaScript-heavy
search engines (Google, DuckDuckGo). The typical setup is 4+ separate containers
(searxng, redis, browser-proxy, novnc, etc.) glued together with networking — fragile,
hard to diagnose, and prone to one container's failure cascading into the rest.

This project collapses the stack into a **single container** with `supervisord` managing
all processes, and adds **four layers of self-healing** so a Chrome crash doesn't
take down search.

## Features

- **One `docker compose up`** to bring up the full stack
- **Three exposed endpoints** for clients:
  - `:8082` — SearxNG (drop-in for `ask-search`, `searxng-mcp`, etc.)
  - `:9223` — Chrome DevTools Protocol (drop-in for any Playwright/Puppeteer client)
  - `:6080` — noVNC for one-time manual login (Google account, captcha solve, etc.)
- **Persistent Chrome profile** via Docker volume — log in once, cookies survive restarts
- **Self-healing**:
  - Process-level: supervisord restarts any dead program (`autorestart=true`)
  - Application-level: `_ensure_browser()` reconnects CDP on each request if the browser died
  - Container-level: `HEALTHCHECK` probes all three ports, marks the container unhealthy on failure
  - Business-level: `watchdog.sh` runs a real search query every 60s; 3 consecutive failures triggers `supervisorctl restart chrome search-proxy` (catches "process alive but browser hung")

## Architecture

```
                    web-kit-backend container (supervisord PID 1, 9 programs)
   ┌─────────────────────────────────────────────────────────────────────┐
   │                                                                     │
   │  Xvfb :99 ── fluxbox ── x11vnc :5900 ── websockify  ─► host:6080    │
   │                                                                     │
   │  google-chrome --remote-debugging-port=9222                         │
   │       │                                                             │
   │       ├─ socat ─► host:9223  (CDP, used by Playwright/Puppeteer)    │
   │       │                                                             │
   │       └─ search-proxy (FastAPI, localhost:3100)                     │
   │              │                                                      │
   │              └─ /google, /ddg endpoints — Chrome-rendered HTML      │
   │                       ▲                                             │
   │  SearxNG (granian, :8080) ── http://localhost:3100/{google,ddg}     │
   │       │                                                             │
   │       └─► host:8082                                                 │
   │                                                                     │
   │  watchdog.sh (60s probe) ─ supervisorctl restart chrome on failure  │
   │                                                                     │
   └─────────────────────────────────────────────────────────────────────┘

   Chrome profile is persisted in a Docker volume.
   Settings live in ./searxng-settings/settings.yml (bind-mounted).
```

## Quick start

```bash
git clone https://github.com/<your-org>/web-kit-backend.git
cd web-kit-backend

# 1. Create your settings file from the template, then edit it
cp searxng-settings/settings.yml.example searxng-settings/settings.yml
$EDITOR searxng-settings/settings.yml      # set secret_key + any API keys you need

# 2. Build and start (first build takes ~5-10 min for Chrome + SearxNG)
docker compose up -d

# 3. Wait ~90s for the stack to warm up
docker compose ps                            # should show "healthy"

# 4. Test
curl 'http://localhost:8082/search?q=test&format=json' | jq '.results | length'
curl http://localhost:9223/json/version | jq .Browser
open http://localhost:6080/vnc.html         # noVNC, for manual cookie login
```

For first-time Google login (avoids captcha later):
1. Open `http://localhost:6080/vnc.html` in a browser
2. Click "Connect" — you'll see a fluxbox desktop with a Chrome window
3. Sign in to your Google account; cookies are saved to the persistent profile

## Configuration

| Where | What | Default |
|---|---|---|
| `searxng-settings/settings.yml` | SearxNG engines, secret key, API keys | required, see `.example` |
| `docker-compose.yml` ports | Host port mappings | 8082, 9223, 6080 |
| `docker-compose.yml` `mem_limit` | Container memory cap | 4 GB |
| `docker-compose.yml` `shm_size` | `/dev/shm` for Chrome | 1 GB |
| `server.py` `MAX_CONCURRENCY` | Parallel Chrome pages | 3 (also via `SEARCH_PROXY_CONCURRENCY` env) |
| `server.py` `FAILURE_CACHE_SECONDS` | Skip-engine quarantine | 60s |

Environment variables passed to the container (set in compose under `environment:`):
- `SEARCH_PROXY_CONCURRENCY` — override page-pool size
- `FAILURE_CACHE_SECONDS` — override quarantine duration
- `PROBE_INTERVAL` — watchdog probe interval (default 60s)
- `PROBE_QUERY` — watchdog probe query string (default `test`)

## Reliability — what we tested

| Failure mode | Recovery time | How |
|---|---|---|
| Chrome process killed | ~12s (CDP back) / ~43s (search results back) | supervisord restart + `_ensure_browser()` reconnect |
| FastAPI search-proxy hung | < 60s | watchdog detects + `supervisorctl restart` |
| Network blip to Google | < 60s per quarantine | failure cache + SearxNG `suspended_times` |
| Container OOM | depends on host | docker `restart: unless-stopped` |

Verified by:
```bash
# Inside the container:
docker exec web-kit-backend pkill -9 -f google-chrome-stable

# From the host, time how long until search returns google+ddg again:
time curl -s 'http://localhost:8082/search?q=test&format=json' \
  | jq -r '.unresponsive_engines | length'
```

## Performance

Benchmarks on a single x86_64 host (loopback):

| Scenario | P50 | P95 | Notes |
|---|---|---|---|
| Sequential SearxNG search | ~3.6s | ~4.5s | bound by Google SERP render time |
| Concurrent ×3 SearxNG | ~9s | ~12s | matches `Semaphore(3)` design |
| LAN client (cross-host) | ~5.7s | ~5.9s | adds JSON payload + RTT |

Run your own bench:
```bash
python3 bench/speed-test.py -n 8 --target http://localhost:8082
```

## Client tooling

The [`client/`](./client/) directory contains the matching client-side skill bundle:
- `SKILL.md` — entry doc for AI agents (Claude Code skill, Copilot CLI, etc.)
- `scripts/ask-search.py` — search wrapper around SearxNG's JSON API
- `scripts/crwlr` — Chrome-rendered page-to-markdown wrapper (uses `crwl` + CDP)
- `scripts/cdp-download` — file download via CDP (handles auth-protected URLs)

To install as a Claude Code skill:
```bash
mkdir -p ~/.claude/skills/web-kit
cp -r client/* ~/.claude/skills/web-kit/
chmod +x ~/.claude/skills/web-kit/scripts/*

# Set required env vars (in your shell rc):
export SEARXNG_URL=http://localhost:8082
export CDP_URL=http://localhost:9223
```

## Repository layout

```
.
├── Dockerfile                  # Multi-step: Playwright base + Chrome + SearxNG
├── docker-compose.yml
├── supervisord.conf            # 9 programs, all autorestart
├── server.py                   # FastAPI search-proxy with _ensure_browser()
├── start-chrome.sh             # Chrome launch wrapper (clears SingletonLock)
├── start-searxng.sh            # granian launcher
├── start-xvfb.sh               # legacy stub (unused, supervisord runs Xvfb directly)
├── healthcheck.sh              # docker HEALTHCHECK script — probes all 3 ports
├── watchdog.sh                 # business-level watchdog
├── searxng-settings/
│   └── settings.yml.example    # template (real settings.yml is gitignored)
├── bench/
│   └── speed-test.py           # stdlib-only benchmark
└── client/                     # web-kit client-side skill bundle
    ├── SKILL.md
    ├── scripts/
    │   ├── ask-search.py
    │   ├── crwlr
    │   └── cdp-download
    └── references/
        ├── engines.md
        └── workflow.md
```

## License

[MIT](./LICENSE).
