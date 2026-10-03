"""Tiny newline-delimited JSON-RPC MCP server used by tests."""

import json
import sys

for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    method = msg["method"]
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "serverInfo": {"name": "fake", "version": "1"}, "capabilities": {}}
    elif method == "tools/list":
        result = {"tools": [{"name": "echo", "description": "echo text"}]}
    elif method == "tools/call":
        params = msg["params"]
        if params["name"] == "boom":
            print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -1, "message": "boom"}}), flush=True)
            continue
        print(json.dumps({"jsonrpc": "2.0", "method": "notifications/message", "params": {}}), flush=True)
        result = {"content": [{"type": "text", "text": params["arguments"].get("text", "")}]}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
