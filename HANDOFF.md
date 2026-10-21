# HANDOFF — claude-conductor

The one file that starts a claude.ai session for conductor work. Read it, then `planner.md` (how a planner session uses conductor, every session, ending with the vocab) and `README.md` (how conductor works, with an index into `docs/`); `claude-ai-new-project.md` covers starting, parking and resuming a project. Every conductor prompt updates *Status* and *Open* before committing; nothing else in this file changes without a reason.

## How work on conductor flows

- Matt discusses with a claude.ai session linked to the box. The session reads and writes `C:\Code\claude-conductor` over the bridge. It writes each prompt to `prompts/NN_name.md` (git-ignored).
- Matt pastes or names the prompt in a Claude Code session in this repo. Conductor never runs its own queue: its prompts always go this way, by hand.
- CC writes `reports/NN_name_report.md` (git-ignored) and ends with `Report written to <path>`. Matt says "report's in", and the session reads it. These reports have no `reviewed` event, so the session keeps *Status*'s `Last report reviewed by the planner: NN_name` line current itself, writing it directly over the bridge.
- **Cleanup conductor** is a standing maintenance pass (`CLEANUP.md`) Matt runs in a Claude Code session here. A planner recommends it in one line with the reason, never runs it unasked: after ~10 prompts since *Status*'s `Last cleanup:` line, or when a doc breaks its size budget (`uv run python tools/sizes.py`).
- Testing uses the sandbox only: `C:\Code\conductor-sandbox` and `C:\Code\conductor-sandbox-control`.
- This replaced the fractal project's relay through Google Drive and hand-placed files. Drive is not part of this workflow, and nothing should reintroduce it as a relay. (A project's *Storage* policy in `claude-ai-new-project.md`, Drive as the one home for some large files, is a separate thing.)

## Design decisions (settled 2026-10-15)

- **Three roles.** The claude.ai planner works files-only. The conductor daemon owns every process. Executors are headless CC run with `--dangerously-skip-permissions`.
- **One install, one instance per project.** Each project has its own control folder, which is a git repo. The handoff docs live there and never in a code repo.
- **Tracked control files change only through conductor's request/apply step.** The tamper guard reverts anything else that happens during an executor run.
- **The planner never deletes.** It writes `signals/` files (every verb: `docs/signals.md`), and conductor consumes them. The app acts through the same signals, so every change to a queue is a logged event.
- **The ask tool is the executor's judgment.** The executor decides whether a question blocks or falls back to a default after 10 minutes. There are no phone notifications; desktop toasts only.
- **Approval.** Matt approves what enters `queue/`. Items in `proposed/` wait for him. A `readonly` item may go straight to the queue.
- **Staging is a resumable session:** a read-only orient phase first, then the same session resumes when it holds the lock.
- **Concurrency within a repo is declared, not judged.** It uses disjoint `paths:` plus per-item worktrees, with serial merges.

## Phases

1. Core: queues, executors, event log, inbox, tamper guard, requests, restart rule. **Done** (`reports/01_core_report.md`).
2. The ask tool, toasts, `conductor init`, and status definitions. **Done** (`reports/02_ask_report.md`). A blocking ask outlives Claude Code's 30-minute silent-call abort through a per-server timeout and 60-second progress pings (`docs/executors.md`, *Long tool calls*).
3. Worktrees and staging: per-item worktrees, serial merges, `paths:` concurrency, stage-then-resume. **Done** (`reports/03_worktrees_report.md`).
4. The app: a PySide6 tray app with Repos, Questions, Events and Proposed panels, reading `events.db`. **Done** (`reports/04_app_report.md`).
5. The first real project, a roguelike deckbuilder, started from a fresh claude.ai session with `claude-ai-new-project.md` alone. Its first sessions are the test of that file. Gaps it finds come back here as *Open* lines and become conductor prompts.

## Status

Last report reviewed by the planner: 25_cleanup_conductor
Last cleanup: 2026-10-20, after prompt 25

Phases 1–4 are done; phase 5 runs live with Corelight (`C:\Code\corelight-control`) and Word Warriors (`C:\Code\wordwarriors-control`, the newest). Each prompt's report in `reports/` says what it changed; `README.md` and `docs/` hold how it all works now.

What is live, in brief: per-repo queues with worktrees, serial merges, `paths:` concurrency and staging; questions, progress lines, outlines, `tell:` messages and amendments; reviewed reports and checkpoints; run targets, pickers, conductor-owned templates and audit notes; memory sampling with a launch guard and a leftover reaper; the desktop app, where the Repos tab follows one item end to end in its Conversation view and the header's memory line shows only under pressure (27). "Restart all" (`CLAUDE.md`, `tools/restart_all.py`) restarts the app and every live daemon, both detached. `open: folder|reveal|file|text|url` and `notify:` (26) let the planner open a checked project path or URL, or toast, without computer use (`docs/signals.md`). From 28: a daemon restarts itself when a commit changes conductor's `src/` or `pyproject.toml` (`docs/restart.md`); `review: all` writes `review.md`, every unreviewed report inlined; readonly items start as soon as queued; one-call requests; free-form amend names; `setup` off the daemon loop; the planner's **pipeline review**.

The planner docs are split (24). `planner.md` is read whole every session (roles, bridge, prompts, reports, `state.md`, maintenance, **Vocab**). `claude-ai-new-project.md` keeps only new / park / resume and project setup, pointing into `docs/` for mechanics. README is an overview plus an index into `docs/`, one topic per file. A session starts with one signal, `session: start`: conductor writes a checkpoint, then `session.md` (the inbox since the read mark, conductor's reports past the marker above, per-repo maintenance facts), then moves the read mark.

A next prompt should know:
- A behaviour change updates `planner.md` (and `claude-ai-new-project.md` where it covers the topic) and the matching `docs/` file, not README's body.
- Conductor's own reports now start with a `status:` line (this repo's `CLAUDE.md`); the digest shows it, or *no status line* for older ones.
- Every doc and code file has a size budget (`CLEANUP.md`, *Size budgets*); `uv run python tools/sizes.py` shows breaches. `Daemon` is `daemon.py` plus six `daemon_*` mixins (signals, messages, reap, merge, restart, setup); the app's tabs are one `app/*_panel.py` each, with an item's detail in `app/trail.py`.

## Open

- Tamper during concurrent runs ends every running item red, because conductor can't attribute it. Acceptable while tamper never actually occurs; `paths:` concurrency and readonly items beside others make concurrent runs more common.
- Each code repo's `CLAUDE.md` must say "commit to the branch you are on", not "commit to `main`". The sandbox's was changed; any real repo's first prompt carries this.
- A fast-forward of the main checkout interrupted mid-write (the daemon killed during `git merge --ff-only`) leaves tracked files dirty, and the merge holds as "uncommitted changes" until Matt resets the checkout. Rare; needs a person.
- Clicking a toast does not open the app. Toasts use Command Prompt's app id; a registered `conductor-app:` URI plus `launch_action` would likely work but needs a human click to prove.
- No report finished before `reviewed:` existed has a `reviewed` event, so on Corelight every earlier report lists as unreviewed until the planner marks it once (**process all**).
- A message (or an amendment to a running item) reaches an executor only after a tool call ends: within about a minute for shell calls (capped at `foreground_seconds`), but not during a blocking `ask`, a long non-shell call (a subagent, a Workflow), an idle end-of-turn wait, or after the last tool call (logged undelivered). The fallback, if that bites: stream user messages into `-p --input-format stream-json` stdin, which changes how executors are launched (prompt, `--resume` staging, the restart rule's view of a live process). The same change would allow switching a running executor into ultracode (no mid-run route works in 2.1.302: `docs/executors.md`, *Ultracode*).
- A command the cap moves to the background is stopped by Claude Code after 10 minutes. No real project has `background` globs yet (Word Warriors' slow lane is the obvious first).
- The app probes each run target over HTTP on the UI thread every refresh (0.3s timeout); several down targets would make it sluggish. Move the probe to a worker if that shows.
- The app's refresh re-reads every `finish`, `red`, `clear` and `reviewed` event each tick through `state.scan`. Fine at hundreds of events; revisit at tens of thousands.
- Only the latest checkpoint shapes the views. The Repos tab's earlier group shows the 10 newest settled items per repo; older ones are reachable through Events (*since checkpoint* off), `conductor events --since checkpoint N` or `conductor open`.
- A leftover worktree is retried only at daemon start, before a `disk_audit` launches, and when its repo's queue empties (never for the item that just emptied it). Add a periodic retry if a stray dev server keeps one around.
- The merge executor's checks are structural (markers gone, nothing else touched). Whether it kept both sides' *intent* shows only by reading: in the sandbox it joined two rewrites of one sentence with a semicolon, which an audit later flagged. A real project's first few `resolve` events are worth a look.
- A Drive-homed folder's main-checkout junction (*Storage*) is made by an executor whose preamble forbids touching the main checkout, so that item's prompt overrides the preamble explicitly. If executors balk, conductor could make the junction from a `shared` entry naming a target folder.
- A cleanup template inlines every unused audit note into its prompt, uncapped. If a repo collects dozens, cap the count per cleanup (oldest first).
- A cleanup's deferred design calls live only in its report's **Deferred** and, once raised, `state.md`'s *Paged*; conductor tracks none. If they get lost, they could become a standing inbox section.
- **Proven with the fake executor only**; each real first use is worth a look: a readonly item beside others (did it pass `CONDUCTOR_PORT` through?), `bug_hunt` (its checker, how it uses the hours), `disk_audit` (its **Deferred** list; junctions removed as links, worktree folders left alone), `claude_md_cleanup`, `amend` (treated as overriding the prompt or plan, at launch, resume and mid-run), and `memory_critical` with its tell.
- Word Warriors needs `open_roots = ["C:/Code/gdrive-sync/wordwarriors"]` in its `conductor.toml`, by request (its planner's job), before **open X** reaches its Drive folder.
- `planner.md` sits at its 20 KB budget after 28 (pipeline review fitted by tightening); the next addition there needs a cut or a budget revision by a cleanup.
- Two window-focus questions need a human look (an `open:` window raised by the daemon may come up behind others too): a forwarded app launch may only flash the taskbar button (`AllowSetForegroundWindow`), and a picker opened by an app-started daemon may come up behind other windows.
- `picks/`, `session.md` and (28) `review.md` joined the runtime paths after the sandbox, Corelight and Word Warriors were made, so each logs a startup `warning` until a request adds them to its `.gitignore`: each project's planner's job (`planner.md`, *Requests*).
- **The self-restart's first real use is worth a look** (a conductor commit with a live Word Warriors daemon): did it restart, adopt executors, keep stages? A second commit during a blocked wait is not re-checked: `daemon_restart`'s `to` names the first, the new daemon runs what is on disk. The handshake wait (up to 20s) holds the old daemon's loop.
- A run recovered after its executor spawned but before the pid save (adopted by session marker) has no `start` event, as before 28 for any pid-less adoption. The exec-recovery branch exists twice (`daemon.recover`, `daemon_setup._recover_spawned`); a cleanup could make it one method.
- `tests/test_ask.py::test_default_taken_then_late_answer` failed once in a full parallel run (1 of 264) and passed alone: a flake under load.
- The picker server lives in the daemon's memory: a restart loses its port and token, so an open picker tab cannot save until `pick:` is sent again. A fixed port and a token in `.conductor/` would let tabs survive, if restarts mid-pick become common.
- `uv run conductor app` runs `.venv\Scripts\conductor.exe`, locked while the app is open, so any `pyproject.toml` change makes every `uv run` fail until the app closes. `uv run python -m conductor app` would avoid the lock (the "launch the conductor" line in `CLAUDE.md` would change with it).
- A staged item sees amendments only in its resume message (worded as overriding the plan); a stage started after an amendment was written still doesn't see it.
- A refused amend file stays in `queue/` for good (the planner never deletes). If it clutters, conductor could move refused files into `.conductor/` after logging.
- **Word Warriors' live daemon may still hang under a Claude Code session** (2026-10-20, 7:01am: started from an app that "launch the conductor" ran as a background command, on old code). To detach it: close the app and launch it again, then **Stop daemon…** and **Start daemon**; executors carry on and are adopted. Matt's step, unless a self-restart (which starts the new daemon detached) gets there first.
- `OCControl.Service` (Alienware OC Controls) swings free RAM by ~10 GiB every ~3 minutes on this box, so the default `memory_pressure_free_gb` (3) trips at its peaks under real work. The fix is outside conductor: stop the service or update AWCC.
- What Claude Code's reaper counts as "critically low" is unknown; after the next reap, compare the `daemon_died` event's memory with `memory.jsonl` around it.
- Executors are still the daemon's own children (exit codes kept), so a tree kill of the daemon takes them; only an app-started daemon is detached.
- The reaper matches an orphan by working folder or command line inside the item's run window: a leftover started while staging is matched only from the launch on, and `worktrees = false` items are never reaped.
- A reply is the first `progress` line after a delivery, by position: an executor that posts a milestone first has it shown as the reply. If that misfires in real use, the preamble could ask for `re #N` and conductor tag by that.
- Log compaction drops images' base64 and `originalFile` copies (`docs/executors.md`); the full session stays in Claude Code's store for `conductor open`, only as long as Claude Code keeps it. Run logs still stay big (Word Warriors' were 78 MB, largest 7.7 MB, per its 051 disk audit); compaction could also trim long tool outputs.
- `session.md` carries every event since the previous read mark, so after a long gap it is large (the sandbox's first was 39 KB). If a real digest grows past comfortable, summarise *Other* by kind beyond a cap. `session:` has no app button by design.
- `tests/test_picker.py::test_save_refusals` is flaky on Windows (`ConnectionAbortedError`, ~1 run in 3): the picker server refuses some saves before reading the body, so the unread bytes reset the connection. Drain the body (capped) before refusing.
