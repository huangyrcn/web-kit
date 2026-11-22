"""The events log: one JSON line per web call, for tuning engines, budgets and the skill.

The CLI appends one event per search / read / download to events.jsonl in the state
directory ($WEBKIT_STATE_DIR, default ~/.local/state/web-kit/; 0600, rotated at 10 MB,
3 files kept). The server prints one per request to stdout (docker logs). Both use the
same fields, so `webkit stats` reads either. Full query text and URLs are recorded; keys,
page text and result lists never are. log = "off" (config) or WEBKIT_LOG=off stops it.

Fields: ts, v, side (cli | server), cmd (search | read | download | ...), mode (local |
remote), agent / session (who called: claude, codex, or empty), tty, client (server: the
caller's address), exit (cli) or status (server), error (class), ms, printed / spilled
(cli output budget); search: q, profile, engine, limit, time, lang, engines (one entry
per engine: status, count, ms, error), results; read: pages (url, final_url, chars,
truncated, ms, error); download: url, bytes, content_type, final_url, resolved_from.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from . import config

MAX_BYTES = 10 * 1024 * 1024
KEEP = 3            # events.jsonl, events.1.jsonl, events.2.jsonl
FIELD_MAX = 2000    # a query or URL longer than this is cut
MESSAGE_MAX = 200   # an engine's error message


def enabled(value: str | None) -> bool:
    return (value or "").strip().lower() not in ("off", "0", "false", "no")


def log_path() -> Path:
    return config.state_dir() / "events.jsonl"


def _rotated(path: Path, i: int) -> Path:
    return path if i == 0 else path.with_name(f"{path.stem}.{i}{path.suffix}")


def files(path: Path | None = None) -> list[Path]:
    """The log and its rotated copies, oldest first."""
    path = path or log_path()
    return [p for p in (_rotated(path, i) for i in range(KEEP - 1, -1, -1)) if p.exists()]


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _clip(v):
    if isinstance(v, str):
        return v if len(v) <= FIELD_MAX else v[:FIELD_MAX] + "…"
    if isinstance(v, dict):
        return {k: _clip(x) for k, x in v.items() if x not in (None, "")}
    if isinstance(v, list):
        return [_clip(x) for x in v]
    return v


def line(event: dict) -> str:
    return json.dumps(_clip(event), ensure_ascii=False) + "\n"


def append(event: dict, path: Path | None = None) -> None:
    """Add one event to the log; never raises (a log must not break a command)."""
    try:
        path = path or log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        text = line(event)
        try:
            if path.stat().st_size + len(text) > MAX_BYTES:
                for i in range(KEEP - 1, 0, -1):
                    if _rotated(path, i - 1).exists():
                        os.replace(_rotated(path, i - 1), _rotated(path, i))
        except FileNotFoundError:
            pass
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass


def engines_report(report) -> list[dict]:
    """A search's per-engine report as logged: error messages cut short."""
    out = []
    for e in report or []:
        if not isinstance(e, dict):
            continue
        e = {k: e[k] for k in ("engine", "status", "count", "ms", "error", "reason", "message") if k in e}
        if isinstance(e.get("message"), str):
            e["message"] = e["message"][:MESSAGE_MAX]
        out.append(e)
    return out


# ─── who is calling ───────────────────────────────────────────────────────────

AGENTS = ("claude", "codex")


def _ancestors(limit: int = 15):
    """(pid, command name) of this process's parents, nearest first."""
    if os.path.isdir("/proc/self"):
        pid = os.getppid()
        for _ in range(limit):
            if pid <= 1:
                return
            try:
                stat = Path(f"/proc/{pid}/stat").read_text()
            except OSError:
                return
            name = stat[stat.index("(") + 1: stat.rindex(")")]
            yield pid, name
            pid = int(stat[stat.rindex(")") + 2:].split()[1])
        return
    try:  # macOS and other systems without /proc: one ps call
        rows = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,comm="], capture_output=True, text=True,
                              timeout=3).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return
    table = {}
    for row in rows:
        parts = row.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), os.path.basename(parts[2]))
    pid = os.getppid()
    for _ in range(limit):
        if pid <= 1 or pid not in table:
            return
        ppid, name = table[pid]
        yield pid, name
        pid = ppid


@lru_cache(maxsize=1)
def caller() -> dict:
    """{agent, session}: which coding agent runs this command, from its environment or,
    failing that, the parent processes. Empty when a person (or something else) runs it."""
    env = os.environ
    if env.get("CLAUDECODE"):
        return {"agent": "claude", "session": env.get("CLAUDE_CODE_SESSION_ID", "")}
    for pid, name in _ancestors():
        low = name.lower()
        for agent in AGENTS:
            if low == agent or low.startswith(agent + "-"):
                session = env.get("CODEX_THREAD_ID", "") if agent == "codex" else ""
                return {"agent": agent, "session": session or f"pid:{pid}"}
    if env.get("AI_AGENT"):
        return {"agent": env["AI_AGENT"].split("_")[0], "session": ""}
    return {"agent": "", "session": ""}


def caller_headers() -> dict:
    c = caller()
    return {k: v for k, v in (("X-Webkit-Agent", c["agent"]), ("X-Webkit-Session", c["session"][:100])) if v}


# ─── stats ────────────────────────────────────────────────────────────────────

WEB_COMMANDS = ("search", "read", "download")


def parse(lines, days: int = 0) -> list[dict]:
    """Events from log lines (other lines, such as server tracebacks, are skipped), the
    last `days` days only (0: all)."""
    since = datetime.now().astimezone() - timedelta(days=days) if days > 0 else None
    events = []
    for raw in lines:
        raw = raw.strip()
        if not raw.startswith("{"):
            continue
        try:
            ev = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(ev, dict) or ev.get("cmd") not in WEB_COMMANDS:
            continue
        if since is not None:
            try:
                if datetime.fromisoformat(ev.get("ts", "")) < since:
                    continue
            except (TypeError, ValueError):
                continue
        events.append(ev)
    return events


def outcome(ev: dict) -> str:
    """ok | empty | an error class."""
    if ev.get("error"):
        return ev["error"]
    if ev.get("exit") == 2 or (ev.get("cmd") == "search" and ev.get("results") == 0):
        return "empty"
    failed = [p.get("error") for p in ev.get("pages") or [] if p.get("error")]
    if failed and len(failed) == len(ev.get("pages") or []):
        return failed[0]
    return "ok"


def _pct(values: list, q: float):
    if not values:
        return None
    v = sorted(values)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))]


def _count(items) -> dict:
    out: dict = {}
    for x in items:
        out[x] = out.get(x, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], str(kv[0]))))


def _domain(url: str) -> str:
    host = urlparse(url or "").hostname or url or "?"
    return host[4:] if host.startswith("www.") else host


def summarize(events: list[dict]) -> dict:
    """What `webkit stats` prints, as data."""
    s: dict = {"events": len(events)}
    if not events:
        return s
    stamps = sorted(ev.get("ts", "") for ev in events)
    s["from"], s["to"] = stamps[0], stamps[-1]
    s["sources"] = _count("server" if ev.get("side") == "server" else f"cli/{ev.get('mode', '?')}" for ev in events)
    s["callers"] = _count(ev.get("agent") or ("person" if ev.get("tty") else "other") for ev in events)

    commands = {}
    for cmd in WEB_COMMANDS:
        evs = [ev for ev in events if ev.get("cmd") == cmd]
        if evs:
            ms = [ev["ms"] for ev in evs if isinstance(ev.get("ms"), (int, float))]
            commands[cmd] = {"calls": len(evs), "outcomes": _count(outcome(ev) for ev in evs),
                             "p50_ms": _pct(ms, 0.5), "p90_ms": _pct(ms, 0.9)}
    s["commands"] = commands

    engines: dict = {}
    for ev in events:
        answered = False
        for e in ev.get("engines") or []:
            st = e.get("status")
            if st == "skipped" or not e.get("engine"):
                continue
            row = engines.setdefault(e["engine"], {"tries": 0, "ok": 0, "empty": 0, "answered": 0,
                                                   "errors": {}, "_ms": [], "_count": []})
            row["tries"] += 1
            if isinstance(e.get("ms"), (int, float)):
                row["_ms"].append(e["ms"])
            if st == "ok":
                row["ok"] += 1
                row["_count"].append(e.get("count", 0))
                if not answered:
                    row["answered"] += 1
                    answered = True
            elif st == "empty":
                row["empty"] += 1
            else:
                cls = e.get("error", "error")
                row["errors"][cls] = row["errors"].get(cls, 0) + 1
    for row in engines.values():
        ms, counts = row.pop("_ms"), row.pop("_count")
        row["p50_ms"], row["p90_ms"] = _pct(ms, 0.5), _pct(ms, 0.9)
        row["avg_results"] = round(sum(counts) / len(counts), 1) if counts else None
        row["errors"] = dict(sorted(row["errors"].items(), key=lambda kv: -kv[1]))
    s["engines"] = dict(sorted(engines.items(), key=lambda kv: -kv[1]["tries"]))

    pages = [p for ev in events for p in ev.get("pages") or []]
    if pages:
        ok = [p for p in pages if not p.get("error")]
        failed: dict = {}
        for p in pages:
            if p.get("error"):
                failed.setdefault(p["error"], []).append(_domain(p.get("url", "")))
        s["pages"] = {"read": len(pages), "ok": len(ok),
                      "truncated": sum(1 for p in ok if p.get("truncated")),
                      "p50_chars": _pct([p.get("chars", 0) for p in ok], 0.5),
                      "p90_chars": _pct([p.get("chars", 0) for p in ok], 0.9),
                      "failed": {cls: {"count": len(ds), "domains": dict(list(_count(ds).items())[:5])}
                                 for cls, ds in sorted(failed.items(), key=lambda kv: -len(kv[1]))}}
    downloads = [ev for ev in events if ev.get("cmd") == "download"]
    if downloads:
        failed = {}
        for ev in downloads:
            if outcome(ev) != "ok":
                failed.setdefault(outcome(ev), []).append(_domain(ev.get("url", "")))
        s["downloads"] = {"calls": len(downloads),
                          "failed": {cls: {"count": len(ds), "domains": dict(list(_count(ds).items())[:5])}
                                     for cls, ds in sorted(failed.items(), key=lambda kv: -len(kv[1]))}}

    cli = [ev for ev in events if ev.get("side") == "cli"]
    if cli:
        s["output"] = {"spilled": sum(1 for ev in cli if ev.get("spilled")),
                       "p50_printed": _pct([ev.get("printed", 0) for ev in cli], 0.5),
                       "p90_printed": _pct([ev.get("printed", 0) for ev in cli], 0.9)}
    seen, repeats = set(), 0
    for ev in sorted(events, key=lambda e: e.get("ts", "")):
        if not ev.get("session"):
            continue
        what = ev.get("q") or ev.get("url") or tuple(p.get("url") for p in ev.get("pages") or [])
        key = (ev.get("side"), ev["session"], ev.get("cmd"), str(what))
        repeats += key in seen
        seen.add(key)
    s["repeats"] = repeats
    return s


def _ms(v) -> str:
    return "-" if v is None else (f"{v / 1000:.1f}s" if v >= 1000 else f"{int(v)}ms")


def _pairs(d: dict) -> str:
    return " ".join(f"{k} {v}" for k, v in d.items())


def render(s: dict, source: str) -> list[str]:
    """`webkit stats` as text lines."""
    if not s.get("events"):
        return [f"no search / read / download events in {source}"]
    lines = [f"{s['events']} events, {s['from'][:16]} .. {s['to'][:16]}, from {source}",
             f"sources: {_pairs(s['sources'])}; callers: {_pairs(s['callers'])}", "",
             "command    calls  p50    p90    outcomes"]
    for cmd, c in s["commands"].items():
        lines.append(f"{cmd:<10} {c['calls']:>5}  {_ms(c['p50_ms']):<6} {_ms(c['p90_ms']):<6} {_pairs(c['outcomes'])}")
    if s.get("engines"):
        lines += ["", "engine              tries  ok  empty  answered  p50    p90    avg  errors"]
        for name, e in s["engines"].items():
            avg = "-" if e["avg_results"] is None else f"{e['avg_results']:g}"
            lines.append(f"{name:<19} {e['tries']:>5} {e['ok']:>3} {e['empty']:>6} {e['answered']:>9}  "
                         f"{_ms(e['p50_ms']):<6} {_ms(e['p90_ms']):<6} {avg:<4} {_pairs(e['errors'])}".rstrip())
    if p := s.get("pages"):
        lines += ["", f"pages: {p['read']} read, {p['ok']} ok, {p['truncated']} long (outline shown); chars "
                      f"p50 {p['p50_chars']}, p90 {p['p90_chars']}"]
        for cls, f in p["failed"].items():
            lines.append(f"  {cls} {f['count']}: {_pairs(f['domains'])}")
    if d := s.get("downloads"):
        lines += ["", f"downloads: {d['calls']}" + ("" if d["failed"] else ", none failed")]
        for cls, f in d["failed"].items():
            lines.append(f"  {cls} {f['count']}: {_pairs(f['domains'])}")
    lines.append("")
    if o := s.get("output"):
        lines.append(f"output: printed p50 {o['p50_printed']}, p90 {o['p90_printed']} chars; "
                     f"{o['spilled']} calls spilled past the budget")
    lines.append(f"repeated calls (same session, same query or URL): {s['repeats']}")
    return lines


def read_sources(paths: list[str]) -> tuple[list[str], str]:
    """Lines from FILEs or '-' (stdin); none given: the log and its rotated copies."""
    if not paths:
        found = files()
        return [ln for p in found for ln in p.read_text(encoding="utf-8", errors="replace").splitlines()], \
            str(log_path())
    lines = []
    for p in paths:
        if p == "-":
            lines += sys.stdin.read().splitlines()
        else:
            lines += Path(p).expanduser().read_text(encoding="utf-8", errors="replace").splitlines()
    return lines, ", ".join("stdin" if p == "-" else p for p in paths)
