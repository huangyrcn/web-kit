"""webkit — search, read, crawl and download the web through a self-hosted,
logged-in browser (web-kit v2 backend).

Output is compact text for agents and humans; --json gives the raw API response.
Errors go to stderr as one JSON line: {"error": <class>, "message": ..., "hint": ...}.

Exit codes:
  0 ok (possibly after engine fallback; the header line says which engine answered)
  1 usage error            2 no results (engines healthy)
  3 failed (engines / network / upstream / timeout)
  4 auth (missing or wrong key)        5 backend unreachable
  6 human action needed (CAPTCHA / login: solve it via noVNC, then retry)
  7 busy (all browser pages in use; retry shortly)
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import API_VERSION, __version__, config
from .client import (EXIT_AUTH, EXIT_FAILED, EXIT_HUMAN, EXIT_NO_RESULTS, EXIT_OK, EXIT_UNREACHABLE,
                     EXIT_USAGE, ApiError, Client)

PROFILES = ("general", "academic", "code", "community")
TIME_RANGES = ("day", "week", "month", "year")
INLINE_READ_CHARS = 3000


# ─── output helpers ────────────────────────────────────────────────────────────

def out(text: str = "") -> None:
    sys.stdout.write(text + ("\n" if not text.endswith("\n") else ""))


def err_json(body: dict) -> None:
    sys.stderr.write(json.dumps(body, ensure_ascii=False) + "\n")


def print_json(data) -> None:
    out(json.dumps(data, ensure_ascii=False, indent=2))


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
    if args.json and not args.read:
        print_json(data)
        return EXIT_OK if results else EXIT_NO_RESULTS

    header = engines_header(data.get("engines", []))
    if args.urls:
        sys.stderr.write(header + "\n")
        for r in results:
            out(r["url"])
    elif not args.json:
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
    if not results:
        return EXIT_NO_RESULTS
    if args.read:
        targets = [r["url"] for r in results[: args.read]]
        return _read_many(client, targets, fmt="fit", output=args.output,
                          max_chars=args.max_chars if args.max_chars is not None else
                          (0 if args.output else INLINE_READ_CHARS),
                          timeout=60, as_json=args.json)
    return EXIT_OK


def _page(client: Client, url: str, fmt: str, max_chars: int, timeout: int, wait_for=None,
          scroll=False, cache=False) -> dict:
    body = {"url": url, "format": fmt, "max_chars": max_chars, "timeout": timeout,
            "wait_for": wait_for, "scroll": scroll, "cache": cache}
    return client.post_json("/v2/page", {k: v for k, v in body.items() if v is not None},
                            timeout=timeout + 45)


def _page_text(page: dict) -> str:
    if page.get("format") == "links":
        return "\n".join(f"{l['href']}\t{l.get('text', '')}" for l in page.get("links", []))
    if page.get("format") == "json":
        return json.dumps(page, ensure_ascii=False, indent=2)
    return page.get("content", "")


def _read_many(client: Client, urls: list[str], fmt: str, output: str | None, max_chars: int,
               timeout: int, as_json: bool = False, **page_kw) -> int:
    code = EXIT_OK
    single_file = output and len(urls) == 1 and not output.endswith("/") and not Path(output).is_dir()
    collected = []
    for i, url in enumerate(urls, 1):
        try:
            page = _page(client, url, fmt, max_chars, timeout, **page_kw)
        except ApiError as e:
            err_json({**e.body, "url": url})
            code = code or e.exit_code
            continue
        collected.append(page)
        text = _page_text(page)
        if output:
            if single_file:
                path = Path(output)
            else:
                Path(output).mkdir(parents=True, exist_ok=True)
                name = slugify(page.get("title") or urlparse(url).path.rsplit("/", 1)[-1] or urlparse(url).netloc)
                path = Path(output) / f"{i:02d}-{name}{_ext(fmt)}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            note = " (truncated)" if page.get("truncated") else ""
            out(f"saved {path} ({len(text)} chars{note}) {page.get('title', '')}".rstrip())
        elif not as_json:
            if len(urls) > 1:
                out(f"\n--- [{i}] {url}" + (f"  {page['title']}" if page.get("title") else ""))
            out(text)
            if page.get("truncated"):
                out(f"\n[truncated at {len(text)} chars; use --max-chars 0 or -o FILE for the full page]")
    if as_json:
        print_json(collected if len(urls) > 1 else (collected[0] if collected else {}))
    return code


def cmd_read(args, client: Client) -> int:
    max_chars = args.max_chars if args.max_chars is not None else 0
    return _read_many(client, args.urls, args.format, args.output, max_chars, args.timeout or 60,
                      as_json=args.json, wait_for=args.wait_for, scroll=args.scroll, cache=args.cache)


def cmd_crawl(args, client: Client) -> int:
    body = {"url": args.url, "strategy": args.strategy, "max_depth": args.max_depth,
            "max_pages": args.max_pages, "include": args.include or [], "exclude": args.exclude or [],
            "format": args.format, "timeout": args.timeout or 180}
    data = client.post_json("/v2/crawl", body, timeout=body["timeout"] + 60)
    if args.json:
        print_json(data)
        return EXIT_OK if data.get("count") else EXIT_NO_RESULTS
    outdir = Path(args.output)
    outdir.mkdir(parents=True, exist_ok=True)
    index = []
    for i, page in enumerate(data.get("pages", []), 1):
        entry = {k: page.get(k) for k in ("url", "depth", "title")}
        if "content" in page:
            name = f"{i:02d}-{slugify(page.get('title') or urlparse(page['url']).path.rsplit('/', 1)[-1] or 'index')}.md"
            (outdir / name).write_text(page["content"], encoding="utf-8")
            entry.update(file=name, chars=page.get("chars"))
            out(f"saved {outdir / name} ({page.get('chars', 0)} chars) {page['url']}")
        else:
            entry.update(error=page.get("error"), message=page.get("message"))
            out(f"failed {page['url']}: {page.get('error')}")
        index.append(entry)
    (outdir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    out(f"# crawled {data.get('count', 0)} pages -> {outdir}/index.json")
    return EXIT_OK if any("file" in e for e in index) else EXIT_FAILED


def _download_once(client: Client, url: str, dest: Path | None, max_mb: int) -> Path:
    resp = client.stream("/v2/download", {"url": url, "max_mb": max_mb or None}, timeout=180)
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
            if expected and int(expected) != size:
                raise ApiError("network", f"download truncated: got {size} of {expected} bytes")
            os.replace(tmp, target)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        ctype = resp.headers.get("Content-Type", "")
    out(f"saved {target} ({size} bytes, {ctype})")
    return target


def cmd_download(args, client: Client) -> int:
    dest = Path(args.output).expanduser() if args.output else None
    deadline = time.monotonic() + args.wait_timeout
    opened = False
    while True:
        try:
            _download_once(client, args.url, dest, args.max_mb)
            return EXIT_OK
        except ApiError as e:
            if e.error != "human_required" or not args.wait_human:
                if e.error == "human_required":
                    e.body.setdefault("vnc", client.cfg.vnc)
                raise
            if not opened:
                _open_for_human(client, args.url)
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
    sys.stderr.write(f"human action needed: open {client.cfg.vnc} (user webkit, password = admin key), "
                     f"solve the page for {url}; retrying every 10s\n")


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


def cmd_browser_open(args, client: Client) -> int:
    client.get_json("/v2/admin/open", {"url": args.url}, admin=True, timeout=60)
    out(f"opened {args.url} in the backend browser")
    out(f"interact via noVNC: {client.cfg.vnc}  (user webkit, password = admin key)")
    return EXIT_OK


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
        raise ApiError("invalid_request", f"unknown key '{args.key}'; one of: url, api-key, admin-key, vnc-url")
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
    from .skill import render

    text = render(build_parser())
    if args.skill_cmd == "show":
        out(text)
        return EXIT_OK
    target = Path(args.dir).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(text, encoding="utf-8")
    out(f"installed {target / 'SKILL.md'} (webkit {__version__})")
    return EXIT_OK


# ─── parser ────────────────────────────────────────────────────────────────────

class _Parser(argparse.ArgumentParser):
    def error(self, message):  # argparse exits 2 by default; 2 means "no results" here
        self.print_usage(sys.stderr)
        err_json({"error": "invalid_request", "message": message})
        sys.exit(EXIT_USAGE)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    g = common.add_argument_group("connection")
    g.add_argument("--url", dest="backend_url", metavar="URL", help="backend URL (default: config / $WEBKIT_URL)")
    g.add_argument("--key", help=argparse.SUPPRESS)
    g.add_argument("--timeout", type=int, help="seconds to wait for the backend")
    common.add_argument("--json", action="store_true", help="print the raw API response as JSON")

    p = _Parser(prog="webkit", description="Search, read, crawl and download the web through the "
                "web-kit backend (a logged-in Chrome).", epilog=__doc__.split("\n\n", 2)[2],
                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"webkit {__version__} (api {API_VERSION})")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=_Parser)

    s = sub.add_parser("search", parents=[common], help="search the web",
                       description="Search with a profile (ordered engine fallback) or one engine. "
                       "First line: '# engines <engine>=<ok:N|empty|error:CLASS|skipped> ...'.")
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
    s.add_argument("--read", type=int, metavar="N", help="also read the top N results (fit markdown)")
    s.add_argument("-o", "--output", metavar="DIR", help="with --read: save pages into DIR")
    s.add_argument("--max-chars", type=int, help=f"with --read: cap per page (default {INLINE_READ_CHARS} inline, 0 = none)")
    s.set_defaults(func=cmd_search)

    r = sub.add_parser("read", parents=[common], help="read pages (and PDFs) as markdown",
                       description="Render URLs in the backend browser and return clean text. "
                       "PDFs are detected and returned as extracted text.")
    r.add_argument("urls", nargs="+", metavar="URL")
    r.add_argument("-f", "--format", choices=("fit", "md", "html", "links", "json"), default="fit",
                   help="fit = main content (default), md = full page, html, links, json")
    r.add_argument("-o", "--output", metavar="PATH", help="save to FILE (one URL) or DIR; prints one line per page")
    r.add_argument("--wait-for", metavar="CSS|MS", help="CSS selector to wait for, or milliseconds")
    r.add_argument("--scroll", action="store_true", help="scroll the full page first (lazy content)")
    r.add_argument("--cache", action="store_true", help="allow a cached copy (default: always fetch fresh)")
    r.add_argument("--max-chars", type=int, help="truncate each page to N chars")
    r.set_defaults(func=cmd_read)

    c = sub.add_parser("crawl", parents=[common], help="crawl a site into a directory",
                       description="Bounded synchronous crawl; one markdown file per page plus index.json.")
    c.add_argument("url")
    c.add_argument("-o", "--output", required=True, metavar="DIR")
    c.add_argument("--strategy", choices=("bfs", "dfs"), default="bfs")
    c.add_argument("--max-depth", type=int, default=2)
    c.add_argument("--max-pages", type=int, default=10, help="default 10, backend cap 30")
    c.add_argument("--include", action="append", metavar="GLOB", help="only URLs matching (repeatable)")
    c.add_argument("--exclude", action="append", metavar="GLOB", help="skip URLs matching (repeatable)")
    c.add_argument("-f", "--format", choices=("fit", "md"), default="fit")
    c.set_defaults(func=cmd_crawl)

    d = sub.add_parser("download", parents=[common], help="download a file with the browser's cookies",
                       description="Stream a file through the backend browser (its logins apply). "
                       f"Default destination: {config.download_dir()}/ ($WEBKIT_DOWNLOAD_DIR).")
    d.add_argument("url")
    d.add_argument("-o", "--output", metavar="PATH", help="target file or directory")
    d.add_argument("--max-mb", type=int, default=0, help="size limit in MB (default: backend limit)")
    d.add_argument("--wait-human", action="store_true",
                   help="on CAPTCHA/login: open the page in the backend browser and retry until solved")
    d.add_argument("--wait-timeout", type=int, default=300, help="seconds to wait with --wait-human (default 300)")
    d.set_defaults(func=cmd_download)

    st = sub.add_parser("status", parents=[common], help="backend health: browser, egress, engines")
    st.set_defaults(func=cmd_status)
    en = sub.add_parser("engines", parents=[common], help="list engines, profiles and time-filter support")
    en.set_defaults(func=cmd_engines)
    dr = sub.add_parser("doctor", parents=[common], help="check config, auth, versions and backend health")
    dr.set_defaults(func=cmd_doctor)

    b = sub.add_parser("browser", help="admin: drive the backend browser (needs the admin key)")
    bsub = b.add_subparsers(dest="browser_cmd", required=True, parser_class=_Parser)
    bo = bsub.add_parser("open", parents=[common], help="open URL in the backend browser for a manual login/CAPTCHA via noVNC")
    bo.add_argument("url")
    bo.set_defaults(func=cmd_browser_open)

    cf = sub.add_parser("config", help="show or change the CLI config")
    csub = cf.add_subparsers(dest="config_cmd", required=True, parser_class=_Parser)
    csub.add_parser("show", help="effective settings and where each comes from").set_defaults(func=cmd_config)
    csub.add_parser("path", help="config file location").set_defaults(func=cmd_config)
    cs = csub.add_parser("set", help="set url / vnc-url (argument) or api-key / admin-key (stdin)")
    cs.add_argument("key", choices=("url", "api-key", "admin-key", "vnc-url"))
    cs.add_argument("value", nargs="?")
    cs.set_defaults(func=cmd_config)

    sk = sub.add_parser("skill", help="agent skill for this CLI version")
    ssub = sk.add_subparsers(dest="skill_cmd", required=True, parser_class=_Parser)
    si = ssub.add_parser("install", help="write SKILL.md (default ~/.claude/skills/web-kit)")
    si.add_argument("--dir", default="~/.claude/skills/web-kit")
    si.set_defaults(func=cmd_skill)
    ssub.add_parser("show", help="print SKILL.md").set_defaults(func=cmd_skill)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    flags = {"url": getattr(args, "backend_url", None), "api_key": getattr(args, "key", None)}
    client = Client(config.load(flags))
    try:
        code = args.func(args, client)
    except ApiError as e:
        err_json(e.body)
        code = e.exit_code
    except KeyboardInterrupt:
        code = 130
    except BrokenPipeError:
        code = EXIT_OK
    sys.exit(code)


if __name__ == "__main__":
    main()
