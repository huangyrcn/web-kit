# skills/tests/test_auth_injection.py
import os, subprocess, sys, pathlib
SKILLS = pathlib.Path(__file__).resolve().parents[1]
SEARXNG_SEARCH = SKILLS / "searxng-search" / "scripts" / "searxng-search"

def run(script, args, env):
    return subprocess.run([sys.executable, str(script), *args],
                          capture_output=True, text=True, env=env)

def test_searxng_search_no_key_fails_fast():
    env = {**os.environ}; env.pop("WEB_KIT_API_KEY", None)
    env.pop("CLAUDE_PLUGIN_OPTION_WEB_KIT_API_KEY", None)
    env["SEARXNG_URL"] = "http://localhost:8082"
    r = run(SEARXNG_SEARCH, ["test query"], env)
    assert r.returncode != 0, f"expected non-zero exit, got 0; stdout={r.stdout}"
    assert "WEB_KIT_API_KEY" in (r.stderr + r.stdout)

PAGE = SKILLS / "browser-fetch" / "scripts" / "page"


def run_uv(script, args, env):
    """Run a uv --script file (installs PEP723 deps) instead of plain python."""
    return subprocess.run(["uv", "run", "--script", str(script), *args],
                          capture_output=True, text=True, env=env)


def test_page_no_key_fails_fast():
    env = {**os.environ}
    env.pop("WEB_KIT_API_KEY", None)
    env.pop("CLAUDE_PLUGIN_OPTION_WEB_KIT_API_KEY", None)
    env["CDP_URL"] = "http://localhost:9223"
    r = run_uv(PAGE, ["https://example.com"], env)
    assert r.returncode != 0, f"expected non-zero exit, got 0; stdout={r.stdout}"
    assert "WEB_KIT_API_KEY" in (r.stderr + r.stdout)


FILE = SKILLS / "browser-fetch" / "scripts" / "file"


def test_file_no_key_fails_fast():
    env = {**os.environ}
    env.pop("WEB_KIT_API_KEY", None)
    env.pop("CLAUDE_PLUGIN_OPTION_WEB_KIT_API_KEY", None)
    env["CDP_URL"] = "http://localhost:9223"
    r = run_uv(FILE, ["https://example.com/x.pdf"], env)
    assert r.returncode != 0, f"expected non-zero exit, got 0; stdout={r.stdout}"
    assert "WEB_KIT_API_KEY" in (r.stderr + r.stdout), (
        f"expected WEB_KIT_API_KEY in output; stderr={r.stderr!r} stdout={r.stdout!r}"
    )


if __name__ == "__main__":
    test_searxng_search_no_key_fails_fast()
    print("searxng-search PASS")
    test_page_no_key_fails_fast()
    print("browser-fetch page PASS")
    test_file_no_key_fails_fast()
    print("browser-fetch file PASS")
