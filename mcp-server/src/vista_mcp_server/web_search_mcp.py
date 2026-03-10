"""
MCP tool for web search — Bing Search API v7, SerpAPI, or LLM-knowledge fallback.
"""

from __future__ import annotations
import os
import logging
from typing import Annotated as A
from urllib.parse import quote_plus
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP(name="Web Search")


def _bing_search(query: str, api_key: str, endpoint: str) -> str | None:
    """Call Bing Search API v7. Returns formatted results or None on failure."""
    import urllib.request
    import json

    url = f"{endpoint}/v7.0/search?q={quote_plus(query)}&count=5&responseFilter=Webpages"
    req = urllib.request.Request(url, headers={"Ocp-Apim-Subscription-Key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        logger.warning("Bing search failed: %s", exc)
        return None

    pages = data.get("webPages", {}).get("value", [])
    if not pages:
        return None

    lines: list[str] = []
    for i, p in enumerate(pages[:5], 1):
        name = p.get("name", "Untitled")
        snippet = p.get("snippet", "")
        page_url = p.get("url", "")
        lines.append(f"[{i}] {name}\n    {snippet}\n    URL: {page_url}")

    return "Web search results:\n\n" + "\n\n".join(lines)


def _serp_search(query: str, api_key: str) -> str | None:
    """Call SerpAPI. Returns formatted results or None on failure."""
    import urllib.request
    import json

    url = f"https://serpapi.com/search.json?q={quote_plus(query)}&api_key={api_key}&num=5"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        logger.warning("SerpAPI search failed: %s", exc)
        return None

    organic = data.get("organic_results", [])
    if not organic:
        return None

    lines: list[str] = []
    for i, r in enumerate(organic[:5], 1):
        title = r.get("title", "Untitled")
        snippet = r.get("snippet", "")
        link = r.get("link", "")
        lines.append(f"[{i}] {title}\n    {snippet}\n    URL: {link}")

    return "Web search results:\n\n" + "\n\n".join(lines)


@mcp.tool()
def web_search(
    query: A[
        str,
        "A specific, well-formed search query. Use scientific terms. "
        'Example: "FLiBe molten salt viscosity measurement recent studies"',
    ],
) -> str:
    """
    Search the web for scientific literature, research groups, recent studies,
    and trends related to molten salts or nuclear energy topics.

    If a web search API key is configured (BING_SEARCH_API_KEY or SERP_API_KEY),
    returns real search results with titles, snippets, and URLs.
    If no API key is available, returns a message asking the LLM to use its own
    scientific knowledge.

    Use this AFTER querying the local database (when relevant) to supplement
    with external research.
    """
    logger.info("web_search called: %s", query)

    # ---- Bing ----
    bing_key = os.environ.get("BING_SEARCH_API_KEY", "")
    if bing_key:
        endpoint = os.environ.get(
            "BING_SEARCH_ENDPOINT", "https://api.bing.microsoft.com"
        ).rstrip("/")
        result = _bing_search(query, bing_key, endpoint)
        if result:
            return result + "\n\nUse these results to inform your answer. Cite sources when relevant."
        return f'No Bing results for "{query}". Answer using your scientific knowledge.'

    # ---- SerpAPI ----
    serp_key = os.environ.get("SERP_API_KEY", "")
    if serp_key:
        result = _serp_search(query, serp_key)
        if result:
            return result + "\n\nUse these results to inform your answer. Cite sources when relevant."
        return f'No SerpAPI results for "{query}". Answer using your scientific knowledge.'

    # ---- Fallback ----
    logger.warning("No web search API key configured — falling back to LLM knowledge.")
    return (
        f'No web search API is configured. Answer the following question using '
        f'your scientific knowledge:\n"{query}"\n\n'
        f'To enable live web search, set BING_SEARCH_API_KEY or SERP_API_KEY in your .env file.'
    )
