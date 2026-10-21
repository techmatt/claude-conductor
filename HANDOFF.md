# HANDOFF — claude-conductor

The one file that starts a claude.ai session for conductor work. Read it, then `planner.md` (how a planner session uses conductor, every session, ending with the vocab) and `README.md` (how conductor works, with an index into `docs/`); `claude-ai-new-project.md` covers starting, parking and resuming a project. Every conductor prompt updates *Status* and *Open* before committing; nothing else in this file changes without a reason.

## How work on conductor flows

- Matt discusses with a claude.ai session linked to the box. The session reads and writes `C:\Code\claude-conductor` over the bridge. It writes each prompt to `prompts/NN_name.md` (git-ignored).
- Matt pastes or names the prompt in a Claude Code session in this repo. Conductor never runs its own queue: its prompts always go this way, by hand.
- CC writes `reports/NN_name_report.md` (git-ignored) and ends with `Report written to <path>`. Matt says "report's in", and the session reads it. These reports have no `reviewed` event: the planner keeps `Last report reviewed by the planner: NN_name` in `planner-notes.md` (git-ignored, its own), with lines for *Open* the next prompt or cleanup merges here; it never edits this file.
- **Cleanup conductor** is a standing maintenance pass (`CLEANUP.md`) Matt runs in a Claude Code session here. A planner recommends it in one line with the reason, never runs it unasked: after ~10 prompts since *Status*'s `Last cleanup:` line, or when a doc breaks its size budget (`uv run python tools/sizes.py`).
- Testing uses the sandbox only: `C:\Code\conductor-sandbox` and `C:\Code\conductor-sandbox-control`.
- Before writing a prompt or saying whether a prompt has started, read `prompts/ledger.md`'s last lines: `start` without `done` is running; never edit its file, give Matt a message to paste into its session.
- Drive is never a relay for this workflow (a project's *Storage* policy is a separate thing).

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

Last cleanup: 2026-10-21, after prompt 37

Phases 1–4 are done; phase 5 runs live with Corelight (`C:\Code\corelight-control`) and Word Warriors (`C:\Code\wordwarriors-control`). Each prompt's report in `reports/` says what it changed; `README.md` and `docs/` hold how it all works now.

What is live, in brief: per-repo queues with worktrees, serial merges, `alongside` items beside others (30; `paths:` still works, undocumented for planners) and staging; exclusive templates run alone; questions, progress lines, outlines, `tell:` messages and amendments; reviewed reports and checkpoints; run targets, pickers, conductor-owned templates and audit notes; memory sampling with a launch guard and a leftover reaper; the desktop app, where the Repos tab follows one item end to end in its Conversation view, the header's memory line shows only under pressure (27), and the window title and tray icon show live status, the icon being conductor's own (29, `docs/app.md`, *Header and title*). "Restart all" (`CLAUDE.md`, `tools/restart_all.py`) restarts the app and every live daemon, both detached. `open: folder|reveal|file|text|url` and `notify:` (26) let the planner open a checked project path or URL, or toast, without computer use (`docs/signals.md`). From 28: the self-restart on a code change (`docs/restart.md`); `review: all` (`review.md`); readonly items start once queued; one-call requests; `setup` off the daemon loop. From 31: rewrites restage, undo a raced launch, and are set aside after launch. From 32: `resume:` items (**ask X**, **post-audit X**; `docs/resume.md`); an edited reviewed report is unreviewed again. From 34: a four-step session start; `closeout:` moves the read mark. From 35: a receipt per planner write (`docs/receipts.md`); the marker in `planner-notes.md`. From 36: the night shift (`docs/night-shift.md`): a stall check, watch items, `backlog\<repo>\` draws within a nightly budget; `general_audit` / `test_perf` renamed `general_cleanup` / `test_cleanup`, the old names kept as aliases. From 37: between full-length draws only Matt's acts count (never a request); a waiting request's receipt is `pending`; a shared item number is never guessed. From 38: atomic writes' temp names carry the pid.

The planner docs are split (24): `planner.md`, read whole every session; `claude-ai-new-project.md`, only new / park / resume and setup; README, an overview plus an index into `docs/`, one topic per file. A session starts with one signal, `session: start`: conductor writes a checkpoint, then `session.md` (the inbox since the read mark, conductor's reports past `planner-notes.md`'s marker, per-repo maintenance facts), then moves the read mark.

A next prompt should know:
- A behaviour change updates `planner.md` (and `claude-ai-new-project.md` where it covers the topic) and the matching `docs/` file, not README's body.
- Conductor's own reports now start with a `status:` line (this repo's `CLAUDE.md`); the digest shows it, or *no status line* for older ones.
- Every doc and code file has a size budget (`CLEANUP.md`, *Size budgets*); `uv run python tools/sizes.py` shows breaches. `Daemon` is `daemon.py` plus nine `daemon_*` mixins (signals, messages, receipts, reap, merge, restart, setup, templates, watch); the app's tabs are one `app/*_panel.py` each, with an item's detail in `app/trail.py`.

## Open

- Tamper is attributed (30) only to an item whose hook saw it name a changed control path; a change made by a script that never names the path, or by a stage or merge executor (no hook), still ends every active item red.
- Messages and amendments (30) are delivered only on the lead's own tool calls (`agent_id` absent in the hook input, Claude Code 2.1.305), so mail waits out a whole subagent or Workflow call. `progress` no longer delivers mail. If the `agent_id` field ever disappears, every call counts as the lead's again.
- `alongside` is proven in the sandbox with real executors (one pair, one tell during a helper); its first real use is worth a look: did both reports say whether they touched the other's area?
- Each code repo's `CLAUDE.md` must say "commit to the branch you are on", not "commit to `main`". The sandbox's was changed; a real repo's first prompt carries it.
- A fast-forward of the main checkout interrupted mid-write (the daemon killed during `git merge --ff-only`) leaves tracked files dirty, and the merge holds as "uncommitted changes" until Matt resets the checkout. Rare; needs a person.
- The app's taskbar icon and live title (29) were checked offscreen only: whether Windows shows conductor's icon (its AppUserModelID `conductor.app`) rather than Python's, a pinned shortcut's icon, and the title's turning glyph in the taskbar need a human look after the next app restart.
- Clicking a toast does not open the app. Toasts use Command Prompt's app id; a registered `conductor-app:` URI plus `launch_action` would likely work but needs a human click to prove.
- Reports finished before `reviewed:` existed have no `reviewed` event: on Corelight they list as unreviewed until marked (**process all**).
- A message reaches an executor only after one of the lead's tool calls ends: not during a blocking `ask`, a subagent or Workflow call, an idle wait, or after the last call (logged undelivered). The fallback: stream user messages into `-p --input-format stream-json` stdin (changes launch, `--resume` staging, the restart rule); it would also allow switching a running executor into ultracode (`docs/executors.md`, *Ultracode*).
- A command the cap moves to the background is stopped by Claude Code after 10 minutes.
- The app's refresh probes run targets on the UI thread (0.3s timeout each) and re-reads every `finish`, `red`, `clear` and `reviewed` event: move to a worker if it gets sluggish.
- Only the latest checkpoint shapes the views. The Repos tab's earlier group shows the 10 newest settled items per repo; older ones via Events (*since checkpoint* off), `conductor events --since checkpoint N` or `conductor open`.
- A leftover worktree is retried only at daemon start, before a `disk_audit` launches, and when its repo's queue empties (never for the item that just emptied it). Add a periodic retry if a stray dev server keeps one around.
- The merge executor's checks are structural (markers gone, nothing else touched); whether it kept both sides' *intent* shows only by reading (in the sandbox it joined two rewrites of one sentence with a semicolon). A real project's first `resolve` events are worth a look.
- A Drive-homed folder's main-checkout junction (*Storage*) is made by an executor whose preamble forbids touching the main checkout, so that item's prompt overrides it; if executors balk, conductor could make the junction from a `shared` entry.
- A cleanup template inlines every unused audit note into its prompt, uncapped. If a repo collects dozens, cap the count per cleanup (oldest first).
- A cleanup's deferred design calls live only in its report's **Deferred** and, once raised, `state.md`'s *Paged*; conductor tracks none (a standing inbox section, if they get lost).
- **Proven with the fake executor only**, worth a look at first real use: a readonly item beside others (`CONDUCTOR_PORT` passed through?), `bug_hunt`, `disk_audit` (junctions removed as links), `claude_md_cleanup`, and `memory_critical` with its tell.
- Word Warriors needs `open_roots = ["C:/Code/gdrive-sync/wordwarriors"]` in its `conductor.toml`, by request (its planner's job), before **open X** reaches its Drive folder.
- **ask X** needs the original's transcript, which Claude Code prunes after `cleanupPeriodDays` (30 by default): then it fails red at launch. `report_changed` checks only reports reviewed since 32 (no hashes before).
- From the first self-audit (sandbox 069): every executor spends a turn on ToolSearch loading the deferred `progress` tool; a repo asking for one-line commits conflicts with Claude Code's `Co-Authored-By` trailer.
- At budget: `planner.md`, `CLAUDE.md`, this file, `executors.md`, `progress.md`, `app-panels.md`, `items.md`; the next addition to any needs a cut or a cleanup's budget revision; `items.md`'s is a split, scheduling into its own file (Matt, 38).
- Word Warriors' doubled inbox (38) never reproduced from its `events.db`; if it recurs, keep the file and diff it with `conductor inbox`.
- Night shift (36): proven with the fake executor only. First real nights: are a watch's judgements and tells useful, does an item's whole `max_hours` fitting the budget leave enough drawn? Full-length counts only a declared `max_hours` ≥ 2 (projects default to 2); back-to-back is per-repo.
- Repos tab (33), 1250px window: Note ~140px; three buttons push Terminal off the edge.
- Window focus needs a human look: an `open:` window, a forwarded app launch or a picker from an app-started daemon may come up behind others.
- `picks/`, `session.md` and (28) `review.md` joined the runtime paths after the sandbox, Corelight and Word Warriors were made, so each logs a startup `warning` until a request adds them to its `.gitignore`: each project's planner's job. `receipts/` (35) and `backlog/` (36) need none: they ignore themselves.
- Self-restart: a second commit during a blocked wait is not re-checked (the new daemon runs what is on disk); the handshake wait (up to 20s) holds the old daemon's loop.
- A run adopted by session marker (spawned, no pid saved) has no `start` event.
- A daemon restart loses the picker server's port and token, so an open picker tab can't save until `pick:` is resent; a fixed port and a token in `.conductor/` would fix it.
- `uv run conductor app` runs `.venv\Scripts\conductor.exe`, locked while the app is open, so any `pyproject.toml` change makes every `uv run` fail until the app closes; `uv run python -m conductor app` would avoid it.
- A staged item sees amendments only in its resume message (worded as overriding the plan); a stage started after an amendment was written still doesn't see it.
- `OCControl.Service` (Alienware OC Controls) swings free RAM by ~10 GiB every ~3 minutes on this box, so the default `memory_pressure_free_gb` (3) trips at its peaks under real work; the fix is outside conductor (stop the service or update AWCC).
- What Claude Code's reaper counts as "critically low" is unknown; after the next reap, compare the `daemon_died` event's memory with `memory.jsonl` around it.
- Executors are still the daemon's own children (exit codes kept), so a tree kill of the daemon takes them; only an app-started daemon is detached.
- The reaper matches an orphan by working folder or command line inside the item's run window: a leftover started while staging is matched only from the launch on, and `worktrees = false` items are never reaped.
- A reply is the first `progress` line after a delivery, by position, so a milestone posted first shows as the reply; if that misfires, the preamble could ask for `re #N`.
- Log compaction drops images' base64 and `originalFile` copies; run logs stay big (Word Warriors' 78 MB, per its 051 disk audit): compaction could trim long tool outputs.
- `session.md` hides reviewed items' events and a closed session's requests (34); daemon restarts in *Other* still pile up.
- Receipts (35): sandbox-proven with the fake executor; first real use is Word Warriors' next session (does the planner stop re-staging `inbox.md`?). A queue item rewritten while the daemon is down gets no new receipt.
- A queue file written after its item launched stays in `queue/` as `.md.bad` (31), like a refused amend file; move both to `.conductor/` if they clutter.
- 31's planner action rules (signals only from Matt's words, `reviewed:` only inside processing) are rules, not checks: worth a look in Word Warriors' next sessions.
