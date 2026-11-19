# The status MCP server (trial)

A trial, built in 136 to learn whether a claude.ai planner chat linked to this machine can
call a conductor tool directly through the Claude desktop app. It is removable and touches
nothing a project uses: no daemon change, no signal, request or receipt.

## What exists

`conductor mcp` (`src/conductor/status_mcp.py`) is a stdio MCP server named
`conductor-status` with one read-only tool:

- **`status(project?)`**. With `project` (the `conductor.toml` `project`, or the control
  folder's name with or without `-control`): the daemon line first, then per repo the running
  item (elapsed, kill time, latest progress line cut at 160 chars), staged items, the queue
  (six names, then `+N more`; why the head waits when it holds or waits), a red hold, `idle`;
  then `proposed:` and any open questions. Without it: every project on one line
  (`fractals: daemon alive, 1 running, 3 queued (<control>)`). An unknown name lists the known ones.
- The daemon line says `daemon alive (pid, since; heartbeat N ago; last event #id kind N ago)`,
  `daemon NOT ANSWERING` (alive, heartbeat stale: `state.daemon_info`), or
  `daemon DOWN: it died (...)` / `daemon DOWN: stopped`, with the last event's age and how to
  start it. A down daemon is an answer, never `isError`.
- A reply is plain text, at most 1,500 characters (cut with `…(cut)`): it lands in the
  planner's context.

The projects are `--control <folder>` (repeatable), else the app's remembered list
(QSettings `conductor/conductor-app`, key `controls`), else the `*-control` folders beside
this repo. It opens each `events.db` as `conductor status` does and writes nothing.
The JSON-RPC loop is `ask_server.serve_stdio`, shared with the executors' `ask` server.

`python tests/mcp_client.py --status [project] [--control <folder> …]` calls it as a client
would; `tests/test_status_mcp.py` covers a live and a dead daemon.

## Registration

The Claude desktop app (Squirrel install, `%LOCALAPPDATA%\AnthropicClaude`) reads local MCP
servers from `%APPDATA%\Claude\claude_desktop_config.json`, `mcpServers`, at start:

```json
"conductor": {"command": "C:\\Code\\claude-conductor\\.venv\\Scripts\\python.exe",
              "args": ["-m", "conductor", "mcp"]}
```

The venv's python, not `uv run`: the app's PATH may lack `uv`, and no sync runs per launch.
The file before the entry is `claude_desktop_config.json.bak-20261119` beside it. The other
route, a desktop extension (`.mcpb` bundle, Settings → Extensions), was not taken.

## Removing it

Delete the `conductor` entry under `mcpServers` (or restore the `.bak`), restart the desktop
app; then delete `status_mcp.py`, `cmd_mcp` in `cli.py`, `call_status` in
`tests/mcp_client.py`, `tests/test_status_mcp.py` and this file.
