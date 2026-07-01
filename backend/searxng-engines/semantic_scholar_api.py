# SPDX-License-Identifier: AGPL-3.0-or-later
"""Semantic Scholar Academic Graph API engine.

Uses the official Graph API and authenticates with an API key via the
case-sensitive ``x-api-key`` header.
"""

import os
import typing as t

from datetime import datetime
from logging import getLogger
from urllib.parse import urlencode

from searx.exceptions import SearxEngineAPIException
from searx.result_types import EngineResults

if t.TYPE_CHECKING:
    from searx.extended_types import SXNG_Response
    from searx.search.processors import OnlineParams

about = {
    "website": "https://www.semanticscholar.org/",
    "wikidata_id": "Q22908627",
    "official_api_documentation": "https://api.semanticscholar.org/api-docs/",
    "use_official_api": True,
    "require_api_key": True,
    "results": "JSON",
}

categories = ["science", "scientific publications"]
paging = True
base_url = "https://api.semanticscholar.org/graph/v1/paper/search"
page_size = 10

api_key = ""
logger = getLogger(__name__)

fields = [
    "title",
    "url",
    "abstract",
    "year",
    "publicationDate",
    "authors",
    "citationCount",
    "venue",
    "journal",
    "externalIds",
    "openAccessPdf",
    "publicationTypes",
    "fieldsOfStudy",
]


def setup(engine_settings: dict[str, t.Any]) -> bool:
    global api_key
    key = engine_settings.get("api_key") or ""
    if key in ("unset", "unknown", "...", "YOUR_SEMANTIC_SCHOLAR_API_KEY"):
        key = ""
    key = key or os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")
    if key:
        api_key = key
        return True
    logger.error("Semantic Scholar Graph API key is not set or invalid.")
    return False


def request(query: str, params: "OnlineParams") -> None:
    args = {
        "query": query,
        "limit": page_size,
        "offset": page_size * (params["pageno"] - 1),
        "fields": ",".join(fields),
    }
    params["headers"]["x-api-key"] = api_key
    params["url"] = f"{base_url}?{urlencode(args)}"


def response(resp: "SXNG_Response") -> EngineResults:
    data = resp.json()
    if "error" in data:
        raise SearxEngineAPIException(str(data["error"]))

    res = EngineResults()
    for item in data.get("data", []):
        title = item.get("title") or ""
        if not title:
            continue

        external_ids = item.get("externalIds") or {}
        pdf = item.get("openAccessPdf") or {}
        journal = item.get("journal") or {}
        publication_date = _parse_date(item.get("publicationDate"))

        res.add(
            res.types.Paper(
                url=item.get("url") or _paper_url(item),
                title=title,
                content=item.get("abstract") or "",
                authors=[author.get("name", "") for author in item.get("authors", []) if author.get("name")],
                journal=_journal_name(journal, item.get("venue") or ""),
                doi=external_ids.get("DOI") or "",
                tags=(item.get("fieldsOfStudy") or []) + (item.get("publicationTypes") or []),
                comments=_citation_comment(item.get("citationCount")),
                pdf_url=pdf.get("url") or "",
                publishedDate=publication_date,
            )
        )
    return res


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None


def _paper_url(item: dict[str, t.Any]) -> str:
    paper_id = item.get("paperId")
    return f"https://www.semanticscholar.org/paper/{paper_id}" if paper_id else ""


def _journal_name(journal: dict[str, t.Any], venue: str) -> str:
    return journal.get("name") or venue or ""


def _citation_comment(value: int | None) -> str:
    if value is None:
        return ""
    return f"{value} citations"
