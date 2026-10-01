# web-kit

[中文](./README.zh-CN.md)

Fallback web access for AI agents: **search, read, crawl, download** through a
real, logged-in Chrome on your server, for when the agent's built-in web tools
fail, are blocked or out of quota, or cannot do the job. Agents use a small CLI
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

**Built-in tools first; web-kit when they fail or cannot do the job.** Built-in
search returns a synthesized answer and is usually the better first try. web-kit
takes over when:

- web search is out of quota or erroring;
- a page read fails (403, 429, timeout, too large) or returns a verification or
  login page instead of content (a challenge page is a failure even with HTTP 200);
- the exact text matters and the built-in reader only summarizes (Claude's WebFetch);
- the file itself is needed (a paper PDF by URL, DOI or arXiv ID).

| Command | Input | Output | Use it to |
|---|---|---|---|
| `webkit search` | a query | ranked links + snippets, no answer | find URLs (no WebSearch quota; Scholar / Semantic Scholar / arXiv) |
| `webkit read` | URL, DOI, `arXiv:ID` | the page's or PDF's text | learn what it says (`--pdf`: the PDF a paper page links) |
| `webkit download` | URL, DOI, `arXiv:ID` | the file on disk (path printed) | keep or hand over the file; a paper page resolves to its PDF |

Not for: anything the built-in tools already did fine, bulk discovery, clicking or
filling forms. One shared Chrome serves every client (`WEBKIT_MAX_PAGES`, default 5;
beyond that exit 7 *busy*) and all traffic leaves through the backend host's egress,
so heavy parallel use raises CAPTCHA risk for everyone.

## Client

```bash
uv tool install git+https://github.com/huangyrcn/web-kit     # update: uv tool upgrade web-kit
webkit config set url http://your-server:8082
webkit config set api-key            # reads the key from stdin
webkit doctor
webkit skill install                 # ~/.claude/skills/web-kit/SKILL.md for this CLI version
webkit skill install --agent codex   # ~/.codex/skills/web-kit/ (SKILL.md + reference.md)
```

```bash
webkit search "query"                          # general: google > duckduckgo > bing
webkit search -p academic --time year "query"  # semantic_scholar > google_scholar > openalex > arxiv
webkit search --read 3 -o out/ "query"         # search, then save the top 3 pages
webkit read https://arxiv.org/pdf/1706.03762   # PDFs come back as text
webkit read --pdf -o paper.md 10.1145/3774904.3792101   # DOI -> paper page -> its PDF, as text
webkit crawl https://docs.example.com -o docs/ --max-pages 10
webkit download https://example.com/paper.pdf  # -> ~/.cache/web-kit/downloads/
webkit download arXiv:2403.01092               # a paper page or DOI resolves to its PDF
webkit status                                  # browser, egress, per-engine health
```

The first line of `search` output reports each engine:
`# engines google=error:network duckduckgo=ok:8 bing=skipped`.
Exit codes: 0 ok · 1 usage · 2 no results · 3 failed (retry once at most) · 4 auth ·
5 unreachable · 6 a person is needed (CAPTCHA/login; don't retry) · 7 busy (retry
later) · 8 blocked (the site refuses this server; don't retry, use another source).
Errors are one JSON line on stderr whose `hint` says what to do next.

Output is budgeted so page text cannot flood an agent's context: each command prints at
most `--max-chars` characters (default 4000, at most 20000). A longer page is saved to
`~/.cache/web-kit/pages/` and only a preview plus its path is printed; with `-o` only
paths are printed; `--json` changes the format, never the amount.

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

Chrome is pinned (`CHROME_VERSION` in the Dockerfile): a browser upgrade voids every
Cloudflare pass the profile holds, so bump it deliberately.

`webkit status` shows the egress probe (a TLS handshake per upstream host, no
queries spent), browser health and recent per-engine outcomes. Self-healing:
supervisord restarts processes; a watchdog restarts Chrome + API when the CDP
link is dead; the container healthcheck covers Chrome, API and SearXNG.

Limits: from a datacenter egress, sites behind DataDome (e.g. ResearchGate) refuse
the server outright (exit 8), and Cloudflare-protected publishers ask for an
interactive check again after their pass expires (about 30 minutes), so a noVNC
pass helps only while a person is around. A residential egress is the only real fix.

## API

| Route | Purpose |
|---|---|
| `GET /v2/search?q=&profile=\|engine=&limit=&time=&lang=` | search with fallback chain |
| `GET /v2/engines` | engines, kinds, time-filter support, profiles |
| `POST /v2/page {url, format: fit\|md\|html\|links\|json, pdf, ...}` | read a page / PDF (`pdf`: the PDF the page links) |
| `POST /v2/crawl {url, strategy, max_depth, max_pages, include, exclude}` | bounded crawl |
| `GET /v2/download?url=&allow_html=` | stream a file with the browser's cookies; a paper page resolves to its PDF, other pages are `not_a_file` |
| `GET /v2/status`, `GET /v2/version` | health and versions |
| `GET /v2/admin/open?url=` | open a URL for a human (noVNC) |

License: MIT.
