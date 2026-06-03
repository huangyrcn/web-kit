---
name: ask-search
description: >
  Multi-engine web search via SearxNG (20+ engines: Google, Bing, arXiv, Scholar, GitHub, npm, Reddit...).
  Returns concise search results with title, URL, snippet, and source engine metadata.
  Use for any web search: general queries, academic papers, code/library lookup, news, discussions.
  Do NOT use for: local file search (use grep), repo search (use gh CLI), docs search (use lark-doc).
argument-hint: 'ask-search "query" [-n 5] [-e engine] [-c category]'
allowed-tools: Bash
---

# ask-search

Multi-engine web search CLI. Based on SearxNG, with Google as the stable default and optional specialized engines.

## Quick start

```bash
ask-search "query"                   # default: Google, top 10
ask-search "query" -n 5              # limit results
ask-search "query" -l zh-CN          # Chinese results
ask-search "query" -c news           # news category
```

## Default search policy

Start simple: run the default Google-backed search first. Do not specify `-e` unless the user explicitly requests a source/engine, or the first search returns weak results.

For agents:
- Use the default output. It is concise and keeps search results from flooding the context.
- `-n` is fine when the user asks for a count.
- Avoid `-e` by default. Engine selection changes the search path and can trigger slow or quarantined engines.
- If a command returns useful results, stop. Do not run a second search just to improve confidence.

## When to use engines

Treat `-e` as an escalation or an explicit user constraint, not the default route.

| Situation | Command |
|------|---------|
| General search | `ask-search "query"` |
| Academic discovery, first attempt | `ask-search "query"` |
| Academic discovery, if Google results are weak | `ask-search "query" -e google_scholar,semantic_scholar,openalex` |
| Known arXiv target or user explicitly asks for arXiv | `ask-search "query" -e arxiv` |
| Code / packages, if general search is weak or user asks for repos/packages | `ask-search "query" -e github,npm,pypi` |
| News | `ask-search "query" -c news` |
| Discussions, if user asks for community opinions | `ask-search "query" -e reddit,hackernews` |

Avoid putting `arxiv` in the default academic engine set. It is rate-limited and may timeout/quarantine; use Google with `site:arxiv.org` or `-e arxiv` only when arXiv is specifically needed.

Full engine list see `references/engines.md`.

## Result quality check

Before escalating engines, check whether the default results are good enough:

- For general search: top results are relevant, credible, and not mostly SEO/noise.
- For academic search: top results include likely paper sources such as arXiv, ACL, ACM, IEEE, Springer, OpenReview, Semantic Scholar, or project pages.
- If results are weak, first try a refined query or `site:` filter when that matches the task.
- Escalate to specialized engines only after default Google results are weak or the user requested a specific source.

## Default output

Search results use concise human-readable output by default:

```
[1] Title
    https://...
    Snippet text (max 200 chars)
    [google]
```

Errors are JSON regardless of output format, so no extra flag is needed for error handling.

## Error handling

All errors output as JSON to stdout (exit code 1):

```json
{"error_type": "timeout", "query": "...", "hint": "..."}
```

| `error_type` | Meaning | Recovery |
|--------------|---------|----------|
| `timeout` | Request too slow | If no explicit engine was requested, retry once with longer timeout `-t 60`; otherwise report the timeout and suggest alternatives |
| `no_results` | No results from any engine | Refine the query first; switch engines only if default results are weak and the user did not constrain the engine |
| `http_error` | SearxNG returned error | Check `http_status`, usually transient |
| `backend_unreachable` | SearxNG down | Check `SEARXNG_URL` env var |

### Recovery strategy

Follow this order:

1. If exit code is 0 and the output contains search results, stop.
2. If the user explicitly provided `-e`, treat that engine set as a constraint. Do not silently switch engines; report the failure and suggest a retry or alternative.
3. If default search times out, retry once with `-t 60`.
4. If default search returns weak or no results, try one refined query before switching engines.
5. If `unresponsive_engines` is non-empty, exclude those engines from the next attempt.
6. Do not retry the same semantic engine set. `-e google` and default Google count as the same engine set.
7. Stop on `backend_unreachable` or persistent `http_error`; report that web search is unavailable instead of trying unrelated web tools.

Do not run multiple searches in parallel just because a query is broad. Start with one Google-backed search, inspect results, then decide whether escalation is needed.

## Environment

`SEARXNG_URL` — SearxNG endpoint (default: `http://localhost:8082`)
