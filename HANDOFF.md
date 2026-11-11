# HANDOFF — claude-conductor

The one file that starts a claude.ai session for conductor work. Read it, then `planner.md` (how a planner session uses conductor, from its *Start* to the vocab) and `README.md` (how conductor works, with an index into `docs/`); `claude-ai-new-project.md` covers starting, parking and resuming a project. Every conductor prompt updates *Status* and *Open* before committing; nothing else in this file changes without a reason.

## How work on conductor flows

- Matt discusses with a claude.ai session linked to the box. The session reads and writes `C:\Code\claude-conductor` over the bridge. It writes each prompt to `prompts/inbox/<YYYYMMDD-HHMMSS>-<project>-<slug>.md` (git-ignored), unnumbered: running it numbers it (`tools/prompts.py`), so planners writing at once never collide.
- Matt says "run the next prompt" (the oldest waiting) or names one in a Claude Code session in this repo. Conductor never runs its own queue: its prompts always go this way, by hand.
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
- **Approval** (revised 2026-10-22, 2026-11-03). The planner writes a prompt only when Matt asks for one (a discussion or a design answer is not asking); it goes straight to `queue/` and Matt stops what he doesn't want. `proposed/` holds only what he says to hold.
- **Staging is a resumable session:** a read-only orient phase first, then the same session resumes when it holds the lock.
- **Concurrency within a repo is declared, not judged.** It uses disjoint `paths:` plus per-item worktrees, with serial merges.

## Phases

1. Core: queues, executors, event log, inbox, tamper guard, requests, restart rule. **Done** (`reports/01_core_report.md`).
2. The ask tool, toasts, `conductor init`, and status definitions. **Done** (`reports/02_ask_report.md`). A blocking ask outlives Claude Code's 30-minute silent-call abort through a per-server timeout and 60-second progress pings (`docs/executors.md`, *Long tool calls*).
3. Worktrees and staging: per-item worktrees, serial merges, `paths:` concurrency, stage-then-resume. **Done** (`reports/03_worktrees_report.md`).
4. The app: a PySide6 tray app with Repos, Questions, Events and Proposed panels, reading `events.db`. **Done** (`reports/04_app_report.md`).
5. The first real project, a roguelike deckbuilder, started from a fresh claude.ai session with `claude-ai-new-project.md` alone. Its first sessions are the test of that file. Gaps it finds come back here as *Open* lines and become conductor prompts.

## Status

Last cleanup: 2026-11-10, after prompt 103

Phases 1–4 are done; phase 5 runs live with Corelight (`C:\Code\corelight-control`), Word Warriors (`C:\Code\wordwarriors-control`), aicomplete (`C:\Code\aicomplete-control`), fractals (`C:\Code\fractals-control`) and workbench (`C:\Code\workbench-control`: small unrelated scripts, a subfolder each; 92). Each prompt's report in `reports/` says what it changed; `README.md` and `docs/` hold how it all works now.

What is live, by area (each prompt's report says when it came; the doc named holds how it works now):

- **Queue and scheduling**: per-repo queues, worktrees, items landed as one squashed commit with serial merges, `alongside` and readonly items beside others, exclusive templates alone, staging, rewrites and amendments, `depends_on` warnings (a cancelled dependency holds only its dependent, 82), `land:` for a kept red branch; a fast-forward that stops part-way is put back and retried, `checkout_dirty` (red) when what is left is not the branch's (`docs/items.md`, `scheduling.md`, `worktrees.md`).
- **Executors**: a light preamble for a small item, a lean one for a watch, the full one otherwise (`docs/preambles.md`); questions, progress lines, outlines, `tell:`, `audit:`, `pause:` / `resume:`, `resume:` items, run targets, conductor-owned templates and audit notes, memory sampling with job objects, a launch guard and a leftover reaper (`docs/executors.md`, `progress.md`, `questions.md`, `templates.md`, `memory.md`).
- **Planner loop**: `session: start` makes a checkpoint and `session.md` (the project's `[links]` first since 60, the inbox, `state.md`'s due lines, *Planner habits*) and moves the read mark; conductor's own reports past the marker list in *Unreviewed reports*; a receipt per planner write; `reviewed:` / `review: all`; `ack: invalid` (`docs/inbox.md`, `receipts.md`, `signals.md`).
- **Pickers** (`docs/picks.md`): one per `pick:` header line, the app's Pick… a menu when several (82); `kind: many` rows (58); since 60 conductor retires a picker (manifest and untracked images) once a later `<name>_r<N>` round is saved, or on `retire: <name>`; executors never delete one, and a retired name is refused (65). `needs_pick: <name>` holds an item until its picker is saved and appends the choices at launch (66).
- **Night shift**: a stall check, `W<n>_watch` items every 30 minutes (and for a question blocked 15), backlog draws within a nightly budget, autopilot off / night / always gating draws only; in a digest routine watches are one count line, the watch's tells and answers listed (`docs/night-shift.md`).
- **Requests and messages** (58): requests apply in write order, wait for late targets and 30s (65; since 84 from the folder's newest write) for a copy identical to HEAD; a target can come as edit blocks against the live copy (84); a folder with no `request.md` moves to `requests/stale/` after an hour (60); a tell never answers a question; queue receipts give the start and why (`docs/control-folder.md`, `questions.md`, `receipts.md`).
- **Parked edits and retros** (59): `parked\<repo>.md` lines go to the next serial item (`[rules]` only to `rules: true`) and skipped ones come back (`docs/parked.md`); reports add `friction:`; the digest marks a workflow retro due (62) on a `partial`/`red` item, a run past ~1.5× its first ETA, or 12 items (`docs/inbox.md`).
- **Executor discipline** (61–102; `docs/preambles.md`, `executors.md`, `questions.md`, `memory.md`, `templates.md`, `worktrees.md`): scope, `out/` scratch, load budget and `nice`, Write/Edit only, sized `timeout`s, no elevation, *Premises*, a ~60-line report; `wait_for`; `ask` with flagged defaults and `design: true`; ≤10 agents per Workflow; done-when rows checked early; `audit: true`; `append_only` union merges; `load_high` as information; the clock on every tool return; *Latitude* (102); *Maintenance facts* (`doc_budget_exempt`, 86).
- **Around it**: `conductor init` makes missing code repos and names a repo without a remote until it has one (91); `rename-repo` (57; `docs/rename-repo.md`); self-restart on a code change (the app once idle, 83) and "restart all", each daemon at its safe point adopting running items (68, 71; `docs/restart.md`); auto-push (`docs/push.md`); `open:` / `notify:` (`docs/open.md`); project tools (64, 67); the app: the Repos tab follows an item end to end, title, tray and tab show live status, a turning tab name while running (77), orange for anything waiting for review (81), `N running` in titles (84), a message headed by its real sender (78) (`docs/app.md`, `app-panels.md`).
- **Remote workers** (87, opt-in, removable; `docs/remote-workers.md`): `[remote]` starts a LAN broker; a `remote: true` item's `farm-seeds` shards a seeded job over browser tabs (wasm), a local-remote worker and the main box behind a per-kind fingerprint gate (native, and the bundle's own for wasm: 89), merged byte-identical where the references agree. Live on aicomplete (88). A status page, app chip and Workers tab, one worker state in each (90-97; `docs/remote-status.md`); one URL for good, riding out restarts and new page versions (94; `remote-url.md`); `remote-diag.md` for the planner, deadlines on preparing (98). The broker chooses each browser's core count by name, a new name from `start_cores` (4) (99, 101, 103; `remote-cores.md`), gives each free shard to the worker expected to finish it first, `main_slots` over every job (100), and copies a remote shard past 1.5× its expected time on a free main slot (103; `remote-placement.md`).

The planner docs: `planner.md` (its *Start*, then read whole at step 5 with `session.md`: the only two files a start stages), `planner-vocab.md` (rare ops only), `claude-ai-new-project.md` (only new / park / resume and setup); README is an overview plus an index into `docs/`.

A next prompt should know:
- A behaviour change updates `planner.md` (and `claude-ai-new-project.md` where it covers the topic) and the matching `docs/` file, not README's body.
- Every doc and code file has a size budget (`CLEANUP.md`, *Size budgets*; `uv run python tools/sizes.py`, 1 KB = 1,000 bytes). `planner.md` and `CLAUDE.md` hold theirs: an addition to `planner.md` pays for itself by folding a rule `docs/` already holds into a pointer. `Daemon` is `daemon.py` plus one `daemon_*` mixin per concern; the app's tabs are one `app/*_panel.py` each.
- Tests wait up to `WAIT_SECONDS` (180s, `tests/conftest.py`) for the daemon, so the full lane passes with a project's executor holding the CPU (`docs/development.md`).

## Open

Accepted gaps nobody plans to fix unless they bite live in `docs/known-limits.md`.

Chores:

- Corelight's `.gitignore` lacks `picks/`, `session.md` and `review.md` (a startup `warning`), and its control folder has no remote (`origin` is all auto-push needs): its planner's and Matt's.
- **Matt's saved start preference** (86): step 2's one access request now names the control folder, `claude-conductor` and `C:\Code\<project>`, in the turn of step 3's shell command (`planner.md` *Start*); `Repos:` is only the fallback. His preference should say the same.
- Staged plans' questions don't always reach the start prompt: 147's 4 were dropped silently (a 10-minute ask, the r62 scope unsettled). Fix: the start prompt carries each staged question with its answer, or "default stands". (aicomplete 153, due from checkpoint #6654)
- `ask` holds the whole session for its full window (147 idle 10 min). Fix: a non-blocking `ask` that returns at once and delivers the answer as a message. (aicomplete 153, due from checkpoint #6654)
- At an item's first outline post, check the outline against its done-when list and flag missing rows then, not near the end (147: two watch tells re-sent them). (aicomplete 153, due from checkpoint #6654)
- **Needs a human**: a toast click doesn't open the app (Command Prompt's app id; try a `conductor-app:` URI plus `launch_action`); windows from an app-started daemon may open behind others.
- Staging `Questions:` that decide a rule's shape should block launch until answered, or reach the planner at once: 156's Q1, answered 4 h after the merge, cost a rework item. (aicomplete 164's retro, due from #7341, as are the next three)
- Remind an executor when its last outline's ETA is over a third behind its latest `eta` (two of 156's three watch tells).
- Aicomplete's item scripts start headless Edge with `--user-data-dir` under `%TEMP%\aic-cdp-*` (not excluded from Defender): the worktree's `out/` would be (170). Its planner's; conductor launches no browser.
- Size splits deferred until a prompt would push past budget (cleanup_2026-11-10): control-folder.md's *Requests* into `docs/requests.md` the next time it would grow; `remote/board.py` (893/900 lines) worker state apart from jobs and shards.
- Defender still takes ~15-18% of the box through a full lane (104): process launches (~7ms of its CPU a git, ~19ms a Python), which no path exclusion covers. Fewer spawns is the only cut on conductor's side; the run is ~4 min.
- `wait_for`'s default `fail` pattern matches "error" in ordinary prose (subagent output under ultracode: aicomplete 173 lost ~10 min). Fix: a default that can't match ordinary text. Due from #7766.

Still not live:

- **The app's remote chip and Workers tab** (91, 97): sandbox only. Amber for a 5-min-old re-run too eager? A new key costs a 27-144 s build; prebuilding untried.
- **82's lines** (fractals): does a load-told executor go smaller rather than relaunch? Sized `timeout`s, no heredoc edits, a cancelled dependency's set-aside item and the Pick… menu are unseen live.
- **Cut-short `ask`** (58): its `resume` is unseen live.
- **Part-way fast-forwards** (after 60): watch the first `warning` "main checkout restored" and any `checkout_dirty`. Does the retry (4 tries, 0.5s doubling) outlast the Windows lock?
- **The light preamble** (55): no retained run has drawn it. Do small items ask with a default, background long calls, answer by number? One run big and blind: lower `LIGHT_WORDS`.
- **Flagged defaults and the load scan** (61): no question has opened `default overrides prompt` yet; does the 0.3s scan on the tick show in receipt times?
- **`pause:`** (48): no `pause:` signal in either project (Word Warriors' 125 paused by `tell:`). Does the item commit, stop its servers and keep calling `wait`? A watch's `answer:` source is unchecked: read the first `watch_answer` events; none yet.
- **`needs_pick`** (66): no live item has used it. Does a planner queue it once the report names the picker, and does the executor take the choices as given?
- **67's executor lines**: `design: true` is live (aicomplete, six by 096, all answered: none piled up). No live run yet of a stage's `Questions:`, `wait_for`'s `fail` and worker `pid`, or the watch's gate-time check. Does a default `fail` stop too often on harmless `error` lines?
- **68's lines**: unseen live: the amendment refusal (on questions merely mentioning one?), `load_high` (is 25% / 3 threads the right share?), the 10-agent Workflow rule.
- **72's lines**: unseen live, `nice -n 10`, scripts-in-files, the design-reversal line, the parked receipt. **A new daemon dying in `startup()` after the handover leaves nothing running**: two causes fixed (the snapshot retry; 77's cp1252 read, `docs/restart.md`), the class open: the ready file comes before `startup()`, so the old daemon can't fall back.
- **73's lines** (aicomplete 076/079): no live run yet of the 6-thread default or the results-folder line (it names `experiments/results/`; no per-repo key). A long-by-design doc is `doc_budget_exempt` (86).
- **74's lines** (aicomplete 089's preamble): does an item halve its threads mid-run on a fresh `Running beside you now`? Scope misses left: *Servers* on every aicomplete full item, "target" pulling the calibration line, "candidate" the ~260-word *Pickers* block.
- **75's lines**: unseen live, the done-when check, audit-note fix rule (does it stretch items?) and parked-in-scope line. A stage plan asking with no `?` parses as none. Cleanups re-verifying repeated self-audit notes: dedupe in `audits.render_notes`.
- **76's lines** (aicomplete 103): the no-elevation line unseen live. **A UAC hang made visible is not built**: `consent.exe` exists only while a prompt is up, so the memory sampler could raise an inbox line then (needs a real prompt to test).
- **83's lines** (aicomplete): unseen live, the app's self-restart once idle (no dialog, menu or unsent text) and a finished item's held question marked *item finished*, sorted last. Does a restart leave one tray icon?
- **81's waiting mark**: unseen on a real taskbar.
- **102's *Latitude***: does a tuning item post reachability early and name what gave way?
- **Repos that don't fit worktrees** (78, fractals checkpoint #1): new-project step 5 starts them with `worktrees = false` (91: a names-only listing of the root decides). Does fractals' report name enough to switch?
- **91's new-project setup**: does the first request edit the stub `state.md`, not replace it?
- **Inbox sections read twice** (aicomplete 11-03): a read spliced across a rewrite? Save the next with its read time.
- **98's diagnostics** (aicomplete): why did the laptop miss two gates (10:17, 10:25pm)?
- **99-103's core counts, placement** (aicomplete): laptop reset to 2, ceiling 3: does it hold; does a farm beat local (161 didn't)? Does the overdue copy cut a read's tail? 103's `answered:` and long-job line: unseen live (its load labels are live: `conductor 3%` in aicomplete's samples).
- **Farm load** (aicomplete 168, #7341): no broker → `run_local` runs native at full `--threads` (12-16 aic-sim, ~95 min): cap at `main_slots`. A `remote: true` item's load warning should name `main_slots` as its share, farming the lighter path (168 read it as "run native"). A worker offline mid-item raised no flag: the watch or *Now* should.
- `farm-seeds` and the worker page come from the working checkout: uncommitted code reaches live jobs and tabs (98, 99; 100's edits reloaded the laptop 4 times).
- `farm-seeds` runs `aic-sim.exe` in place: a rebuild mid-job fails, a straggler holds it (167). Copy it per job. Due from #7341.
- A spec newer than a worker's bundle isn't in it (167's laptop failed every shard of one). 100 drops a worker from a job at its first failed shard; to do: rebuild the bundle when a job names a spec it lacks. Due from #7341.
- **Remote workers** (87–89): the laptop joined over *Public* only via an older Allow rule for uv's `python.exe`; *Keep screen awake* is hidden on the LAN URL (no secure context).
- **A remote with `push = false`** (workbench, 92, since fixed by hand): init should set `push = true` for a reachable remote; the digest should flag one.
