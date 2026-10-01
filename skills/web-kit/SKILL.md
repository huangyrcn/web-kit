---
name: web-kit
description: "Fallback web access through a self-hosted, logged-in Chrome (`webkit` CLI), for when the built-in web tools fail or cannot do the job. Use when web search is out of quota or erroring (\"Web search was not performed\"); a page read fails (403, 429, timeout, too large, unsupported type) or returns a verification or login page instead of content (\"Just a moment\", \"请稍候\", \"Verify you are human\", reCAPTCHA, OpenReview or Cloudflare checks, a /challenge redirect, HTML where a PDF was expected); you need a page's exact full text or a PDF's text and the built-in reader only summarizes; you need the file itself (a paper PDF by URL, DOI or arXiv ID); or you need Google Scholar, Semantic Scholar or arXiv results. 读不了、被拦、验证页、下载论文 PDF、原文全文。 Not the default for search or reading; never for posting, submitting forms, purchases or account changes."
---

# web-kit (`webkit` CLI 2.0.0.dev0, API v2)

Use the built-in web tools first (Claude Code: WebSearch / WebFetch; Codex: web search / open /
find). web-kit takes over when they fail, are blocked or out of quota, or cannot do the job. It is
a real Chrome on a server, logged in to the sites the user has passed once.

## Which command

| You need | Command | You get |
|---|---|---|
| URLs for a topic (built-in search failed or is out of quota), or a specific engine | `webkit search "query"` (`-p academic` for papers) | ranked links + snippets, no answer; then `read` what you need |
| What a page or PDF says | `webkit read TARGET -o page.md` | text (PDFs: extracted text with page markers) |
| The paper PDF behind a paper page or DOI, as text | `webkit read --pdf TARGET -o paper.md` | the PDF's text |
| The file itself (to keep, hand over, or process) | `webkit download TARGET` | the saved path; nothing printed from the content |

TARGET is a URL, a DOI (`10.1145/...`, `doi:...`) or `arXiv:ID`. Don't know the URL: `search`.
Want to know what it says: `read`. Need the file: `download`.

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
  accessible" and continue with other sources. (A person can pass exit-6 pages in noVNC; passes on
  Cloudflare sites can expire within ~30 minutes.)
- Exit 7 (busy): retry in a minute; do not start more parallel `webkit` calls.
- Exit 3: retry once at most, then report. Exit 4 / 5: run `webkit doctor` and report.
- Errors are one JSON line on stderr; its `hint` says what to do next.

## Rules

- Output is budgeted: a command prints at most 4000 chars (`--max-chars`, up to 20000). Longer
  page text is saved to a file and the output gives its path (plus a preview for `read`); with
  `-o` only paths are printed. `--json` changes the format, never the amount.
- Read saved files selectively: `grep -n 'term' FILE` to locate the part you need, then read just
  those lines. Never `cat` a whole page or paper into context.
- `search` output starts with `# engines google=ok:8 duckduckgo=skipped ...`. `error:network` on
  every engine means the backend lost its route out: say so, do not guess results.
- One shared browser and one exit IP serve every agent: no bulk searching, few parallel calls.

## Typical use

```bash
webkit search -p academic "temporal knowledge graph forecasting LLM"
webkit read https://example.org/post                                 # short page: printed; long: file + preview
webkit read -o paper.md "https://openreview.net/pdf?id=FXdMgfCDer"   # a PDF comes back as text
grep -n -i "ablation" paper.md                                       # then read only those lines
webkit read --pdf -o paper.md 10.1145/3774904.3792101                # DOI -> paper page -> its PDF, as text
webkit read arXiv:2403.01092                                         # the abstract page
webkit download arXiv:2403.01092                                     # the PDF file
webkit download -o papers/ https://dl.acm.org/doi/pdf/10.1145/3774904.3792101
webkit status                                                        # which engines / egress work now
```

Setup: `uv tool install git+https://github.com/huangyrcn/web-kit`, then `webkit config set url URL`,
`webkit config set api-key` (key on stdin), `webkit skill install [--agent codex]`.
When anything fails, run `webkit doctor` first. Every option of every command:
`reference.md` next to this file, or `webkit <command> --help`.
