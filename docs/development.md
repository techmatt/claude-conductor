# Development

The suite runs against a fake executor (`tests/fake_claude.py`, which obeys `FAKE …`
lines in the prompt), so the scheduling, guard, request, restart, question, worktree,
merge and staging paths are covered without spending Claude runs. It runs in two lanes,
in parallel (`pytest-xdist`, up to 8 workers):

```
uv run pytest            # the fast lane, after every change (~48s; 223 tests; 2026-10-20)
uv run pytest -m slow    # the slow lane alone
uv run pytest -m ""      # everything (~150s; 307 tests); run this before committing
uv run pytest -n0 tests/test_x.py::test_y    # one test, no workers (startup ~3s otherwise)
```

`@pytest.mark.slow` marks a test that took 2.5s or more serially: several executors, a
merge or prose conflict, a stage, a restart, or a real wait on a clock. The time is
real git and process spawns (~30ms each on Windows), not sleeps, so parallelism is the
lever. Keep new tests deterministic under load: hold an executor open with `env.hold()`
(a `FAKE wait-for` line) and let it go with `env.release()` rather than a fixed `FAKE
sleep`, which races a loaded machine. Every `Env` copies a code repo and control folder
built once per worker. The fake runs the real message hook only when a message is
waiting; `FAKE subagent <tool>` is a helper subagent's call, its hook input carrying the
`agent_id` / `agent_type` Claude Code adds (`executors.md`, *Delivery*), and `FAKE
subagent progress <json>` a helper's `progress` call followed by that hook. A conftest fixture turns the self-restart's code probe off, so a commit to
conductor during a run never restarts a test's daemon; `tests/test_restart.py` swaps
`code_probe`, `head_probe`, `import_check` and `restart_spawner` for stand-ins.
`tests/test_setup.py` covers setup runners (a slow one waits on a file, like
`env.hold()`), `tests/test_review.py` the `review:` bundle.

Where things live, beyond the obvious: `Daemon` is `daemon.py` plus `daemon_*` mixins
(signals, messages, receipts, reap, merge, `daemon_restart` for the self-restart, `daemon_setup`
for setup runners and the second half of a launch or stage); `setup_run.py` is the
runner; `review.py` renders `review.md`; `state.early_starts` picks readonly and
`alongside` items that start behind the head (`tests/test_alongside.py`); `session.reports_past_marker` finds conductor's reports
past the planner's marker (`planner-notes.md`) and `inbox.unreviewed_lines` builds the unreviewed summary, each
shared by the digest, the inbox and the review bundle.
A staging prompt makes the fake obey only `FAKE stage-…` lines and remember the prompt
under its session id; `--resume` recalls it, so a test can see that a resumed session
kept what it read while staging. Every session also appends its prompt to a transcript
under `$CLAUDE_CONFIG_DIR/projects/fake/` (an autouse fixture points that at the test's
own folder); `--resume <orig> --fork-session --session-id <new>` starts from the
original's transcript, leaves it alone, and writes `FORKED FROM <orig>` and a
`FORK MEMORY:` line per `REMEMBER …` line the original was sent. `FAKE slow <text>` adds `slow: <text>` and
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

`uv run python tools/restart_all.py [--check]` is "restart all" (`CLAUDE.md`). It covers every
live daemon of the `*-control` folders beside this repo, and the app. `--check` lists what a
restart would interrupt (running or staged items, open questions) and exits 1 if anything
is active. Without it, each daemon is stopped by `stop: daemon`, waited for and started
detached (`procs.spawn_daemon`). The app is then killed and started detached with the folders
it was launched with. No app running: it starts one on the newest control folder.
