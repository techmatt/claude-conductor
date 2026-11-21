# HANDOFF — claude-conductor

The one file that starts a claude.ai session for conductor work. Read it, then `planner.md` (how a planner session uses conductor, from its *Start* to the vocab) and `README.md` (how conductor works, with an index into `docs/`); `claude-ai-new-project.md` covers starting, parking and resuming a project. Every conductor prompt updates *Status* and *Open* before committing; nothing else in this file changes without a reason.

## How work on conductor flows

- Matt discusses with a claude.ai session linked to the box. The session reads and writes `C:\Code\claude-conductor` over the bridge. It writes each prompt to `prompts/inbox/<YYYYMMDD-HHMMSS>-<project>-<slug>.md` (git-ignored), unnumbered: running it numbers it (`tools/prompts.py`), so planners writing at once never collide.
- Matt says "run the next prompt" (the oldest waiting) or names one in a Claude Code session in this repo. Conductor never runs its own queue: its prompts always go this way, by hand.
- CC writes `reports/NN_name_report.md` (git-ignored) and ends with `Report written to <path>`. Matt says "report's in", and the session reads it. These reports have no `reviewed` event: the planner keeps `Last report reviewed by the planner: NN_name` in `planner-notes.md` (git-ignored, its own), with lines for *Open* the next prompt or cleanup merges here; it never edits this file. Ideas not yet planned: `planner-ideas.md` (likewise).
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

Last cleanup: 2026-11-17, after prompt 132

Phases 1–4 are done; phase 5 runs live with Corelight (`C:\Code\corelight-control`), Word Warriors (`C:\Code\wordwarriors-control`), aicomplete (`C:\Code\aicomplete-control`), fractals (`C:\Code\fractals-control`), workbench (`C:\Code\workbench-control`: small unrelated scripts, a subfolder each; 92) and Helium Hustle (`C:\Code\helium-hustle-control`). Each prompt's report in `reports/` says what it changed; `README.md` and `docs/` hold how it works.

Live, by area (the doc named holds the detail):

- **Queue and scheduling**: per-repo queues, worktrees, items landed as one squashed commit with serial merges, `alongside` and readonly items beside others, exclusive templates alone, staging, rewrites and amendments, `depends_on` warnings (a cancelled dependency holds only its dependent, 82), `land:` for a kept red branch; a fast-forward stopping part-way is put back and retried (`checkout_dirty`, red, when what is left is not the branch's); a rewrite's receipt names what changed, or that identical bytes changed nothing (128) (`docs/items.md`, `scheduling.md`, `worktrees.md`).
- **Executors**: a light preamble for a small item, a lean one for a watch, the full one otherwise (`docs/preambles.md`); questions, progress lines, outlines, `tell:`, `audit:`, `pause:` / `resume:`, `resume:` items, run targets, conductor-owned templates and audit notes (with *Re-derived or re-read*, 108; since 113 every writable item, post-audits included, fixes the obvious in place and notes only Matt's calls or large work, so a post-audit is serial and lands), memory sampling with job objects, a launch guard and a leftover reaper (`docs/executors.md`, `progress.md`, `questions.md`, `templates.md`, `memory.md`). 135: one machine-wide model (`opus` / `fable`: app, `model:`) for new sessions; stages, resumes and forks keep theirs (`executors.md`, *Model*).
- **Planner loop**: `session: start` makes a checkpoint and `session.md` (the project's `[links]` first since 60, the inbox, `state.md`'s due lines, *Planner habits*) and moves the read mark; since 112 each `session:` is numbered per project (from history) and `session.md`'s title is `<display name> session N` (`display_name` in `conductor.toml`), the planner's first line, and a checkpoint comes only from a signal or the CLI; conductor's own reports past the marker list in *Unreviewed reports*; a receipt per planner write (one leaving `state.md` past `state_budget_kb`, 15 KB since 137, names lines to fold, or **split state** while unsplit; 106, 118); `reviewed:` / `review: all`; `ack: invalid`; 129: receipt-only no-ops for tidy-ups already true, refusals ending in the accepted form, `open: file <path>?<query>`, *Simple actions* (`docs/inbox.md`, `receipts.md`, `signals.md`, `open.md`).
- **Pickers** (`docs/picks.md`): one per `pick:` header line, a Pick… menu when several (82); `kind: many` rows (58); a picker retires on a later `_r<N>` round or `retire:` (60, 65); `needs_pick:` holds an item until its picker is saved (66); images at a judgeable scale (121).
- **Night shift**: a stall check, `W<n>_watch` items every 30 minutes (and for a question blocked 15), backlog draws within a nightly budget, autopilot gating draws only; routine watches one digest line (`docs/night-shift.md`).
- **Requests and messages** (58): requests apply in write order, wait for late targets and 30s from the folder's newest write for a copy identical to HEAD; edit-block targets (84); a folder with no `request.md` goes stale after an hour; a tell never answers a question; queue receipts give the start and why (`docs/requests.md`, `questions.md`, `receipts.md`).
- **Parked edits and retros** (59): `parked\<repo>.md` lines go to the next serial item (`[rules]` too since 143) and skipped ones come back (`docs/parked.md`); reports add `friction:`, and `warnings:` (109: `<kind>: <text>` parts from a repo's CLAUDE.md *Warning checks* or `warning_checks` commands, leading the inbox, `session.md` and `review.md` by item, counted per kind, never a hold, never toasted (115), one unchanged since the last reviewed report folded to a *standing* line, from main to the repeat line (121); `docs/warnings.md`); workflow retros (62: `partial`/`red`, past ~1.5× first ETA, or 12 items) queue themselves (107, `docs/retros.md`); a cancelled one stays cancelled (115), a `partial` Matt told to stop queues none (117), nor one whose bars only wait on Matt, each naming a question or listed choice (131, 132), and a new queue file whose number is taken in its repo is renumbered, its receipt saying so (115, `items.md`).
- **Toasts** (115, `docs/inbox.md` *Toasts*): only what Matt acts on: one per finished item with its queue state merged in (partial/red in the title), questions with no default, a picker opened for him, and errors (red, refused requests and signals, push failures, daemon or run target down, the watch's stuck flag). The rest is panel only; toasts within a minute merge.
- **Executor discipline** (61–123): every executor rule, its origin and where it lives (preamble text, the PreToolUse hook, or dropped) is one row of the rule ledger, `docs/hooks.md`; `docs/preambles.md` is the preambles' structure. 122: the hook denies what it can check (shell edits, `%TEMP%` …), a watch's third tell to an item becomes a notify, cleanup notes are deduped. 123: short preambles, a fix-loop cap, a diff review before every writable full item's report. 128: a subagent finishing ends a `wait_for`; preamble lines on subagent waits, by-rate ETAs, Write/Edit only; `reviewed:` takes a list. 133: refused shell edits named plainly (write a script); a taken design default gets one revisit nudge at a report written with over an hour left (`questions.md`). Also: `append_only` union merges, `out/keep/` (`worktrees.md`), `write_roots`, staging defaults (`questions.md`), `MAX_AGENTS` (`ultracode.md`).
- **API outages** (111; `docs/api-retry.md`): an executor exiting on an API failure (403 / Cloudflare, auth, 5xx, 429, overloaded) waits in `running/`, clock paused, resuming its session in its worktree after 2 / 10 / 30 min (`api_retry_minutes`), red naming the API only when they're spent; `retry: <item>` resumes a kept red item on demand and lifts its hold when it lands; an API-only red queues no retro; a resume item's red never holds; a fork's cost is its own. Same prompt: `farm-seeds` failed at once (exit 3) when the broker stayed gone (126: it now finishes on the main box), its paragraph says commit before farming, kept reads name their spent seeds, and the full preamble binds CLAUDE.md *Working rules* and sends a departure from Matt's staging answer to `design: true`.
- **Machine restarts** (105; `docs/restart.md`, *Machine restarts*): a pending Windows Update restart warns once a boot and day while work waits; a run cut off resumes its session in place once, its deadline moved, else red naming the restart; `external_data: true` items commit per step and a red one's inbox line offers `land:` first. A rewritten staged item's amendments stand and are named (105, `items.md`).
- **Around it**: `conductor init` makes missing code repos and names a repo (and since 117 the control folder) without a remote until it has one (91); `rename-repo` (57; `docs/rename-repo.md`); self-restart on a code change (the app once idle, 83) and "restart all", each daemon at its safe point adopting running items (68, 71; `docs/restart.md`); auto-push (`docs/push.md`); `open:` / `notify:` (`docs/open.md`); project tools (64, 67); port blocks and port ownership (119; `control-folder.md`, `run-targets.md`); the app: the Repos tab follows an item end to end, title, tray and tab show live status (77–127), a message headed by its real sender (78) (`docs/app.md`, `app-panels.md`).
- **Remote workers** (87, opt-in, removable; `docs/remote-workers.md`): `[remote]` starts a LAN broker; a farming item's (`remote: true`, or by default since 105) `farm-seeds` shards a seeded job over browser tabs (wasm), a local-remote worker and the main box behind per-kind fingerprint gates (89), merged byte-identical. Live on aicomplete (88). Status page, app chip, Workers tab, URL, diag, cores, placement: `remote-*.md`. 114: its commit named, a dirty tree warned, `--cancel` / stop / kill leave no shard (`farm-seeds.md`). 118: it makes its out folder. 116: an out-of-memory retries in a fresh core, then reloads (`remote-status.md`). 124: `conductor remote test` (`remote-status.md`, *Worker test*); a direct native run is reminded once to farm. 126: a lost broker means a reconnect, else the main box finishes (`remote-placement.md`). 130: a tree pinned at start (133: `--at <commit>`); load tells once a stretch (`memory.md`). 134: a failed run shows a shard's output tail once per cause.

- **Split state, hunts on yield** (137): `state.md` is the wavefront; `habits.md` and `paged.md` are control files (the digest reads `habits.md`, else the old section); standing rulings live in each repo's `docs/rulings.md`, named in the preamble and diff review when present. Bug hunts default to recent changes and stop after two thin rounds (`templates.md`).
- **Planner MCP tools** (136, 138, 140; `docs/mcp.md`): `conductor mcp`, in the Claude desktop app, is the planner's surface: `start_session`, `status`, `read` (repo files, conductor's reports: 140), and writes that write the bridge's files `by: planner`, marked `via: mcp` (140), returning the receipt; `edit_state` moves sections (140). A launcher runs the server fresh per code change; `--selftest`. 141: calls sent together each answer, a project's writes in order; traffic log; markdown `repo` reads header-first. 142: calls answer in 65 s; staging questions close on a later amendment. The bridge is the exception (images).
- **Scoreboard, dead daemons** (139): `conductor stats` / `read(what="stats")` (`docs/stats.md`); the app toasts a dead or hung daemon once, offering a restart (`memory.md`).
- **143**: `memory_note` (`memory-notes.md`); `edit_state` takes a unique piece; an API red leaves a stub report, no retro, a 90-min last retry at night (`api-retry.md`); `locks: [quiet]` (`scheduling.md`); one thread budget; small pytest calls foreground; `~/.claude` and unfarmed-readonly warnings.
- **144**: `park` remove / replace by a piece (`parked.md`); `retry: X model: opus`, a resume item's `model:`, usage-limit waits (`api-retry.md`); `edit_state` pieces (`mcp.md`).
- **145**: a finished item's launch defaults sit in its `review.md` section.

Planner docs: `planner.md` (`start_session` returns it whole), `planner-bridge.md`, `planner-vocab.md` (rare ops, 125), `claude-ai-new-project.md` (new / park / resume, setup); README is an overview and an index into `docs/`.

A next prompt should know:
- A behaviour change updates `planner.md` (and `claude-ai-new-project.md` where it covers the topic) and the matching `docs/` file, not README's body.
- Every doc and code file has a size budget (`CLEANUP.md`, *Size budgets*; `uv run python tools/sizes.py`, 1 KB = 1,000 bytes). `planner.md` and `CLAUDE.md` hold theirs: an addition to `planner.md` pays for itself by folding a rule `docs/` already holds into a pointer. `Daemon` is `daemon.py` plus one `daemon_*` mixin per concern; the app's tabs are one `app/*_panel.py` each.
- Tests wait up to `WAIT_SECONDS` (180s, `tests/conftest.py`) for the daemon, so the full lane passes while a project's executor holds the CPU (`docs/development.md`).

## Open

Accepted gaps nobody plans to fix unless they bite live in `docs/known-limits.md`.

Chores:

- Corelight's `.gitignore` lacks `picks/`, `session.md` and `review.md` (a startup `warning`), and its control folder has no remote (auto-push needs only `origin`): its planner's and Matt's.
- `ask` holds the whole session for its window (147 idle 10 min); fix: a non-blocking `ask` answered by message. (aicomplete 153, #6654)
- Check a first outline against its done-when list (147: two watch tells re-sent rows). (aicomplete 153)
- **Needs a human**: a toast click doesn't open the app (try a `conductor-app:` URI plus `launch_action`); an app-started daemon's windows may open behind.
- Staging `Questions:` deciding a rule's shape could block launch (156's Q1 cost a rework); since 120, open rulings. (aicomplete 164, #7341, as are the next three)
- Remind an executor whose outline ETA trails its `eta` by a third (156: two watch tells).
- Aicomplete's scripts put headless Edge's `--user-data-dir` in `%TEMP%` (Defender scans it, not `out/`; 170): its planner's.
- Size splits wait until a prompt would pass budget (`tools/sizes.py`); one adding to `planner.md` or this file trims first, in the same item (both at budget again after 135).
- 116's OOM fix (`index.html`'s recycle, reload) is unrun in a browser: check aicomplete's laptop tab (`coresReplaced`, no `load failed` gate).
- `wait_for`'s default `fail` pattern matches "error" in ordinary prose (subagent output under ultracode: aicomplete 173 lost ~10 min). Fix: a default that can't match ordinary text. Due from #7766.
- An item-file edit that only appends answers to the stage's own questions should keep the stage and deliver once (177 restaged, got two copies). (aicomplete 193, due from #8636, as are the next two)
- A browser result refused (HTTP 4xx) is dropped by the page; its shard waits out its lease (10 min at aicomplete). 126's `MAX_BODY` (256 MB) makes it rare. If it bites: post `ok: false` after a 4xx.
- `farm-seeds`: no worker → one native run at the item's threads (30 vs ~10 min). Shards below a seed (a shard is `shard_seeds` seeds × every cell on one thread; 260: 2 h → ~20 min) need `native`/`run` to take `--part p/P` (contiguous cells): agree it with aicomplete's planner. Due from #32.
- **A `bars:` header line** (Matt agreed 11-11, #8636; 114 built the status half, 131 `partial:` for a partial's misses): unmet soft bars of a `done` (`bars: 2 of 6 unmet — d0 88.5 vs ≥90`) for the digests. Not built.
- farm-seeds' help: name `.conductor\remote\jobs\<id>\shards` (they survive a stopped job). (aicomplete 202, #8974, as is the next)
- A calibration item `partial` with a named next lever gets a hint to queue it as a follow-up (192's d0 pick waited for 196).
- Check a queued prompt's absolute write paths against `write_roots` (receipt warning) and show them in staging: 186's path outside surfaced only at run time (a 10-min `ask`). (wordwarriors 193, #5587, as is the next)
- Warn when a test lane starts with `CONDUCTOR_PORT` taken (an item's own dev server made `@smoke` fail to bind).
- Preamble: a conductor tool's `Connection closed` (daemon restart) means keep working and retry, never end the turn (009 red at 2:35am); "detached" is conductor's `start` only; a long item writes its report per milestone with real numbers. (workbench 013, #265)
- aicomplete 290 (post-audit of 278, #13203): a `recover` event lists the cut session's background commands, so the resume knows what to redo (278 lost ~3 turns); the `remote` hook advises farming only on `aic-sim experiment`/`farm` with seeds, not `run --games 1 --trace`; a self-audit preamble names the audited merge commit.

- Promote **process all** / **review all** from planner-vocab.md into planner.md when a cleanup frees room.

- **`external_data` landing mid-run** (105, not built): its branch lands only at the end or by `land:`. If it bites: a `land_now` progress field fast-forwarding main to the branch head between steps, refused while another item holds the repo.

Still not live:

- **105**: a cut-off run's resume (redoing the cut step?), the update-restart warning, `external_data`'s red line, restaged amendments' receipt.

- **127–132**, unseen live: a cut taskbar button reading `◐ 1 running` (127); `matt:` only on real hand-offs naming a question or choice (131–132).
- **134**: unseen live: is aicomplete's cause in its sim's last 20 lines? A browser's exception enough?
- **133**: unseen live: the design-default revisit nudge, and a paired read farming its old side with `--at`.
- **124**: the worker test is proven in the sandbox (headless Chrome) only: run it on aicomplete with the laptop joined. Does the farm reminder catch aicomplete's real direct runs, and stay quiet otherwise?
- **123**: short preambles and the diff review, sandbox only: anything lost from *Dropped in 123*? real bugs found, at what cost?
- **119–122**, unseen live: Helium's **Run game** by the card viewer, old servers replaced (119); a Workflow call at the hook, stages naming defaults (120); `unchanged from main:`, `UNCHANGED_RE` over-catching (121); hook denies in items, a helper's `agent_id`, legit calls denied (122).
- **139**: the dead-daemon toast and Restart button are sandbox-proven only; `stats`' corrections rule right?
- **143**, unseen live: a `[rules]` edit carried; a `memory_note`; a stub report; `quiet` holding another project.
- **144**, fake-tested only: check the usage-limit wait (box's zone) and `retry: … model:` on the first live limit red; build nothing till then.
- TO VERIFY (11-19 review): over 7.5 reports processed before the 200k mark; habits.md pruned against `docs/tuning-items.md`.
- `farm-seeds` keeps no per-worker offered/overtaken tally in `job.json`. (aicomplete 37)
- **142**: the launcher's 75 s limit takes at the next app restart; two chats on one project are only counted (if it bites: a session token).
- **MCP step 2 of 3** (11-19): sessions MCP-first, a non-image bridge write noted. Step 3 (retire the bridge to an emergency doc; the daemon flags planner writes without `via: mcp`) once `stats` shows five sessions in a row, two projects, `bridge` 0, no lost write. Later: a Fable preamble trim trial, reports writing `friction: none`.
- **137**: fractals is split (19.6 KB, still over 15); wordwarriors, workbench, helium next: is 15 KB right? Does a diff review cite `docs/rulings.md`; do hunts stop on yield (two thin rounds) without dropping real bugs?
- **117**: `told_to_stop` matching "don't stop"? **108**: do notes name files?
- **114–115**, unseen live: `--cancel` freeing `aic-sim.exe`, a killed `native_build` finishing; toast settle times, a renumbered item's `depends_on`.
- **135**: unseen live: does a `--resume` with `--model` keep the stage's cache; do Fable items obey preamble and hooks?
- **113's fix-in-place**: unseen live: items fixing instead of noting; post-audits landing small fixes.
- **112**: aicomplete needs `display_name = "AIcomplete"` (its planner's request); does claude.ai title a chat from the planner's bold first line?
- **111**: unseen live: a `--resume` after a 403 carrying on mid-step, its cost. **110**: kept reads' 3 items / 2 GB right?
- **109's `warnings:`**: unseen live. aicomplete's design checker (205) is the first user: its `CLAUDE.md` *Warning checks* or `warning_checks` is its planner's to add; do executors run the checks and copy the parts verbatim?
- **Farm load** (aicomplete 168, #7341): no broker → `run_local` runs native at full `--threads` (~95 min): cap at `main_slots`. A `remote: true` item's load warning should name `main_slots` as its share. A worker offline mid-item raised no flag: the watch or *Now* should.
- The worker page comes from conductor's checkout: uncommitted page code reaches live tabs (100 reloaded the laptop 4 times; `farm-seeds` pinned since 130).
- A spec newer than a worker's bundle isn't in it (167's laptop failed every shard of one). 100 drops a worker from a job at its first failed shard; to do: rebuild the bundle when a job names a spec it lacks. Due from #7341.
- **Remote workers** (87–89): the laptop joined over *Public* only via an old Allow rule for uv's `python.exe`; *Keep screen awake* hides on the LAN URL.
- **A remote with `push = false`** (workbench, 92, since fixed by hand): init should set `push = true` for a reachable remote; the digest should flag one.
