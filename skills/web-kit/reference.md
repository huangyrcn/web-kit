# web-kit command reference (`webkit` CLI 2.0.0.dev0, API v2)

Generated from `webkit <command> --help`. When to use which command: SKILL.md.


## webkit search

```
usage: webkit search [-h] [--url URL] [--timeout TIMEOUT] [--json]
                     [--max-chars N] [-p {general,academic,code,community}]
                     [-e ENGINE] [-n LIMIT] [--time {day,week,month,year}]
                     [--lang LANG] [--urls] [--read N] [-o DIR]
                     query [query ...]

Find URLs. Returns raw result links with snippets from live engines: no synthesized
answer, and no WebSearch quota. Use it when WebSearch is out of quota or failing, or
for a specific engine (Google Scholar, Semantic Scholar, arXiv) or a date filter.
Then `webkit read` the results you need.
First line: '# engines <engine>=<ok:N|empty|error:CLASS|skipped> ...'.

positional arguments:
  query

options:
  -h, --help            show this help message and exit
  --json                JSON output (same amount, different format)
  --max-chars N         most characters this command prints (default 4000, at
                        most 20000); longer text goes to a file and its path
                        is printed
  -p, --profile {general,academic,code,community}
                        engine chain: general=google>duckduckgo>bing, academic
                        =semantic_scholar>google_scholar>openalex>arxiv,
                        code=github>google, community=hackernews>stackoverflow
                        (default: general)
  -e, --engine ENGINE   use exactly this engine (see `webkit engines`)
  -n, --limit LIMIT     max results (default 10)
  --time {day,week,month,year}
                        recency filter; engines without it are skipped
  --lang LANG           language, e.g. en, zh-CN
  --urls                print only result URLs (engine header on stderr)
  --read N              also read the top N results into files (-o DIR, else
                        the cache); prints their paths
  -o, --output DIR      with --read: save pages into DIR

connection:
  --url URL             backend URL (default: config / $WEBKIT_URL)
  --timeout TIMEOUT     seconds to wait for the backend
```

## webkit read

```
usage: webkit read [-h] [--url URL] [--timeout TIMEOUT] [--json]
                   [--max-chars N] [--pdf] [-f {fit,md,html,links,json}]
                   [-o PATH] [--wait-for CSS|MS] [--scroll] [--cache]
                   TARGET [TARGET ...]

Learn what a page says, as text. Use it when the built-in reader fails (403, 429,
timeout, too large), returns a verification or login page, or gives a summary where
you need the exact text (quotes, numbers, tables, a full paper).
Targets: URLs, DOIs (10.1145/... or doi:...), arXiv IDs (arXiv:2403.01092 -> abstract page).
PDFs come back as extracted text with '<!-- page N -->' markers; --pdf reads the PDF a
paper page links instead of the page. A verification page is never returned as
content: it is exit 6 (needs a person) or 8 (blocked).
Output: a page that fits --max-chars is printed; a longer one is saved to a file and
only a preview and the path are printed. With -o, only paths are printed.

positional arguments:
  TARGET

options:
  -h, --help            show this help message and exit
  --json                JSON output (same amount, different format)
  --max-chars N         most characters this command prints (default 4000, at
                        most 20000); longer text goes to a file and its path
                        is printed
  --pdf                 read the paper PDF the page links (citation_pdf_url)
  -f, --format {fit,md,html,links,json}
                        fit = main content (default), md = full page, html,
                        links, json
  -o, --output PATH     save to FILE (one target) or DIR; prints one line per
                        page
  --wait-for CSS|MS     CSS selector to wait for, or milliseconds
  --scroll              scroll the full page first (lazy content)
  --cache               allow a cached copy (default: always fetch fresh)

connection:
  --url URL             backend URL (default: config / $WEBKIT_URL)
  --timeout TIMEOUT     seconds to wait for the backend
```

## webkit download

```
usage: webkit download [-h] [--url URL] [--timeout TIMEOUT] [--json]
                       [--max-chars N] [-o PATH] [--max-mb MAX_MB]
                       [--allow-html] [--wait-human]
                       [--wait-timeout WAIT_TIMEOUT]
                       TARGET

Get the file itself (PDF, archive, dataset), saved to disk through the backend
browser, so its logins and cookies apply. Prints the saved path, not the content.
Targets: URLs, DOIs, arXiv IDs. A paper page resolves to the PDF it links; any other
web page is an error (not_a_file): use `read` for text.
Default destination: /home/ray/.cache/web-kit/downloads/ ($WEBKIT_DOWNLOAD_DIR).

positional arguments:
  TARGET

options:
  -h, --help            show this help message and exit
  --json                JSON output (same amount, different format)
  --max-chars N         most characters this command prints (default 4000, at
                        most 20000); longer text goes to a file and its path
                        is printed
  -o, --output PATH     target file or directory
  --max-mb MAX_MB       size limit in MB (default: backend limit)
  --allow-html          save a web page as-is instead of resolving its PDF
  --wait-human          only with a person at noVNC: open the page there and
                        retry until they pass the check
  --wait-timeout WAIT_TIMEOUT
                        seconds to wait with --wait-human (default 300)

connection:
  --url URL             backend URL (default: config / $WEBKIT_URL)
  --timeout TIMEOUT     seconds to wait for the backend
```

## webkit crawl

```
usage: webkit crawl [-h] [--url URL] [--timeout TIMEOUT] [--json]
                    [--max-chars N] -o DIR [--strategy {bfs,dfs}]
                    [--max-depth MAX_DEPTH] [--max-pages MAX_PAGES]
                    [--include GLOB] [--exclude GLOB] [-f {fit,md}]
                    url

Bounded synchronous crawl of one site: one markdown file per page plus index.json.

positional arguments:
  url

options:
  -h, --help            show this help message and exit
  --json                JSON output (same amount, different format)
  --max-chars N         most characters this command prints (default 4000, at
                        most 20000); longer text goes to a file and its path
                        is printed
  -o, --output DIR
  --strategy {bfs,dfs}
  --max-depth MAX_DEPTH
  --max-pages MAX_PAGES
                        default 10, backend cap 30
  --include GLOB        only URLs matching (repeatable)
  --exclude GLOB        skip URLs matching (repeatable)
  -f, --format {fit,md}

connection:
  --url URL             backend URL (default: config / $WEBKIT_URL)
  --timeout TIMEOUT     seconds to wait for the backend
```

## webkit status

```
usage: webkit status [-h] [--url URL] [--timeout TIMEOUT] [--json]
                     [--max-chars N]

backend health: browser, egress, engines

options:
  -h, --help         show this help message and exit
  --json             JSON output (same amount, different format)
  --max-chars N      most characters this command prints (default 4000, at
                     most 20000); longer text goes to a file and its path is
                     printed

connection:
  --url URL          backend URL (default: config / $WEBKIT_URL)
  --timeout TIMEOUT  seconds to wait for the backend
```

## webkit doctor

```
usage: webkit doctor [-h] [--url URL] [--timeout TIMEOUT] [--json]
                     [--max-chars N]

check config, keys, versions and backend health

options:
  -h, --help         show this help message and exit
  --json             JSON output (same amount, different format)
  --max-chars N      most characters this command prints (default 4000, at
                     most 20000); longer text goes to a file and its path is
                     printed

connection:
  --url URL          backend URL (default: config / $WEBKIT_URL)
  --timeout TIMEOUT  seconds to wait for the backend
```
