# web-kit

[中文](./README.zh-CN.md)

Fallback web access for AI agents: **search, read, download** through the real,
logged-in Chrome on the agent's own machine, for when the agent's built-in web
tools fail, are blocked or out of quota, or cannot do the job. Agents use a small
CLI (`webkit`); it drives that Chrome through [opencli](https://github.com/jackwener/opencli).

```
 agent ──► webkit CLI ──► opencli adapters, `opencli web read` ──► OpenCLI extension ──► your Chrome
             │             (search engines, pages as Markdown)                         (your logins, your IP)
             └─► urllib + pdftotext   (PDFs and files, fetched directly)
```

`webkit` keeps the contract agents rely on: read-only commands, exit codes that
say what to do next, every engine's outcome reported, fallback chains, a bounded
output, and verification pages never returned as content. It runs only opencli
*read* commands, as adapters (they attach to a blank tab, reuse warm tabs and
never evaluate a page script). A server backend is still supported as
`backend = "remote"` ([below](#remote-mode-server-backend)).

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
filling forms. Your Chrome and your IP serve every agent on the machine: heavy
parallel use raises CAPTCHA risk on your own accounts.

## Setup (local mode, the default)

```bash
uv tool install git+https://github.com/huangyrcn/web-kit     # update: uv tool upgrade web-kit
npm install -g @jackwener/opencli    # then add the OpenCLI extension to Chrome (Chrome Web Store)
webkit config set profile main       # optional: the opencli profile alias (`opencli profile list`)
webkit config set s2-api-key         # optional, from stdin: Semantic Scholar API key
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
most `--max-chars` characters in total, stdout and stderr together (default 4000, at most
20000). A longer page is saved to `~/.cache/web-kit/pages/` and only a preview plus its
path is printed; with `-o` only paths are printed; whatever else does not fit (long lists,
many errors) goes to one file named in the last line; `--json` changes the format, never
the amount.

Local-mode notes: keep Chrome open (exit 5 otherwise). Extensions that embed their
own pages in sites (Immersive Translate, Zotero, Scholar PDF Reader) block browser
automation on those pages (error `browser_conflict`). Engines: `webkit engines`
(no time filters locally). PDFs are fetched without browser cookies, so paywalled
publisher PDFs fail. `crawl` and `read -f html` need the remote backend.

## Remote mode (server backend)

`webkit config set backend remote`, `webkit config set url http://your-server:8082`,
`webkit config set api-key` (stdin). The backend:

```bash
cd backend
cp .env.example .env && chmod 600 .env      # keys, ports, bind address
cp searxng-settings/settings.yml.example searxng-settings/settings.yml
docker compose up -d --build
```

Optional: set `WEBKIT_S2_API_KEY` in `.env` ([free key](https://www.semanticscholar.org/product/api#api-key)). The `semantic_scholar` engine then asks the Graph API first, at most one request per second, and falls back to the website on any API failure; without a key it uses the website only.

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
