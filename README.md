# web-kit

[中文](./README.zh-CN.md)

A self-hosted web access service for AI agents: **search, read, crawl, download**,
all performed by a real, logged-in Chrome on your server. Agents use a small CLI
(`webkit`); the server does all the browsing.

```
 agent ──► webkit CLI ──HTTP + key──►  backend container (one API port)
                                        ├─ Caddy (auth)
                                        ├─ webkit-api ── Chrome (persistent profile, patchright)
                                        │                 └─ crawl4ai for pages, CDP for downloads
                                        ├─ SearXNG (API engines: github, pypi, openalex, ...)
                                        └─ noVNC (admin: log in / solve a CAPTCHA once)
```

Why not just a metasearch box or a crawler service: web-kit keeps *your* browser
identity (cookies, logins) on the server, scrapes the engines that matter
(Google, DuckDuckGo, Google Scholar, Semantic Scholar, arXiv) with that browser,
and reports every engine's outcome so an empty answer is never ambiguous.

## Positioning

**WebSearch finds, web-kit reads.** Agents keep their built-in search for general
discovery and use web-kit when the exact content matters:

| Need | Tool |
|---|---|
| Read a page or PDF verbatim (quotes, numbers, tables, full papers), JS- or login-gated pages | `webkit read` (not WebFetch, which returns a model summary) |
| Files behind a login | `webkit download` |
| Papers, CS/ML | `webkit search -p academic` (Semantic Scholar, Google Scholar, OpenAlex, arXiv) |
| General web search | the agent's built-in search; `webkit search` once that is unavailable or its budget is spent |
| Clicking, typing, forms | not web-kit (browser automation) |

Deployment constraints to keep in mind: one shared Chrome serves every client
(`WEBKIT_MAX_PAGES`, default 5; beyond that requests get exit 7 *busy*), and all
traffic leaves through the backend host's egress, so heavy parallel search from
many agents raises CAPTCHA risk for everyone. That is why bulk discovery stays on
the built-in search.

## Client

```bash
uv tool install git+https://github.com/huangyrcn/web-kit     # update: uv tool upgrade web-kit
webkit config set url http://your-server:8082
webkit config set api-key            # reads the key from stdin
webkit doctor
webkit skill install                 # ~/.claude/skills/web-kit/SKILL.md for this CLI version
```

```bash
webkit search "query"                          # general: google > duckduckgo > bing
webkit search -p academic --time year "query"  # semantic_scholar > google_scholar > openalex > arxiv
webkit search --read 3 -o out/ "query"         # search, then save the top 3 pages
webkit read https://arxiv.org/pdf/1706.03762   # PDFs come back as text
webkit crawl https://docs.example.com -o docs/ --max-pages 10
webkit download https://example.com/paper.pdf  # -> ~/.cache/web-kit/downloads/
webkit status                                  # browser, egress, per-engine health
```

The first line of `search` output reports each engine:
`# engines google=error:network duckduckgo=ok:8 bing=skipped`.
Exit codes: 0 ok · 1 usage · 2 no results · 3 failed · 4 auth · 5 unreachable ·
6 human action needed (CAPTCHA/login) · 7 busy. Errors are one JSON line on stderr.

## Backend

```bash
cd backend
cp .env.example .env && chmod 600 .env      # keys, ports, bind address
cp searxng-settings/settings.yml.example searxng-settings/settings.yml
docker compose up -d --build
```

- `8082` API (`X-API-Key`; `/v2/admin/*` needs `X-Admin-Key`)
- `6080` noVNC, admin only (user `webkit`, password = admin key): log in to sites once; cookies persist in the `chrome-profile` volume
- `9223` raw CDP, admin only

`webkit status` shows the egress probe (a TLS handshake per upstream host, no
queries spent), browser health and recent per-engine outcomes. Self-healing:
supervisord restarts processes; a watchdog restarts Chrome + API when the CDP
link is dead; the container healthcheck covers Chrome, API and SearXNG.

Limits: Cloudflare Turnstile, DataDome/PerimeterX and sites that require a
residential IP are out of reach of a single datacenter/home egress.

## API

| Route | Purpose |
|---|---|
| `GET /v2/search?q=&profile=\|engine=&limit=&time=&lang=` | search with fallback chain |
| `GET /v2/engines` | engines, kinds, time-filter support, profiles |
| `POST /v2/page {url, format: fit\|md\|html\|links\|json, ...}` | read a page / PDF |
| `POST /v2/crawl {url, strategy, max_depth, max_pages, include, exclude}` | bounded crawl |
| `GET /v2/download?url=` | stream a file with the browser's cookies |
| `GET /v2/status`, `GET /v2/version` | health and versions |
| `GET /v2/admin/open?url=` | open a URL for a human (noVNC) |

License: MIT.
