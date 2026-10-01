"""SKILL.md generated from the CLI's own --help, so the skill an agent reads
always matches the installed CLI version."""

from __future__ import annotations

import argparse

from . import API_VERSION, __version__

_HEADER = """---
name: web-kit
description: "web-kit (`webkit` CLI): the agents' self-hosted browser. Rule: WebSearch finds, web-kit reads. Use it to read a URL or PDF as full, unsummarized text (read this URL, what does this page say, full text, the paper at, this PDF, 读这个页面, 原文, 全文, 这篇论文), pages that need JavaScript or a login (paywalled, 需要登录), downloading files with the browser's cookies (download the file, 下载这篇), crawling a docs site, and searching specific engines: Google Scholar, Semantic Scholar, arXiv, Google, DuckDuckGo, GitHub (找论文, 学术检索). Also the search fallback once WebSearch reports its session budget is spent. Not for clicking or filling forms."
---

# web-kit (`webkit` CLI {version}, API {api})

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

"""


def render(parser: argparse.ArgumentParser) -> str:
    parts = [_HEADER.format(version=__version__, api=API_VERSION)]
    sub_action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))  # noqa: SLF001
    for name, sp in sub_action.choices.items():
        if name in ("skill", "config"):
            continue
        parts.append(f"### webkit {name}\n\n```\n{sp.format_help().strip()}\n```\n")
        for a in sp._actions:  # noqa: SLF001 - nested groups (browser open)
            if isinstance(a, argparse._SubParsersAction):  # noqa: SLF001
                for sub_name, ssp in a.choices.items():
                    parts.append(f"### webkit {name} {sub_name}\n\n```\n{ssp.format_help().strip()}\n```\n")
    return "\n".join(parts)
