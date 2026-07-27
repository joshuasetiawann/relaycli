from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from pydantic import BaseModel, Field

from relaycli.core.logging import get_logger
from relaycli.tools.base import ToolContext, ToolResult
from relaycli.tools.registry import Tool, ToolRegistry

_log = get_logger(__name__)


class WebSearchArgs(BaseModel):
    query: str = Field(description="Search query")
    max_results: int | None = Field(default=5, ge=1, le=20, description="Number of results (max 20)")


def websearch(args: WebSearchArgs, ctx: ToolContext | None) -> ToolResult:
    started = time.perf_counter()
    try:
        results = _search_duckduckgo(args.query, args.max_results or 5)
        if results:
            output = _format_results(results)
            return ToolResult(ok=True, output=output, summary=f"{len(results)} results")
        return ToolResult(ok=True, output="(no results)")
    except Exception as exc:
        _log.error(
            "websearch crashed: elapsed_ms=%.0f query=%r",
            (time.perf_counter() - started) * 1000, args.query[:200],
            exc_info=True,
        )
        return ToolResult.error(str(exc))


def _search_duckduckgo(query: str, max_results: int) -> list[dict]:
    encoded = urllib.parse.quote(query)
    url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "RelayCLI/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (OSError, json.JSONDecodeError, urllib.error.URLError):
        return _fallback_html_search(query, max_results)

    results = []
    answer = data.get("AbstractText", "")
    if answer:
        results.append({"title": data.get("Heading", "Answer"), "snippet": answer, "url": data.get("AbstractURL", "")})

    related = data.get("RelatedTopics", [])
    for item in related:
        if isinstance(item, dict) and "Text" in item:
            results.append({"title": item.get("Text", "").split(" - ")[0], "snippet": item.get("Text", ""), "url": item.get("FirstURL", "")})
            if len(results) >= max_results:
                break
        if isinstance(item, dict) and "Topics" in item:
            for sub in item.get("Topics", []):
                if isinstance(sub, dict):
                    results.append({"title": sub.get("Text", "").split(" - ")[0], "snippet": sub.get("Text", ""), "url": sub.get("FirstURL", "")})
                    if len(results) >= max_results:
                        break
    return results[:max_results]


def _fallback_html_search(query: str, max_results: int) -> list[dict]:
    import re
    encoded = urllib.parse.quote(query)
    url = f"https://html.duckduckgo.com/html/?q={encoded}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError):
        return []

    results = []
    for match in re.finditer(
        r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</(?:a|td)>',
        html, re.DOTALL,
    ):
        url = match.group(1)
        title = re.sub(r"<[^>]+>", "", match.group(2)).strip()
        snippet = re.sub(r"<[^>]+>", "", match.group(3)).strip()
        results.append({"title": title, "snippet": snippet, "url": url})
        if len(results) >= max_results:
            break
    return results


def _format_results(results: list[dict]) -> str:
    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "").strip()
        snippet = r.get("snippet", "").strip()
        url = r.get("url", "").strip()
        lines.append(f"{i}. {title or '(no title)'}")
        if snippet:
            lines.append(f"   {snippet[:200]}")
        if url:
            lines.append(f"   {url}")
        lines.append("")
    return "\n".join(lines).strip()


def register(reg: ToolRegistry) -> None:
    reg.add(Tool(name="websearch", description="Search the web for current information (uses DuckDuckGo, no API key needed)",
                 args_model=WebSearchArgs, func=websearch))
