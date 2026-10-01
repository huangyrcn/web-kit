"""End-to-end checks against a running v2 backend, through the real CLI.

    WEBKIT_INTEGRATION=1 WEBKIT_URL=... WEBKIT_API_KEY=... [WEBKIT_ADMIN_KEY=...] pytest tests/test_integration.py

Uses live sites (Google, arXiv, example.com), so a failure can be upstream; the
error class printed by the CLI says which.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("WEBKIT_INTEGRATION") != "1",
                                reason="set WEBKIT_INTEGRATION=1 with a live backend")

ARXIV_PDF = "https://arxiv.org/pdf/1706.03762"  # Attention Is All You Need


def webkit(*args, timeout=300, env=None):
    proc = subprocess.run([sys.executable, "-m", "webkit.cli", *args], capture_output=True, text=True,
                          timeout=timeout, env={**os.environ, **(env or {})})
    return proc.returncode, proc.stdout, proc.stderr


def test_auth_is_enforced():
    code, _, err = webkit("status", env={"WEBKIT_API_KEY": "wrong"})
    assert code == 4, err
    base = os.environ["WEBKIT_URL"]
    for path in ("/search?q=x&format=json", "/internal/health", "/"):  # SearXNG / internals never exposed
        req = urllib.request.Request(base + path, headers={"X-API-Key": os.environ["WEBKIT_API_KEY"]})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=15)
        assert e.value.code == 404


def test_status_and_version():
    code, out, err = webkit("status", "--json")
    data = json.loads(out)
    assert data["browser"]["connected"], data
    assert data["version"]["api"] == "v2" and data["version"]["chrome"] != "unknown"


def test_engines_listing():
    code, out, _ = webkit("engines", "--json")
    data = json.loads(out)
    names = {e["engine"] for e in data["engines"]}
    assert {"google", "duckduckgo", "semantic_scholar", "arxiv", "bing", "github"} <= names


@pytest.mark.parametrize("args,expect_engine", [
    (("search", "LANTERN temporal knowledge graph forecasting"), None),
    (("search", "-p", "academic", "temporal knowledge graph forecasting LLM"), None),
    (("search", "-e", "github", "paper-search-mcp"), "github"),
    (("search", "-e", "arxiv", "temporal graph link prediction"), "arxiv"),
])
def test_search(args, expect_engine):
    code, out, err = webkit(*args)
    assert code == 0, (out, err)
    header = out.splitlines()[0]
    assert header.startswith("# engines "), header
    if expect_engine:
        assert f"{expect_engine}=ok:" in header


@pytest.mark.parametrize("engine,query", [
    ("google", "LANTERN temporal knowledge graph forecasting"),
    ("duckduckgo", "LANTERN temporal knowledge graph forecasting"),
    ("bing", "temporal knowledge graph forecasting"),
    ("google_scholar", "temporal knowledge graph forecasting"),
    ("semantic_scholar", "temporal knowledge graph forecasting"),
    ("arxiv", "temporal knowledge graph"),
])
def test_each_engine_answers_itself(engine, query):
    """A profile hides a broken engine behind its fallback; test each one directly."""
    code, out, err = webkit("search", "-e", engine, "-n", "3", query)
    if code == 3 and any(f"={c}" in err for c in ("captcha", "blocked", "human_required")):
        pytest.skip(f"{engine}: upstream anti-bot challenge (solve once via noVNC): {err.splitlines()[-1][:160]}")
    assert code == 0, (out, err)
    assert out.splitlines()[0].startswith(f"# engines {engine}=ok:"), out.splitlines()[0]
    assert "http" in out


def test_google_time_filter_returns_full_page():
    code, out, err = webkit("search", "--json", "-e", "google", "--time", "week", "Claude Code")
    assert code == 0, err
    assert len(json.loads(out)["results"]) >= 4  # a week of "Claude Code" fills a results page


def test_search_time_filter_skips_unsupported():
    code, out, err = webkit("search", "--json", "-p", "academic", "--time", "week", "graph neural network")
    data = json.loads(out) if out else json.loads(err.splitlines()[-1])
    report = {e["engine"]: e["status"] for e in data.get("engines", [])}
    assert report.get("semantic_scholar") == "skipped" and report.get("arxiv") == "skipped"


def test_read_html():
    code, out, err = webkit("read", "--json", "https://example.com")
    page = json.loads(out)
    assert code == 0 and page["title"] == "Example Domain" and page["content"], err


def test_read_fit_drops_site_chrome(tmp_path):
    code, out, err = webkit("read", "-o", str(tmp_path / "kg.md"), "https://en.wikipedia.org/wiki/Knowledge_graph")
    assert code == 0, err
    head = (tmp_path / "kg.md").read_text()[:600]
    assert "Main menu" not in head and "knowledge" in head.lower()


def test_long_page_prints_a_preview_and_a_path():
    code, out, err = webkit("read", "https://en.wikipedia.org/wiki/Knowledge_graph")
    assert code == 0, err
    assert len(out) <= 4400 and "the full text is in" in out


def test_read_pdf_as_text():
    code, out, err = webkit("read", "--max-chars", "4000", ARXIV_PDF, timeout=180)
    assert code == 0, err
    assert "<!-- page 1 -->" in out and "Attention" in out


def test_read_links():
    code, out, err = webkit("read", "-f", "links", "https://example.com")
    assert code == 0 and "iana.org" in out, err


def test_crawl(tmp_path):
    code, out, err = webkit("crawl", "https://example.com", "-o", str(tmp_path), "--max-pages", "2",
                            "--max-depth", "1", timeout=300)
    assert code == 0, (out, err)
    index = json.loads((tmp_path / "index.json").read_text())
    assert index and any("file" in e for e in index)


def test_download_pdf(tmp_path):
    code, out, err = webkit("download", ARXIV_PDF, "-o", str(tmp_path) + "/", timeout=300)
    assert code == 0, err
    [f] = list(tmp_path.iterdir())
    assert f.read_bytes()[:5] == b"%PDF-" and f.suffix == ".pdf"


def test_download_http_error():
    code, _, err = webkit("download", "https://example.com/definitely-missing-file.pdf")
    assert code == 3 and json.loads(err.splitlines()[-1])["error"] == "upstream_http"


@pytest.mark.skipif(not os.environ.get("WEBKIT_ADMIN_KEY"), reason="admin key not provided")
def test_doctor_with_admin():
    code, out, err = webkit("doctor")
    assert "✓ backend reachable" in out and "✓ admin key accepted" in out, out + err


def test_read_fit_has_no_skip_links(tmp_path):
    code, out, err = webkit("read", "-o", str(tmp_path / "s.md"),
                            "https://link.springer.com/article/10.1007/s11263-015-0816-y", timeout=180)
    assert code == 0, err
    head = (tmp_path / "s.md").read_text()[:1500]
    assert "Skip to" not in head and "ImageNet" in head


def test_read_pdf_flag_follows_the_paper_page():
    code, out, err = webkit("read", "--pdf", "--json", "--max-chars", "3000", "arXiv:2403.01092", timeout=240)
    assert code == 0, err
    page = json.loads(out)
    assert page["kind"] == "pdf" and page["pdf_url"].startswith("https://arxiv.org/pdf/2403.01092")
    assert "<!-- page 1 -->" in page["content"]


def test_download_resolves_a_paper_page(tmp_path):
    code, out, err = webkit("download", "arXiv:2403.01092", "-o", str(tmp_path) + "/", timeout=300)
    assert code == 0, err
    [f] = list(tmp_path.iterdir())
    assert f.read_bytes()[:5] == b"%PDF-" and "linked from the page" in out


def test_download_of_a_web_page_is_not_a_file():
    code, _, err = webkit("download", "https://example.com", timeout=180)
    assert code == 3 and json.loads(err.splitlines()[-1])["error"] == "not_a_file", err


def test_datadome_wall_is_classified_never_content():
    # ResearchGate (DataDome) answers datacenter egress with a CAPTCHA (exit 6) or, after
    # repeated visits, a ban (exit 8); unit tests pin which marker means which.
    code, _, err = webkit("read", "https://www.researchgate.net/", timeout=180)
    if code == 0:
        pytest.skip("ResearchGate let this egress through")
    assert (code, json.loads(err.splitlines()[-1])["error"]) in ((6, "human_required"), (8, "blocked")), err


def test_google_query_that_used_to_get_ai_mode():
    code, out, err = webkit("search", "-e", "google",
                            'Wu Xie "V*: Guided Visual Search as a Core Mechanism for Multimodal LLMs" CVPR 2024 arxiv')
    if code == 3 and "captcha" in err:
        pytest.skip("google: upstream anti-bot challenge")
    assert code == 0 and "google=ok" in out.splitlines()[0], (out, err)
    assert "guided visual search" in out.lower()

