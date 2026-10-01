---
name: web-kit
description: "web-kit (`webkit` CLI): the agents' self-hosted browser. Rule: WebSearch finds, web-kit reads. Use it to read a URL or PDF as full, unsummarized text (read this URL, what does this page say, full text, the paper at, this PDF, 读这个页面, 原文, 全文, 这篇论文), pages that need JavaScript or a login (paywalled, 需要登录), downloading files with the browser's cookies (download the file, 下载这篇), crawling a docs site, and searching specific engines: Google Scholar, Semantic Scholar, arXiv, Google, DuckDuckGo, GitHub (找论文, 学术检索). Also the search fallback once WebSearch reports its session budget is spent. Not for clicking or filling forms."
---

# web-kit (`webkit` CLI 2.0.0.dev0, API v2)

**Rule: WebSearch finds, web-kit reads.** web-kit is a self-hosted, logged-in Chrome reached
through the `webkit` CLI. It returns full, unsummarized text.

## When to use

- **Read**: whenever exact content matters (quotes, numbers, tables, a full paper or PDF) or the page
  needs JavaScript or a login: `webkit read URL`. Prefer it over WebFetch, which returns a model summary.
- **Find papers**: CS / ML: `webkit search -p academic "..."` (Semantic Scholar > Google Scholar >
  OpenAlex > arXiv). Biomedical, or when you need DOIs, citation counts or PubMed: the `paper-search` CLI.
- **General web search**: use WebSearch. If WebSearch answers with the "web search budget" notice,
  switch to `webkit search` for the rest of the task and say so in your report.
- **Files behind a login**: `webkit download URL`.
- **Not for**: clicking, typing or filling forms.

Install / update: `uv tool install --force git+https://github.com/huangyrcn/web-kit` (then `webkit skill install`).
When anything fails, run `webkit doctor` first.

## Typical use

```bash
webkit read https://arxiv.org/pdf/2106.12345                              # PDFs come back as text
webkit read -o page.md https://example.com/doc                            # large pages: save, then grep/read the file
webkit search -p academic "temporal knowledge graph forecasting LLM"      # semantic_scholar > google_scholar > openalex > arxiv
webkit search "temporal graph link prediction negative sampling"          # general: google > duckduckgo > bing
webkit search --time month "claude code release notes"                    # recency filter (engines without it are skipped)
webkit search --read 3 -o /tmp/q "query"                                  # search, then save the top 3 pages
webkit crawl https://docs.example.com -o /tmp/docs --max-pages 10
webkit download https://example.com/paper.pdf                             # -> ~/.cache/web-kit/downloads/
webkit status                                                             # which engines / egress work right now
```

## Rules

- Read the first line of `search` output: `# engines google=ok:8 duckduckgo=skipped ...`.
  `error:network` on every engine means the backend lost its route out; say so, do not guess results.
- Exit codes: 0 ok, 1 usage, 2 no results (engines healthy), 3 failed, 4 auth, 5 backend unreachable,
  6 human action needed (CAPTCHA/login), 7 busy. Errors are one JSON line on stderr.
- Exit 7 (busy): retry later; do not start more parallel `webkit` calls.
- Exit 6: tell the user to solve the page via noVNC (`webkit browser open URL` needs the admin key),
  or use `webkit download --wait-human`.
- Save long pages with `-o` instead of printing them into context. `search --read` caps inline text at
  3000 chars per page unless `-o DIR` is given.
- Results are fetched live (no cache) unless `--cache` is passed.

## Command reference


### webkit search

```
usage: webkit search [-h] [--url URL] [--timeout TIMEOUT] [--json]
                     [-p {general,academic,code,community}] [-e ENGINE]
                     [-n LIMIT] [--time {day,week,month,year}] [--lang LANG]
                     [--urls] [--read N] [-o DIR] [--max-chars MAX_CHARS]
                     query [query ...]

Search with a profile (ordered engine fallback) or one engine. First line: '#
engines <engine>=<ok:N|empty|error:CLASS|skipped> ...'.

positional arguments:
  query

options:
  -h, --help            show this help message and exit
  --json                print the raw API response as JSON
  -p {general,academic,code,community}, --profile {general,academic,code,community}
                        engine chain: general=google>duckduckgo>bing, academic
                        =semantic_scholar>google_scholar>openalex>arxiv,
                        code=github>google, community=hackernews>stackoverflow
                        (default: general)
  -e ENGINE, --engine ENGINE
                        use exactly this engine (see `webkit engines`)
  -n LIMIT, --limit LIMIT
                        max results (default 10)
  --time {day,week,month,year}
                        recency filter; engines without it are skipped
  --lang LANG           language, e.g. en, zh-CN
  --urls                print only result URLs (engine header on stderr)
  --read N              also read the top N results (fit markdown)
  -o DIR, --output DIR  with --read: save pages into DIR
  --max-chars MAX_CHARS
                        with --read: cap per page (default 3000 inline, 0 =
                        none)

connection:
  --url URL             backend URL (default: config / $WEBKIT_URL)
  --timeout TIMEOUT     seconds to wait for the backend
```

### webkit read

```
usage: webkit read [-h] [--url URL] [--timeout TIMEOUT] [--json]
                   [-f {fit,md,html,links,json}] [-o PATH] [--wait-for CSS|MS]
                   [--scroll] [--cache] [--max-chars MAX_CHARS]
                   URL [URL ...]

Render URLs in the backend browser and return clean text. PDFs are detected
and returned as extracted text.

positional arguments:
  URL

options:
  -h, --help            show this help message and exit
  --json                print the raw API response as JSON
  -f {fit,md,html,links,json}, --format {fit,md,html,links,json}
                        fit = main content (default), md = full page, html,
                        links, json
  -o PATH, --output PATH
                        save to FILE (one URL) or DIR; prints one line per
                        page
  --wait-for CSS|MS     CSS selector to wait for, or milliseconds
  --scroll              scroll the full page first (lazy content)
  --cache               allow a cached copy (default: always fetch fresh)
  --max-chars MAX_CHARS
                        truncate each page to N chars

connection:
  --url URL             backend URL (default: config / $WEBKIT_URL)
  --timeout TIMEOUT     seconds to wait for the backend
```

### webkit crawl

```
usage: webkit crawl [-h] [--url URL] [--timeout TIMEOUT] [--json] -o DIR
                    [--strategy {bfs,dfs}] [--max-depth MAX_DEPTH]
                    [--max-pages MAX_PAGES] [--include GLOB] [--exclude GLOB]
                    [-f {fit,md}]
                    url

Bounded synchronous crawl; one markdown file per page plus index.json.

positional arguments:
  url

options:
  -h, --help            show this help message and exit
  --json                print the raw API response as JSON
  -o DIR, --output DIR
  --strategy {bfs,dfs}
  --max-depth MAX_DEPTH
  --max-pages MAX_PAGES
                        default 10, backend cap 30
  --include GLOB        only URLs matching (repeatable)
  --exclude GLOB        skip URLs matching (repeatable)
  -f {fit,md}, --format {fit,md}

connection:
  --url URL             backend URL (default: config / $WEBKIT_URL)
  --timeout TIMEOUT     seconds to wait for the backend
```

### webkit download

```
usage: webkit download [-h] [--url URL] [--timeout TIMEOUT] [--json] [-o PATH]
                       [--max-mb MAX_MB] [--wait-human]
                       [--wait-timeout WAIT_TIMEOUT]
                       url

Stream a file through the backend browser (its logins apply). Default
destination: /home/ray/.cache/web-kit/downloads/ ($WEBKIT_DOWNLOAD_DIR).

positional arguments:
  url

options:
  -h, --help            show this help message and exit
  --json                print the raw API response as JSON
  -o PATH, --output PATH
                        target file or directory
  --max-mb MAX_MB       size limit in MB (default: backend limit)
  --wait-human          on CAPTCHA/login: open the page in the backend browser
                        and retry until solved
  --wait-timeout WAIT_TIMEOUT
                        seconds to wait with --wait-human (default 300)

connection:
  --url URL             backend URL (default: config / $WEBKIT_URL)
  --timeout TIMEOUT     seconds to wait for the backend
```

### webkit status

```
usage: webkit status [-h] [--url URL] [--timeout TIMEOUT] [--json]

options:
  -h, --help         show this help message and exit
  --json             print the raw API response as JSON

connection:
  --url URL          backend URL (default: config / $WEBKIT_URL)
  --timeout TIMEOUT  seconds to wait for the backend
```

### webkit engines

```
usage: webkit engines [-h] [--url URL] [--timeout TIMEOUT] [--json]

options:
  -h, --help         show this help message and exit
  --json             print the raw API response as JSON

connection:
  --url URL          backend URL (default: config / $WEBKIT_URL)
  --timeout TIMEOUT  seconds to wait for the backend
```

### webkit doctor

```
usage: webkit doctor [-h] [--url URL] [--timeout TIMEOUT] [--json]

options:
  -h, --help         show this help message and exit
  --json             print the raw API response as JSON

connection:
  --url URL          backend URL (default: config / $WEBKIT_URL)
  --timeout TIMEOUT  seconds to wait for the backend
```

### webkit browser

```
usage: webkit browser [-h] {open} ...

positional arguments:
  {open}
    open      open URL in the backend browser for a manual login/CAPTCHA via
              noVNC

options:
  -h, --help  show this help message and exit
```

### webkit browser open

```
usage: webkit browser open [-h] [--url URL] [--timeout TIMEOUT] [--json] url

positional arguments:
  url

options:
  -h, --help         show this help message and exit
  --json             print the raw API response as JSON

connection:
  --url URL          backend URL (default: config / $WEBKIT_URL)
  --timeout TIMEOUT  seconds to wait for the backend
```
