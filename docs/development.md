# Development

The suite runs against a fake executor (`tests/fake_claude.py`, which obeys `FAKE …`
lines in the prompt), so the scheduling, guard, request, restart, question, worktree,
merge and staging paths are covered without spending Claude runs. It runs in two lanes,
in parallel (`pytest-xdist`, up to 8 workers):

```
uv run pytest            # the fast lane, after every change (~38s; 169 tests; 2026-10-20)
uv run pytest -m slow    # the slow lane alone
uv run pytest -m ""      # everything (~90s; 228 tests); run this before committing
uv run pytest -n0 tests/test_x.py::test_y    # one test, no workers (startup ~3s otherwise)
```

`@pytest.mark.slow` marks a test that took 2.5s or more serially: several executors, a
merge or prose conflict, a stage, a restart, or a real wait on a clock. The time is
real git and process spawns (~30ms each on Windows), not sleeps, so parallelism is the
lever. Keep new tests deterministic under load: hold an executor open with `env.hold()`
(a `FAKE wait-for` line) and let it go with `env.release()` rather than a fixed `FAKE
sleep`, which races a loaded machine. Every `Env` copies a code repo and control folder
built once per worker. The fake runs the real message hook only when a message is
waiting.
A staging prompt makes the fake obey only `FAKE stage-…` lines and remember the prompt
under its session id; `--resume` recalls it, so a test can see that a resumed session
kept what it read while staging. `FAKE slow <text>` adds `slow: <text>` and
`FAKE pick <path>` adds `pick: <path>` to the report header after `carry:` (a test writes the manifest and images with `FAKE commit` first);
picker tests stub the browser and bind port 0. `FAKE leave <s>` starts a process in the
worktree that outlives the executor and `FAKE hog <MB> <s>` a child holding touched
memory (each pid in the report) for the reaper and sampler. The test template turns
every memory threshold off, so the box's real memory never pauses a test; guard tests
set `daemon.memory_probe` to a stand-in box. `procs.claude_ancestor` is stubbed for
every test (the suite usually runs inside Claude Code). `FAKE ask {json}` calls the
real ask server through the item's `--mcp-config` using `tests/mcp_client.py`, a
scripted MCP client that also runs standalone:
`python tests/mcp_client.py <mcp.json> '{"question": "…", "default": "…"}'`. The sandbox
(`C:\Code\conductor-sandbox`, `C:\Code\conductor-sandbox-control`) is for end-to-end
runs with real executors.

Two scripts in `tools/` serve the maintenance pass (`CLEANUP.md`) and any session:
`uv run python tools/sizes.py [--all]` prints docs and code against their size budgets
(the table in `CLEANUP.md`) and exits 1 on a breach; `uv run python tools/sandbox_proof.py
<sandbox control folder> [--shot <png>]` drives the sandbox with the fake executor (an
item through queue and merge, `session: start`, a request) and then renders the app on
it offscreen, refusing while the sandbox's own daemon runs.
