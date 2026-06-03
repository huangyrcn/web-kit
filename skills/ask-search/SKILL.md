---
name: ask-search
description: >
  Web search skill backed by SearxNG. Supports 20+ engines grouped into
  general, academic, code, community, and book categories. Default is Google.
  Returns concise human-readable results; errors are JSON regardless of output
  format.
argument-hint: 'ask-search "query" [-n count] [-e engine(s)] [-l lang] [-c category] [-t timeout]'
allowed-tools: Bash
---

# ask-search

A web search skill that queries a SearxNG backend. It supports multiple
engines, but defaults to Google. The skill is designed to be part of a larger
research workflow: find → read → synthesize.

## When to use this skill

Use this skill when the task requires information from the web — for example:
- General lookups, background reading, or fact-checking
- Finding academic papers, citations, or datasets
- Locating repos, packages, APIs, or docs
- Checking recent news, policies, or announcements
- Gathering community experiences, opinions, or comparisons

Do not use this skill for local file search (use grep), repo-level search
(use `gh` CLI), or in-repo doc search (use project-specific tools).

## Input

The primary input is a natural language query. Some optional flags are
available to adjust the search scope or output:

| Flag | Meaning | Example |
|---|---|---|
| query | What to search for (required) | `"knowledge graph multi-hop reasoning"` |
| `-n` | Limit number of results | `-n 5` |
| `-e` | Specific engine(s), comma-separated | `-e google_scholar,semantic_scholar` |
| `-l` | Language preference | `-l zh-CN` |
| `-c` | Category | `-c news` |
| `-t` | Request timeout in seconds | `-t 60` |
| `-u` | Output URLs only | `-u` |

## Tools used

- `ask-search` CLI (the script bundled with this skill)
- `SEARXNG_URL` backend (default: `http://localhost:8082`)

## Default behavior

If you do not specify any flags:

- Engine: Google
- Results: top 10
- Output: concise human-readable list with title, URL, snippet, and engine tag
- Errors: JSON to stdout with an `error_type` field (exit code 1)

## Available engines

The supported engines are grouped by what they index. You do not need to
use all of them; most tasks only need one group at most.

### General
google, bing, duckduckgo

### Academic
google_scholar, semantic_scholar, openalex, dblp, pubmed, arxiv

### Code and packages
github, github_code, gitlab, huggingface, npm, pypi, crates, lib_rs,
pkg_go_dev, sourcehut, microsoft_learn, nvd

### Community
hackernews, reddit, wikidata

### Books and library
annas_archive, zlibrary

The full reference with usage examples is in `references/engines.md`.

## Typical workflow

A useful way to think about search in a research task is:

1. **Start with the default search.** The default already indexes a wide range
   of sources (paper aggregators, repos, blogs, docs, forums). This is
   often enough to understand the landscape of a topic.

2. **Observe what kind of results appear.** If the top results already point
   to the right kind of sources (papers, repos, news, forums, etc.), you
   may already have what you need.

3. **If the results are not from the expected domain, consider using a
   domain-specific engine set.** For example:
   - Academic metadata: `-e google_scholar,semantic_scholar,openalex`
   - Code and packages: `-e github,npm,pypi`
   - Community experience: `-e reddit,hackernews`
   - News and recent events: `-c news`

4. **If the results are from the right domain but not specific enough, you
   can narrow down further** — for example by using `site:arxiv.org` in the
   query, or by pointing at a specific source with `-e`.

## How to interpret results

When reading results, some useful observations:

- The default output already shows which engine each result came from.
- If results cluster around a particular domain (papers, repos, docs, etc.),
   that may indicate which domain-specific engine set could be useful next.
- If results are already rich and directly relevant, additional searches may
   not add much value.
- A `site:` filter can be useful when you already know the kind of source
   you want, without switching the underlying engine set.

## Notes that may help with engine choice

- For academic tasks, dedicated academic engines often provide cleaner
  metadata (authors, years, venues, citations). Google, however, already
  indexes most major paper sources, so the default is a reasonable first step.
- For code and package tasks, the dedicated engines point directly to repos
  and package registries, which the default may surface less reliably.
- Some engines (notably arxiv) can be slower or more prone to timeout. If
  you do not specifically need arXiv as a source, the default or another
  academic engine is often sufficient.
- `-e` changes which search path is used; it does not just filter the
  default results.
- The default human-readable output is intentionally concise. If you need
  structured results for scripting, `-j` is available but hidden from the
  default help because it produces longer output.

## Error handling

All errors are JSON to stdout (exit code 1):

```json
{"error_type": "timeout", "query": "...", "hint": "..."}
```

Common error types:

| error_type | What it means | What is available to you |
|---|---|---|
| `timeout` | The request took too long | You can try a longer timeout with `-t 60` |
| `no_results` | No results came back from the requested engines | You can refine the query or try different engines |
| `http_error` | The backend returned an HTTP error | Usually transient |
| `backend_unreachable` | The SearxNG backend could not be reached | Check `SEARXNG_URL` |

If the command succeeds but some engines failed, the output includes
information about which engines did not respond. This may be useful for
deciding whether to retry without those engines.

## Final output

Depending on how you invoke the skill, you may get:

- **Default human-readable list** (recommended for most tasks):
  ```
  [1] Title
      https://...
      Snippet text (max 200 chars)
      [google]
  ```
- **URL-only list** (useful for feeding into `crwlr`):
  ```
  https://...
  https://...
  ```
- **JSON** (mainly for scripting or structured parsing; errors are always JSON)

## Environment

`SEARXNG_URL` — SearxNG endpoint (default: `http://localhost:8082`)
