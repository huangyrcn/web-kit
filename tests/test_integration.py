"""End-to-end checks against a live web-kit, through the real CLI: a web-kit server
(remote mode) or this machine's Chrome (local mode).

    WEBKIT_INTEGRATION=1 WEBKIT_BACKEND=remote WEBKIT_URL=... WEBKIT_API_KEY=... [WEBKIT_ADMIN_KEY=...] \\
        PYTHONPATH=src pytest tests/test_integration.py
    WEBKIT_INTEGRATION=1 WEBKIT_BACKEND=local PYTHONPATH=src pytest tests/test_integration.py

Uses live sites, so a failure can be upstream; the error class printed by the CLI says
which. Anti-bot checks (exit 6 / 8) skip an engine instead of failing it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("WEBKIT_INTEGRATION") != "1",
                                reason="set WEBKIT_INTEGRATION=1 with a live web-kit")
REMOTE = os.environ.get("WEBKIT_BACKEND") == "remote"
remote_only = pytest.mark.skipif(not REMOTE, reason="remote mode only")

ARXIV_PDF = "https://arxiv.org/pdf/1706.03762"  # Attention Is All You Need
SHORT_PAGE = "https://info.cern.ch/hypertext/WWW/TheProject.html"
LONG_PAGE = "https://en.wikipedia.org/wiki/Knowledge_graph"


def webkit(*args, timeout=300, env=None):
    proc = subprocess.run([sys.executable, "-m", "webkit.cli", *args], capture_output=True, text=True,
                          timeout=timeout, env={**os.environ, **(env or {})})
    return proc.returncode, proc.stdout, proc.stderr


def last_error(err: str) -> dict:
    return json.loads(err.strip().splitlines()[-1])


@remote_only
def test_auth_is_enforced_and_healthz_says_only_ok():
    code, _, err = webkit("status", env={"WEBKIT_API_KEY": "wrong"})
    assert code == 4, err
    base = os.environ["WEBKIT_URL"]
    with urllib.request.urlopen(base + "/healthz", timeout=30) as r:
        assert json.load(r) == {"ok": True}
    req = urllib.request.Request(base + "/v2/nothing", headers={"X-API-Key": os.environ["WEBKIT_API_KEY"]})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=15)
    assert e.value.code == 404


def test_status():
    code, out, err = webkit("status", "--json")
    data = json.loads(out)
    assert code == 0 and data["extension_connected"] and data["plugin"], data
    if REMOTE:
        assert data["mode"] == "server" and data["chrome"], data


def test_engines_listing():
    code, out, _ = webkit("engines", "--json")
    names = {e["engine"] for e in json.loads(out)["engines"]}
    assert {"google", "semantic_scholar", "google_scholar", "arxiv", "biorxiv", "medrxiv"} <= names
    assert not names & {"brave", "duckduckgo", "bing"}


@pytest.mark.parametrize("engine,query", [
    ("google", "temporal knowledge graph forecasting"),
    ("google_scholar", "temporal knowledge graph forecasting"),
    ("semantic_scholar", "temporal knowledge graph forecasting"),
    ("openalex", "temporal knowledge graph forecasting"),
    ("arxiv", "temporal knowledge graph"),
    ("pubmed", "spatial transcriptomics"),
    ("dblp", "graph transformers"),
    ("biorxiv", "spatial transcriptomics"),
    ("medrxiv", "spatial transcriptomics"),
    ("hackernews", "graph neural networks"),
    ("stackoverflow", "python asyncio"),
    ("cnki", "图神经网络"),
    ("wanfang", "图神经网络"),
    ("baidu_scholar", "图神经网络"),
])
def test_each_engine_answers_itself(engine, query):
    """A profile hides a broken engine behind its fallback; test each one directly."""
    code, out, err = webkit("search", "-e", engine, "-n", "3", query)
    if code in (3, 6, 8) and any(c in err for c in ("human_required", "blocked", "captcha")):
        pytest.skip(f"{engine}: anti-bot check (a person can pass it): {err.splitlines()[-1][:160]}")
    assert code == 0, (out, err)
    assert out.startswith(f"# engines {engine}=ok:"), out
    assert "http" in out
    if engine == "google":  # real result URLs, not Google's opaque /goto redirects
        assert "google.com/goto?" not in out, out


def test_read_a_short_page_with_numbered_links():
    code, out, err = webkit("read", SHORT_PAGE)
    assert code == 0, err
    assert "[1]: https://" in out and "](http" not in out


def test_read_a_long_page_prints_its_outline():
    code, out, err = webkit("read", LONG_PAGE)
    assert code == 0, err
    assert len(out) <= 2600 and "outline (line: heading" in out and "saved to" in out, out


def test_read_links():
    code, out, err = webkit("read", "-f", "links", SHORT_PAGE)
    assert code == 0 and out.startswith("1\thttps://"), (out, err)


def test_read_sends_files_to_download():
    code, _, err = webkit("read", ARXIV_PDF)
    assert code == 3 and last_error(err)["error"] == "not_a_page", err


def test_download_pdf(tmp_path):
    code, out, err = webkit("download", ARXIV_PDF, "-o", str(tmp_path) + "/", timeout=300)
    assert code == 0, err
    [f] = list(tmp_path.iterdir())
    assert f.read_bytes()[:5] == b"%PDF-" and f.suffix == ".pdf"


def test_download_resolves_a_paper_page(tmp_path):
    code, out, err = webkit("download", "arXiv:2403.01092", "-o", str(tmp_path) + "/", timeout=300)
    assert code == 0, err
    [f] = list(tmp_path.iterdir())
    assert f.read_bytes()[:5] == b"%PDF-" and "linked from the page" in out


def test_download_of_a_web_page_is_not_a_file():
    code, _, err = webkit("download", SHORT_PAGE, timeout=180)
    assert code == 3 and last_error(err)["error"] == "not_a_file", err


def test_download_http_error():
    code, _, err = webkit("download", "https://info.cern.ch/definitely-missing-file.pdf")
    assert code == 3 and last_error(err)["error"] == "not_found", err


@remote_only
@pytest.mark.skipif(not os.environ.get("WEBKIT_ADMIN_KEY"), reason="admin key not provided")
def test_doctor_with_admin():
    code, out, err = webkit("doctor")
    assert "✓ server reachable" in out and "✓ admin key accepted" in out, out + err


def test_datadome_wall_is_classified_never_content():
    # ResearchGate (DataDome) answers distrusted egress with a CAPTCHA (exit 6) or a ban (exit 8).
    code, _, err = webkit("read", "https://www.researchgate.net/", timeout=180)
    if code == 0:
        pytest.skip("ResearchGate let this egress through")
    assert (code, last_error(err)["error"]) in ((6, "human_required"), (8, "blocked")), err
