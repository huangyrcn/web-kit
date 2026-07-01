# SearxNG engines reference

searxng-search supports the following engines, default is Google. Use at most
one non-default engine per search; run separate searches to compare engines.
The group table lives in SKILL.md; this file holds per-engine usage notes.
CLI aliases use underscores for shell convenience; the script maps them to
SearXNG engine names such as `google scholar` before making the request.

```bash
uv run --script ${SKILL_DIR}/scripts/searxng-search "query"                                      # default Google
uv run --script ${SKILL_DIR}/scripts/searxng-search "query" -e google_scholar                    # one academic engine
uv run --script ${SKILL_DIR}/scripts/searxng-search "query" -e arxiv                             # arXiv alone
```

## General

google, bing, duckduckgo

- Google is the default and indexes the broadest range of sources (papers,
  repos, blogs, docs, forums). A reasonable first step for almost any query.
- Bing and DuckDuckGo are useful as alternates when Google rate-limits or
  returns captcha pages.

## Academic

google_scholar, semantic_scholar, openalex, dblp, pubmed, arxiv

- `google_scholar`, `semantic_scholar`, `openalex` provide clean academic
  metadata (authors, year, venue, citations). Best for literature surveys and
  citation chasing. Run them one at a time when you need to compare coverage.
- `dblp` is strong for CS bibliography; `pubmed` for biomedical.
- `arxiv` can be slower or prone to timeout. If you do not specifically need
  arXiv as a source, the default or another academic engine is usually enough.
  To restrict to arXiv content without the slow arxiv engine, use
  `site:arxiv.org` on the default engine instead.

## Code and packages

github, github_code, gitlab, huggingface, npm, pypi, crates, lib_rs,
pkg_go_dev, sourcehut, microsoft_learn, nvd

- `github` searches repos; `github_code` searches inside repo code.
- `npm`, `pypi`, `crates`, `lib_rs`, `pkg_go_dev` point at language package
  registries — more reliable than the default for "is there a package that…".
- `huggingface` for models/datasets.
- `nvd` for CVE / vulnerability lookups.
- `microsoft_learn` for MS docs (Azure, .NET, etc.).

## Community

hackernews, reddit, wikidata

- `reddit`, `hackernews` for community experience, pitfalls, comparisons,
  "has anyone tried…".
- `wikidata` for structured entity data.

## Books and library

annas_archive, zlibrary

- For finding books, textbooks, and papers hosted on shadow libraries.

## Query operators (work on Google and most engines)

- `site:arxiv.org` — restrict to a domain
- `after:2025`, `before:2024`, `2024..2025` — date ranges
- `"exact phrase"` — quoted phrase match
- `-word` — exclude a term
