"""Per-engine outcome history, so `/v2/status` can say which engines work now."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

WINDOW = 20


@dataclass
class EngineState:
    recent: deque = field(default_factory=lambda: deque(maxlen=WINDOW))  # (ts, status)
    last_ok: float | None = None
    last_error: dict | None = None

    def record(self, status: str, error: str | None = None, message: str = "") -> None:
        now = time.time()
        self.recent.append((now, status))
        if status in ("ok", "empty"):
            self.last_ok = now
        else:
            self.last_error = {"class": error, "message": message[:300], "at": _iso(now)}

    def summary(self) -> dict:
        statuses = [s for _, s in self.recent]
        errors = sum(1 for s in statuses if s == "error")
        if not statuses:
            state = "unknown"
        elif all(s == "error" for s in statuses[-3:]):
            state = "down"
        elif errors:
            state = "degraded"
        else:
            state = "ok"
        return {
            "state": state,
            "calls": len(statuses),
            "errors": errors,
            "last_ok": _iso(self.last_ok) if self.last_ok else None,
            "last_error": self.last_error,
        }


_engines: dict[str, EngineState] = {}


def record(engine: str, status: str, error: str | None = None, message: str = "") -> None:
    _engines.setdefault(engine, EngineState()).record(status, error, message)


def snapshot() -> dict[str, dict]:
    return {name: st.summary() for name, st in sorted(_engines.items())}


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
