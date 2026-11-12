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

1–4. **Done**: the core (queues, executors, event log, inbox, tamper guard, requests, restart rule), the ask tool and toasts, worktrees and staging, the PySide6 tray app (`reports/01`–`04_*_report.md`).
5. The first real project, a roguelike deckbuilder, started from a fresh claude.ai session with `claude-ai-new-project.md` alone. Its first sessions are the test of that file. Gaps it finds come back here as *Open* lines and become conductor prompts.

## Status

Last cleanup: 2026-11-12, after prompt 108

Phases 1–4 are done; phase 5 runs live with Corelight (`C:\Code\corelight-control`), Word Warriors (`C:\Code\wordwarriors-control`), aicomplete (`C:\Code\aicomplete-control`), fractals (`C:\Code\fractals-control`) and workbench (`C:\Code\workbench-control`: small unrelated scripts, a subfolder each; 92). Each prompt's report in `reports/` says what it changed; `README.md` and `docs/` hold how it all works now.

What is live, by area (each prompt's report says when it came; the doc named holds how it works now):

- **Queue and scheduling**: per-repo queues, worktrees, items landed as one squashed commit with serial merges, `alongside` and readonly items beside others, exclusive templates alone, staging, rewrites and amendments, `depends_on` warnings (a cancelled dependency holds only its dependent, 82), `land:` for a kept red branch; a fast-forward that stops part-way is put back and retried, `checkout_dirty` (red) when what is left is not the branch's (`docs/items.md`, `scheduling.md`, `worktrees.md`).
- **Executors**: a light preamble for a small item, a lean one for a watch, the full one otherwise (`docs/preambles.md`); questions, progress lines, outlines, `tell:`, `audit:`, `pause:` / `resume:`, `resume:` items, run targets, conductor-owned templates and audit notes (with *Re-derived or re-read*, 108), memory sampling with job objects, a launch guard and a leftover reaper (`docs/executors.md`, `progress.md`, `questions.md`, `templates.md`, `memory.md`).
- **Planner loop**: `session: start` makes a checkpoint and `session.md` (the project's `[links]` first since 60, the inbox, `state.md`'s due lines, *Planner habits*) and moves the read mark; conductor's own reports past the marker list in *Unreviewed reports*; a receipt per planner write (one leaving `state.md` past 25 KB names lines to fold, 106); `reviewed:` / `review: all`; `ack: invalid` (`docs/inbox.md`, `receipts.md`, `signals.md`).
- **Pickers** (`docs/picks.md`): one per `pick:` header line, a Pick… menu when several (82); `kind: many` rows (58); conductor retires a picker once a later `_r<N>` round is saved or on `retire:` (60, 65); `needs_pick:` holds an item until its picker is saved (66).
- **Night shift**: a stall check, `W<n>_watch` items every 30 minutes (and for a question blocked 15), backlog draws within a nightly budget, autopilot gating draws only; routine watches one digest line (`docs/night-shift.md`).
- **Requests and messages** (58): requests apply in write order, wait for late targets and 30s from the folder's newest write for a copy identical to HEAD; edit-block targets (84); a folder with no `request.md` goes stale after an hour; a tell never answers a question; queue receipts give the start and why (`docs/requests.md`, `questions.md`, `receipts.md`).
- **Parked edits and retros** (59): `parked\<repo>.md` lines go to the next serial item (`[rules]` only to `rules: true`) and skipped ones come back (`docs/parked.md`); reports add `friction:`, and `warnings:` (109: `<kind>: <text>` parts from a repo's CLAUDE.md *Warning checks* or `warning_checks` commands, leading the inbox, `session.md` and `review.md` by item, counted per kind, toasted, never a hold; `docs/warnings.md`); workflow retros (62: `partial`/`red`, past ~1.5× first ETA, or 12 items) queue themselves (107, `docs/retros.md`).
- **Executor discipline** (61–102; `docs/preambles.md`, `executors.md`, `questions.md`, `memory.md`, `templates.md`, `worktrees.md`): scope, `out/` scratch, load budget and `nice`, Write/Edit only, sized `timeout`s, no elevation, *Premises*, a ~60-line report; `wait_for`; `ask` with flagged defaults and `design: true`; ≤10 agents per Workflow; done-when rows checked early; `audit: true`; `append_only` union merges; `load_high` as information; the clock on every tool return; *Latitude* (102); *Maintenance facts* (`doc_budget_exempt`, 86); `start` for a job that must outlive its shell (106, `executors.md`).
- **Machine restarts** (105; `docs/restart.md`, *Machine restarts*): a pending Windows Update restart warns once a boot and day while work waits; a run cut off (no result after a restart) resumes its own session in place once, its deadline moved by the time lost, else red naming the restart; `external_data: true` items commit per step and a red one's inbox line offers `land:` first. A rewritten staged item's amendments stand and are named (105, `items.md`).
- **Around it**: `conductor init` makes missing code repos and names a repo without a remote until it has one (91); `rename-repo` (57; `docs/rename-repo.md`); self-restart on a code change (the app once idle, 83) and "restart all", each daemon at its safe point adopting running items (68, 71; `docs/restart.md`); auto-push (`docs/push.md`); `open:` / `notify:` (`docs/open.md`); project tools (64, 67); the app: the Repos tab follows an item end to end, title, tray and tab show live status (77, 81, 84), a message headed by its real sender (78) (`docs/app.md`, `app-panels.md`).
- **Remote workers** (87, opt-in, removable; `docs/remote-workers.md`): `[remote]` starts a LAN broker; a farming item's (`remote: true`, or by default since 105) `farm-seeds` shards a seeded job over browser tabs (wasm), a local-remote worker and the main box behind per-kind fingerprint gates (89), merged byte-identical. Live on aicomplete (88). Status page, app chip, Workers tab (`remote-status.md`); one URL (`remote-url.md`); `remote-diag.md`; per-browser core counts (`remote-cores.md`) and placement, a lagging shard copied to a free main slot (100, 103; `remote-placement.md`).

The planner docs: `planner.md` (its *Start*, then read whole at step 5 with `session.md`: the only two files a start stages), `planner-vocab.md` (rare ops only), `claude-ai-new-project.md` (only new / park / resume and setup); README is an overview plus an index into `docs/`.

A next prompt should know:
- A behaviour change updates `planner.md` (and `claude-ai-new-project.md` where it covers the topic) and the matching `docs/` file, not README's body.
- Every doc and code file has a size budget (`CLEANUP.md`, *Size budgets*; `uv run python tools/sizes.py`, 1 KB = 1,000 bytes). `planner.md` and `CLAUDE.md` hold theirs: an addition to `planner.md` pays for itself by folding a rule `docs/` already holds into a pointer. `Daemon` is `daemon.py` plus one `daemon_*` mixin per concern; the app's tabs are one `app/*_panel.py` each.
- Tests wait up to `WAIT_SECONDS` (180s, `tests/conftest.py`) for the daemon, so the full lane passes with a project's executor holding the CPU (`docs/development.md`).

## Open

Accepted gaps nobody plans to fix unless they bite live in `docs/known-limits.md`.

Chores:

- Corelight's `.gitignore` lacks `picks/`, `session.md` and `review.md` (a startup `warning`), and its control folder has no remote (`origin` is all auto-push needs): its planner's and Matt's.
- **Matt's saved start preference** (86) should match `planner.md` *Start*: step 2's one access request names the control folder, `claude-conductor` and `C:\Code\<project>`; `Repos:` is the fallback.
- Staged plans' questions don't always reach the start prompt: 147's 4 were dropped silently (a 10-minute ask, the r62 scope unsettled), and 192 re-decided one. Fix: the start (resume) prompt lists each stage `Questions:` with its answer, or "unanswered: your default stands". (aicomplete 153, from #6654; 202, #8974)
- `ask` holds the whole session for its full window (147 idle 10 min). Fix: a non-blocking `ask` that returns at once and delivers the answer as a message (the daemon would then apply the default deadline, which only the server's waiting call does now; was a known limit since 65). (aicomplete 153, due from checkpoint #6654)
- At an item's first outline post, check the outline against its done-when list and flag missing rows then, not near the end (147: two watch tells re-sent them). (aicomplete 153, due from checkpoint #6654)
- **Needs a human**: a toast click doesn't open the app (try a `conductor-app:` URI plus `launch_action`); an app-started daemon's windows may open behind others.
- Staging `Questions:` that decide a rule's shape should block launch until answered, or reach the planner at once: 156's Q1, answered 4 h after the merge, cost a rework item. (aicomplete 164's retro, due from #7341, as are the next three)
- Remind an executor when its last outline's ETA is over a third behind its latest `eta` (two of 156's three watch tells).
- Aicomplete's item scripts start headless Edge with `--user-data-dir` under `%TEMP%\aic-cdp-*` (not excluded from Defender): the worktree's `out/` would be (170). Its planner's; conductor launches no browser.
- Size splits deferred until a prompt would push past budget (cleanup 11-12's picks): `remote/board.py` (893/900 lines), worker state apart from jobs and shards; `executor.py` (894/900) and `daemon.py` (1092/1100) are next.
- Defender still takes ~15-18% of the box through a full lane (104): process launches (~7ms of its CPU a git, ~19ms a Python), which no path exclusion covers. Fewer spawns is the only cut on conductor's side; the run is ~4 min.
- `wait_for`'s default `fail` pattern matches "error" in ordinary prose (subagent output under ultracode: aicomplete 173 lost ~10 min). Fix: a default that can't match ordinary text. Due from #7766.
- An item-file edit that only appends answers to the stage's own questions should keep the stage and deliver once (177 restaged, got two copies). (aicomplete 193, due from #8636, as are the next two)
- The preamble's "never `sed -i` / `-c` / heredoc" line was broken six times in 177 without consequence: a hook enforces it, or it goes.
- `farm-seeds` with no worker connected: one native run at the item's threads, not shards one at a time (30 vs ~10 min); build into its own target dir (or copy `aic-sim.exe` per job: 167's straggler held it, from #7341) so a running `aic-sim` can't fail a rebuild; farm specs from a git-ignored folder (19 scratch specs landed on main).
- **Report status is whether the work got done, not whether bars were met** (Matt agreed 11-11, due from #8636): `done` = planned work finished, measurements trustworthy; `partial` = work undone or untrustworthy (scope skipped, cap hit mid-plan, an unfinished read, an unbelieved result, an unexplained bar miss); `red` as now. A `bars:` header line lists unmet bars (`bars: 2 of 6 unmet — d0 88.5 vs ≥90; ...`); auto post-audits and retros key on `partial`/`red`. Touches `docs/reports.md`, the preamble.
- A readonly item's write-up in its worktree's `out/` is unreachable to later items (196 got 194's leads by amendment): copy it beside its report (`reports/<item>.writeup.md`) or name it there. (aicomplete 194/196, #8919)
- `wait_for` on a log should end on `farm-seeds`' "the broker went away" fallback line as on `fail` (192 lost ~13 min); farm-seeds' help names `.conductor\remote\jobs\<id>\shards` (finished shards survive a stopped job). (aicomplete 202, #8974, as is the next)
- A calibration item `partial` with a named next lever gets a hint to queue it as a follow-up (192's d0 pick waited for 196).
- Preamble: a conductor tool returning `Connection closed` (daemon restart) means keep working with shell waits and retry, never end the turn (009 went red at 2:35am); "detached" means conductor's `start` only. Items over a few hours write their report incrementally with real numbers per milestone, not `TBD`. (workbench 013, #265)

- **`external_data` landing mid-run** (105, not built): the item commits per step, but its branch lands only at the end or by `land:`. Plan if it bites: a `land_now` progress field that has conductor fast-forward main to the branch head between steps (the item's worktree then rebases on nothing new), refused while another item holds the repo.

Still not live:

- **105's lines**: a cut-off run's resume (does a real session pick up from `.continueN.md` and redo the cut step?), the update-restart warning (registry keys read live: none pending on 11-11), `external_data`'s red line, the restaged amendments' receipt, aicomplete's `[remote] default = true` (by request after 105's commit).

- **107's auto retros**: unseen on a real project. In the sandbox, the first daemon after 107 queued a retro of an item 13 days old whose transcript was gone, which went red at launch; cleanup 11-12 skips such items. **108's *Re-derived or re-read***: do notes name files, and cleanups add the caching line?
- **109's `warnings:`**: unseen live. aicomplete's design checker (205) is the first user: its `CLAUDE.md` *Warning checks* or `warning_checks` is its planner's to add; do executors run the checks and copy the parts verbatim?
- **The app's remote chip and Workers tab** (91, 97): sandbox only. Amber for a 5-min-old re-run too eager? A new key costs a 27-144 s build; prebuilding untried.
- **82's lines** (fractals): does a load-told executor go smaller rather than relaunch? Sized `timeout`s, no heredoc edits, a cancelled dependency's set-aside item and the Pick… menu are unseen live.
- **83's lines**: unseen live, the app's self-restart once idle and a finished item's held question sorted last; one tray icon after? **81's waiting mark** unseen on a real taskbar.
- **102's *Latitude***: does a tuning item post reachability early and name what gave way?
- **Repos that don't fit worktrees** (78, 91): start `worktrees = false`; does fractals' report name enough to switch? Does a new project's first request edit the stub `state.md`, not replace it?
- **Inbox sections read twice** (aicomplete 11-03): a read spliced across a rewrite? Save the next with its read time. **98**: why did the laptop miss gates?
- **99-103's core counts, placement**: laptop at 2, ceiling 3: does it hold; does a farm beat local (161 didn't)? Does the overdue copy cut a read's tail? 103's `answered:` and long-job line unseen live.
- **Farm load** (aicomplete 168, #7341): no broker → `run_local` runs native at full `--threads` (~95 min): cap at `main_slots`. A `remote: true` item's load warning should name `main_slots` as its share (168 read it as "run native"). A worker offline mid-item raised no flag: the watch or *Now* should.
- `farm-seeds` and the worker page come from the working checkout: uncommitted code reaches live jobs and tabs (98, 99; 100's edits reloaded the laptop 4 times).
- A spec newer than a worker's bundle isn't in it (167's laptop failed every shard of one). 100 drops a worker from a job at its first failed shard; to do: rebuild the bundle when a job names a spec it lacks. Due from #7341.
- **Remote workers** (87–89): the laptop joined over *Public* only via an old Allow rule for uv's `python.exe`; *Keep screen awake* is hidden on the LAN URL.
- **A remote with `push = false`** (workbench, 92, since fixed by hand): init should set `push = true` for a reachable remote; the digest should flag one.
