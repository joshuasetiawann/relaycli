from __future__ import annotations

import ipaddress
import json
import socket
import urllib.error
import urllib.request
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from relaycli.tools.base import ToolContext, ToolResult
from relaycli.tools.registry import Tool, ToolRegistry

_MAX_FETCH_BYTES = 1_048_576


class UnsafeURL(ValueError):
    """A URL that could reach a local, private, or non-HTTP resource."""


def _resolve_addresses(host: str, port: int) -> set[str]:
    return {
        item[4][0]
        for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    }


def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeURL("webfetch only allows http:// or https:// URLs")
    if not parsed.hostname or parsed.username or parsed.password:
        raise UnsafeURL("webfetch requires a public host and does not allow URL credentials")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeURL("webfetch URL has an invalid port") from exc
    try:
        addresses = _resolve_addresses(parsed.hostname, port)
    except socket.gaierror as exc:
        raise UnsafeURL(f"webfetch could not resolve host '{parsed.hostname}'") from exc
    if not addresses:
        raise UnsafeURL(f"webfetch could not resolve host '{parsed.hostname}'")
    for address in addresses:
        try:
            public = ipaddress.ip_address(address).is_global
        except ValueError:
            public = False
        if not public:
            raise UnsafeURL(
                f"webfetch refuses non-public address '{address}' for host '{parsed.hostname}'"
            )


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_url(req: urllib.request.Request):
    opener = urllib.request.build_opener(_SafeRedirectHandler())
    return opener.open(req, timeout=30)


class WebFetchArgs(BaseModel):
    url: str = Field(description="URL to fetch")
    format: Literal["text", "markdown", "json"] = Field(
        default="text", description="Output format: text, markdown, or json"
    )


def webfetch(args: WebFetchArgs, ctx: ToolContext | None) -> ToolResult:
    try:
        _validate_public_url(args.url)
        req = urllib.request.Request(args.url, headers={"User-Agent": "RelayCLI/1.0"})
        with _open_url(req) as resp:
            content_type = resp.headers.get("Content-Type", "")
            declared = resp.headers.get("Content-Length")
            if declared:
                try:
                    if int(declared) > _MAX_FETCH_BYTES:
                        return ToolResult.error(
                            f"Response is too large ({declared} bytes; limit {_MAX_FETCH_BYTES})."
                        )
                except ValueError:
                    pass
            data = resp.read(_MAX_FETCH_BYTES + 1)
            truncated = len(data) > _MAX_FETCH_BYTES
            data = data[:_MAX_FETCH_BYTES]
    except UnsafeURL as exc:
        return ToolResult.error(str(exc), summary="fetch (unsafe URL)")
    except urllib.error.HTTPError as exc:
        return ToolResult.error(f"HTTP {exc.code}: {exc.reason}")
    except urllib.error.URLError as exc:
        return ToolResult.error(f"URL error: {exc.reason}")
    except OSError as exc:
        return ToolResult.error(str(exc))

    if args.format == "json":
        try:
            parsed = json.loads(data)
            output = json.dumps(parsed, indent=2)[:10000]
            if truncated:
                output += f"\n[... response truncated at {_MAX_FETCH_BYTES} bytes ...]"
            return ToolResult(ok=True, output=output)
        except json.JSONDecodeError:
            pass

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")

    output = _html_to_text(text)[:10000] if (
        "text/html" in content_type and args.format == "markdown"
    ) else text[:10000]
    if truncated:
        output += f"\n[... response truncated at {_MAX_FETCH_BYTES} bytes ...]"
    return ToolResult(ok=True, output=output, summary=f"fetched {len(data)} bytes")


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
