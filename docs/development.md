# Development

The suite runs against a fake executor (`tests/fake_claude.py`, which obeys `FAKE …`
lines in the prompt), so the scheduling, guard, request, restart, question, worktree,
merge and staging paths are covered without spending Claude runs. It runs in two lanes,
in parallel (`pytest-xdist`, up to 8 workers):

```
uv run pytest            # the fast lane, after every change (~90-130s; 526 tests; 2026-11-10)
uv run pytest -m slow    # the slow lane alone
uv run pytest -m ""      # everything (~230-300s; 668 tests); run this before committing
uv run pytest -n0 tests/test_x.py::test_y    # one test, no workers (startup ~3s otherwise)
```

`@pytest.mark.slow` marks a test that took 2.5s or more serially: several executors, a
merge or prose conflict, a stage, a restart, or a real wait on a clock. The time is
real git and process spawns (~30ms each on Windows), not sleeps, so parallelism is the
lever. Pytest's settings live in `pytest.ini`, which wins over `pyproject.toml`'s older
table. A passing test's folder is deleted at its teardown; left for a later session,
that cost 12-16s at the end of every run. Temp folders live in `.pytest-tmp/<pid>` under
the repo (git-ignored; the root `conftest.py` sets `--basetemp`), not `%TEMP%`: `C:\Code`
is under the Defender exclusion and `%TEMP%` is not, and Defender averaged ~18% CPU
through a run there (103, aicomplete 170). One folder a run, so two at once never clear
each other's; a green run removes its own, the next run any whose pid is gone. `Env`'s daemons skip the load scan (~0.5s) and
its repos have no sample hooks. Keep new tests deterministic under load: hold an executor open with `env.hold()`
(a `FAKE wait-for` line) and let it go with `env.release()` rather than a fixed `FAKE
sleep`, which races a loaded machine. `env.tick_until` and `env.hold()` give up only after
`WAIT_SECONDS` (180s, `tests/conftest.py`): a passing test never waits it out, and a project's
executor holding the CPU no longer fails the full lane (58 and 59 committed red from it).
Under 12 burning cores plus a second suite run (2026-10-30) the full lane took 25 min and
398 of 401 passed: two still timed out and one push-batching test was widened. Every `Env` copies a code repo and control folder
built once per worker. The fake runs the real message hook only when a message is
waiting; `FAKE subagent <tool>` is a helper subagent's call, its hook input carrying the
`agent_id` / `agent_type` Claude Code adds (`executors.md`, *Delivery*), and `FAKE
subagent progress <json>` a helper's `progress` call followed by that hook. A conftest fixture turns the self-restart's code probe off, so a commit to
conductor during a run never restarts a test's daemon; `tests/test_restart.py` swaps
`code_probe`, `head_probe`, `import_check` and `restart_spawner` for stand-ins.
The remote workers' tests live in their module (`src/conductor/remote/tests/`, a second
`testpaths` entry, their own small conftest) so deleting it takes them too (`remote-workers.md`).
`tests/test_setup.py` covers setup runners (a slow one waits on a file, like
`env.hold()`), `tests/test_review.py` the `review:` bundle.

Where things live, beyond the obvious: `Daemon` is `daemon.py` plus `daemon_*` mixins
(signals, messages, receipts, reap, merge, templates, watch, push, memory, `daemon_restart` for the self-restart, `daemon_setup`
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

`uv run python tools/restart_all.py [--check | --force]` is "restart all" (`CLAUDE.md`; the
app's button runs it too, `app.md`) for every live daemon of the `*-control` folders beside
this repo, and the app; how, `restart.md`, *Asked restarts*. It prints every 30s what a
daemon still waits on and exits 1 if one still waits after 10 minutes (`RESTART_WAIT`).
`--force` stops and starts each daemon detached (`procs.spawn_daemon`) instead; `--check`
lists what runs now and exits 0. No app running: it starts one on the newest control folder.

`uv run python tools/prompts.py list | next [--anyway] | run <slug|file> [--anyway] |
done <name> <report path|stopped> | migrate` is "run the next prompt" (`CLAUDE.md`). Planners write conductor prompts to
`prompts/inbox/<YYYYMMDD-HHMMSS>-<project>-<slug>.md` with no number, because several
planner sessions write without seeing each other (78 and 91 were each written twice, and 91
was skipped once 92 ran). A claim takes the oldest (or the one named), gives it one past
the highest number in `prompts/` or the ledger, moves it to `prompts/NN_<slug>.md` and
appends the ledger's `start`. It refuses while a `start` has no `done`/`stopped`/`stale`
after it; `--anyway` appends `<time> stale <name> (run anyway)` first. `migrate` sends a
numbered prompt with neither a ledger line nor a report back to the inbox (none at 93).
