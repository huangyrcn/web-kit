# skills/tests/test_auth_injection.py
import os, subprocess, sys, pathlib
SKILLS = pathlib.Path(__file__).resolve().parents[1]
ASK = SKILLS / "ask-search" / "scripts" / "ask-search"

def run(script, args, env):
    return subprocess.run([sys.executable, str(script), *args],
                          capture_output=True, text=True, env=env)

def test_ask_search_no_key_fails_fast():
    env = {**os.environ}; env.pop("WEB_KIT_API_KEY", None)
    env.pop("CLAUDE_PLUGIN_OPTION_WEB_KIT_API_KEY", None)
    env["SEARXNG_URL"] = "http://localhost:8082"
    r = run(ASK, ["test query"], env)
    assert r.returncode != 0, f"expected non-zero exit, got 0; stdout={r.stdout}"
    assert "WEB_KIT_API_KEY" in (r.stderr + r.stdout)

CDP_DL = SKILLS / "cdp-download" / "scripts" / "cdp-download"


def run_uv(script, args, env):
    """Run a uv --script file (installs PEP723 deps) instead of plain python."""
    return subprocess.run(["uv", "run", "--script", str(script), *args],
                          capture_output=True, text=True, env=env)


def test_cdp_download_no_key_fails_fast():
    env = {**os.environ}
    env.pop("WEB_KIT_API_KEY", None)
    env.pop("CLAUDE_PLUGIN_OPTION_WEB_KIT_API_KEY", None)
    env["CDP_URL"] = "http://localhost:9223"
    r = run_uv(CDP_DL, ["https://example.com/x.pdf"], env)
    assert r.returncode != 0, f"expected non-zero exit, got 0; stdout={r.stdout}"
    assert "WEB_KIT_API_KEY" in (r.stderr + r.stdout), (
        f"expected WEB_KIT_API_KEY in output; stderr={r.stderr!r} stdout={r.stdout!r}"
    )


if __name__ == "__main__":
    test_ask_search_no_key_fails_fast()
    print("ask-search PASS")
    test_cdp_download_no_key_fails_fast()
    print("cdp-download PASS")
