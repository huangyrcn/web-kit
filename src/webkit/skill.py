"""The agent skill, generated from the CLI so it always matches the installed version:
SKILL.md (when and how to use web-kit) plus reference.md (every command's --help),
which agents open only when they need an option."""

from __future__ import annotations

import argparse

from . import API_VERSION, __version__

_HEADER = """---
name: web-kit
description: "Fallback web access through a real, logged-in Chrome (`webkit` CLI, via opencli: the user's own Chrome on this machine, or a web-kit server's), for when the built-in web tools fail or cannot do the job. Use when web search is out of quota or erroring (\\"Web search was not performed\\"); a page read fails (403, 429, timeout, too large, unsupported type) or returns a verification or login page instead of content (\\"Just a moment\\", \\"请稍候\\", \\"Verify you are human\\", reCAPTCHA, OpenReview or Cloudflare checks, a /challenge redirect, HTML where a PDF was expected); you need a page's exact full text and the built-in reader only summarizes; you need a file itself (a paper PDF by URL, DOI or arXiv ID); or you need Google Scholar, Semantic Scholar or arXiv results. 读不了、被拦、验证页、下载论文 PDF、原文全文。 Not the default for search or reading; never for posting, submitting forms, purchases or account changes."
---

# web-kit (`webkit` CLI {version}, API {api})

Use the built-in web tools first (Claude Code: WebSearch / WebFetch; Codex: web search / open /
find). web-kit takes over when they fail, are blocked or out of quota, or cannot do the job. It
drives a real Chrome through opencli: the user's own on this machine (their logins, their network),
or a web-kit server's (`webkit doctor` says which). Use `webkit` only: never call `opencli` yourself.

## Which command

| You need | Command | You get |
|---|---|---|
| URLs for a topic (built-in search failed or is out of quota), or a specific engine | `webkit search "query"` (`-p academic` for papers) | ranked links + snippets, no answer; then `read` what you need |
| What a web page says | `webkit read TARGET` | its Markdown; a long page: its outline and the file it is saved in |
| A file: a paper's PDF, a dataset, an archive | `webkit download TARGET` | the saved path; open the file with your own tools |

TARGET is a URL, a DOI (`10.1145/...`, `doi:...`) or `arXiv:ID`. Don't know the URL: `search`.
A web page: `read`. A file (a PDF too): `download`; `read` answers `not_a_page` for files.
`webkit engines` lists every engine
(`-e NAME`, e.g. dblp, pubmed, biorxiv, medrxiv, cnki) and profile (`-p general|academic|community`).

## When the built-in tools fail

- A verification or login page is a failure even with HTTP 200: "Just a moment…", "请稍候…",
  "Verify you are human", reCAPTCHA, a `/challenge` redirect, HTML where you asked for a PDF.
  Stop using the built-in tool on that URL (no more open / find / fetch / curl on it) and run
  `webkit read` or `webkit download` once.
- Web search out of quota ("Web search was not performed") or erroring: use `webkit search` for
  the rest of the task and say so in your report.
- Claude Code's WebFetch returns a model summary: when exact text matters (quotes, numbers,
  tables, a full paper), use `webkit read`.

## When web-kit fails

- Exit 6 (needs a person) or 8 (blocked): do not retry and do not wait. Report the URL as "not
  accessible" and continue with other sources. (A person can pass exit-6 pages in that Chrome:
  on this machine, or in the server's noVNC; the error's hint says where.)
- Exit 7 (busy): retry in a minute; do not start more parallel `webkit` calls.
- Exit 3: retry once at most, then report. Error `browser_conflict`: another Chrome extension
  (Immersive Translate, Zotero, ...) blocks automation on that page: use another source.
- Exit 5: the browser is not reachable (local: Chrome with the OpenCLI extension is not running or
  not connected; remote: the web-kit server is down): tell the user, then run `webkit doctor`.
- Errors are one JSON line on stderr; its `hint` says what to do next.

## Rules

- Output is budgeted: a command prints at most 4000 chars in total, errors included
  (`--max-chars`, up to 20000). A longer page is saved to a file, and `read` prints its size, path,
  first paragraph and outline (line numbers of its headings); with `-o` only paths are printed;
  anything else that does not fit goes to one file named in the last line. `--json` changes the
  format, never the amount.
- Read saved files in small pieces; lines can be very long. Go to a section by the outline's line
  numbers, or locate a term: `grep -n -m 10 'term' FILE | cut -c1-200`; then read only those lines
  (your file-reading tool with an offset and a limit, or `sed -n 'START,ENDp' FILE | cut -c1-500`).
  Never `cat` a whole page or saved output.
- Links in page text are `[text][N]`; the URLs are listed after the text as `[N]: URL`
  (`grep -m1 '^\\[N\\]:' FILE`). `-f links` prints only that list (N, URL, link text).
- `search` output starts with `# engines semantic_scholar=ok:8 google_scholar=skipped ...`. `error:unreachable` on
  every engine means Chrome is not connected: say so, do not guess results.
- One browser and one exit IP serve every agent: no bulk searching, few parallel calls.
- `download` fetches files directly, without browser cookies: paywalled publisher PDFs fail (exit 8);
  prefer arXiv, PMC or other open copies.

## Typical use

```bash
webkit search -p academic "temporal knowledge graph forecasting LLM"
webkit read https://example.org/post                                 # short page: printed; long: outline + file
sed -n '140,200p' FILE | cut -c1-500                                 # one section, by the outline's line numbers
grep -n -i -m 10 "ablation" FILE | cut -c1-200                       # or locate a term, then read those lines
webkit read arXiv:2403.01092                                         # the abstract page
webkit download arXiv:2403.01092                                     # the PDF file: open it with your own tools
webkit download 10.48550/arXiv.2301.09633                            # DOI -> its open-access PDF
webkit search -e dblp "graph transformers"                          # also: pubmed, biorxiv, medrxiv, ...
webkit status                                                        # is the local Chrome connected
```

Setup: `npm install -g @jackwener/opencli` and the OpenCLI extension in Chrome;
`opencli plugin install github:huangyrcn/web-kit` (biorxiv, medrxiv);
`uv tool install git+https://github.com/huangyrcn/web-kit`; then `webkit doctor` and
`webkit skill install [--agent codex]`.
When anything fails, run `webkit doctor` first. Every option of every command:
`reference.md` next to this file, or `webkit <command> --help`.
"""

_REFERENCE_HEADER = """# web-kit command reference (`webkit` CLI {version}, API {api})

Generated from `webkit <command> --help`. When to use which command: SKILL.md.

"""


def render(parser: argparse.ArgumentParser) -> str:
    """SKILL.md: when and how to use web-kit."""
    return _HEADER.format(version=__version__, api=API_VERSION)


def render_reference(parser: argparse.ArgumentParser) -> str:
    """reference.md: every command's --help."""
    parts = [_REFERENCE_HEADER.format(version=__version__, api=API_VERSION)]
    sub_action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))  # noqa: SLF001
    for name, sp in sub_action.choices.items():
        if name in ("skill", "config", "engines"):
            continue
        parts.append(f"## webkit {name}\n\n```\n{sp.format_help().strip()}\n```\n")
        for a in sp._actions:  # noqa: SLF001 - nested command groups
            if isinstance(a, argparse._SubParsersAction):  # noqa: SLF001
                for sub_name, ssp in a.choices.items():
                    parts.append(f"## webkit {name} {sub_name}\n\n```\n{ssp.format_help().strip()}\n```\n")
    return "\n".join(parts)
