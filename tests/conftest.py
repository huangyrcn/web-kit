"""Shared test setup."""

import pytest


@pytest.fixture(autouse=True)
def events_log(monkeypatch, tmp_path):
    """Every test writes its events log under tmp_path, never into the real state directory."""
    monkeypatch.setenv("WEBKIT_STATE_DIR", str(tmp_path / "state"))
    for name in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "AI_AGENT", "CODEX_THREAD_ID", "WEBKIT_LOG"):
        monkeypatch.delenv(name, raising=False)
    from webkit import events
    events.caller.cache_clear()
    yield tmp_path / "state" / "events.jsonl"
    events.caller.cache_clear()
