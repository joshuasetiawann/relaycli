from __future__ import annotations

import json
import urllib.error
import urllib.request
from pydantic import BaseModel, Field
from relaycli.tools.base import ToolContext, ToolResult
from relaycli.tools.registry import Tool, ToolRegistry


class WebFetchArgs(BaseModel):
    url: str = Field(description="URL to fetch")
    format: str | None = Field(default="text", description="Output format: text or markdown")


def webfetch(args: WebFetchArgs, ctx: ToolContext | None) -> ToolResult:
    try:
        req = urllib.request.Request(args.url, headers={"User-Agent": "RelayCLI/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            content_type = resp.headers.get("Content-Type", "")
            data = resp.read()
    except urllib.error.HTTPError as exc:
        return ToolResult.error(f"HTTP {exc.code}: {exc.reason}")
    except urllib.error.URLError as exc:
        return ToolResult.error(f"URL error: {exc.reason}")
    except OSError as exc:
        return ToolResult.error(str(exc))

    if args.format == "json":
        try:
            parsed = json.loads(data)
            return ToolResult(ok=True, output=json.dumps(parsed, indent=2)[:10000])
        except json.JSONDecodeError:
            pass

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")

    if "text/html" in content_type and args.format == "markdown":
        return ToolResult(ok=True, output=_html_to_text(text)[:10000],
                          summary=f"fetched {len(text)} bytes")
    return ToolResult(ok=True, output=text[:10000], summary=f"fetched {len(text)} bytes")


def _html_to_text(html: str) -> str:
    import re
    text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    return "\n".join(lines[:200])


def register(reg: ToolRegistry) -> None:
    reg.add(Tool(name="webfetch", description="Fetch content from a URL (web pages, APIs, raw text)",
                 args_model=WebFetchArgs, func=webfetch))
