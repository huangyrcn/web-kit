# web-kit

[中文](./README.zh-CN.md)

Fallback web access for AI agents: **search, read, download** through the real,
logged-in Chrome on the agent's own machine, for when the agent's built-in web
tools fail, are blocked or out of quota, or cannot do the job. Agents use a small
CLI (`webkit`); it drives that Chrome through [opencli](https://github.com/jackwener/opencli).

```
 agent ──► webkit CLI ──► opencli adapters, `opencli web read` ──► OpenCLI extension ──► your Chrome
             │             (search engines, pages as Markdown)                         (your logins, your IP)
             └─► urllib   (download: PDFs and files, fetched directly, saved as they are)
```

`webkit` keeps the contract agents rely on: read-only commands, exit codes that
say what to do next, every engine's outcome reported, fallback chains, a bounded
output, and verification pages never returned as content. It runs only opencli
*read* commands, as adapters (they attach to a blank tab, reuse warm tabs and
never evaluate a page script). The same code also runs as a server in a container
(`backend = "remote"`, [below](#remote-mode-the-web-kit-server)), for machines without
their own Chrome.

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
| `webkit read` | URL, DOI, `arXiv:ID` | a web page as Markdown (links as `[text][N]`, URLs listed at the end) | learn what a page says; a long page prints its outline and is saved to a file |
| `webkit download` | URL, DOI, `arXiv:ID` | the file on disk (path printed) | get a file (a PDF too); a paper page resolves to its PDF; what the file says is for the agent's own tools |

Not for: anything the built-in tools already did fine, bulk discovery, clicking or
filling forms. Your Chrome and your IP serve every agent on the machine: heavy
parallel use raises CAPTCHA risk on your own accounts.

## Setup (local mode, the default)

```bash
uv tool install git+https://github.com/huangyrcn/web-kit     # update: uv tool upgrade web-kit
npm install -g @jackwener/opencli    # then add the OpenCLI extension to Chrome (Chrome Web Store)
opencli plugin install github:huangyrcn/web-kit   # web-kit's own adapters: biorxiv, medrxiv
webkit config set profile main       # optional: the opencli profile alias (`opencli profile list`)
webkit config set s2-api-key         # optional, from stdin: Semantic Scholar API key
webkit doctor
webkit skill install                 # ~/.claude/skills/web-kit/SKILL.md for this CLI version
webkit skill install --agent codex   # ~/.codex/skills/web-kit/ (SKILL.md + reference.md)
```

```bash
webkit search "query"                          # general: google
webkit search -p academic "query"              # semantic_scholar > google_scholar > openalex > arxiv
webkit search --read 3 -o out/ "query"         # search, then save the top 3 pages
webkit read https://example.org/post           # short page: printed; long: outline + saved file
webkit download https://example.com/paper.pdf  # -> ~/.cache/web-kit/downloads/
webkit download arXiv:2403.01092               # a paper page or DOI resolves to its PDF
webkit status                                  # opencli, the Chrome extension, the plugin
webkit stats                                   # what the last 7 days of calls did (events log)
```

The first line of `search` output reports each engine:
`# engines semantic_scholar=error:busy google_scholar=ok:8 openalex=skipped arxiv=skipped`.
Exit codes: 0 ok · 1 usage · 2 no results · 3 failed (retry once at most) · 4 auth ·
5 unreachable · 6 a person is needed (CAPTCHA/login; don't retry) · 7 busy (retry
later) · 8 blocked (the site refuses this server; don't retry, use another source).
Errors are one JSON line on stderr whose `hint` says what to do next.

Output is budgeted so page text cannot flood an agent's context: each command prints at
most `--max-chars` characters in total, stdout and stderr together (default 4000, at most
20000). A longer page is saved to `~/.cache/web-kit/pages/` and only its size, path, first
paragraph and outline (the line number of each heading) are printed; with `-o` only paths are printed; whatever else does not fit (long lists,
many errors) goes to one file named in the last line; `--json` changes the format, never
the amount.

Who does what: **opencli** knows sites (one adapter per site: URL, page structure,
fields); **webkit** knows agents (commands, engine chains, one result shape, error
classes and exit codes, output budget, PDFs). A site opencli has no adapter for gets
one in `opencli-plugin/` of this repo (a name official opencli does not use; dropped
once upstream has one), and one line in webkit's engine table.

Local-mode notes: keep Chrome open (exit 5 otherwise). Extensions that embed their
own pages in sites (Immersive Translate, Zotero, Scholar PDF Reader) block browser
automation on those pages (error `browser_conflict`). Engines: `webkit engines`
(no time filters locally). PDFs are fetched without browser cookies, so paywalled
publisher PDFs fail. `read -f html` is not available.

Events log: every `search` / `read` / `download` appends one JSON line to
`~/.local/state/web-kit/events.jsonl` (0600; rotated at 10 MB, 3 files kept): the full
query or URLs, each engine's outcome and time, page sizes, error class, exit code, how
much was printed, and which agent called (Claude Code or Codex, from its environment).
Never keys, page text or result lists. `webkit stats` summarizes it: outcomes and
latency per command and per engine, failing domains, output budget use, repeated calls.
`webkit config set log off` stops it.

## Remote mode (the web-kit server)

The server is local mode in a container: Chrome with the OpenCLI extension
(force-installed by Chrome policy), the opencli daemon, web-kit's opencli plugin, and
`webkit-serve` (`src/webkit/serve.py`), which answers the `/v2` API with the same
code, so results, errors and exit codes are the same in both modes.

```bash
cp backend/.env.example backend/.env && chmod 600 backend/.env   # bind address, keys
docker compose -f backend/docker-compose.yml up -d --build      # build context: the repo root
```

Bind the ports to an address only trusted clients reach (e.g. the host's tailnet
address, `WEBKIT_BIND`); every route needs its key all the same. Clients:
`webkit config set backend remote`, `webkit config set url http://your-server:8082`,
`webkit config set api-key` (stdin), then `webkit doctor`.

- `8082` API (`X-API-Key`; `/v2/admin/*` needs `X-Admin-Key`; `/healthz` answers only ok / not ok)
- `6080` noVNC for a person (user `webkit`, password = admin key): log in to sites
  once; the profile lives in the `chrome-data` volume. `download --wait-human` opens
  the page there.

The server writes the same events, one JSON line per request, to stdout (with the
caller's address; a healthy `/healthz` is not logged; `WEBKIT_LOG=off` stops it):
`docker logs web-kit 2>&1 | webkit stats -`.

Pinned: Chrome (`CHROME_VERSION`; a new Chrome voids the Cloudflare passes the profile
holds), Node and opencli. The OpenCLI extension updates itself from the Web Store; if a
new one stops talking to the pinned opencli, `webkit doctor` shows it as not connected:
rebuild with a newer `OPENCLI_VERSION`. supervisord restarts any process that exits.

Limits: a datacenter egress is distrusted by many sites (Google may answer a fresh,
logged-out profile with redirect links or its AI mode; Scholar and DataDome sites may
refuse it). Logging in through noVNC helps some; a residential egress is the real fix.

## API

| Route | Purpose |
|---|---|
| `GET /v2/search?q=&profile=\|engine=&limit=&time=&lang=` | search with fallback chain |
| `GET /v2/engines` | engines, kinds, profiles |
| `POST /v2/page {url, format: fit\|md\|links\|json, wait_for}` | read a web page as Markdown (files: `not_a_page`) |
| `GET /v2/download?url=&allow_html=&max_mb=` | stream a file; a paper page or DOI resolves to its PDF, other pages are `not_a_file` |
| `GET /v2/status`, `GET /v2/version` | health and versions |
| `GET /v2/admin/open?url=`, `GET /v2/admin/ping` | open a URL in the server's Chrome for a person (noVNC); check the admin key |

License: MIT.
