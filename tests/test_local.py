"""Local mode (src/webkit/local.py) with a fake opencli and fake HTTP: offline."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from webkit import cli, local
from webkit.client import ApiError
from webkit.config import Config


def ok(data) -> str:
    return json.dumps({"ok": True, "data": data})


def fail(code: str, msg: str = "x", exit_code: int = 1) -> tuple[int, str, str]:
    return exit_code, json.dumps({"ok": False, "error": {"code": code, "message": msg, "exitCode": exit_code}}), ""


@pytest.fixture
def opencli(monkeypatch):
    """answers: first word sequence of the opencli args (e.g. 'google search', 'web read') -> reply."""
    answers: dict = {}
    calls: list[list[str]] = []

    def fake(cfg, args, timeout=local.OPENCLI_TIMEOUT, profile=True):
        calls.append(args)
        key = " ".join(args[:2])
        reply = answers.get(key)
        if reply is None:
            return 0, ok([]), ""
        reply = reply.pop(0) if isinstance(reply, list) else reply
        return reply if isinstance(reply, tuple) else (0, reply, "")

    monkeypatch.setattr(local, "_run_opencli", fake)
    return answers, calls


CFG = Config(backend="local", profile="main", s2_api_key="k")
WEB = "# Title\n> 原文链接: {url}\n\n---\n\n{body}"


# ─── engines and search ────────────────────────────────────────────────────────

def test_engine_rows_map_onto_the_result_shape(opencli):
    answers, calls = opencli
    answers["google-scholar search"] = ok([{"rank": 1, "title": "GNN", "url": "https://n.org/x", "authors": "G Corso",
                                            "source": "Nature", "year": "2024", "cited": "788"},
                                           {"rank": 2, "title": "", "url": "https://n.org/y"}])
    [r] = local.run_engine(CFG, "google_scholar", "-gnn", 99)
    assert r == {"title": "GNN", "url": "https://n.org/x", "snippet": "", "published": "2024",
                 "extra": {"byline": "G Corso - Nature", "citations": "788"}}
    assert calls[0] == ["google-scholar", "search", "--limit", "20", "-f", "json", "--", "-gnn"]


def test_google_keeps_only_web_results(opencli):
    answers, _ = opencli
    answers["google search"] = ok([{"type": "paa", "title": "Q?", "url": "https://q"},
                                   {"type": "result", "title": "A", "url": "https://a", "snippet": "s"}])
    assert [r["url"] for r in local.run_engine(CFG, "google", "x", 5)] == ["https://a"]


def test_profile_falls_back_and_reports_every_engine(opencli):
    answers, _ = opencli
    answers["semanticscholar search"] = fail("COMMAND_EXEC", "HTTP 500")
    answers["google-scholar search"] = ok([{"title": "T", "url": "https://t", "year": "2020"}])
    out = local.search(CFG, {"q": "gnn", "profile": "academic", "limit": 5})
    assert [(e["engine"], e["status"]) for e in out["engines"]] == [
        ("semantic_scholar", "error"), ("google_scholar", "ok"), ("openalex", "skipped"), ("arxiv", "skipped")]
    assert out["results"][0]["engine"] == "google_scholar" and out["fallback_used"] is True
    assert cli.engines_header(out["engines"]).startswith("# engines semantic_scholar=error:upstream_http google_scholar=ok:1")


def test_a_browser_engine_answering_nothing_is_checked_for_a_wall(opencli):
    answers, calls = opencli
    answers["google-scholar search"] = fail("EMPTY_RESULT", "no results", 66)
    answers["web read"] = (0, WEB.format(url="https://scholar.google.com/sorry/index",
                                         body="Our systems have detected unusual traffic from your computer network."), "")
    with pytest.raises(ApiError) as e:
        local.run_engine(CFG, "google_scholar", "gnn", 5)
    assert e.value.error == "human_required" and calls[-1][:2] == ["web", "read"]
    answers["web read"] = (0, WEB.format(url="https://scholar.google.com/x", body="Your search did not match any articles."), "")
    answers["google-scholar search"] = fail("EMPTY_RESULT", "no results", 66)
    assert local.run_engine(CFG, "google_scholar", "gnn", 5) == []


def test_api_engines_trust_an_empty_answer(opencli):
    answers, calls = opencli
    answers["openalex search"] = ok([])
    assert local.run_engine(CFG, "openalex", "x", 5) == [] and all(c[:2] != ["web", "read"] for c in calls)


@pytest.mark.parametrize("reply,cls", [
    (fail("BROWSER_CONNECT", "extension not connected", 69), "unreachable"),
    (fail("attach_failed", "Cannot access a chrome-extension:// URL of different extension"), "browser_conflict"),
    (fail("SESSION_BUSY", "slot wait timeout", 75), "busy"),
    (fail("AUTH_REQUIRED", "login", 77), "human_required"),
    (fail("COMMAND_EXEC", "HTTP 500"), "upstream_http"),
])
def test_opencli_failures_map_to_webkit_classes(opencli, reply, cls):
    answers, _ = opencli
    answers["google search"] = reply
    with pytest.raises(ApiError) as e:
        local.run_engine(CFG, "google", "x", 5)
    assert e.value.error == cls and (cls != "browser_conflict" or "Immersive Translate" in e.value.body["hint"])


def test_every_engine_failing_is_one_error_with_a_hint(opencli):
    answers, _ = opencli
    answers["google search"] = fail("BROWSER_CONNECT", "x", 69)
    answers["brave search"] = fail("BROWSER_CONNECT", "x", 69)
    with pytest.raises(ApiError) as e:
        local.search(CFG, {"q": "x"})
    assert e.value.error == "engines_failed" and "open Chrome" in e.value.body["hint"]
    assert e.value.exit_code == 3


def test_time_filter_skips_engines_and_unknown_names_are_usage_errors(opencli):
    out = local.search(CFG, {"q": "x", "time": "week", "engine": "google"})
    assert out["engines"] == [{"engine": "google", "status": "skipped", "reason": "no 'week' time filter"}]
    for params in ({"q": "x", "engine": "bing"}, {"q": "x", "profile": "code"}):
        with pytest.raises(ApiError) as e:
            local.search(CFG, params)
        assert e.value.exit_code == 1


# ─── pages ─────────────────────────────────────────────────────────────────────

def test_read_returns_markdown_without_opencli_header(opencli):
    answers, calls = opencli
    answers["web read"] = (0, WEB.format(url="https://e.org/final", body="Hello [a](https://e.org/a) [b](https://x.org/b)"), "")
    out = local.page(CFG, {"url": "https://e.org/", "format": "fit", "wait_for": ".row"})
    assert out == {"url": "https://e.org/", "final_url": "https://e.org/final", "title": "Title", "kind": "html",
                   "format": "fit", "content": "Hello [a](https://e.org/a) [b](https://x.org/b)"}
    assert calls[-1][-2:] == ["--wait-for", ".row"] and "--download-images" in calls[-1]
    links = local.page(CFG, {"url": "https://e.org/", "format": "links"})["links"]
    assert [(l["href"], l["scope"]) for l in links] == [("https://e.org/a", "internal"), ("https://x.org/b", "external")]


def test_read_refuses_wall_pages_and_empty_pages(opencli):
    answers, _ = opencli
    answers["web read"] = (0, WEB.format(url="https://e.org/", body="Just a moment... Checking your browser"), "")
    with pytest.raises(ApiError) as e:
        local.page(CFG, {"url": "https://e.org/"})
    assert e.value.exit_code == 6
    answers["web read"] = (0, WEB.format(url="https://e.org/", body="You have been blocked. Error 1020"), "")
    with pytest.raises(ApiError) as e:
        local.page(CFG, {"url": "https://e.org/"})
    assert e.value.exit_code == 8
    answers["web read"] = (0, "# untitled\n> 原文链接: https://e.org/\n\n---\n", "")
    with pytest.raises(ApiError) as e:
        local.page(CFG, {"url": "https://e.org/"})
    assert e.value.error == "unexpected_page"


def test_read_html_format_is_not_available_locally(opencli):
    with pytest.raises(ApiError) as e:
        local.page(CFG, {"url": "https://e.org/", "format": "html"})
    assert e.value.exit_code == 1


def _tiny_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


@pytest.fixture
def web(monkeypatch):
    """Fake plain HTTP: url -> (content type, body)."""
    pages: dict = {}

    def fake_fetch(url, max_bytes, accept="*/*"):
        if url not in pages:
            raise local.err("not_found", f"{url} answered HTTP 404")
        ctype, body = pages[url]
        return url, ctype, body, {"Content-Type": ctype}

    monkeypatch.setattr(local, "fetch", fake_fetch)
    return pages


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="needs pdftotext")
def test_pdfs_are_fetched_directly_and_turned_into_text(opencli, web):
    web["https://arxiv.org/pdf/1810.00826"] = ("application/pdf", _tiny_pdf("Hello web-kit"))
    out = local.page(CFG, {"url": "https://arxiv.org/pdf/1810.00826"})
    assert out["kind"] == "pdf" and out["pages"] == 1 and "<!-- page 1 -->" in out["content"]
    assert "Hello web-kit" in out["content"]
    pdf_page = local.page(CFG, {"url": "https://arxiv.org/abs/1810.00826", "pdf": True})
    assert pdf_page["pdf_url"] == "https://arxiv.org/pdf/1810.00826"


def test_pdf_behind_a_doi_or_a_paper_page(opencli, web):
    answers, calls = opencli
    answers["openalex work"] = ok({"openAccessUrl": "https://repo.org/paper.pdf"})
    assert local.resolve_pdf(CFG, "https://doi.org/10.1/x") == "https://repo.org/paper.pdf"
    assert calls[-1] == ["openalex", "work", "-f", "json", "--", "10.1/x"]
    web["https://pub.org/article"] = ("text/html", b'<meta name="citation_pdf_url" content="/article.pdf">')
    assert local.resolve_pdf(CFG, "https://pub.org/article") == "https://pub.org/article.pdf"
    web["https://pub.org/nolink"] = ("text/html", b"<html>no meta</html>")
    assert local.resolve_pdf(CFG, "https://pub.org/nolink") is None


def test_a_non_pdf_answer_is_not_a_file(opencli, web):
    web["https://pub.org/x.pdf"] = ("text/html", b"<html>login</html>")
    with pytest.raises(ApiError) as e:
        local.page(CFG, {"url": "https://pub.org/x.pdf"})
    assert e.value.error == "not_a_file"


def test_download_streams_the_file_with_the_backend_headers(opencli, web):
    web["https://arxiv.org/pdf/1810.00826"] = ("application/pdf", b"%PDF-1.4 tiny")
    resp = local.download(CFG, {"url": "https://arxiv.org/abs/1810.00826"})
    with resp:
        assert resp.read() == b"%PDF-1.4 tiny"
    assert resp.headers["X-Webkit-Filename"] == "1810.00826.pdf" and "X-Webkit-Resolved-From" in resp.headers
    web["https://blog.org/post"] = ("text/html", b"<html>post</html>")
    with pytest.raises(ApiError) as e:
        local.download(CFG, {"url": "https://blog.org/post"})
    assert e.value.error == "not_a_file"


# ─── opencli process, status, CLI wiring ──────────────────────────────────────

def test_opencli_runs_with_profile_key_and_warm_tabs(monkeypatch):
    seen = {}

    def fake_run(argv, capture_output, text, timeout, env):
        seen.update(argv=argv, env=env)
        return subprocess.CompletedProcess(argv, 0, ok([]), "")

    monkeypatch.setattr(local.subprocess, "run", fake_run)
    local._run_opencli(Config(opencli="/opt/n/bin/opencli", profile="main", s2_api_key="k"), ["google", "search"])
    assert seen["argv"] == ["/opt/n/bin/opencli", "--profile", "main", "google", "search"]
    assert seen["env"]["SEMANTIC_SCHOLAR_API_KEY"] == "k" and seen["env"]["PATH"].startswith("/opt/n/bin")
    assert "google-scholar:1" in seen["env"]["OPENCLI_SITE_SLOTS"] and seen["env"]["OPENCLI_WINDOW"] == "background"


def test_status_reports_the_extension_for_the_configured_profile(opencli, monkeypatch):
    answers, _ = opencli
    answers["--version"] = (0, "2.0.3\n", "")
    answers["profile list"] = (0, ok({"profiles": [{"alias": "main", "contextId": "c1", "connected": True,
                                                    "default": True}]}), "")
    answers["plugin list"] = (0, ok([{"name": "webkit", "commands": ["rxiv"]}]), "")
    st = local.status(CFG)
    assert st["opencli"] == "2.0.3" and st["extension_connected"] is True and st["error"] is None and st["plugin"]
    st = local.status(Config(profile="work"))
    assert st["extension_connected"] is False and "work" in st["error"]


def test_cli_runs_locally_by_default(opencli, monkeypatch, tmp_path, capsys):
    answers, _ = opencli
    monkeypatch.setenv("WEBKIT_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("WEBKIT_CACHE_DIR", str(tmp_path / "cache"))
    for v in ("WEBKIT_BACKEND", "WEBKIT_URL"):
        monkeypatch.delenv(v, raising=False)
    answers["google search"] = ok([{"type": "result", "title": "A", "url": "https://a", "snippet": "s"}])
    with pytest.raises(SystemExit) as e:
        cli.main(["search", "x"])
    assert e.value.code == 0 and capsys.readouterr().out.splitlines()[0] == "# engines google=ok:1 brave=skipped"
    with pytest.raises(SystemExit) as e:
        cli.main(["crawl", "https://e.org"])
    assert e.value.code == 1


def test_rate_limited_api_engines_retry(opencli, monkeypatch):
    answers, calls = opencli
    monkeypatch.setattr(local.time, "sleep", lambda s: None)
    answers["semanticscholar search"] = [fail("COMMAND_EXEC", "returned HTTP 429 (rate limited)"),
                                         ok([{"title": "T", "url": "https://s/1", "year": 2020}])]
    assert len(local.run_engine(CFG, "semantic_scholar", "x", 5)) == 1 and len(calls) == 2
    answers["google search"] = [fail("COMMAND_EXEC", "HTTP 429")]
    with pytest.raises(ApiError):
        local.run_engine(CFG, "google", "x", 5)  # browser engines: no retry
    assert len(calls) == 3


def test_download_into_a_new_directory(opencli, web, monkeypatch, tmp_path):
    monkeypatch.setenv("WEBKIT_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.delenv("WEBKIT_BACKEND", raising=False)
    web["https://arxiv.org/pdf/2403.01092"] = ("application/pdf", b"%PDF-1.4 tiny")
    with pytest.raises(SystemExit) as e:
        cli.main(["download", "-o", str(tmp_path / "papers") + "/", "arXiv:2403.01092"])
    assert e.value.code == 0 and (tmp_path / "papers" / "2403.01092.pdf").read_bytes() == b"%PDF-1.4 tiny"


def test_chinese_engines_report_their_security_checks(opencli):
    answers, _ = opencli
    answers["cnki search"] = ok([])
    answers["web read"] = (0, WEB.format(url="https://oversea.cnki.net/kns/search",
                                         body="Please complete security verification\nSwipe right to complete verification"), "")
    with pytest.raises(ApiError) as e:
        local.run_engine(CFG, "cnki", "图神经网络", 5)
    assert e.value.exit_code == 6


def test_preprint_engines_come_from_the_webkit_plugin(opencli):
    answers, calls = opencli
    answers["biorxiv search"] = ok([{"rank": 1, "title": "Spatial deconvolution by Redeconve",
                                     "authors": "Zixiang Zhou, Yunshan Zhong", "date": "2022-12-22",
                                     "doi": "10.1101/2022.12.22.521551",
                                     "url": "https://www.biorxiv.org/content/10.1101/2022.12.22.521551v1"}])
    [r] = local.run_engine(CFG, "biorxiv", "spatial deconvolution", 5)
    assert r["published"] == "2022-12-22" and r["extra"] == {"authors": ["Zixiang Zhou", "Yunshan Zhong"],
                                                             "doi": "10.1101/2022.12.22.521551"}
    assert calls[-1][:2] == ["biorxiv", "search"]
    answers["medrxiv search"] = (2, json.dumps({"ok": False, "error": {"code": "unknown_command",
                                                                       "message": "Unknown command: medrxiv"}}), "")
    with pytest.raises(ApiError) as e:
        local.run_engine(CFG, "medrxiv", "x", 5)
    assert e.value.error == "engine_unavailable" and "opencli plugin install" in e.value.body["hint"]


def test_an_empty_answer_is_checked_in_the_title_and_on_long_pages(opencli):
    answers, _ = opencli
    answers["baidu-scholar search"] = ok([])
    answers["web read"] = (0, "# 百度安全验证\n> 原文链接: https://xueshu.baidu.com/s\n\n---\n", "")
    with pytest.raises(ApiError) as e:
        local.run_engine(CFG, "baidu_scholar", "图神经网络", 5)
    assert e.value.exit_code == 6
    answers["wanfang search"] = ok([])
    body = "## 来自 iframe: https://www.wanfangdata.com.cn/verify?ip=x\n" + "导航 " * 3000
    answers["web read"] = (0, WEB.format(url="https://s.wanfangdata.com.cn/paper", body=body), "")
    with pytest.raises(ApiError) as e:
        local.run_engine(CFG, "wanfang", "图神经网络", 5)
    assert e.value.exit_code == 6
