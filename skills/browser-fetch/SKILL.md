---
name: browser-fetch
description: >
  Fetch web content via a real Chrome browser (JS rendering, anti-scrape
  bypass, login cookies). Two modes: `page` renders a URL to markdown (read
  article/docs/SPA content); `file` downloads a URL to a raw file (PDF/zip
  with auth/cookies). Use `page` when you want to read page content; use
  `file` when you want to save the resource. Trigger keywords: "read this
  page", "fetch this page", "scrape this page", "cloudflare", "page blocked",
  "读一下这个网页", "抓取这个页面", "下载PDF", "下载文件", "download file",
  "download this". Needs CDP_URL + WEB_KIT_API_KEY.
metadata:
  argument-hint: 'browser-fetch page|file [options] URL'
allowed-tools: Bash
---

# browser-fetch

Fetch web content via a real Chrome browser connected over CDP. Executes JS,
bypasses anti-scraping, and reuses login cookies from the shared browser
profile. Two subcommands cover the two things an agent typically wants from a
URL: read its **content** (`page`) or save it as a **file** (`file`).

## page vs file — which to use

| You want… | Use | Output |
|---|---|---|
| Read an article, doc page, or SPA's rendered content | `page` | markdown (or json) |
| Save a PDF, zip, image, or other binary as a file | `file` | raw file on disk |
| "What does this PDF say?" (extract text) | `page` | markdown |
| "Download this PDF" (save the file) | `file` | raw .pdf |

Rule of thumb: **want the content → `page`; want the file → `file`.**

## How to invoke

Always use `uv run --script` — the script files may lack execute permission
after deployment.

### page — render URL to markdown

```bash
# Save to file (recommended)
uv run --script ${SKILL_DIR}/scripts/page -O out.md "https://example.com"

# Output to stdout
uv run --script ${SKILL_DIR}/scripts/page -o md "https://example.com"

# Focused body content (less navigation/footer noise)
uv run --script ${SKILL_DIR}/scripts/page -o md-fit -O out.md "https://example.com"

# JSON (url, title, markdown, links)
uv run --script ${SKILL_DIR}/scripts/page -o json "https://example.com"

# Deep crawl (follow links)
uv run --script ${SKILL_DIR}/scripts/page --deep-crawl bfs --max-depth 20 -O out.md "https://..."

# Bypass cache
uv run --script ${SKILL_DIR}/scripts/page -bc -O out.md "https://example.com"
```

### file — download URL to a raw file

```bash
uv run --script ${SKILL_DIR}/scripts/file <url> [output_file]
uv run --script ${SKILL_DIR}/scripts/file <url> output.pdf
uv run --script ${SKILL_DIR}/scripts/file <url> output.pdf --interactive
```

## page — output formats

| Format | Flag | Content |
|---|---|---|
| `md` | `-o md` | Full page markdown (default) |
| `md-fit` | `-o md-fit` | Focused body content, less navigation noise |
| `json` | `-o json` | JSON with url, title, markdown, links |
| `all` | `-o all` | Full JSON dump |

## page — output noise

Default `md` output includes navigation bars, site maps, footers, and other
boilerplate. The actual content is usually in the middle of the file.

When using `page` to answer a user's question:
1. Save to file with `-O`.
2. Read the file and extract the relevant content sections.
3. Synthesize your answer from the extracted content, not the raw output.

For pages with heavy boilerplate, `-o md-fit` extracts only the main body.

## page — deep crawl

For multi-page sites or documentation:

```bash
uv run --script ${SKILL_DIR}/scripts/page --deep-crawl bfs --max-depth 5 -O out.md "https://docs.example.com"
```

- `bfs` (breadth-first) — good for documentation sites
- `dfs` (depth-first) — good for blog archives or changelogs
- `--max-depth` — default 10, reduce for focused crawling

## file — download strategy

The `file` subcommand tries three strategies in order:

1. **Network.loadNetworkResource + IO.read** — streaming, low memory. Preferred.
2. **Fetch.requestPaused + Page.navigate** — bypasses CSP, buffers in memory. Fallback when streaming fails (CSP violations, HTTP 4xx/5xx).
3. **Interactive** — opt-in via `--interactive`. When blocked by reCAPTCHA,
   opens a noVNC window for a human to solve it, then retries.

If `output_file` is omitted, the filename is derived from the URL.

## file — when to use --interactive

Add `--interactive` only when the download is blocked by an interactive
challenge (reCAPTCHA, hCaptcha) that needs a human. It opens the noVNC web
client (auto-derived from `CDP_URL` host on port 6080) so the user can solve
the challenge in the real browser, then retries the download.

## Typical workflow with searxng-search

A common research pattern combines search and page reading:

```bash
# Step 1: Search and get URLs
uv run --script ${SKILL_DIR}/../searxng-search/scripts/searxng-search "query" -u -n 5

# Step 2: Read the most relevant pages
uv run --script ${SKILL_DIR}/scripts/page -O /tmp/page1.md "https://..."
uv run --script ${SKILL_DIR}/scripts/page -O /tmp/page2.md "https://..."

# Step 3: Synthesize from the downloaded content
```

Or inline:
1. `searxng-search` for a topic, observe which URLs look most relevant
2. `page -O` to read those pages
3. Compare and synthesize

See `references/workflow.md` for batch patterns and sub-agent templates.

## When to use browser-fetch vs curl/wget

Use browser-fetch when:
- curl/wget returns empty or incomplete content (JS-rendered SPA)
- The site blocks automated requests (Cloudflare, anti-bot)
- You need to see the page as a user would see it
- The resource requires login cookies (log in via noVNC first, then fetch)

Use curl/wget when:
- The content is static HTML
- The site doesn't block simple requests
- You just need a quick check of a simple page

## Environment

`CDP_URL` — Chrome DevTools Protocol endpoint (required, e.g. `http://localhost:9223`)

`WEB_KIT_API_KEY` — required; injected as the `X-API-Key` header on the CDP
connection (used by both `page` and `file`).
