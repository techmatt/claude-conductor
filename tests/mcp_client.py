"""A scripted MCP client for the ask server: what Claude Code does, minus Claude.

    python tests/mcp_client.py <mcp-config.json> '<ask arguments as JSON>'

Starts the server named in the config, initializes, lists tools, calls `ask` and prints
the result text. The fake executor uses `call_tool` for its `FAKE ask` and `FAKE progress` lines.

    python tests/mcp_client.py --status [project] [--control <folder> ...]

Starts `conductor mcp` (the trial status server, docs/mcp.md) as the desktop app would and
prints what `status` returns.
"""

import json
import subprocess
import sys
from pathlib import Path


class Client:
    def __init__(self, mcp_config: Path | None = None, command: list[str] | None = None):
        if command is None:
            spec = json.loads(Path(mcp_config).read_text(encoding="utf-8"))["mcpServers"]["conductor"]
            command = [spec["command"], *spec["args"]]
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, text=True, encoding="utf-8")
        self.next_id = 0

    def request(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        mid = self.next_id
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": mid, "method": method,
                                          "params": params or {}}) + "\n")
        self.proc.stdin.flush()
        while True:
            msg = json.loads(self.proc.stdout.readline())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg["result"]

    def notify(self, method: str, params: dict | None = None) -> None:
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}}) + "\n")
        self.proc.stdin.flush()

    def start(self) -> list[dict]:
        self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "mcp_client", "version": "0"}})
        self.notify("notifications/initialized")
        return self.request("tools/list")["tools"]

    def close(self) -> None:
        self.proc.stdin.close()
        self.proc.wait(timeout=10)


def call_tool(mcp_config: Path, name: str, arguments: dict) -> tuple[str, bool]:
    c = Client(mcp_config)
    try:
        tools = c.start()
        assert [t["name"] for t in tools] == ["ask", "progress", "wait", "wait_for", "start"], tools
        result = c.request("tools/call", {"name": name, "arguments": arguments})
        return result["content"][0]["text"], result.get("isError", False)
    finally:
        c.close()


def call_ask(mcp_config: Path, arguments: dict) -> tuple[str, bool]:
    return call_tool(mcp_config, "ask", arguments)


def call_status(project: str | None = None, controls: list[Path] = ()) -> tuple[str, bool]:
    """Call the trial status server's one tool, the server started as `conductor mcp`."""
    cmd = [sys.executable, "-m", "conductor", "mcp", *(a for c in controls for a in ("--control", str(c)))]
    c = Client(command=cmd)
    try:
        tools = c.start()
        assert [t["name"] for t in tools] == ["status"], tools
        result = c.request("tools/call", {"name": "status", "arguments": {"project": project} if project else {}})
        return result["content"][0]["text"], result.get("isError", False)
    finally:
        c.close()


if __name__ == "__main__" and sys.argv[1:2] == ["--status"]:
    rest, controls = sys.argv[2:], []
    while "--control" in rest:
        i = rest.index("--control")
        controls.append(Path(rest[i + 1]))
        del rest[i:i + 2]
    sys.stdout.reconfigure(encoding="utf-8")
    text, is_error = call_status(rest[0] if rest else None, controls)
    print(("ERROR: " if is_error else "") + text)
elif __name__ == "__main__":
    text, is_error = call_ask(Path(sys.argv[1]), json.loads(sys.argv[2]))
    print(("ERROR: " if is_error else "") + text)
