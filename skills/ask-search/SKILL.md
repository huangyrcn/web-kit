---
name: ask-search
description: >
  Multi-engine web search via SearxNG (20+ engines: Google, Bing, arXiv, Scholar, GitHub, npm, Reddit...).
  Returns structured results with title, URL, snippet, and source engine metadata.
  Use for any web search: general queries, academic papers, code/library lookup, news, discussions.
  Do NOT use for: local file search (use grep), repo search (use gh CLI), docs search (use lark-doc).
argument-hint: 'ask-search "query" [-n 5] [-e engine] [-j] [-c category]'
allowed-tools: Bash
---

# ask-search

Multi-engine web search CLI. Based on SearxNG, aggregated 20+ search engines.

## Quick start

```bash
ask-search "query"                   # default: google, top 10
ask-search "query" -j                # JSON output (recommended for agents)
ask-search "query" -e arxiv,openalex # specific engines
ask-search "query" -n 5              # limit results
ask-search "query" -l zh-CN          # Chinese results
ask-search "query" -c news           # news category
```

## Engine selection

Default: google. Only use `-e` for specific scenarios:

| Task | Command |
|------|---------|
| General search | `ask-search "query"` (no `-e` needed) |
| Academic papers | `ask-search "query" -e arxiv,google_scholar,semantic_scholar,openalex` |
| Code / packages | `ask-search "query" -e github,npm,pypi` |
| News | `ask-search "query" -c news` |
| Discussions | `ask-search "query" -e reddit,hackernews` |

Full engine list see `references/engines.md`.

## Output format (JSON)

Use `-j` for structured output. Schema:

```json
{
  "query": "search terms",
  "results": [
    {
      "title": "Page title",
      "url": "https://...",
      "content": "Snippet text",
      "engines": ["google", "bing"],
      "score": 3.5
    }
  ],
  "unresponsive_engines": [["engine_name", "reason"]]
}
```

- `results` — sorted by relevance, each item has `title`, `url`, `content`, `engines` (which engines returned it)
- `unresponsive_engines` — engines that failed or were quarantined
- `score` — higher = more engines agreed on this result

## Default output (human-readable)

Without `-j`, prints formatted text:

```
[1] Title
    https://...
    Snippet text (max 200 chars)
    [google]
```

## Error handling

All errors output as JSON to stdout (exit code 1):

```json
{"error_type": "timeout", "query": "...", "hint": "..."}
```

| `error_type` | Meaning | Recovery |
|--------------|---------|----------|
| `timeout` | Request too slow | Retry with longer timeout `-t 60`, or switch engines |
| `no_results` | No results from any engine | Retry with different engines |
| `http_error` | SearxNG returned error | Check `http_status`, usually transient |
| `backend_unreachable` | SearxNG down | Check `SEARXNG_URL` env var |

### Recovery strategy

When `no_results` or `timeout`:

1. If `unresponsive_engines` is non-empty, some engines failed — retry with different `-e` set
2. For general queries: try default (google) with longer timeout `-t 60`
3. Do not retry with the same engine set that just failed

## Environment

`SEARXNG_URL` — SearxNG endpoint (default: `http://localhost:8082`)
