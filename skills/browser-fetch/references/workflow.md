# Advanced workflows

This file covers multi-step patterns that combine `browser-fetch` with
`searxng-search`. For engine selection (which search engine to use), see the
`searxng-search` skill and its `references/engines.md`.

## 1. Search → read → synthesize

```
searxng-search "query" -n 5
  ↓ scan snippets, pick 2-3 most relevant URLs
browser-fetch page -O /tmp/page1.md "url1"
browser-fetch page -O /tmp/page2.md "url2"
  ↓ compare across sources
synthesize an answer
```

## 2. page vs file in a research flow

Both subcommands share the same Chrome/CDP backend and login cookies, so they
interoperate:

- Found a paper PDF via `searxng-search` and want its text → `browser-fetch page`
  renders it to markdown.
- Want to keep the PDF for later → `browser-fetch file` saves the raw file.
- A doc site spans many pages → `browser-fetch page --deep-crawl bfs` walks it.

## 3. Batch reading

```bash
i=0
for url in $(uv run --script ${SKILL_DIR}/../searxng-search/scripts/searxng-search "query" -u -n 3); do
  i=$((i + 1))
  uv run --script ${SKILL_DIR}/scripts/page -O "/tmp/page-${i}.md" "$url"
done
```

## 4. Sub-agent prompt template

```
Goal: [describe what to research]

Steps:
1. Run searxng-search with the default engine; observe which domain results come from.
2. If needed, re-search with a domain-specific engine set.
3. Pick 3-5 most relevant URLs.
4. Use `browser-fetch page -O /tmp/xxx.md` to read each page.
5. Synthesize and conclude.
```
