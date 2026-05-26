# web-kit

[简体中文](./README.zh-CN.md)

A skill bundle for AI coding agents (Claude Code, Copilot CLI, etc.) that gives them
**three lightweight web primitives**: search, page reading, and authenticated download.
Plus a self-hosted **single-container backend** that powers them all.

```
┌──────────────────────────┐         ┌──────────────────────────────────┐
│   skill/  (the AI skill) │  ◄───►  │   backend/  (the server side)    │
│   ─────────────────────  │         │   ─────────────────────────────  │
│   ask-search             │         │   SearxNG + Chrome+Playwright    │
│   crwlr                  │         │   CDP, all in one container,     │
│   cdp-download           │         │   self-healing in 4 layers       │
└──────────────────────────┘         └──────────────────────────────────┘
        │                                            ▲
        └─ uses ──── SEARXNG_URL=http://...:8082 ────┤
                     CDP_URL=http://...:9223 ────────┘
```

You can run them together (this repo's main story) or split:
- **skill only** — point at any existing SearxNG / CDP-enabled Chrome you already have.
- **backend only** — drop-in replacement for the typical 4-container searxng + browser-proxy stack.

---

## skill/ — the AI skill

Three commands an agent can call:

| Tool | What it does | When agents use it |
|---|---|---|
| `ask-search "<query>"` | SearxNG-aggregated web search, multi-engine | "search for X", "look this up", "找一下…" |
| `crwlr crawl -o md "<url>"` | Real-browser-rendered page → clean markdown | "read this page", "what does this URL say", "转成 markdown" |
| `cdp-download <url>` | File download via Chrome DevTools Protocol (uses browser cookies) | When `wget`/`curl` fails on auth-protected URLs |

Compared to a vanilla agent's built-in web tools, this skill gives:
- Real JS rendering (the page the user actually sees, not a server-side stripped version)
- Persistent login cookies (login once via noVNC, all subsequent crawls are authenticated)
- Aggregation across many search engines (Google, DDG, Bing, Brave, plus 13 academic sources)
- Lightweight CLI surface — no MCP server / no extra runtime

### Install (Claude Code example)

One runtime dependency: [`uv`](https://docs.astral.sh/uv/) — runs all Python
scripts and auto-installs their inline dependencies (crawl4ai, websocket-client,
etc.) on first use.

```bash
# 1. Clone the repo and copy the skill into your skills dir
git clone https://github.com/huangyrcn/web-kit.git
cp -r web-kit/skill ~/.claude/skills/web-kit
chmod +x ~/.claude/skills/web-kit/scripts/*

# 2. Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 3. Tell the skill where its backend lives
export SEARXNG_URL=http://your-host:8082
export CDP_URL=http://your-host:9223

# 4. Smoke test
~/.claude/skills/web-kit/scripts/ask-search "hello world" -n 3
~/.claude/skills/web-kit/scripts/crwlr crawl -o md "https://example.com"
```

For Copilot CLI, Gemini CLI, or other platforms, see `skill/SKILL.md` — the doc lists
the per-platform skill loading mechanism.

### Required environment variables

| Var | Used by | Example |
|---|---|---|
| `SEARXNG_URL` | `ask-search` | `http://localhost:8082` |
| `CDP_URL` | `crwlr`, `cdp-download` | `http://localhost:9223` |

If you already have a SearxNG instance and a Chrome with CDP exposed, you can stop here.

---

## backend/ — the single-container server

Self-hosted backend that serves both `SEARXNG_URL` (port 8082) and `CDP_URL` (port 9223),
plus a noVNC interface (port 6080) for one-time manual cookie login.

### Why bundle into one container?

Self-hosted SearxNG with a JS-rendering search-proxy typically requires 4+ separate
containers (searxng, redis, browser-proxy, novnc) glued together with networking.
That's fragile — one container's failure cascades into the rest, and there's no
single place to monitor or restart things.

This backend collapses it into **one container**, with `supervisord` managing all
processes and **four layers of self-healing**:

1. **Process-level** — supervisord `autorestart=true` restarts any crashed process.
2. **Application-level** — `_ensure_browser()` reconnects CDP on each request if
   the underlying Chrome crashed in between.
3. **Container-level** — Docker `HEALTHCHECK` probes all three exposed ports.
4. **Business-level** — `watchdog.sh` probes internal health every 300s and
   triggers `supervisorctl restart chrome search-proxy` after 3 consecutive
   failures (catches "process alive but browser hung").

### Quick start

```bash
cd backend
cp searxng-settings/settings.yml.example searxng-settings/settings.yml
$EDITOR searxng-settings/settings.yml      # set secret_key + any API keys

docker compose up -d                        # first build ~5-10 min for Chrome + SearxNG
sleep 90                                    # warm-up

curl 'http://localhost:8082/search?q=test&format=json' | jq '.results | length'
curl http://localhost:9223/json/version | jq .Browser
```

For first-time Google login (avoids captchas later):
1. Open `http://localhost:6080/vnc.html`
2. Click **Connect** — you'll see a fluxbox desktop with a Chrome window
3. Sign in to Google; cookies persist in the Docker volume

### Network exposure & access control

By default the three host ports (`8082`, `9223`, `6080`) are bound to
**`127.0.0.1` only**. The CDP and noVNC endpoints give full browser-session
takeover — including any cookies you logged in with — so we don't expose
them to the LAN unless you explicitly opt in.

To use the backend from another machine, the recommended pattern is an
SSH tunnel:

```bash
# On the client machine:
ssh -L 8082:localhost:8082 -L 9223:localhost:9223 -L 6080:localhost:6080 your-server
```

If you need to expose ports on your LAN directly, set both env vars before
`docker compose up -d`:

```bash
# In backend/.env (or your shell env):
WEB_KIT_BIND=0.0.0.0
WEB_KIT_VNC_PASSWORD=$(openssl rand -hex 12)   # required when binding to LAN
```

When `WEB_KIT_VNC_PASSWORD` is set, the noVNC client will prompt for it.
SearxNG and the CDP endpoint themselves still have no auth layer — the
binding choice is your only access control. Don't set `WEB_KIT_BIND=0.0.0.0`
on a network you don't trust.

### Architecture

```
        backend container (supervisord PID 1, 9 supervised programs)
   ┌─────────────────────────────────────────────────────────────────────┐
   │                                                                     │
   │  Xvfb :99 ── fluxbox ── x11vnc :5900 ── websockify ─► host:6080     │
   │                                                                     │
   │  google-chrome --remote-debugging-port=9222                         │
   │       │                                                             │
   │       ├─ socat ─► host:9223  (CDP, used by Playwright/Puppeteer)    │
   │       │                                                             │
   │       └─ search-proxy (FastAPI :3100, localhost only)               │
   │              ↑                                                      │
   │              └─ /google, /ddg endpoints — Chrome-rendered HTML      │
   │                                                                     │
   │  SearxNG (granian :8080) ── http://localhost:3100/{google,ddg}      │
   │       └─► host:8082                                                 │
   │                                                                     │
   │  watchdog.sh (300s probe) ── supervisorctl restart on failure       │
   │                                                                     │
   └─────────────────────────────────────────────────────────────────────┘
```

### Reliability — measured on a single x86_64 host

| Failure mode | Recovery | How |
|---|---|---|
| Chrome killed (`pkill -9`) | ~12s CDP back, ~43s search results back | supervisord + `_ensure_browser()` reconnect |
| FastAPI search-proxy hung | < 60s | watchdog detects + restarts |
| Network blip to upstream | < 60s per quarantine | failure cache + SearxNG `suspended_times` |
| Container OOM | depends on host | `restart: unless-stopped` |

### Anti-detection

The browser side uses [`patchright`](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright)
(a drop-in Playwright replacement that patches CDP-level leaks like
`Runtime.enable` / `Console.enable`) plus a minimal Chrome-flag set.
Verified pass results on canonical test sites:

| Test site | Result |
|---|---|
| `bot.sannysoft.com` | WebDriver / WebDriver Advanced / Chrome / Plugins / `debugTool: false` — all passed |
| `nowsecure.nl` (Cloudflare anti-bot) | Passes Cloudflare challenge consistently across 3+ runs |
| Google / DuckDuckGo / Bing SERP | Stable, low captcha frequency with persistent profile |

Limits — the backend will **not** reliably pass:
- Cloudflare Turnstile (interactive widget)
- DataDome / PerimeterX / Akamai Bot Manager
- Any site that requires residential IPs (datacenter IP fingerprinting)

For those, plug the `ask-search` / `crwlr` clients into a SaaS like Bright Data,
ZenRows, or ScrapFly instead.

Verify yourself:
```bash
docker exec web-kit-backend pkill -9 -f google-chrome-stable
time until curl -s 'http://localhost:8082/search?q=test&format=json' \
            | jq -e '.unresponsive_engines | length == 0' >/dev/null; do sleep 1; done
```

### Performance

Loopback benchmarks on the host:

| Scenario | P50 | P95 |
|---|---|---|
| Sequential SearxNG search | ~3.6s | ~4.5s |
| Concurrent ×3 | ~9s | ~12s |
| LAN cross-host | ~5.7s | ~5.9s |

Run your own bench:
```bash
python3 backend/bench/speed-test.py -n 8 --target http://localhost:8082
```

### Configuration knobs

| Where | What | Default |
|---|---|---|
| `searxng-settings/settings.yml` | SearxNG engines, secret key, API keys | required, see `.example` |
| `docker-compose.yml` `mem_limit` | container memory cap | 4 GB |
| `docker-compose.yml` `shm_size` | `/dev/shm` for Chrome | 1 GB |
| env `SEARCH_PROXY_CONCURRENCY` | parallel Chrome pages | 3 |
| env `FAILURE_CACHE_SECONDS` | engine quarantine duration | 60s |
| env `PROBE_INTERVAL` | watchdog probe interval | 300s |

---

## Repository layout

```
.
├── skill/                       ← the AI skill bundle
│   ├── SKILL.md
│   ├── scripts/
│   │   ├── ask-search
│   │   ├── crwlr
│   │   └── cdp-download
│   └── references/
│       ├── engines.md
│       └── workflow.md
│
├── backend/                     ← the single-container server
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── supervisord.conf
│   ├── server.py                — FastAPI search-proxy with _ensure_browser()
│   ├── start-chrome.sh / start-searxng.sh / start-xvfb.sh
│   ├── healthcheck.sh, watchdog.sh
│   ├── searxng-settings/settings.yml.example   ← real settings.yml is gitignored
│   └── bench/speed-test.py
│
├── README.md
├── LICENSE          (MIT)
└── .gitignore
```

---

## License

[MIT](./LICENSE).
