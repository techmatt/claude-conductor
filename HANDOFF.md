# HANDOFF — claude-conductor

The one file that starts a claude.ai session for conductor work. Read it, then `planner.md` (how a planner session uses conductor, from its *Start* to the vocab) and `README.md` (how conductor works, with an index into `docs/`); `claude-ai-new-project.md` covers starting, parking and resuming a project. Every conductor prompt updates *Status* and *Open* before committing; nothing else in this file changes without a reason.

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

Last cleanup: 2026-10-31, after prompt 63

Phases 1–4 are done; phase 5 runs live with Corelight (`C:\Code\corelight-control`), Word Warriors (`C:\Code\wordwarriors-control`) and aicomplete (`C:\Code\aicomplete-control`). Each prompt's report in `reports/` says what it changed; `README.md` and `docs/` hold how it all works now.

What is live, by area (each prompt's report says when it came; the doc named holds how it works now):

- **Queue and scheduling**: per-repo queues, worktrees, items landed as one squashed commit with serial merges, `alongside` and readonly items beside others, exclusive templates alone, staging, rewrites and amendments, `depends_on` warnings, `land:` for a kept red branch; a fast-forward that stops part-way is put back and retried, `checkout_dirty` (red) when what is left is not the branch's (`docs/items.md`, `scheduling.md`, `worktrees.md`).
- **Executors**: a light preamble for a small item, a lean one for a watch, the full one otherwise (`docs/executors.md`, *Three preambles*); questions, progress lines, outlines, `tell:`, `audit:`, `pause:` / `resume:`, `resume:` items, run targets, conductor-owned templates and audit notes, memory sampling with job objects, a launch guard and a leftover reaper (`docs/executors.md`, `progress.md`, `questions.md`, `templates.md`, `memory.md`).
- **Planner loop**: `session: start` makes a checkpoint and `session.md` (the project's `[links]` first since 60, the inbox, `state.md`'s due lines, *Planner habits*) and moves the read mark; conductor's own reports past the marker list in *Unreviewed reports*; a receipt per planner write; `reviewed:` / `review: all`; `ack: invalid` (`docs/inbox.md`, `receipts.md`, `signals.md`).
- **Pickers** (`docs/picks.md`): `kind: many` rows (58); since 60 conductor retires a picker (manifest and untracked images) once a later `<name>_r<N>` round is saved, or on `retire: <name>`; executors never delete one.
- **Night shift**: a stall check, `W<n>_watch` items every 30 minutes (and for a question blocked 15), backlog draws within a nightly budget, autopilot off / night / always gating draws only; in a digest routine watches are one count line, the watch's tells and answers listed (`docs/night-shift.md`).
- **Requests and messages** (58): requests apply in write order, wait for late targets and 10s for a copy identical to HEAD; a folder with no `request.md` moves to `requests/stale/` after an hour (60); a tell never answers a question; queue receipts give the start and why (`docs/control-folder.md`, `questions.md`, `receipts.md`).
- **Parked edits and retros** (59): `parked\<repo>.md` lines go to the next serial item (`[rules]` only to `rules: true`) and skipped ones come back (`docs/parked.md`); reports add `friction:`; the digest marks a workflow retro due (62) on a `partial`/`red` item, a run past ~1.5× its first ETA, or 12 items (`docs/inbox.md`).
- **Executor discipline** (61): both preambles carry the scope, scratch, load-budget, `~/.claude` and leftover-budget rules; `wait_for` replaces shell polls; a question can flag `default overrides prompt`; *Now* shows machine load and ETA drift; `quiet` holds off while an item's tools still write (`docs/executors.md`, `memory.md`, `progress.md`). 62: a lean watch preamble, *Running beside you now*, one subagent rule, `quiet` waits out a promised time, commits dated by git.
- **Around it**: `conductor rename-repo` rekeys a repo everywhere, history included, and can move its checkout (57; `docs/control-folder.md`); self-restart on a code change and "restart all" (`docs/restart.md`); auto-push of repos and a control folder with a remote, this repo after each commit (`docs/push.md`); `open:` / `notify:` (`docs/open.md`); project tools, a Repos-tab button or `tool: X` that starts a `[tools.<name>]` and opens it, stopped with the daemon (64; `docs/app.md`); the app, whose Repos tab follows an item end to end and whose title and tray icon show live status (`docs/app.md`).

The planner docs: `planner.md` (its *Start*, then read whole at step 5 with `session.md`: the only two files a start stages), `planner-vocab.md` (rare ops only), `claude-ai-new-project.md` (only new / park / resume and setup); README is an overview plus an index into `docs/`.

A next prompt should know:
- A behaviour change updates `planner.md` (and `claude-ai-new-project.md` where it covers the topic) and the matching `docs/` file, not README's body.
- Every doc and code file has a size budget (`CLEANUP.md`, *Size budgets*; `uv run python tools/sizes.py`, 1 KB = 1,000 bytes). `planner.md` and `CLAUDE.md` hold theirs: an addition to `planner.md` pays for itself by folding a rule `docs/` already holds into a pointer. `Daemon` is `daemon.py` plus one `daemon_*` mixin per concern; the app's tabs are one `app/*_panel.py` each.
- Tests wait up to `WAIT_SECONDS` (180s, `tests/conftest.py`) for the daemon, so the full lane passes with a project's executor holding the CPU (`docs/development.md`).

## Open

Accepted gaps nobody plans to fix unless they bite live in `docs/known-limits.md`.

Bugs:

- **`identical to HEAD` after a real edit**: four real `state.md` edits on aicomplete applied as identical (2026-10-29 #614, 10-30 #1115 and #1126, 10-31 #1811); each was resent under a fresh name about 20s later and committed. Raise `SAME_WAIT_SECONDS` (10s) past that gap, or find why the copy arrives so late.
- **Retirement strands a tracked manifest**: `pick_retired` keeps a manifest git tracks but deletes its untracked images, so aicomplete's `card_frames_r2.json` and `scene_style_r1.json` show 51 broken tiles (its D21) and leave empty image folders. Skip retiring images whose manifest stays, or have the picker refuse a retired name.
- **`disk_cleanup`'s Drive scope**: the template lets an item delete in the main checkout and only move into Drive's archive, yet its step 2 acts on Drive-homed files, so aicomplete 049 deleted 41 files in Drive through the junction and said so. Say which it is: pick "moves only in Drive".
- **The app's validator can be older than the daemon**: after 60's restart the open app flagged 034 `hold` / "unknown front-matter fields" that the daemon had staged. The app should say so, or reload.

- **Merge subject from item title** (aicomplete 051's post-audit of 046): the squash took 046's title "Rules revision 21" for code at r22, a permanently wrong subject on main. Use the final commit's subject, or strip revision numbers from titles.
- **`wait_for` misses failures and dead processes** (051): it matched only the success pattern, so a panic went unseen for ~5 min (up to 15–20 min of gate), and waits on an exited script returned "Nothing yet" for 4 and 11 min. Add a `fail_line` (or a preamble line: match `panicked|error` too) and report when the watched process has exited.
- **Review budget unenforced** (051): 046's review ran 16 agents against "about 10", and `ultracode: true` pushes the other way. Say in the preamble that finders plus verifiers count, or have Workflow cap them.

Improvements:

- **Watch: check gate time against the deadline at each outline** (051): the 2:04am tell was right but 35 min after it mattered. Compare a gate row's given time with the item's measured rate at every outline ("56 min given, your rate says 44–83, no second run fits").
- **`planner.md`: rule deferred design calls at once** (aicomplete, 2026-10-31): Matt took the pick on every deferred call this session (044's eight, 048's fifteen, 049's two, 046's three) with "your take is good", each a round trip and a second `state.md` request. *Deferred design calls* could record the picks as rulings in the same processing request and list them, Matt overriding any he wants.
- **`planner.md`: one commit call per request** (aicomplete): processing a report took two bridge commits (targets, then `request.md` with the signal) because it says to write `request.md` last. Requests already wait for late targets (58); if write order inside one bridge call is safe, it can say "targets and `request.md` in one call, `request.md` listed last".
- **`planner.md` *Start* step 1 can return part of the tool set** (2026-10-30 and 10-31): the `select:` search loaded only the folder-access and list tools; stage and commit needed a second search. If it returns only some of the four, load the rest in the same turn before step 2.
- **63's deferred calls, Matt to confirm** (planner's picks = 63's): raise `SAME_WAIT_SECONDS` to 30s; on retirement the picker refuses a retired name rather than stranding a tracked manifest; `disk_cleanup` may only move files in a Drive home, never delete. These settle the first three Bugs above.
- **Project tools** (64): the first use is AI Complete's card viewer; its `[tools.cards]` is that planner's request. No stop verb or button yet: a hung tool goes with the daemon. Add `tool: X stop` if that bites.

Chores:

- Corelight's `.gitignore` lacks `picks/`, `session.md` and `review.md` (a startup `warning` until its planner's request adds them), and its control folder has no remote (adding `origin` is all auto-push needs): its planner's and Matt's.
- **Needs a human**: a toast click doesn't open the app (it uses Command Prompt's app id; a registered `conductor-app:` URI plus `launch_action` would likely work), and an `open:` window, a forwarded app launch or a picker from an app-started daemon may come up behind others.

Still not live:

- **Cut-short `ask`** (58): no message has landed during an `ask` since 58, so its `resume` is unseen. Parked edits skipped by an item and returned: 050 carries 14, the first big test.
- **Part-way fast-forwards** (after 60): watch the first `warning` "main checkout restored" and any `checkout_dirty`. Does the retry (4 tries, 0.5s doubling) outlast the Windows lock?
- **The light preamble** (55): no retained aicomplete or Word Warriors run has drawn it. Do small items still ask with a default, background long calls and answer by number? One that ran big and blind means lower `LIGHT_WORDS`.
- **Flagged defaults and the load scan** (61): no question has opened `default overrides prompt` yet; does the 0.3s scan on the tick show in receipt times?
- **`pause:`** (48): no `pause:` signal in either project (Word Warriors' 125 paused by `tell:`). Does the item commit, stop its servers and keep calling `wait`? A watch's `answer:` source is unchecked: read the first `watch_answer` events; none yet.
- **Long-lived servers** (54): a command the cap moves to the background is stopped after 10 minutes, so the preamble says to start servers detached. Did one last?
