---
name: crwlr
description: >
  用 Chrome 浏览器获取网页内容（执行 JS，绕过反爬）。当 curl/wget/WebFetch 被拦截时使用。
  中文触发词："读一下这个网页"、"抓取这个页面"、"网页被拦截了"、"cloudflare"、"网页打不开"。
  英文触发词："read this page", "fetch this page", "page blocked", "cloudflare", "scrape this page"。
  也适用于 JS 渲染的页面（SPA、React、Vue 等），普通 curl 拿不到完整内容时用 crwlr。
argument-hint: 'crwlr crawl [-O file] [-o md|md-fit|json|all] [--deep-crawl bfs|dfs] URL'
allowed-tools: Bash
---

# crwlr

用 Chrome 浏览器获取网页内容，转为 markdown。通过 CDP 连接远程 Chrome 实例，
执行 JS 渲染完整页面后提取内容。适用于 curl/wget/WebFetch 被反爬拦截、
或页面依赖 JS 渲染拿不到完整内容的场景。

## How to invoke

```bash
# 保存到文件（推荐）
uv run --script ${SKILL_DIR}/scripts/crwlr crawl -O out.md "https://example.com"

# 输出到 stdout
uv run --script ${SKILL_DIR}/scripts/crwlr crawl -o md "https://example.com"

# 尝试输出更聚焦的正文（减少导航栏/页脚噪声）
uv run --script ${SKILL_DIR}/scripts/crwlr crawl -o md-fit -O out.md "https://example.com"

# 输出 JSON（含 url、title、markdown、links）
uv run --script ${SKILL_DIR}/scripts/crwlr crawl -o json "https://example.com"

# 深度爬取（跟随页面链接）
uv run --script ${SKILL_DIR}/scripts/crwlr crawl --deep-crawl bfs --max-depth 20 -O out.md "https://..."

# 跳过缓存
uv run --script ${SKILL_DIR}/scripts/crwlr crawl -bc -O out.md "https://example.com"
```

## Output formats

| Format | Flag | Content |
|---|---|---|
| `md` | `-o md` | Full page markdown (default) |
| `md-fit` | `-o md-fit` | Focused body content, less navigation noise |
| `json` | `-o json` | JSON with url, title, markdown, links |
| `all` | `-o all` | Full JSON dump |

## Output noise

Default `md` output includes navigation bars, site maps, footers, and other
boilerplate. The actual article/content is usually in the middle of the file.

When using crwlr to answer a user's question:
1. Save to file with `-O`.
2. Read the file and extract the relevant content sections.
3. Synthesize your answer from the extracted content, not the raw output.

For pages with heavy boilerplate, try `-o md-fit` which attempts to extract
only the main body content.

## Deep crawl

For multi-page sites or documentation:

```bash
uv run --script ${SKILL_DIR}/scripts/crwlr crawl --deep-crawl bfs --max-depth 5 -O out.md "https://docs.example.com"
```

- `bfs` (breadth-first) — good for documentation sites
- `dfs` (depth-first) — good for blog archives or changelogs
- `--max-depth` — default 10, reduce for focused crawling

## Typical workflow with ask-search

A common research pattern combines search and page reading:

```bash
# Step 1: Search and get URLs
uv run --script ${SKILL_DIR}/../ask-search/scripts/ask-search "query" -u -n 5

# Step 2: Read the most relevant pages
uv run --script ${SKILL_DIR}/scripts/crwlr crawl -O /tmp/page1.md "https://..."
uv run --script ${SKILL_DIR}/scripts/crwlr crawl -O /tmp/page2.md "https://..."

# Step 3: Synthesize from the downloaded content
```

Or inline:
1. `ask-search` for a topic, observe which URLs look most relevant
2. `crwlr crawl -O` to read those pages
3. Compare and synthesize

## When to use crwlr vs curl/wget

Use crwlr when:
- curl/wget returns empty or incomplete content (JS-rendered SPA)
- The site blocks automated requests (Cloudflare, anti-bot)
- You need to see the page as a user would see it
- The page requires login cookies (use VNC to log in first, then crwlr)

Use curl/wget when:
- The content is static HTML
- The site doesn't block simple requests
- You just need a quick check of a simple page

## Environment

`CDP_URL` — Chrome DevTools Protocol endpoint (required, e.g. `http://localhost:9223`)

`WEB_KIT_API_KEY` — required; injected as the `X-API-Key` header on the CDP connection.
