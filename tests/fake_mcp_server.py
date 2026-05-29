"""A tiny MCP server speaking newline-delimited JSON-RPC over stdio.

Used by test_mcp.py to exercise the real client end-to-end with no network
and no node/npx. Tools: echo (returns its input), boom (isError result),
slow (sleeps past the test's timeout), env_probe (reports FAKE_MCP_SECRET).
"""

import json
import os
import sys
import time


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


TOOLS = [
    {
        "name": "echo",
        "description": "Echo text back.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "boom",
        "description": "Always fails.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "slow",
        "description": "Sleeps.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "env_probe",
        "description": "Report the FAKE_MCP_SECRET env var.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def main():
    sys.stderr.write("fake-mcp-server booted\n")
    sys.stderr.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method, msg_id = msg.get("method"), msg.get("id")
        if method == "initialize":
            send({
                "jsonrpc": "2.0", "id": msg_id,
                "result": {
                    "protocolVersion": msg["params"]["protocolVersion"],
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake", "version": "1.0"},
                },
            })
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            params = msg["params"]
            name, args = params["name"], params.get("arguments") or {}
            if name == "echo":
                send({
                    "jsonrpc": "2.0", "id": msg_id,
                    "result": {"content": [{"type": "text", "text": "echo: " + args["text"]}]},
                })
            elif name == "boom":
                send({
                    "jsonrpc": "2.0", "id": msg_id,
                    "result": {"content": [{"type": "text", "text": "kaboom"}], "isError": True},
                })
            elif name == "slow":
                time.sleep(30)
                send({"jsonrpc": "2.0", "id": msg_id,
                      "result": {"content": [{"type": "text", "text": "finally"}]}})
            elif name == "env_probe":
                send({
                    "jsonrpc": "2.0", "id": msg_id,
                    "result": {"content": [{
                        "type": "text",
                        "text": "secret=" + os.environ.get("FAKE_MCP_SECRET", "(unset)"),
                    }]},
                })
            else:
                send({"jsonrpc": "2.0", "id": msg_id,
                      "error": {"code": -32602, "message": "unknown tool"}})
        elif method == "exit_now":  # test helper: die abruptly
            sys.exit(1)
        # notifications ignored


if __name__ == "__main__":
    main()
