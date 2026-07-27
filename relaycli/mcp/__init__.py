"""MCP package — re-exports for backward compatibility."""

from relaycli.mcp.client import MCPClient, MCPError, MCPServerConfig, PRESETS
from relaycli.mcp.bridge import (
    _clients,
    _sanitize,
    configured_servers,
    enabled_servers,
    extend_registry,
    get_client,
    remove_server,
    save_server,
    server_status,
    shutdown_all,
)

__all__ = [
    "MCPClient", "MCPError", "MCPServerConfig", "PRESETS",
    "_clients", "_sanitize",
    "configured_servers", "enabled_servers", "extend_registry",
    "get_client", "remove_server", "save_server", "server_status",
    "shutdown_all",
]
