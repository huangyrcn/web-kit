"""/v2/search and /v2/engines: engine chains with explicit per-engine outcomes.

A profile is an ordered fallback chain. Engines are tried in order until one
returns results; every engine tried is reported with status ok / empty / error /
skipped, so an empty answer is never ambiguous.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Query

from . import searxng, serp, stats
from .errors import WebkitError, classify

router = APIRouter()

PROFILES: dict[str, list[str]] = {
    "general": ["google", "duckduckgo", "bing"],
    "academic": ["semantic_scholar", "google_scholar", "openalex", "arxiv"],
    "code": ["github", "google"],
    "community": ["hackernews", "stackoverflow"],
}
TIME_RANGES = ("day", "week", "month", "year")
ALL_ENGINES = list(serp.ENGINES) + list(searxng.ENGINES)

_config_cache: tuple[float, dict] = (0.0, {})


async def _searxng_time_support() -> dict[str, bool]:
    global _config_cache
    if time.time() - _config_cache[0] > 600 or not _config_cache[1]:
        _config_cache = (time.time(), await searxng.config())
    by_name = {e.get("name"): e for e in _config_cache[1].get("engines", [])}
    return {cli: bool(by_name.get(sx, {}).get("time_range_support")) for cli, sx in searxng.ENGINES.items()}


async def time_support(engine: str) -> list[str]:
    if engine in serp.TIME_SUPPORT:
        return serp.TIME_SUPPORT[engine]
    return list(TIME_RANGES) if (await _searxng_time_support()).get(engine) else []


async def _call(engine: str, q: str, limit: int, time_range: str | None, lang: str | None) -> list[dict]:
    if engine in serp.ENGINES:
        return await serp.ENGINES[engine](q, limit, time_range, lang)
    return await searxng.search(engine, q, limit, time_range, lang)


@router.get("/v2/search")
async def search(
    q: str = Query(..., min_length=1),
    profile: str = Query("general"),
    engine: str | None = Query(None),
    limit: int = Query(10, ge=1, le=50),
    time: str | None = Query(None),
    lang: str | None = Query(None),
):
    if time is not None and time not in TIME_RANGES:
        raise WebkitError("invalid_request", f"time must be one of {', '.join(TIME_RANGES)}")
    if engine:
        if engine not in ALL_ENGINES:
            raise WebkitError("invalid_request", f"unknown engine '{engine}'", hint="see `webkit engines`")
        if time and time not in await time_support(engine):
            raise WebkitError("invalid_request", f"engine '{engine}' does not support time filter '{time}'",
                              hint="see `webkit engines` for per-engine time support")
        chain = [engine]
    else:
        if profile not in PROFILES:
            raise WebkitError("invalid_request", f"unknown profile '{profile}'",
                              hint=f"profiles: {', '.join(PROFILES)}")
        chain = PROFILES[profile]

    report: list[dict] = []
    results: list[dict] = []
    for name in chain:
        if results:
            report.append({"engine": name, "status": "skipped"})
            continue
        if time and time not in await time_support(name):
            report.append({"engine": name, "status": "skipped", "reason": f"no '{time}' time filter"})
            continue
        t0 = _now_ms()
        try:
            found = await _call(name, q, limit, time, lang)
        except Exception as e:  # noqa: BLE001 - every failure becomes a reported outcome
            err = classify(e)
            if err.error == "busy":
                raise err
            stats.record(name, "error", err.error, err.message)
            report.append({"engine": name, "status": "error", "error": err.error,
                           "message": err.message, "ms": _now_ms() - t0})
            continue
        status = "ok" if found else "empty"
        stats.record(name, status)
        report.append({"engine": name, "status": status, "count": len(found), "ms": _now_ms() - t0})
        for r in found:
            r["engine"] = name
        results = found

    tried = [r for r in report if r["status"] != "skipped"]
    if not results and tried and all(r["status"] == "error" for r in tried):
        classes = sorted({r["error"] for r in tried})
        raise WebkitError(
            "engines_failed",
            "every engine failed: " + "; ".join(f"{r['engine']}={r['error']}" for r in tried),
            hint=("the backend lost its route out; see `webkit status`" if set(classes) <= {"network", "timeout"}
                  else "an anti-bot check blocked the browser; solve it once via `webkit browser open URL` (noVNC)"
                  if set(classes) & {"captcha", "blocked", "human_required"}
                  else "see `webkit status` for engine health"),
            engines=report, classes=classes,
        )

    for i, r in enumerate(results, 1):
        r["rank"] = i
    return {
        "query": q,
        "profile": None if engine else profile,
        "time": time,
        "engines": report,
        "fallback_used": len(tried) > 1 and bool(results),
        "results": results,
    }


@router.get("/v2/engines")
async def engines():
    membership: dict[str, list[str]] = {}
    for prof, chain in PROFILES.items():
        for e in chain:
            membership.setdefault(e, []).append(prof)
    out = []
    for name in ALL_ENGINES:
        out.append({
            "engine": name,
            "kind": "crawler" if name in serp.ENGINES else "api",
            "time": await time_support(name),
            "profiles": membership.get(name, []),
        })
    return {"profiles": PROFILES, "engines": out}


def _now_ms() -> int:
    return int(time.monotonic() * 1000)
