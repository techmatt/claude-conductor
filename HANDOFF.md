# HANDOFF — claude-conductor

The one file that starts a claude.ai session for conductor work. Read it, then `planner.md` (how a planner session uses conductor, ending with the vocab; a start reads `planner-start.md`) and `README.md` (how conductor works, with an index into `docs/`); `claude-ai-new-project.md` covers starting, parking and resuming a project. Every conductor prompt updates *Status* and *Open* before committing; nothing else in this file changes without a reason.

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
- **Approval** (revised 2026-10-22). Every prompt Matt asks for, discusses or has the planner write goes straight to `queue/`; Matt stops what he doesn't want. `proposed/` holds only what he says to hold.
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

What is live, in brief: per-repo queues with worktrees, serial merges, `alongside` items beside others (30; `paths:` still works, undocumented for planners) and staging; exclusive templates run alone; questions, progress lines, outlines, `tell:` messages and amendments; reviewed reports and checkpoints; run targets, pickers, conductor-owned templates and audit notes; memory sampling with a launch guard and a leftover reaper; the desktop app, where the Repos tab follows one item end to end in its Conversation view, the header's memory line shows only under pressure (27), and the window title and tray icon show live status, the icon being conductor's own (29, `docs/app.md`, *Header and title*). "Restart all" (`CLAUDE.md`, `tools/restart_all.py`) restarts the app and every live daemon, both detached. `open: folder|reveal|file|text|url` and `notify:` (26) let the planner open a checked project path or URL, or toast, without computer use (`docs/signals.md`). From 28: self-restart on a code change; `review: all`; readonly items start once queued. From 31: rewrites restage, undo a raced launch, and are set aside after launch. From 32: `resume:` items (**ask X**, **post-audit X**; `docs/resume.md`); an edited reviewed report is unreviewed again. From 34: a four-step session start; `closeout:` moves the read mark. From 35: a receipt per planner write (`docs/receipts.md`; from 43 every kind within ~1s); the marker in `planner-notes.md`. From 36: the night shift (`docs/night-shift.md`): a stall check, watch items, `backlog\<repo>\` draws within a nightly budget; `general_cleanup` / `test_cleanup` (old names aliased). From 37: only Matt's acts count between full-length draws. From 39: auto-push (`docs/push.md`); this repo pushes after each commit. From 40: autopilot off / night / always (`docs/night-shift.md`); a one-line header, one daemon button; Enter-sends message boxes; rare vocab in `planner-vocab.md`. From 41: `session.md` carries `state.md`'s due lines, so a start stages only `planner.md` and `session.md`; executors run `date` before each ETA. From 42: a control folder with a remote is auto-pushed, batched after request commits (`docs/push.md`; Word Warriors' is on GitHub); a request applies once `request.md` (written last) and its targets read. From 43: watches are `W<n>_watch`, never a queue number; `audit:` on a running item; the guard ignores `refs/remotes/*`. From 44 (`docs/worktrees.md`): items land as one squashed commit; a red of conductor's own making lands, no hold; `land:` lands a kept red branch; a `conductor:` report line when the outcome differs from `status:`; a holding red's toast says HELD; the planner auto-approves. From 45: a queue item whose `depends_on` can't be met in order gets an `accepted` receipt with a `warning` (`docs/items.md`); a `planner-*` signal settles 0.25s. From 46: a leaner digest and inbox (`docs/inbox.md`, *settled*); a signal sent with a request gets its receipt with the request's; `ack: invalid` / Dismiss, and "N rejected" counts since the checkpoint. From 47: the start's signal is written under `/mnt/user-data/outputs/`, and its commit and one stage of `planner-start.md`, `session.md` and `planner.md` go out in one turn.

The planner docs are split (24, 46): `planner-start.md`, the session start checklist (five calls); `planner.md`, read whole at its step 5; `claude-ai-new-project.md`, only new / park / resume and setup; README, an overview plus an index into `docs/`. A session starts with one signal, `session: start`: conductor writes a checkpoint, then `session.md` (the inbox since the read mark, `state.md`'s due lines, conductor's reports past `planner-notes.md`'s marker, per-repo maintenance facts), then moves the read mark.

A next prompt should know:
- A behaviour change updates `planner.md` (and `claude-ai-new-project.md` where it covers the topic) and the matching `docs/` file, not README's body.
- Every doc and code file has a size budget (`CLEANUP.md`, *Size budgets*); `uv run python tools/sizes.py` shows breaches. `Daemon` is `daemon.py` plus ten `daemon_*` mixins (signals, messages, receipts, reap, merge, restart, setup, templates, watch, push); the app's tabs are one `app/*_panel.py` each, with an item's detail in `app/trail.py`.

## Open

- Tamper is attributed (30) only to an item whose hook saw it name a changed control path; a change made by a script that never names the path, or by a stage or merge executor (no hook), still ends every active item red.
- Messages and amendments (30) are delivered only on the lead's own tool calls (`agent_id` absent in the hook input, Claude Code 2.1.305), so mail waits out a whole subagent or Workflow call. `progress` no longer delivers mail. If the `agent_id` field ever disappears, every call counts as the lead's again.
- `alongside` is proven in the sandbox with real executors (one pair, one tell during a helper); its first real use is worth a look: did both reports say whether they touched the other's area?
- Each code repo's `CLAUDE.md` must say "commit to the branch you are on", not "commit to `main`". The sandbox's was changed; a real repo's first prompt carries it.
- A fast-forward of the main checkout interrupted mid-write (the daemon killed during `git merge --ff-only`) leaves tracked files dirty, and the merge holds as "uncommitted changes" until Matt resets the checkout. Rare; needs a person.
- Clicking a toast does not open the app. Toasts use Command Prompt's app id; a registered `conductor-app:` URI plus `launch_action` would likely work but needs a human click to prove.
- Reports finished before `reviewed:` existed have no `reviewed` event: on Corelight they list as unreviewed until marked (**process all**).
- A message reaches an executor only after one of the lead's tool calls ends: not during a blocking `ask`, a subagent or Workflow call, an idle wait, or after the last call (logged undelivered). The fallback: stream user messages into `-p --input-format stream-json` stdin (changes launch, `--resume` staging, the restart rule); it would also allow switching a running executor into ultracode (`docs/executors.md`, *Ultracode*).
- A command the cap moves to the background is stopped by Claude Code after 10 minutes.
- Receipts: a heavy tick (~50 checkpoint warnings) still delays one 2.5-3.2s (45). **They still miss the first stage about a third of the time** (Word Warriors session 12, after 46): a request's, a queue item's or an amendment's receipt staged in the call right after the commit was missing 5 times; re-staging once always found it. 46 fixed only the signal-with-request case. Measure every kind on the live daemon, from the bridge's write to the receipt landing; if ~1s can't be met, `planner.md` should make wait-or-re-stage the normal path, not a fallback.
- **A picker whose round was cleaned up can still be opened**: Matt's app sent `pick:` for 109's manifest after 112 deleted the decided round, giving a rejected signal. The inbox's *Picks* lists every saved pick for the rest of the session. Hide a pick once saved, and have the app's pick action check that the manifest exists.
- `planner-start.md` step 3 assumes a workspace shell and `TZ=America/Los_Angeles`; Matt's saved preference must carry steps 1–5 (the file is first read at step 5).
- A red the planner never saw but wrote `state.md` after is hidden from the digest (46); *Now* and the app still show its effect.
- The app's refresh probes run targets on the UI thread (0.3s timeout each) and re-reads every `finish`, `red`, `clear` and `reviewed` event: move to a worker if it gets sluggish.
- Only the latest checkpoint shapes the views. The Repos tab's earlier group shows the 10 newest settled items per repo; older ones via Events (*since checkpoint* off), `conductor events --since checkpoint N` or `conductor open`.
- A leftover worktree is retried only at daemon start, before a `disk_audit` launches, and when its repo's queue empties (never for the item that just emptied it). Add a periodic retry if a stray dev server keeps one around.
- The merge executor's checks are structural (markers gone, nothing else touched); whether it kept both sides' *intent* shows only by reading (in the sandbox it joined two rewrites of one sentence with a semicolon). A real project's first `resolve` events are worth a look.
- A Drive-homed folder's main-checkout junction (*Storage*) is made by an executor whose preamble forbids touching the main checkout, so that item's prompt overrides it; if executors balk, conductor could make the junction from a `shared` entry.
- A cleanup template inlines every unused audit note into its prompt, uncapped. If a repo collects dozens, cap the count per cleanup (oldest first).
- A cleanup's deferred design calls live only in its report's **Deferred** and, once raised, `state.md`'s *Paged*; conductor tracks none (a standing inbox section, if they get lost).
- **Proven with the fake executor only**, worth a look at first real use: a readonly item beside others (`CONDUCTOR_PORT` passed through?), `bug_hunt`, `disk_audit` (junctions removed as links), `claude_md_cleanup`, and `memory_critical` with its tell.
- **ask X** needs the original's transcript, which Claude Code prunes after `cleanupPeriodDays` (30 by default): then it fails red at launch. `report_changed` checks only reports reviewed since 32 (no hashes before).
- From the first self-audit (sandbox 069): every executor spends a turn on ToolSearch loading the deferred `progress` tool; a repo asking for one-line commits conflicts with Claude Code's `Co-Authored-By` trailer.
- At budget: `CLAUDE.md`, this file, `executors.md`, `progress.md`, `app-panels.md`, `items.md`; the next addition to any needs a cut or a cleanup's budget revision; `items.md`'s is a split, scheduling into its own file (Matt, 38).
- Night shift (36): proven with the fake executor only. First real nights: are a watch's judgements and tells useful, does an item's whole `max_hours` fitting the budget leave enough drawn? Full-length counts only a declared `max_hours` ≥ 2; back-to-back is per-repo. Autopilot (40) too; the window's ~1050px floor is the panels'.
- Repos tab (33), 1250px window: Note ~140px; three buttons push Terminal off the edge.
- Window focus needs a human look: an `open:` window, a forwarded app launch or a picker from an app-started daemon may come up behind others.
- `picks/`, `session.md` and (28) `review.md` joined the runtime paths after the sandbox, Corelight and Word Warriors were made, so each logs a startup `warning` until a request adds them to its `.gitignore`: each project's planner's job. `receipts/` (35) and `backlog/` (36) need none: they ignore themselves.
- Self-restart: a second commit during a blocked wait is not re-checked (the new daemon runs what is on disk); the handshake wait (up to 20s) holds the old daemon's loop.
- A daemon restart loses the picker server's port and token, so an open picker tab can't save until `pick:` is resent; a fixed port and a token in `.conductor/` would fix it.
- `uv run conductor app` runs `.venv\Scripts\conductor.exe`, locked while the app is open, so any `pyproject.toml` change makes every `uv run` fail until the app closes; `uv run python -m conductor app` would avoid it.
- A staged item sees amendments only in its resume message (worded as overriding the plan); a stage started after an amendment was written still doesn't see it.
- Corelight's control folder (`C:\Code\corelight-control`) has no remote, so it is not pushed; adding `origin` is all auto-push needs.
- Requests trust the planner to write `request.md` last: a target still being written by a writer that allows shared reads could be read half-done (the readable check catches only a writer holding it locked).
- `OCControl.Service` (Alienware OC Controls) swings free RAM by ~10 GiB every ~3 minutes on this box, so the default `memory_pressure_free_gb` (3) trips at its peaks under real work; the fix is outside conductor (stop the service or update AWCC).
- What Claude Code's reaper counts as "critically low" is unknown; after the next reap, compare the `daemon_died` event's memory with `memory.jsonl` around it.
- Executors are still the daemon's own children (exit codes kept), so a tree kill of the daemon takes them; only an app-started daemon is detached.
- The reaper matches an orphan by working folder or command line inside the item's run window: a leftover started while staging is matched only from the launch on, and `worktrees = false` items are never reaped.
- A reply is the first `progress` line after a delivery, by position, so a milestone posted first shows as the reply; if that misfires, the preamble could ask for `re #N`.
- Log compaction drops images' base64 and `originalFile` copies; run logs stay big (Word Warriors' 78 MB, per its 051 disk audit): compaction could trim long tool outputs.
- Daemon restarts and stage chatter in a digest's *Other* still pile up after the last `state.md` request.
- 31's planner action rules (signals only from Matt's words, `reviewed:` only inside processing) are rules, not checks: worth a look in Word Warriors' next sessions.
