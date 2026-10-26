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

Last cleanup: 2026-10-23, after prompt 50

Phases 1–4 are done; phase 5 runs live with Corelight (`C:\Code\corelight-control`) and Word Warriors (`C:\Code\wordwarriors-control`). Each prompt's report in `reports/` says what it changed; `README.md` and `docs/` hold how it all works now.

What is live, by area (each prompt's report says when it came; the doc named holds how it works now):

- **Queue and scheduling**: per-repo queues, worktrees, items landed as one squashed commit with serial merges, `alongside` and readonly items beside others, exclusive templates alone, staging, rewrites and amendments, `depends_on` warnings, `land:` for a kept red branch (`docs/items.md`, `scheduling.md`, `worktrees.md`).
- **Executors**: a light ~300-word preamble for a small item (under 150 words, no `ultracode` / `template` / `resume` / `audit` / `max_hours`, no picker words; 55), the full one otherwise with *Pickers* and *Servers* only where they apply (`docs/executors.md`, *Two preambles*); questions (a tell answers an item's own blocking one), progress lines, outlines, `tell:`, `audit:`, `pause:` / `resume:` with the `wait` tool, `resume:` items, run targets, pickers, conductor-owned templates (`content_cleanup` the newest; "audit" names only read-only passes, so `disk_audit` / `content_audit` are aliases since 54; the bug hunt caps browsers at one test lane plus one checker sweep) and audit notes, memory sampling (each executor in a job object, so orphans count; a rise checked every tick) with a launch guard and a leftover reaper (`docs/executors.md`, `progress.md`, `questions.md`, `templates.md`, `memory.md`).
- **Planner loop**: one `session: start` signal makes a checkpoint, `session.md` (inbox, `state.md`'s due lines, its *Planner habits* apart under their own heading since 55) and moves the read mark; conductor's own reports past the marker (ordered by `prompts/ledger.md`, so a cleanup can be the marker) list in every inbox's *Unreviewed reports*; *Now* gives each repo's next item number, and a saved picker reads *picks saved <time>* in every digest; a receipt per planner write, handled around each launch too; `reviewed:` / `review: all`; `ack: invalid` (`docs/inbox.md`, `receipts.md`, `signals.md`).
- **Night shift**: a stall check, `W<n>_watch` items every 30 minutes (and for a question blocked 15), backlog draws within a nightly budget, autopilot off / night / always gating draws only; in a digest routine watches are one count line, the watch's tells and answers listed (`docs/night-shift.md`).
- **Around it**: self-restart on a code change and "restart all" (`docs/restart.md`); auto-push of repos and a control folder with a remote, this repo after each commit (`docs/push.md`); `open:` / `notify:` (`docs/open.md`); the app, whose Repos tab follows an item end to end and whose title and tray icon show live status (`docs/app.md`).

The planner docs: `planner.md` (its *Start*, then read whole at step 5 with `session.md`: the only two files a start stages), `planner-vocab.md` (rare ops only), `claude-ai-new-project.md` (only new / park / resume and setup); README is an overview plus an index into `docs/`.

A next prompt should know:
- A behaviour change updates `planner.md` (and `claude-ai-new-project.md` where it covers the topic) and the matching `docs/` file, not README's body.
- Every doc and code file has a size budget (`CLEANUP.md`, *Size budgets*; `uv run python tools/sizes.py`): files read every session (`planner.md` 25.0 of 25 KB after 55, with nothing left to give: the next addition moves a row to `planner-vocab.md`; since 52 folded in the start and a normal session's vocab, `CLAUDE.md`) hold theirs, on-demand docs have ~20% headroom. `tools/sandbox_proof.py` marks every unreviewed sandbox item reviewed at the end of a run. `Daemon` is `daemon.py` plus one `daemon_*` mixin per concern; the app's tabs are one `app/*_panel.py` each, an item's detail `app/trail.py`.

## Open

- The light preamble (55) drops outline, ETAs, pickers and servers for a body under 150 words. Watch the first few: did a small item still ask with a default, background long calls and answer messages by number? If one turned out big and ran blind (no outline on *Now*), lower `LIGHT_WORDS` or add a signal word.
- Planner habits (55): `state.md`'s new *Planner habits* section is copied into `session.md` under its own heading, never with *Pending fixes*. Word Warriors' and Corelight's habit lines still sit under *Pending fixes* (conductor never edits a real control folder): `planner.md` tells the planner to move them at its next `state.md` write. Check the next session start no longer lists them to Matt.
- Tamper is attributed (30) only to an item whose hook saw it name a changed control path; a change made by a script that never names the path, or by a stage or merge executor (no hook), still ends every active item red.
- Messages and amendments reach an executor only after one of the lead's own tool calls ends (`agent_id` absent in the hook input, Claude Code 2.1.305; if that field ever disappears, every call counts as the lead's): not during a blocking `ask`, a subagent or Workflow call, an idle wait, or after the last call (logged undelivered). The fallback: stream user messages into `-p --input-format stream-json` stdin (changes launch, `--resume` staging, the restart rule; it would also allow switching a running executor into ultracode, `docs/executors.md`).
- `alongside` is proven in the sandbox with real executors (one pair, one tell during a helper); its first real use is worth a look: did both reports say whether they touched the other's area?
- Each code repo's `CLAUDE.md` must say "commit to the branch you are on", not "commit to `main`". The sandbox's was changed; a real repo's first prompt carries it.
- A fast-forward of the main checkout interrupted mid-write (the daemon killed during `git merge --ff-only`) leaves tracked files dirty, and the merge holds as "uncommitted changes" until Matt resets the checkout. Rare; needs a person.
- Clicking a toast does not open the app. Toasts use Command Prompt's app id; a registered `conductor-app:` URI plus `launch_action` would likely work but needs a human click to prove.
- Reports finished before `reviewed:` existed have no `reviewed` event: on Corelight they list as unreviewed until marked (**process all**).
- A command the cap moves to the background is stopped by Claude Code after 10 minutes. The preamble's *Servers* paragraph (54) says to start long-lived servers detached; did the next item with a preview server keep it past 10 minutes?
- Receipts: a heavy tick (~50 checkpoint warnings) still delays one 2.5-3.2s. **They missed the first stage about a third of the time** (Word Warriors session 12); 46 and 50 fixed the signal-with-request and request-with-queue-items cases (sandbox 1.5s → 0.6–0.8s, not yet seen live). Measure every kind on the live daemon, bridge write to receipt; if ~1s can't be met, `planner.md` should make wait-or-re-stage the normal path, not a fallback.
- **A picker whose round was cleaned up can still be opened**: Matt's app sent `pick:` for 109's manifest after 112 deleted the decided round, giving a rejected signal. The inbox's *Picks* lists every saved pick for the rest of the session. Hide a pick once saved, and have the app's pick action check that the manifest exists.
- `planner.md` *Start* step 3 assumes a workspace shell and `TZ=America/Los_Angeles`; Matt's saved preference must carry steps 1–5 (the file is first read at step 5).
- Job objects (53) are proven by tests and a scripted orphan, not yet live: at the next bug hunt, does the memory line's biggest item match Task Manager, with little `chrome.exe`/`node.exe` left "outside"? If Claude Code or Chromium ever refuses to start a child under a job without breakaway, that shows as a failed launch or a dead browser in a run log.
- Pause (48) is proven with the fake executor only: at first real use, did the item commit, stop its servers and keep calling `wait` (not end its session, not ask)? A watch's `answer:` source is not checked by conductor; read the first few `watch_answer` events.
- A red the planner never saw but wrote `state.md` after is hidden from the digest (46); *Now* and the app still show its effect.
- The app's refresh probes run targets on the UI thread (0.3s timeout each) and re-reads every `finish`, `red`, `clear` and `reviewed` event: move to a worker if it gets sluggish.
- Only the latest checkpoint shapes the views. The Repos tab's earlier group shows the 10 newest settled items per repo; older ones via Events (*since checkpoint* off), `conductor events --since checkpoint N` or `conductor open`.
- A leftover worktree is retried only at daemon start, before a `disk_cleanup` launches, and when its repo's queue empties (never for the item that just emptied it). Add a periodic retry if a stray dev server keeps one around.
- The merge executor's checks are structural (markers gone, nothing else touched); whether it kept both sides' *intent* shows only by reading (in the sandbox it joined two rewrites of one sentence with a semicolon). A real project's first `resolve` events are worth a look.
- A Drive-homed folder's main-checkout junction (*Storage*) is made by an executor whose preamble forbids touching the main checkout, so that item's prompt overrides it; if executors balk, conductor could make the junction from a `shared` entry.
- A cleanup template inlines every unused audit note into its prompt, uncapped. If a repo collects dozens, cap the count per cleanup (oldest first).
- A cleanup's deferred design calls live only in its report's **Deferred** and, once raised, `state.md`'s *Paged*; conductor tracks none (a standing inbox section, if they get lost).
- **Proven with the fake executor only**, worth a look at first real use: a readonly item beside others (`CONDUCTOR_PORT` passed through?), `bug_hunt`, `disk_cleanup` (junctions removed as links), `claude_md_cleanup`, and `memory_critical` with its tell.
- **ask X** needs the original's transcript, which Claude Code prunes after `cleanupPeriodDays` (30 by default): then it fails red at launch. `report_changed` checks only reports reviewed since 32 (no hashes before).
- From the first self-audit (sandbox 069): every executor spends a turn on ToolSearch loading the deferred `progress` tool; a repo asking for one-line commits conflicts with Claude Code's `Co-Authored-By` trailer.
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
- The reaper matches an orphan by its executor's job (53), else by working folder or command line inside the item's run window: a leftover started while staging is matched only from the launch on, and `worktrees = false` items are never reaped, job or not.
- A reply is the first `progress` line after a delivery, by position, so a milestone posted first shows as the reply; if that misfires, the preamble could ask for `re #N`.
- Run logs are compacted, then gzipped at 256 KB or more (54); Word Warriors' ~278 MB of logs is gzipped at the next daemon start. Nothing is ever dropped: if the gzipped total still grows too fast, drop logs of items reviewed long ago.
- Word Warriors' `conductor.toml` still lists `scratch` under `shared` (its scratch moved to `C:\Code\wordwarriors-scratch`, item 149), so every worktree links it, the preamble names it, and conductor makes an empty `C:\Code\wordwarriors\scratch` (in 151 too). Its planner drops `scratch` from `shared` by request; then the empty folder in the main checkout can go. `shared` is per repo, so another project with an in-repo scratch keeps it. `wd_tmp` (stray, worktrees root) was removed in 54.
- The preamble (54) says to leave a step whose question default can't be undone until last in the item (wordwarriors 152 lost shots a tell asked to keep). Worth a look at the next delete-with-default.
- Daemon restarts and stage chatter in a digest's *Other* still pile up after the last `state.md` request.
- 31's planner action rules (signals only from Matt's words, `reviewed:` only inside processing) are rules, not checks: worth a look in Word Warriors' next sessions.
