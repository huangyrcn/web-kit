---
name: searxng-search
description: >
  Aggregate web search across 20+ engines (Google, Google Scholar, Semantic
  Scholar, arXiv, GitHub, Reddit, etc.). Returns titles, URLs, and snippets.
  Use for: fact lookups, background reading, finding academic papers or
  citations, locating repos/packages/APIs, checking recent news, and gathering
  community discussions or comparisons. Trigger keywords: "search," "look up,"
  "find," "查," "搜," "找," "有没有," and similar. Supports category, language,
  and engine selection with automatic academic/code/community engine groups.
  Do not use for local file search (use grep), repo-level search (use gh CLI),
  or in-repo doc search.
metadata:
  argument-hint: 'searxng-search "query" [-n count] [-e engine(s)] [-l lang] [-c category] [-t timeout]'
allowed-tools: Bash
---

# searxng-search

A web search skill that queries a SearxNG backend. Aggregates results across
20+ engines, defaults to Google. Part of a research workflow: find → read →
synthesize. Pair with `browser-fetch` to read the pages this skill finds.

## Input

The primary input is a natural language query. Optional flags adjust scope:

| Flag | Meaning | Example |
|---|---|---|
| query | What to search for (required) | `"knowledge graph multi-hop reasoning"` |
| `-n` | Limit number of results | `-n 5` |
| `-e` | Specific engine(s), comma-separated | `-e google_scholar,semantic_scholar` |
| `-l` | Language preference | `-l zh-CN` |
| `-c` | Category | `-c news` |
| `-t` | Request timeout in seconds | `-t 60` |
| `-u` | Output URLs only | `-u` |
| `-j` | Output JSON (structured, for scripting) | `-j` |

## How to invoke

The script is bundled with this skill. Always invoke it via the script path
so it works regardless of whether `searxng-search` is on the user's PATH:

```bash
uv run --script ${SKILL_DIR}/scripts/searxng-search "query"
uv run --script ${SKILL_DIR}/scripts/searxng-search "query" -n 5
uv run --script ${SKILL_DIR}/scripts/searxng-search "query" -e google_scholar,semantic_scholar
uv run --script ${SKILL_DIR}/scripts/searxng-search "query" -c news -l zh-CN
```

**Do not invoke the script directly** (e.g. `${SKILL_DIR}/scripts/searxng-search ...`) — the
file may not have execute permission after deployment. Always use `uv run --script`.

The only external dependency is `uv` (auto-installs inline Python deps on
first run) and a running SearxNG backend.

## Environment

`SEARXNG_URL` — SearxNG endpoint (default: `http://localhost:8082`)

`WEB_KIT_API_KEY` — required; sent as the `X-API-Key` header to authenticate to the gateway.

## Default behavior

If you do not specify any flags:

- Engine: Google
- Results: top 10
- Output: concise human-readable list with title, URL, snippet, and engine tag
- Errors: JSON to stdout with an `error_type` field (exit code 1)

## Available engines

Engines are grouped by what they index. Most tasks need at most one group.

| Group | Engines |
|---|---|
| General | google, bing, duckduckgo |
| Academic | google_scholar, semantic_scholar, openalex, dblp, pubmed, arxiv |
| Code and packages | github, github_code, gitlab, huggingface, npm, pypi, crates, lib_rs, pkg_go_dev, sourcehut, microsoft_learn, nvd |
| Community | hackernews, reddit, wikidata |
| Books and library | annas_archive, zlibrary |

Per-engine usage notes and examples are in `references/engines.md`.

## Typical workflow

1. **Start with the default search.** The default already indexes a wide range
   of sources (paper aggregators, repos, blogs, docs, forums). Often enough to
   understand the landscape of a topic.

2. **Observe what kind of results appear.** If the top results already point to
   the right kind of sources (papers, repos, news, forums), you may already
   have what you need.

3. **If results are not from the expected domain, switch to a domain-specific
   engine set.** For example:
   - Academic metadata: `-e google_scholar,semantic_scholar,openalex`
   - Code and packages: `-e github,npm,pypi`
   - Community experience: `-e reddit,hackernews`
   - News and recent events: `-c news`

4. **If results are from the right domain but not specific enough, narrow
   further** — e.g. `site:arxiv.org` in the query, or point at a specific
   source with `-e`.

## How to interpret results

- The default output shows which engine each result came from.
- If results cluster around a particular domain (papers, repos, docs), that
  may indicate which domain-specific engine set could be useful next.
- If results are already rich and directly relevant, additional searches may
  not add much value.
- A `site:` filter is useful when you already know the kind of source you
  want, without switching the underlying engine set.

## Notes on engine choice

- For academic tasks, dedicated academic engines often provide cleaner
  metadata (authors, years, venues, citations). Google already indexes most
  major paper sources, so the default is a reasonable first step.
- For code and package tasks, dedicated engines point directly to repos and
  package registries, which the default may surface less reliably.
- Some engines (notably arxiv) can be slower or more prone to timeout. If you
  do not specifically need arXiv as a source, the default or another academic
  engine is often sufficient.
- `-e` changes which search path is used; it does not just filter the default
  results.
- The default human-readable output is intentionally concise. For structured
  results, `-j` is available (hidden from default help because it produces
  longer output).

## Error handling

All errors are JSON to stdout (exit code 1):

```json
{"error_type": "timeout", "query": "...", "hint": "..."}
```

| error_type | What it means | What is available to you |
|---|---|---|
| `timeout` | The request took too long | Try a longer timeout with `-t 60` |
| `no_results` | No results came back from the requested engines | Refine the query or try different engines |
| `http_error` | The backend returned an HTTP error | Usually transient |
| `backend_unreachable` | The SearxNG backend could not be reached | Check `SEARXNG_URL` |

If the command succeeds but some engines failed, the output includes which
engines did not respond — useful for deciding whether to retry without them.

## Final output

Depending on how you invoke the skill, you may get:

- **Default human-readable list** (recommended for most tasks):
  ```
  [1] Title
      https://...
      Snippet text (max 200 chars)
      [google]
  ```
- **URL-only list** (useful for feeding into `browser-fetch`):
  ```
  https://...
  https://...
  ```
- **JSON** (mainly for scripting or structured parsing; errors are always JSON)
