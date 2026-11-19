"""A scripted MCP client for the ask server: what Claude Code does, minus Claude.

    python tests/mcp_client.py <mcp-config.json> '<ask arguments as JSON>'

Starts the server named in the config, initializes, lists tools, calls `ask` and prints
the result text. The fake executor uses `call_tool` for its `FAKE ask` and `FAKE progress` lines.

    python tests/mcp_client.py --status [project] [--control <folder> ...]
    python tests/mcp_client.py --planner <tool> '<arguments as JSON>' [--control <folder> ...]

Starts `conductor mcp` (the planner's server, docs/mcp.md) as the desktop app would and
prints what the tool returns.
"""

import json
import subprocess
import sys
from pathlib import Path


class Client:
    def __init__(self, mcp_config: Path | None = None, command: list[str] | None = None, env: dict | None = None):
        if command is None:
            spec = json.loads(Path(mcp_config).read_text(encoding="utf-8"))["mcpServers"]["conductor"]
            command = [spec["command"], *spec["args"]]
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, text=True, encoding="utf-8", env=env)
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

    def call(self, tool: str, arguments: dict) -> tuple[str, bool]:
        result = self.request("tools/call", {"name": tool, "arguments": arguments})
        return result["content"][0]["text"], result.get("isError", False)

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


PLANNER_TOOLS = ["status", "start_session", "read", "queue_item", "amend", "signal", "edit_state",
                 "answer", "park", "note"]


def planner_command(controls: list[Path] = ()) -> list[str]:
    """`conductor mcp` as the desktop app starts it: the launcher, which runs the server."""
    return [sys.executable, "-m", "conductor", "mcp", *(a for c in controls for a in ("--control", str(c)))]


def call_planner(tool: str, arguments: dict, controls: list[Path] = ()) -> tuple[str, bool]:
    """Call one tool of the planner's server (`conductor mcp`, docs/mcp.md), started as the
    desktop app starts it, with `--control` for each folder given."""
    c = Client(command=planner_command(controls))
    try:
        tools = c.start()
        assert [t["name"] for t in tools] == PLANNER_TOOLS, tools
        return c.call(tool, arguments)
    finally:
        c.close()


def call_status(project: str | None = None, controls: list[Path] = ()) -> tuple[str, bool]:
    return call_planner("status", {"project": project} if project else {}, controls)


if __name__ == "__main__" and sys.argv[1:2] == ["--planner"]:
    # python tests/mcp_client.py --planner <tool> '<arguments as JSON>' [--control <folder> ...]
    rest, controls = sys.argv[2:], []
    while "--control" in rest:
        i = rest.index("--control")
        controls.append(Path(rest[i + 1]))
        del rest[i:i + 2]
    sys.stdout.reconfigure(encoding="utf-8")
    text, is_error = call_planner(rest[0], json.loads(rest[1]) if len(rest) > 1 else {}, controls)
    print(("ERROR: " if is_error else "") + text)
elif __name__ == "__main__" and sys.argv[1:2] == ["--status"]:
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
