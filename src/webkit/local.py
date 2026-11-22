"""Local mode: webkit's commands run on this machine, through opencli and this machine's
Chrome (the profile its OpenCLI extension is in). There is no backend: every function
here returns the same shape as the backend's /v2 API, so cli.py prints it the same way.

Only opencli *read* commands run (the ENGINES table, `web read`, `openalex work`), and
only as opencli adapters: they attach to a blank tab before navigating, never use a
visible tab group, and reuse warm tabs (OPENCLI_SITE_SLOTS). No page script is
evaluated. PDFs are fetched directly (urllib, no cookies) and turned into text with
pdftotext.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote, quote_plus, unquote, urljoin, urlparse

from . import API_VERSION, __version__
from .client import ApiError

OPENCLI_TIMEOUT = 120
# Warm tabs opencli keeps per site and reuses, instead of a tab (and window) per call.
SITE_SLOTS = "web:2,google:1,google-scholar:1,brave:1,dblp:1"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36"
PDF_MAX_BYTES = 60 * 1024 * 1024
WALL_TEXT_LIMIT = 4000  # longer pages are content, whatever words they contain

# Interstitials and refusals, as in the backend (backend/app/webkit_api/download.py).
HUMAN_MARKERS = ("recaptcha", "verify you are human", "unusual traffic", "checking if the site connection is secure",
                 "cf-challenge", "captcha", "just a moment", "are you a robot", "verifying your browser",
                 "checking your browser", "complete the check below", "attention required",
                 "enable javascript and cookies", "请稍候", "正在验证", "是否是真人", "cookies must be enabled",
                 "安全验证", "滑动验证", "security verification", "swipe right to complete verification",
                 "wanfangdata.com.cn/verify")  # CNKI, Baidu, Wanfang (its check is an iframe)
BLOCK_MARKERS = ("you have been blocked", "error 1020", "error 1005", "error 1006", "error 1009",
                 "the owner of this website has banned", "you don't have permission to access",
                 "the url you requested has been blocked")
INTERFERING = "another Chrome extension (Immersive Translate, Zotero, Scholar PDF Reader, ...)"


def err(error: str, message: str, hint: str = "", **extra) -> ApiError:
    body = {"error": error, "message": message, **({"hint": hint} if hint else {}), **extra}
    return ApiError(error, message, body=body)


# ─── opencli ───────────────────────────────────────────────────────────────────

def _opencli_exe(cfg) -> str:
    exe = cfg.opencli or shutil.which("opencli") or ""
    if not exe:
        raise err("unreachable", "opencli is not installed on this machine",
                  hint="npm install -g @jackwener/opencli, add its Chrome extension, then `webkit doctor`")
    return exe


def _run_opencli(cfg, args: list[str], timeout: float = OPENCLI_TIMEOUT, profile: bool = True):
    """(exit code, stdout, stderr) of one opencli call."""
    exe = _opencli_exe(cfg)
    env = dict(os.environ)
    if os.path.dirname(exe):  # opencli is a node script: find node next to it (nvm)
        env["PATH"] = os.path.dirname(exe) + os.pathsep + env.get("PATH", "")
    env.setdefault("OPENCLI_WINDOW", "background")
    env.setdefault("OPENCLI_SITE_SLOTS", SITE_SLOTS)
    if cfg.s2_api_key:
        env["SEMANTIC_SCHOLAR_API_KEY"] = cfg.s2_api_key
    prefix = ["--profile", cfg.profile] if profile and cfg.profile else []
    try:
        p = subprocess.run([exe, *prefix, *args], capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise err("timeout", f"opencli did not finish within {timeout:.0f}s: {' '.join(args[:2])}") from None
    except OSError as e:
        raise err("unreachable", f"cannot run opencli: {e}") from None
    return p.returncode, p.stdout, p.stderr


def envelope(stdout: str) -> dict | None:
    text = stdout.strip()
    for candidate in (text, text.splitlines()[-1] if text else ""):
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict) and "ok" in value:
            return value
    return None


def _opencli_error(what: str, code: int, env: dict | None) -> ApiError | None:
    """ApiError for a failed opencli envelope; None when it only says 'no results'
    (its browser adapters say that for CAPTCHA pages too: callers check the page)."""
    e = (env or {}).get("error") or {}
    ecode, emsg = str(e.get("code") or ""), " ".join(str(e.get("message") or "").split())[:240]
    msg = f"{what}: {emsg or f'opencli exit {code}'}"
    if ecode in ("EMPTY_RESULT", "NOT_FOUND") or code == 66:
        return None
    if code == 69 or ecode in ("BROWSER_CONNECT", "ADAPTER_LOAD"):
        return err("unreachable", msg, hint="open Chrome (the profile with the OpenCLI extension) on this machine; "
                                            "`webkit doctor` shows the connection")
    if ecode == "attach_failed" or "chrome-extension://" in emsg or "Cannot attach to this target" in emsg:
        return err("browser_conflict", msg, hint=f"{INTERFERING} is embedded in that page and blocks the browser "
                                                 "automation; turn it off for this site or read another source")
    if code == 75 or ecode in ("TIMEOUT", "SESSION_BUSY"):
        return err("busy", msg, hint="the browser is busy with other requests; retry in a minute")
    if code == 77 or ecode in ("AUTH_REQUIRED", "LOGIN_WALL"):
        return err("human_required", msg, hint="log in to the site in Chrome on this machine, then retry")
    return err("upstream_http", msg)


def wall_kind(text: str) -> str | None:
    head = text[:8192].lower()
    if any(m in head for m in BLOCK_MARKERS):
        return "blocked"
    if any(m in head for m in HUMAN_MARKERS):
        return "human"
    return None


def _wall_error(url: str, what: str, text: str) -> ApiError | None:
    kind = wall_kind(text) if len(text) < WALL_TEXT_LIMIT else None
    if kind == "blocked":
        return err("blocked", f"{what}: the site refuses this browser (block page instead of content)", url=url,
                   hint="do not retry: use another source, or report the URL as not accessible")
    if kind == "human":
        return err("human_required", f"{what}: the site shows a verification page", url=url,
                   hint="a person can pass it in Chrome on this machine; otherwise report it as not accessible")
    return None


# ─── web read ──────────────────────────────────────────────────────────────────

_SOURCE_LINE = re.compile(r"^>\s*(?:原文链接|Source|URL)\s*[:：]\s*(\S+)\s*$", re.M)


def web_read(cfg, url: str, wait_for: str | None = None, wait: int | None = None,
             timeout: float = OPENCLI_TIMEOUT) -> tuple[str, str, str]:
    """(title, final url, markdown) of a page rendered in this machine's Chrome."""
    args = ["web", "read", "--url", url, "--stdout", "true", "--download-images", "false"]
    if wait_for:
        args += ["--wait-for", wait_for]
    if wait is not None:
        args += ["--wait", str(wait)]
    code, stdout, _ = _run_opencli(cfg, args, timeout)
    if code != 0:
        raise (_opencli_error("read", code, envelope(stdout))
               or err("unexpected_page", f"read: the page rendered no content at {url[:120]}"))
    return split_header(stdout, url)


def split_header(md: str, url: str) -> tuple[str, str, str]:
    """opencli's markdown starts '# title', '> 原文链接: url', '---': return them apart."""
    lines = md.splitlines()
    title = lines[0][2:].strip() if lines and lines[0].startswith("# ") else ""
    head = "\n".join(lines[:6])
    m = _SOURCE_LINE.search(head)
    final = m.group(1) if m else url
    body = md
    sep = re.search(r"^---\s*$", md, re.M)
    if title and sep and sep.start() < len(head) + 1:
        body = md[sep.end():]
    if title in ("untitled", "Untitled"):
        title = ""
    return title, final, body.strip("\n")


# ─── search ────────────────────────────────────────────────────────────────────

def _result(title, url, snippet="", published="", **extra) -> dict:
    out = {"title": " ".join(str(title).split()), "url": str(url).strip(), "snippet": " ".join(str(snippet or "").split())}
    if published not in ("", None):
        out["published"] = str(published).strip()
    extra = {k: v for k, v in extra.items() if v not in ("", None, [], {})}
    if extra:
        out["extra"] = extra
    return out


def _authors(value) -> list[str]:
    if isinstance(value, list):
        return [str(a).strip() for a in value if str(a).strip()]
    return [a.strip() for a in re.split(r",|;", str(value or "")) if a.strip() and a.strip() not in ("…", "...")]


def _plain(r):
    return _result(r["title"], r["url"], r.get("snippet", ""))


def _google(r):
    return _plain(r) if r.get("type", "result") == "result" else None


def _scholar(r):
    byline = " - ".join(x for x in (str(r.get("authors") or "").strip(), str(r.get("source") or "").strip()) if x)
    return _result(r["title"], r["url"], "", r.get("year"), byline=byline, citations=r.get("cited"))


def _s2(r):
    return _result(r["title"], r["url"], "", r.get("year"), authors=_authors(r.get("firstAuthor")),
                   citations=r.get("citationCount"), doi=r.get("doi"))


def _openalex(r):
    return _result(r["title"], r["url"], "", r.get("year"), authors=_authors(r.get("firstAuthor")),
                   venue=r.get("venue"), citations=r.get("citations"), doi=r.get("doi"),
                   open_access=r.get("openAccess") or None)


def _arxiv(r):
    return _result(r["title"], r["url"], "", r.get("published"), authors=_authors(r.get("authors")),
                   category=r.get("primary_category"))


def _paper(venue_key: str, year_key: str = "year"):
    def make(r):
        return _result(r["title"], r["url"], "", r.get(year_key), authors=_authors(r.get("authors")),
                       venue=r.get(venue_key), doi=r.get("doi"), citations=r.get("cited"))
    return make


def _hn(r):
    return _result(r["title"], r["url"], "", score=r.get("score"), comments=r.get("comments"), author=r.get("author"))


def _so(r):
    return _result(r["title"], r["url"], "", score=r.get("score"), answers=r.get("answers"), tags=r.get("tags"))


@dataclass(frozen=True)
class Engine:
    command: str                         # opencli "<site> <command>": a read command
    limit: int                           # largest --limit it accepts
    row: Callable[[dict], dict | None]   # opencli row -> result
    search_url: str = ""                 # browser engines: page to check when they answer nothing
    retries: int = 0                     # retries on a rate-limit answer (429)


ENGINES: dict[str, Engine] = {
    "google": Engine("google search", 100, _google, "https://www.google.com/search?q={q}"),
    "brave": Engine("brave search", 18, _plain, "https://search.brave.com/search?q={q}"),
    "google_scholar": Engine("google-scholar search", 20, _scholar, "https://scholar.google.com/scholar?q={q}&hl=zh-CN"),
    # S2 answers 429 even with a key, and opencli retries only without one.
    "semantic_scholar": Engine("semanticscholar search", 100, _s2, retries=2),
    "openalex": Engine("openalex search", 200, _openalex),
    "arxiv": Engine("arxiv search", 25, _arxiv, retries=2),
    "pubmed": Engine("pubmed search", 100, _paper("journal")),
    "dblp": Engine("dblp search", 50, _paper("venue"), "https://dblp.org/search?q={q}"),
    # Chinese literature: browser engines behind slider / security checks a person passes in Chrome.
    "cnki": Engine("cnki search", 20, _paper("journal", "date"),
                   "https://oversea.cnki.net/kns/search?dbcode=CFLS&kw={q}&korder=SU"),
    "wanfang": Engine("wanfang search", 20, _paper("source"), "https://s.wanfangdata.com.cn/paper?q={q}"),
    "baidu_scholar": Engine("baidu-scholar search", 20, _paper("journal"), "https://xueshu.baidu.com/s?wd={q}"),
    "hackernews": Engine("hackernews search", 50, _hn),
    "stackoverflow": Engine("stackoverflow search", 50, _so),
}
PROFILES: dict[str, list[str]] = {
    "general": ["google", "brave"],
    "academic": ["semantic_scholar", "google_scholar", "openalex", "arxiv"],
    "community": ["hackernews", "stackoverflow"],
}


def run_engine(cfg, name: str, q: str, limit: int, lang: str | None = None) -> list[dict]:
    spec = ENGINES[name]
    args = [*spec.command.split(), "--limit", str(max(1, min(limit, spec.limit)))]
    if lang and name == "google":
        args += ["--lang", lang]
    for attempt in range(spec.retries + 1):
        code, stdout, stderr = _run_opencli(cfg, [*args, "-f", "json", "--", q])
        env = envelope(stdout)
        if attempt < spec.retries and env and not env.get("ok") and _rate_limited(env):
            time.sleep(1.5 * (attempt + 1))
            continue
        break
    if env is None:
        raise err("upstream_http", f"{name}: opencli printed no JSON (exit {code}): {stderr.strip()[-200:]}")
    if env.get("ok"):
        rows = [r for r in env.get("data") or [] if isinstance(r, dict)]
        results, seen = [], set()
        for r in rows:
            if not r.get("title") or not str(r.get("url") or "").startswith("http"):
                continue
            item = spec.row(r)
            if item and item["url"] not in seen:
                seen.add(item["url"])
                results.append(item)
            if len(results) >= limit:
                break
        if results:
            return results
        if rows:
            raise err("unexpected_page", f"{name}: {len(rows)} rows, none with a title and URL")
    elif (e := _opencli_error(name, code, env)) is not None:
        raise e
    if spec.search_url:  # a browser engine's "no results" may be a CAPTCHA page: look at it
        url = spec.search_url.format(q=quote_plus(q))
        _, _, text = web_read(cfg, url)
        wall = _wall_error(url, name, text)
        if wall:
            raise wall
    return []


def _rate_limited(env: dict) -> bool:
    msg = str((env.get("error") or {}).get("message") or "").lower()
    return "429" in msg or "rate limit" in msg


def search(cfg, params: dict) -> dict:
    q = str(params.get("q") or "").strip()
    limit = int(params.get("limit") or 10)
    time_range, lang, engine = params.get("time"), params.get("lang"), params.get("engine")
    profile = params.get("profile") or "general"
    if not q:
        raise err("invalid_request", "empty query")
    if engine:
        if engine not in ENGINES:
            raise err("invalid_request", f"unknown engine '{engine}'", hint="see `webkit engines`")
        chain = [engine]
    elif profile in PROFILES:
        chain = PROFILES[profile]
    else:
        raise err("invalid_request", f"unknown profile '{profile}'", hint=f"profiles: {', '.join(PROFILES)}")
    report: list[dict] = []
    results: list[dict] = []
    for name in chain:
        if results:
            report.append({"engine": name, "status": "skipped"})
            continue
        if time_range:
            report.append({"engine": name, "status": "skipped", "reason": f"no '{time_range}' time filter"})
            continue
        t0 = time.monotonic()
        try:
            found = run_engine(cfg, name, q, limit, lang)
        except ApiError as e:
            report.append({"engine": name, "status": "error", "error": e.error, "message": e.message,
                           "ms": int((time.monotonic() - t0) * 1000)})
            continue
        report.append({"engine": name, "status": "ok" if found else "empty", "count": len(found),
                       "ms": int((time.monotonic() - t0) * 1000)})
        for r in found:
            r["engine"] = name
        results = found
    tried = [r for r in report if r["status"] != "skipped"]
    if not results and tried and all(r["status"] == "error" for r in tried):
        classes = sorted({r["error"] for r in tried})
        hint = ("open Chrome (the profile with the OpenCLI extension) on this machine; `webkit doctor`"
                if "unreachable" in classes else
                "anti-bot checks stopped every engine: use WebSearch or another profile"
                if set(classes) & {"captcha", "blocked", "human_required"} else "see `webkit doctor`")
        raise err("engines_failed", "every engine failed: " + "; ".join(f"{r['engine']}={r['error']}" for r in tried),
                  hint=hint, engines=report, classes=classes)
    for i, r in enumerate(results, 1):
        r["rank"] = i
    return {"query": q, "profile": None if engine else profile, "time": time_range, "engines": report,
            "fallback_used": len(tried) > 1 and bool(results), "results": results}


def engines() -> dict:
    membership: dict[str, list[str]] = {}
    for prof, chain in PROFILES.items():
        for e in chain:
            membership.setdefault(e, []).append(prof)
    return {"profiles": PROFILES, "engines": [
        {"engine": n, "kind": "browser" if s.search_url else "api", "time": [], "profiles": membership.get(n, [])}
        for n, s in ENGINES.items()]}


# ─── files and PDFs ────────────────────────────────────────────────────────────

_ARXIV_ABS = re.compile(r"^https?://(?:www\.)?arxiv\.org/abs/([^?#]+)")
_PDF_META = re.compile(r"""<meta[^>]+name=["']citation_pdf_url["'][^>]+content=["']([^"']+)["']""", re.I)
_PDF_META_REV = re.compile(r"""<meta[^>]+content=["']([^"']+)["'][^>]+name=["']citation_pdf_url["']""", re.I)


def looks_pdf(url: str) -> bool:
    u = urlparse(url)
    return (u.path.lower().endswith(".pdf") or (u.netloc.endswith("arxiv.org") and u.path.startswith("/pdf/"))
            or (u.netloc.endswith("openreview.net") and u.path.startswith("/pdf")))


def fetch(url: str, max_bytes: int, accept: str = "*/*") -> tuple[str, str, bytes, dict]:
    """(final url, content type, body, headers) of a plain GET: no browser, no cookies."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise err("too_large", f"{url} is larger than {max_bytes // (1 << 20)} MB")
            return r.geturl(), r.headers.get("Content-Type", ""), data, dict(r.headers)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise err("blocked", f"{url} answered HTTP {e.code}", url=url,
                      hint="it needs a login or refuses scripts; downloads with browser cookies are not "
                           "supported locally yet: use an open-access copy (arXiv, PMC, the author's page)") from None
        if e.code == 404:
            raise err("not_found", f"{url} answered HTTP 404", url=url) from None
        if e.code == 429:
            raise err("busy", f"{url} answered HTTP 429 (rate limited)", hint="retry in a minute") from None
        raise err("upstream_http", f"{url} answered HTTP {e.code}", status_code=e.code) from None
    except urllib.error.URLError as e:
        raise err("network", f"cannot reach {url}: {e.reason}") from None


def pdf_to_text(data: bytes) -> tuple[str, str, int]:
    """(title, text with '<!-- page N -->' markers, pages) via pdftotext."""
    exe = shutil.which("pdftotext")
    if not exe:
        raise err("internal", "pdftotext is not installed", hint="sudo apt install poppler-utils")
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(data)
        f.flush()
        p = subprocess.run([exe, "-enc", "UTF-8", f.name, "-"], capture_output=True, timeout=120)
    if p.returncode != 0:
        raise err("unexpected_page", f"pdftotext failed: {p.stderr.decode('utf-8', 'replace').strip()[:200]}")
    pages = p.stdout.decode("utf-8", "replace").split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    text = "\n\n".join(f"<!-- page {i} -->\n{pg.strip()}" for i, pg in enumerate(pages, 1))
    title = next((ln.strip() for pg in pages for ln in pg.splitlines() if ln.strip()), "")
    return title[:200], text, len(pages)


def resolve_pdf(cfg, url: str) -> str | None:
    """The PDF behind a paper page, DOI or arXiv abstract, found without a browser."""
    if looks_pdf(url):
        return url
    if m := _ARXIV_ABS.match(url):
        return f"https://arxiv.org/pdf/{m.group(1)}"
    u = urlparse(url)
    if u.netloc.endswith("doi.org"):
        code, stdout, _ = _run_opencli(cfg, ["openalex", "work", "-f", "json", "--", unquote(u.path.lstrip("/"))], 60)
        env = envelope(stdout) or {}
        data = env.get("data")
        row = (data[0] if isinstance(data, list) and data else data) if env.get("ok") else None
        oa = (row or {}).get("openAccessUrl") if isinstance(row, dict) else None
        if oa:
            return oa
    try:
        final, ctype, body, _ = fetch(url, 8 * 1024 * 1024, "text/html,*/*")
    except ApiError:
        return None
    if "pdf" in ctype:
        return final
    html = body.decode("utf-8", "replace")
    m = _PDF_META.search(html) or _PDF_META_REV.search(html)
    return urljoin(final, m.group(1)) if m else None


def read_pdf(url: str, pdf_url: str, fmt: str) -> dict:
    final, ctype, data, _ = fetch(pdf_url, PDF_MAX_BYTES, "application/pdf,*/*")
    if not data.startswith(b"%PDF-"):
        raise err("not_a_file", f"{pdf_url} did not return a PDF ({ctype or 'unknown type'})", url=url,
                  hint="the publisher may need a login (not supported locally yet); read the page itself instead")
    title, text, pages = pdf_to_text(data)
    out = {"url": url, "final_url": final, "title": title, "kind": "pdf", "format": fmt, "pages": pages}
    if pdf_url != url:
        out["pdf_url"] = pdf_url
    if fmt == "links":
        out["links"] = []
    else:
        out["content"] = text
    return out


# ─── /v2/page and /v2/download equivalents ─────────────────────────────────────

_MD_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")


def page(cfg, body: dict) -> dict:
    url, fmt = str(body.get("url") or ""), body.get("format") or "fit"
    if urlparse(url).scheme not in ("http", "https"):
        raise err("invalid_request", "only http(s) URLs can be read")
    if fmt == "html":
        raise err("invalid_request", "-f html is not available in local mode", hint="use -f md (the page as Markdown)")
    if body.get("pdf"):
        pdf_url = resolve_pdf(cfg, url)
        if not pdf_url:
            raise err("not_found", "found no PDF for this page (no arXiv/DOI open-access copy, no citation_pdf_url)",
                      url=url, hint="read the page itself (without --pdf)")
        return read_pdf(url, pdf_url, fmt)
    if looks_pdf(url):
        return read_pdf(url, url, fmt)
    wait_for = body.get("wait_for")
    timeout = float(body.get("timeout") or 60) + 30
    if wait_for and str(wait_for).isdigit():
        title, final, text = web_read(cfg, url, wait=max(1, round(int(wait_for) / 1000)), timeout=timeout)
    else:
        title, final, text = web_read(cfg, url, wait_for=(wait_for or "").removeprefix("css:") or None, timeout=timeout)
    wall = _wall_error(url, "read", title + "\n" + text)
    if wall:
        raise wall
    if not text.strip():
        raise err("unexpected_page", f"the page rendered no text at {url[:120]}",
                  hint="it may load its content late: retry with --wait-for CSS (or milliseconds)")
    out = {"url": url, "final_url": final, "title": title, "kind": "html", "format": fmt}
    if fmt == "links":
        out["links"] = [{"href": h, "text": t.strip(), "scope": "internal" if urlparse(h).netloc == urlparse(final).netloc
                         else "external"} for t, h in _MD_LINK.findall(text)]
    elif fmt == "json":
        out["content"] = text
        out["metadata"] = {"title": title}
    else:
        out["content"] = text
    return out


class Download:
    """What cli._download_once reads from a streamed backend response."""

    def __init__(self, data: bytes, headers: dict):
        self._buf, self.headers = io.BytesIO(data), headers

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _filename(final: str, headers: dict, ctype: str) -> str:
    cd = headers.get("Content-Disposition") or headers.get("content-disposition") or ""
    m = re.search(r"""filename\*?=(?:UTF-8'')?["']?([^"';]+)""", cd, re.I)
    name = unquote(m.group(1)) if m else os.path.basename(urlparse(final).path) or "download"
    name = re.sub(r"[^\w.\-]+", "_", name).strip("._") or "download"
    if "pdf" in ctype and not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name


def download(cfg, params: dict) -> Download:
    url = str(params.get("url") or "")
    max_bytes = int(params.get("max_mb") or 500) * 1024 * 1024
    allow_html = bool(params.get("allow_html"))
    target = url if looks_pdf(url) or allow_html else (resolve_pdf(cfg, url) or url)
    final, ctype, data, headers = fetch(target, max_bytes)
    if "html" in ctype.lower() and not allow_html:
        raise err("not_a_file", "that URL is a web page, and no PDF was found behind it", url=url,
                  hint="`webkit read` gives its text; --allow-html saves the HTML itself")
    if not data:
        raise err("upstream_http", "the site sent an empty file; nothing saved", url=url)
    out = {"X-Webkit-Filename": quote(_filename(final, headers, ctype)), "Content-Length": str(len(data)),
           "Content-Type": ctype, "X-Webkit-Final-Url": quote(final)}
    if target != url:
        out["X-Webkit-Resolved-From"] = quote(url)
    return Download(data, out)


# ─── status ────────────────────────────────────────────────────────────────────

def status(cfg) -> dict:
    out = {"mode": "local", "webkit": __version__, "api": API_VERSION, "opencli": None, "extension_connected": False,
           "profiles": [], "profile": cfg.profile or None, "pdftotext": bool(shutil.which("pdftotext")),
           "s2_api_key": bool(cfg.s2_api_key), "error": None}
    try:
        code, stdout, _ = _run_opencli(cfg, ["--version"], 20, profile=False)
        out["opencli"] = stdout.strip().splitlines()[-1] if code == 0 and stdout.strip() else None
        env = envelope(_run_opencli(cfg, ["profile", "list"], 30, profile=False)[1]) or {}
    except ApiError as e:
        out["error"] = e.message
        return out
    profiles = ((env.get("data") or {}).get("profiles")) or []
    out["profiles"] = [{"alias": p.get("alias"), "context": p.get("contextId"), "connected": bool(p.get("connected")),
                        "default": bool(p.get("default"))} for p in profiles]
    want = [p for p in out["profiles"] if not cfg.profile or cfg.profile in (p["alias"], p["context"])]
    out["extension_connected"] = any(p["connected"] for p in want)
    if not out["extension_connected"]:
        out["error"] = ("the OpenCLI extension is not connected" + (f" for profile {cfg.profile}" if cfg.profile else "")
                        + ": open Chrome on this machine")
    return out


class LocalClient:
    """Stands in for client.Client: the same paths, answered on this machine."""

    local = True

    def __init__(self, cfg, timeout: float = 120.0):
        self.cfg, self.timeout = cfg, timeout

    def get_json(self, path: str, params: dict | None = None, admin: bool = False, timeout: float | None = None) -> dict:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        if path == "/v2/search":
            return search(self.cfg, params)
        if path == "/v2/engines":
            return engines()
        if path == "/v2/status":
            return status(self.cfg)
        if path == "/v2/version":
            return {"webkit": __version__, "api": API_VERSION, "mode": "local"}
        raise err("invalid_request", f"{path} is not available in local mode")

    def post_json(self, path: str, body: dict, timeout: float | None = None) -> dict:
        if path == "/v2/page":
            return page(self.cfg, body)
        raise err("invalid_request", f"{path.rsplit('/', 1)[-1]} is not available in local mode",
                  hint="read pages one by one with `webkit read`")

    def stream(self, path: str, params: dict, timeout: float | None = None):
        if path == "/v2/download":
            return download(self.cfg, params)
        raise err("invalid_request", f"{path} is not available in local mode")
