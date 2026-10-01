"""webkit — fallback web access for agents, through a real, logged-in Chrome on a
server (web-kit v2 backend).

Output is compact text for agents and humans; --json changes the format, never the
amount. Every command prints at most --max-chars characters (default 4000): page text
beyond that is saved to a file and only its path is printed. Errors go to stderr as one
JSON line: {"error": <class>, "message": ..., "hint": ...}; the hint says what to do next.

Exit codes:
  0 ok (possibly after engine fallback; the header line says which engine answered)
  1 usage error            2 no results (engines healthy)
  3 failed (engines / network / upstream / timeout / not a file): retry once at most
  4 auth (missing or wrong key)        5 backend unreachable
  6 a person is needed (CAPTCHA / login): do not retry; report the URL as not accessible
  7 busy (all browser pages in use): retry in a minute
  8 blocked (the site refuses this server): do not retry; use another source
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import API_VERSION, __version__, config
from .client import (EXIT_FAILED, EXIT_NO_RESULTS, EXIT_OK, EXIT_USAGE, ApiError, Client)

PROFILES = ("general", "academic", "code", "community")
TIME_RANGES = ("day", "week", "month", "year")
DEFAULT_BUDGET = 4000   # chars a content command prints (search / read / crawl / download)
MAX_BUDGET = 20000      # hard ceiling for --max-chars; full text only ever goes to files
DIAG_BUDGET = 20000     # status / doctor / engines
NOTE_RESERVE = 300      # room kept for each "saved to ..." line
FINAL_RESERVE = 300     # room kept for the closing "more output in FILE" line
MIN_BUDGET = 600        # below this the closing line itself would not fit
ERR_LINE_MAX = 1200


# ─── output: one budget per command ───────────────────────────────────────────

class Output:
    """Everything a command prints (stdout and stderr) goes through here, under one
    budget. Whole lines only: a line that does not fit, and everything after it, goes to
    a spill file (which also repeats what was shown), and one closing line, paid for from
    a small reserve inside the budget, says where."""

    def __init__(self) -> None:
        self.configure(None, "webkit")

    def configure(self, limit: int | None, cmd: str) -> None:
        self.limit, self.used, self.cmd = limit, 0, cmd
        self._shown: list[str] = []
        self._spill = None
        self.spill_path: Path | None = None
        self.spilled = self.spilled_errors = 0
        self.hints_seen: set[str] = set()

    def remaining(self) -> int:
        """Room left for ordinary lines (the closing line's reserve excluded)."""
        if self.limit is None:
            return 10**12
        return max(0, self.limit - FINAL_RESERVE - self.used)

    def write(self, text: str, stream=None) -> None:
        stream = stream or sys.stdout
        if not text.endswith("\n"):
            text += "\n"
        if self._spill is None and len(text) <= self.remaining():
            stream.write(text)
            self.used += len(text)
            if self.limit is not None:
                self._shown.append(text)
            return
        if self._spill is None:
            self.spill_path = _cache_file("output", f"{self.cmd}-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}",
                                          ".txt")
            self._spill = self.spill_path.open("w", encoding="utf-8")
            self._spill.write("".join(self._shown))
        self._spill.write(text)
        self.spilled += text.count("\n")
        if stream is sys.stderr:
            self.spilled_errors += 1

    def close(self) -> None:
        if self._spill is None:
            return
        self._spill.close()
        self._spill = None
        errors = f", {self.spilled_errors} of them errors" if self.spilled_errors else ""
        sys.stdout.write(f"[{self.spilled} more lines{errors} not shown (output budget {self.limit}); all output is "
                         f"in {self.spill_path}: search it with grep -n -m 10 'term' FILE | cut -c1-200]\n")


OUT = Output()


def out(text: str = "") -> None:
    OUT.write(text)


def err_json(body: dict) -> None:
    """One JSON line on stderr, inside the command's budget. A hint already shown in this
    command is not repeated."""
    body = dict(body)
    hint = body.get("hint")
    if hint and hint in OUT.hints_seen:
        body["hint"] = "(same as above)"
    elif hint:
        OUT.hints_seen.add(hint)
    line = json.dumps(body, ensure_ascii=False)
    if len(line) > ERR_LINE_MAX:
        slim = {k: (v[:400] if isinstance(v, str) else v) for k, v in body.items()
                if k in ("error", "message", "hint", "url", "status", "vnc", "classes")}
        line = json.dumps(slim, ensure_ascii=False)[:ERR_LINE_MAX]
    OUT.write(line, stream=sys.stderr)


def print_json(data, summary: dict | None = None, shrink=()) -> None:
    """JSON that fits the budget is printed as is. Larger JSON is saved to a file, then
    printed compact, then through each `shrink` step (e.g. shorter snippets) with a
    "full_json" pointer; if nothing fits, a small valid stub points at the file."""
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if len(text) + 1 <= OUT.remaining():
        out(text)
        return
    path = _cache_file("output", f"{OUT.cmd}-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}", ".json")
    path.write_text(text, encoding="utf-8")
    candidate = data
    for step in (None, *shrink):
        if step is not None:
            candidate = step(candidate)
        marked = {**candidate, "full_json": str(path)} if isinstance(candidate, dict) else candidate
        compact = json.dumps(marked, ensure_ascii=False, separators=(",", ":"))
        if len(compact) + 1 <= OUT.remaining():
            out(compact)
            return
    out(json.dumps({"truncated": True, "saved_to": str(path), "chars": len(text), "summary": summary or {}},
                   ensure_ascii=False))


def _snippets(limit: int):
    """A shrink step for search JSON: snippets cut to `limit` chars (0 drops them)."""
    def step(data: dict) -> dict:
        results = []
        for r in data.get("results", []):
            short = {k: v for k, v in r.items() if k != "snippet"}
            if limit and r.get("snippet"):
                short["snippet"] = r["snippet"][:limit]
            results.append(short)
        return {**data, "results": results}
    return step


def _tilde(path: Path) -> str:
    """Paths under $HOME as ~/..., so generated help (and the skill) carry no username."""
    home = str(Path.home())
    return "~" + str(path)[len(home):] if str(path).startswith(home + os.sep) else str(path)


def _cache_file(kind: str, stem: str, ext: str) -> Path:
    d = config.cache_dir() / kind
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{stem}{ext}"


def _budget(args) -> int | None:
    default = getattr(args, "default_budget", None)
    asked = getattr(args, "max_chars", None)
    if default is None:
        return None
    return max(MIN_BUDGET, min(asked if asked and asked > 0 else default, MAX_BUDGET))


def engines_header(report: list[dict]) -> str:
    """Fixed grammar, part of the CLI contract:
    '# engines <name>=<ok:N|empty|error:CLASS|skipped> ...'"""
    parts = []
    for e in report:
        st = e.get("status")
        if st == "ok":
            parts.append(f"{e['engine']}=ok:{e.get('count', 0)}")
        elif st == "error":
            parts.append(f"{e['engine']}=error:{e.get('error', 'unknown')}")
        else:
            parts.append(f"{e['engine']}={st}")
    return "# engines " + " ".join(parts)


def slugify(text: str, fallback: str = "page") -> str:
    s = re.sub(r"[^\w\-]+", "-", unquote(text or ""), flags=re.UNICODE).strip("-").lower()
    return (s[:60] or fallback).strip("-") or fallback


_DOI = re.compile(r"^(?:doi:\s*)?(10\.\d{4,9}/\S+)$", re.I)
_ARXIV = re.compile(r"^arxiv:\s*([a-z\-]+(?:\.[a-z]{2})?/\d{7}|\d{4}\.\d{4,5})(v\d+)?$", re.I)


def normalize_target(text: str) -> str:
    """URLs pass through; a DOI (10.x/y, doi:10.x/y) becomes https://doi.org/..., an
    arXiv ID (arXiv:2403.01092) its abstract page."""
    t = text.strip()
    if m := _DOI.match(t):
        return "https://doi.org/" + m.group(1)
    if m := _ARXIV.match(t):
        return f"https://arxiv.org/abs/{m.group(1)}{m.group(2) or ''}"
    return t


def _ext(fmt: str) -> str:
    return {"html": ".html", "json": ".json", "links": ".tsv"}.get(fmt, ".md")


# ─── commands ──────────────────────────────────────────────────────────────────

def cmd_search(args, client: Client) -> int:
    params = {"q": " ".join(args.query), "limit": args.limit, "time": args.time, "lang": args.lang}
    if args.engine:
        params["engine"] = args.engine
    else:
        params["profile"] = args.profile
    data = client.get_json("/v2/search", params, timeout=args.timeout or 150)
    results = data.get("results", [])
    code = EXIT_OK if results else EXIT_NO_RESULTS
    targets = [r["url"] for r in results[: args.read or 0]]
    if args.json:
        if targets:  # pages go to files; JSON lists them, never their text
            read_code, data["pages"] = _read_many(client, targets, "fit", args.output, 60, as_json=True,
                                                  to_files=True)
            code = code or read_code
        print_json(data, summary={"results": len(results)}, shrink=(_snippets(100), _snippets(0)))
        return code

    header = engines_header(data.get("engines", []))
    if args.urls:
        OUT.write(header, stream=sys.stderr)
        for r in results:
            out(r["url"])
    else:
        out(header)
        for r in results:
            line = f"{r['rank']}. {r['title']}"
            if r.get("published"):
                line += f"  ({r['published']})"
            out(line)
            out(f"   {r['url']}")
            if r.get("snippet"):
                snippet = r["snippet"]
                out("   " + (snippet[:200] + "…" if len(snippet) > 200 else snippet))
    if targets:
        code = _read_many(client, targets, "fit", args.output, 60, to_files=True)[0] or code
    return code


def _page(client: Client, url: str, fmt: str, timeout: int, wait_for=None, scroll=False, cache=False,
          pdf=False) -> dict:
    body = {"url": url, "format": fmt, "max_chars": 0, "timeout": timeout,
            "wait_for": wait_for, "scroll": scroll, "cache": cache}
    if pdf:
        body["pdf"] = True
    return client.post_json("/v2/page", {k: v for k, v in body.items() if v is not None},
                            timeout=timeout + 45)


def _page_text(page: dict) -> str:
    if page.get("format") == "links":
        return "\n".join(f"{l['href']}\t{l.get('text', '')}" for l in page.get("links", []))
    if page.get("format") == "json":
        return json.dumps(page, ensure_ascii=False, indent=2)
    return page.get("content", "")


def _page_path(output: str | None, single_file: bool, i: int, url: str, page: dict, fmt: str) -> Path:
    if output and single_file:
        return Path(output)
    name = slugify(page.get("title") or urlparse(url).path.rsplit("/", 1)[-1] or urlparse(url).netloc)
    if output:
        return Path(output) / f"{i:02d}-{name}{_ext(fmt)}"
    digest = hashlib.sha1(f"{url}|{fmt}".encode()).hexdigest()[:8]
    return _cache_file("pages", f"{name}-{digest}", _ext(fmt))


def _cut(text: str, limit: int) -> str:
    """At most `limit` chars, ending at a line break when one is reasonably close."""
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit)
    return text[: cut if cut > limit // 2 else limit]


def _read_many(client: Client, urls: list[str], fmt: str, output: str | None, timeout: int,
               as_json: bool = False, to_files: bool = False, **page_kw) -> tuple[int, list[dict]]:
    """Read pages under the command's output budget. With -o (or to_files) every page goes
    to a file and only a manifest is printed. Otherwise a page that fits the budget is
    printed and a longer one is saved to the cache with a preview. Returns (exit code,
    one manifest entry per page); with as_json nothing is printed and the caller prints
    the entries."""
    code = EXIT_OK
    single_file = bool(output) and len(urls) == 1 and not output.endswith("/") and not Path(output).is_dir()
    to_files = to_files or bool(output)
    pages = []
    for i, url in enumerate(urls, 1):
        try:
            page = _page(client, url, fmt, timeout, **page_kw)
        except ApiError as e:
            err_json({**e.body, "url": url})
            code = code or e.exit_code
            continue
        pages.append((i, url, page, _page_text(page)))

    entries = []
    for k, (i, url, page, text) in enumerate(pages):
        title = page.get("title") or ""
        meta = {"url": url, **{f: page[f] for f in ("final_url", "title", "kind", "pages", "pdf_url")
                               if page.get(f) not in (None, "")}, "chars": len(text)}
        if to_files:
            path = _page_path(output, single_file, i, url, page, fmt)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            meta["path"] = str(path)
            if not as_json:
                out(f"saved {path} ({len(text)} chars) {title}".rstrip())
            entries.append(meta)
            continue
        room = max(0, OUT.remaining() - NOTE_RESERVE * (len(pages) - k))
        header = (f"--- [{i}] {url}" + (f"  {title}" if title else "")) if len(urls) > 1 else ""
        if as_json:
            room = int(max(0, room - len(json.dumps(meta, ensure_ascii=False)) - 80) * 0.9)  # escaping
        if len(text) + len(header) + 2 <= room:
            if as_json:
                meta["content"] = text
            else:
                if header:
                    out(header)
                out(text)
        else:
            path = _page_path(None, False, i, url, page, fmt)
            path.write_text(text, encoding="utf-8")
            preview = _cut(text, max(0, room - len(header) - 2))
            meta.update(content=preview, truncated=True, path=str(path))
            if not as_json:
                if header:
                    out(header)
                if preview:
                    out(preview)
                out(f"[{len(preview)} of {len(text)} chars shown; the full text is in {path}: locate what you "
                    "need with grep -n -m 10 'term' FILE | cut -c1-200, then read only those lines]")
        entries.append(meta)
    return code, entries


def cmd_read(args, client: Client) -> int:
    urls = [normalize_target(u) for u in args.urls]
    code, entries = _read_many(client, urls, args.format, args.output, args.timeout or 60, as_json=args.json,
                               wait_for=args.wait_for, scroll=args.scroll, cache=args.cache, pdf=args.pdf)
    if args.json:
        print_json(entries if len(urls) > 1 else (entries[0] if entries else {}), summary={"pages": len(entries)})
    return code


def cmd_crawl(args, client: Client) -> int:
    body = {"url": args.url, "strategy": args.strategy, "max_depth": args.max_depth,
            "max_pages": args.max_pages, "include": args.include or [], "exclude": args.exclude or [],
            "format": args.format, "timeout": args.timeout or 180}
    data = client.post_json("/v2/crawl", body, timeout=body["timeout"] + 60)
    outdir = Path(args.output)
    outdir.mkdir(parents=True, exist_ok=True)
    index = []
    for i, page in enumerate(data.get("pages", []), 1):
        entry = {k: page.get(k) for k in ("url", "depth", "title")}
        if "content" in page:
            name = f"{i:02d}-{slugify(page.get('title') or urlparse(page['url']).path.rsplit('/', 1)[-1] or 'index')}.md"
            (outdir / name).write_text(page["content"], encoding="utf-8")
            entry.update(file=name, chars=len(page["content"]))
        else:
            entry.update(error=page.get("error"), message=page.get("message"))
        index.append(entry)
    (outdir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print_json({"url": data.get("url"), "count": data.get("count", 0), "dir": str(outdir), "pages": index},
                   summary={"count": data.get("count", 0), "dir": str(outdir)})
    else:
        for e in index:
            out(f"saved {outdir / e['file']} ({e['chars']} chars) {e['url']}" if "file" in e
                else f"failed {e['url']}: {e.get('error')}")
        out(f"# crawled {data.get('count', 0)} pages -> {outdir}/index.json")
    return EXIT_OK if any("file" in e for e in index) else EXIT_FAILED


def _download_once(client: Client, url: str, dest: Path | None, max_mb: int, allow_html: bool = False,
                   as_json: bool = False) -> Path:
    resp = client.stream("/v2/download", {"url": url, "max_mb": max_mb or None, "allow_html": allow_html},
                         timeout=180)
    with resp:
        name = unquote(resp.headers.get("X-Webkit-Filename") or "") or "download"
        target = dest if dest is not None and not (dest.is_dir() or str(dest).endswith("/")) \
            else (dest or config.download_dir()) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        expected = resp.headers.get("Content-Length")
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".webkit-", suffix=".part")
        size = 0
        try:
            with os.fdopen(fd, "wb") as f:
                while chunk := resp.read(1 << 20):
                    f.write(chunk)
                    size += len(chunk)
            if size == 0:
                raise ApiError("upstream_http", "the backend delivered an empty file; nothing saved")
            if expected and int(expected) != size:
                raise ApiError("network", f"download truncated: got {size} of {expected} bytes")
            os.replace(tmp, target)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        ctype = resp.headers.get("Content-Type", "")
        landing = unquote(resp.headers.get("X-Webkit-Resolved-From") or "")
        final = unquote(resp.headers.get("X-Webkit-Final-Url") or "")
    if as_json:
        print_json({"path": str(target), "bytes": size, "content_type": ctype, "final_url": final,
                    **({"resolved_from": landing} if landing else {})})
    else:
        note = f"; the PDF linked from the page: {final}" if landing else ""
        out(f"saved {target} ({size} bytes, {ctype}{note})")
    return target


def cmd_download(args, client: Client) -> int:
    dest = Path(args.output).expanduser() if args.output else None
    url = normalize_target(args.url)
    deadline = time.monotonic() + args.wait_timeout
    opened = False
    while True:
        try:
            _download_once(client, url, dest, args.max_mb, args.allow_html, args.json)
            return EXIT_OK
        except ApiError as e:
            if e.error != "human_required" or not args.wait_human:
                if e.error == "human_required":
                    e.body.setdefault("vnc", client.cfg.vnc)
                raise
            if not opened:
                _open_for_human(client, url)
                opened = True
            if time.monotonic() > deadline:
                raise ApiError("human_required", f"still blocked after {args.wait_timeout}s",
                               body={**e.body, "vnc": client.cfg.vnc}) from None
            time.sleep(10)


def _open_for_human(client: Client, url: str) -> None:
    if client.cfg.admin_key:
        try:
            client.get_json("/v2/admin/open", {"url": url}, admin=True, timeout=60)
        except ApiError as e:
            err_json(e.body)
    OUT.write(f"human action needed: open {client.cfg.vnc} (user webkit, password = admin key), "
              f"solve the page for {url}; retrying every 10s", stream=sys.stderr)


def cmd_status(args, client: Client) -> int:
    data = client.get_json("/v2/status", timeout=args.timeout or 30)
    if args.json:
        print_json(data)
        return EXIT_OK if data.get("ok") else EXIT_FAILED
    v, b = data.get("version", {}), data.get("browser", {})
    out(f"web-kit {v.get('webkit')} (api {v.get('api')}, image {v.get('image')}, chrome {v.get('chrome')}, "
        f"searxng {str(v.get('searxng_ref'))[:7]})")
    out(f"browser: {'connected' if b.get('connected') else 'DISCONNECTED'}, pages {b.get('pages_in_use')}/"
        f"{b.get('max_pages')} in use, {b.get('waiting')} waiting")
    egress = data.get("egress", {})
    down = data.get("egress_down", [])
    if not egress:
        out("egress: not probed yet")
    elif down:
        out("egress: DOWN " + ", ".join(f"{h} ({egress[h].get('error')})" for h in down)
            + f"; ok {len(egress) - len(down)}/{len(egress)}")
    else:
        out(f"egress: ok ({len(egress)}/{len(egress)} hosts)")
    engines = data.get("engines", {})
    if engines:
        out("engines (since backend start):")
        for name, st in engines.items():
            line = f"  {name}: {st['state']} ({st['errors']}/{st['calls']} errors)"
            if st.get("last_error") and st["state"] != "ok":
                le = st["last_error"]
                line += f", last error {le['class']} at {le['at']}"
            out(line)
    sx = data.get("searxng", {})
    line = f"searxng: {'healthy' if sx.get('healthy') else 'UNHEALTHY'}"
    if sx.get("engine_errors"):
        line += "; api engine errors: " + ", ".join(
            f"{k} {v.get('error_rate')}% ({v.get('error')})" for k, v in sx["engine_errors"].items())
    out(line)
    return EXIT_OK if data.get("ok") else EXIT_FAILED


def cmd_engines(args, client: Client) -> int:
    data = client.get_json("/v2/engines")
    if args.json:
        print_json(data)
        return EXIT_OK
    out("profiles (fallback order):")
    for prof, chain in data["profiles"].items():
        out(f"  {prof}: {' -> '.join(chain)}")
    out("engines:")
    for e in data["engines"]:
        time_s = ",".join(e["time"]) if e["time"] else "-"
        out(f"  {e['engine']:<17} {e['kind']:<8} time={time_s:<20} {' '.join(e['profiles'])}".rstrip())
    return EXIT_OK


def cmd_doctor(args, client: Client) -> int:
    cfg = client.cfg
    checks: list[tuple[bool, str]] = []
    out(f"config file: {config.config_path()}")
    out(f"url: {cfg.url}  [{cfg.sources.get('url')}]")
    out(f"api key: {config.mask(cfg.api_key)}  [{cfg.sources.get('api_key')}]")
    out(f"admin key: {config.mask(cfg.admin_key)}  [{cfg.sources.get('admin_key')}]")
    code = EXIT_OK
    try:
        v = client.get_json("/v2/version", timeout=15)
        checks.append((True, f"backend reachable, authenticated: web-kit {v.get('webkit')}"))
        if v.get("api") != API_VERSION:
            checks.append((False, f"API version mismatch: backend {v.get('api')}, CLI {API_VERSION}"))
            code = code or EXIT_FAILED
        elif str(v.get("webkit", "")).split(".")[0] != __version__.split(".")[0]:
            checks.append((False, f"major version differs: backend {v.get('webkit')}, CLI {__version__}"))
    except ApiError as e:
        checks.append((False, f"{e.error}: {e.message}" + (f" — {e.body.get('hint')}" if e.body.get("hint") else "")))
        code = e.exit_code
    if code == EXIT_OK:
        try:
            st = client.get_json("/v2/status", timeout=30)
            b = st.get("browser", {})
            checks.append((bool(b.get("connected")), "browser connected" if b.get("connected") else "browser DISCONNECTED"))
            down = st.get("egress_down", [])
            checks.append((not down, "egress ok" if not down else "egress DOWN: " + ", ".join(down)))
            bad = [n for n, s in st.get("engines", {}).items() if s.get("state") == "down"]
            checks.append((not bad, "no engine down" if not bad else "engines down: " + ", ".join(bad)))
            if not all(ok for ok, _ in checks):
                code = EXIT_FAILED
        except ApiError as e:
            checks.append((False, f"status failed: {e.error}: {e.message}"))
            code = e.exit_code
    if cfg.admin_key and code in (EXIT_OK, EXIT_FAILED):
        try:
            client.get_json("/v2/admin/ping", admin=True, timeout=15)
            checks.append((True, "admin key accepted"))
        except ApiError as e:
            checks.append((False, f"admin key: {e.error}: {e.message}"))
    for ok, msg in checks:
        out(f"{'✓' if ok else '✗'} {msg}")
    return code


def cmd_config(args, _client) -> int:
    if args.config_cmd == "path":
        out(str(config.config_path()))
        return EXIT_OK
    if args.config_cmd == "show":
        cfg = config.load()
        for key in config.KEYS:
            val = getattr(cfg, key)
            shown = config.mask(val) if key.endswith("key") else (val or "(unset)")
            out(f"{key} = {shown}  [{cfg.sources.get(key)}]")
        out(f"download_dir = {config.download_dir()}")
        return EXIT_OK
    key = args.key.replace("-", "_")
    if key not in config.KEYS:
        raise ApiError("invalid_request", f"unknown key '{args.key}'; one of: {', '.join(CONFIG_KEYS)}")
    if key.endswith("_key"):
        if args.value:
            raise ApiError("invalid_request", "pass keys on stdin, not as an argument (shell history)")
        value = getpass.getpass(f"{args.key}: ") if sys.stdin.isatty() else sys.stdin.readline().strip()
    else:
        value = args.value or ""
    values = config.read_file()
    values[key] = value
    path = config.write_file(values)
    out(f"{args.key} saved to {path}")
    return EXIT_OK


def cmd_skill(args, _client) -> int:
    from .skill import render, render_reference

    parser = build_parser()
    if args.skill_cmd == "show":
        out(render_reference(parser) if args.reference else render(parser))
        return EXIT_OK
    target = Path(args.dir or SKILL_DIRS[args.agent]).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(render(parser), encoding="utf-8")
    (target / "reference.md").write_text(render_reference(parser), encoding="utf-8")
    out(f"installed {target}/SKILL.md and reference.md (webkit {__version__})")
    return EXIT_OK


# ─── parser ────────────────────────────────────────────────────────────────────

class _Parser(argparse.ArgumentParser):
    def error(self, message):  # argparse exits 2 by default; 2 means "no results" here
        self.print_usage(sys.stderr)
        err_json({"error": "invalid_request", "message": message})
        sys.exit(EXIT_USAGE)


CONFIG_KEYS = ("url", "api-key", "admin-key", "vnc-url")
SKILL_DIRS = {"claude": "~/.claude/skills/web-kit", "codex": "~/.codex/skills/web-kit"}

POSITIONING = """\
Fallback web access for agents, through a real, logged-in Chrome on a server.
Use it when the built-in web tools fail, are blocked or out of quota, or cannot do
the job; when they work, prefer them.

  search    find URLs: raw result links + snippets, no answer; no WebSearch quota
  read      learn what a page says: page / PDF / DOI / arXiv:ID -> text
  download  get the file itself, saved to disk (a DOI or paper page -> its PDF)
  crawl     a few pages of one site into a directory

  status, doctor, engines, config, skill: health and setup

Order: built-in tools -> search/read/download -> report "not accessible".
"""


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    g = common.add_argument_group("connection")
    g.add_argument("--url", dest="backend_url", metavar="URL", help="backend URL (default: config / $WEBKIT_URL)")
    g.add_argument("--key", help=argparse.SUPPRESS)
    g.add_argument("--timeout", type=int, help="seconds to wait for the backend")
    common.add_argument("--json", action="store_true", help="JSON output (same amount, different format)")
    common.add_argument("--max-chars", type=int, metavar="N",
                        help=f"most characters this command prints (default {DEFAULT_BUDGET}, at most "
                        f"{MAX_BUDGET}); longer text goes to a file and its path is printed")

    raw = argparse.RawDescriptionHelpFormatter
    p = _Parser(prog="webkit", description=POSITIONING, epilog=__doc__.split("\n\n", 2)[2], formatter_class=raw)
    p.add_argument("--version", action="version", version=f"webkit {__version__} (api {API_VERSION})")
    # Subcommands carry no help= so argparse does not list them a second time under POSITIONING.
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND", parser_class=_Parser)

    s = sub.add_parser("search", parents=[common], formatter_class=raw, description="""\
Find URLs. Returns raw result links with snippets from live engines: no synthesized
answer, and no WebSearch quota. Use it when WebSearch is out of quota or failing, or
for a specific engine (Google Scholar, Semantic Scholar, arXiv) or a date filter.
Then `webkit read` the results you need.
First line: '# engines <engine>=<ok:N|empty|error:CLASS|skipped> ...'.""")
    s.add_argument("query", nargs="+")
    s.add_argument("-p", "--profile", choices=PROFILES, default="general",
                   help="engine chain: general=google>duckduckgo>bing, "
                   "academic=semantic_scholar>google_scholar>openalex>arxiv, code=github>google, "
                   "community=hackernews>stackoverflow (default: general)")
    s.add_argument("-e", "--engine", help="use exactly this engine (see `webkit engines`)")
    s.add_argument("-n", "--limit", type=int, default=10, help="max results (default 10)")
    s.add_argument("--time", choices=TIME_RANGES, help="recency filter; engines without it are skipped")
    s.add_argument("--lang", help="language, e.g. en, zh-CN")
    s.add_argument("--urls", action="store_true", help="print only result URLs (engine header on stderr)")
    s.add_argument("--read", type=int, metavar="N",
                   help="also read the top N results into files (-o DIR, else the cache); prints their paths")
    s.add_argument("-o", "--output", metavar="DIR", help="with --read: save pages into DIR")
    s.set_defaults(func=cmd_search, default_budget=DEFAULT_BUDGET)

    r = sub.add_parser("read", parents=[common], formatter_class=raw, description=f"""\
Learn what a page says, as text. Use it when the built-in reader fails (403, 429,
timeout, too large), returns a verification or login page, or gives a summary where
you need the exact text (quotes, numbers, tables, a full paper).
Targets: URLs, DOIs (10.1145/... or doi:...), arXiv IDs (arXiv:2403.01092 -> abstract page).
PDFs come back as extracted text with '<!-- page N -->' markers; --pdf reads the PDF a
paper page links instead of the page. A verification page is never returned as
content: it is exit 6 (needs a person) or 8 (blocked).
Output: a page that fits --max-chars is printed; a longer one is saved to a file and
only a preview and the path are printed. With -o, only paths are printed.""")
    r.add_argument("urls", nargs="+", metavar="TARGET")
    r.add_argument("--pdf", action="store_true", help="read the paper PDF the page links (citation_pdf_url)")
    r.add_argument("-f", "--format", choices=("fit", "md", "html", "links", "json"), default="fit",
                   help="fit = main content (default), md = full page, html, links, json")
    r.add_argument("-o", "--output", metavar="PATH", help="save to FILE (one target) or DIR; prints one line per page")
    r.add_argument("--wait-for", metavar="CSS|MS", help="CSS selector to wait for, or milliseconds")
    r.add_argument("--scroll", action="store_true", help="scroll the full page first (lazy content)")
    r.add_argument("--cache", action="store_true", help="allow a cached copy (default: always fetch fresh)")
    r.set_defaults(func=cmd_read, default_budget=DEFAULT_BUDGET)

    d = sub.add_parser("download", parents=[common], formatter_class=raw, description=f"""\
Get the file itself (PDF, archive, dataset), saved to disk through the backend
browser, so its logins and cookies apply. Prints the saved path, not the content.
Targets: URLs, DOIs, arXiv IDs. A paper page resolves to the PDF it links; any other
web page is an error (not_a_file): use `read` for text.
Default destination: {_tilde(config.download_dir())}/ ($WEBKIT_DOWNLOAD_DIR).""")
    d.add_argument("url", metavar="TARGET")
    d.add_argument("-o", "--output", metavar="PATH", help="target file or directory")
    d.add_argument("--max-mb", type=int, default=0, help="size limit in MB (default: backend limit)")
    d.add_argument("--allow-html", action="store_true", help="save a web page as-is instead of resolving its PDF")
    d.add_argument("--wait-human", action="store_true",
                   help="only with a person at noVNC: open the page there and retry until they pass the check")
    d.add_argument("--wait-timeout", type=int, default=300, help="seconds to wait with --wait-human (default 300)")
    d.set_defaults(func=cmd_download, default_budget=DEFAULT_BUDGET)

    c = sub.add_parser("crawl", parents=[common], formatter_class=raw, description="""\
Bounded synchronous crawl of one site: one markdown file per page plus index.json.""")
    c.add_argument("url")
    c.add_argument("-o", "--output", required=True, metavar="DIR")
    c.add_argument("--strategy", choices=("bfs", "dfs"), default="bfs")
    c.add_argument("--max-depth", type=int, default=2)
    c.add_argument("--max-pages", type=int, default=10, help="default 10, backend cap 30")
    c.add_argument("--include", action="append", metavar="GLOB", help="only URLs matching (repeatable)")
    c.add_argument("--exclude", action="append", metavar="GLOB", help="skip URLs matching (repeatable)")
    c.add_argument("-f", "--format", choices=("fit", "md"), default="fit")
    c.set_defaults(func=cmd_crawl, default_budget=DEFAULT_BUDGET)

    st = sub.add_parser("status", parents=[common], description="backend health: browser, egress, engines")
    st.set_defaults(func=cmd_status, default_budget=DIAG_BUDGET)
    dr = sub.add_parser("doctor", parents=[common], description="check config, keys, versions and backend health")
    dr.set_defaults(func=cmd_doctor, default_budget=DIAG_BUDGET)
    en = sub.add_parser("engines", parents=[common], description="list engines, profiles and time-filter support")
    en.set_defaults(func=cmd_engines, default_budget=DIAG_BUDGET)

    cf = sub.add_parser("config", description="show or change the CLI config")
    csub = cf.add_subparsers(dest="config_cmd", required=True, parser_class=_Parser)
    csub.add_parser("show", help="effective settings and where each comes from").set_defaults(func=cmd_config)
    csub.add_parser("path", help="config file location").set_defaults(func=cmd_config)
    cs = csub.add_parser("set", help="set url / vnc-url (argument) or api-key / admin-key (stdin)")
    cs.add_argument("key", choices=CONFIG_KEYS)
    cs.add_argument("value", nargs="?")
    cs.set_defaults(func=cmd_config)

    sk = sub.add_parser("skill", description="agent skill (SKILL.md) for this CLI version")
    ssub = sk.add_subparsers(dest="skill_cmd", required=True, parser_class=_Parser)
    si = ssub.add_parser("install", help="write SKILL.md + reference.md for Claude Code (default) or Codex")
    si.add_argument("--agent", choices=tuple(SKILL_DIRS), default="claude",
                    help="claude -> ~/.claude/skills/web-kit, codex -> ~/.codex/skills/web-kit")
    si.add_argument("--dir", help="write here instead")
    si.set_defaults(func=cmd_skill)
    sh = ssub.add_parser("show", help="print SKILL.md (or --reference: reference.md)")
    sh.add_argument("--reference", action="store_true", help="print reference.md instead")
    sh.set_defaults(func=cmd_skill)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    flags = {"url": getattr(args, "backend_url", None), "api_key": getattr(args, "key", None)}
    client = Client(config.load(flags))
    OUT.configure(_budget(args), args.cmd)
    try:
        code = args.func(args, client)
    except ApiError as e:
        err_json(e.body)
        code = e.exit_code
    except KeyboardInterrupt:
        code = 130
    except BrokenPipeError:
        code = EXIT_OK
    finally:
        try:
            OUT.close()
        except BrokenPipeError:
            pass
    sys.exit(code)


if __name__ == "__main__":
    main()
