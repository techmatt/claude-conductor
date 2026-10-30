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

Last cleanup: 2026-10-30, in prompt 60

Phases 1–4 are done; phase 5 runs live with Corelight (`C:\Code\corelight-control`), Word Warriors (`C:\Code\wordwarriors-control`) and aicomplete (`C:\Code\aicomplete-control`). Each prompt's report in `reports/` says what it changed; `README.md` and `docs/` hold how it all works now.

What is live, by area (each prompt's report says when it came; the doc named holds how it works now):

- **Queue and scheduling**: per-repo queues, worktrees, items landed as one squashed commit with serial merges, `alongside` and readonly items beside others, exclusive templates alone, staging, rewrites and amendments, `depends_on` warnings, `land:` for a kept red branch; a fast-forward that stops part-way is put back and retried, `checkout_dirty` (red) when what is left is not the branch's (`docs/items.md`, `scheduling.md`, `worktrees.md`).
- **Executors**: a light preamble for a small item, the full one otherwise (`docs/executors.md`, *Two preambles*); questions, progress lines, outlines, `tell:`, `audit:`, `pause:` / `resume:`, `resume:` items, run targets, conductor-owned templates and audit notes, memory sampling with job objects, a launch guard and a leftover reaper (`docs/executors.md`, `progress.md`, `questions.md`, `templates.md`, `memory.md`).
- **Planner loop**: `session: start` makes a checkpoint and `session.md` (the project's `[links]` first since 60, the inbox, `state.md`'s due lines, *Planner habits*) and moves the read mark; conductor's own reports past the marker list in *Unreviewed reports*; a receipt per planner write; `reviewed:` / `review: all`; `ack: invalid` (`docs/inbox.md`, `receipts.md`, `signals.md`).
- **Pickers** (`docs/picks.md`): `kind: many` rows (58); since 60 conductor retires a picker (manifest and untracked images) once a later `<name>_r<N>` round is saved, or on `retire: <name>`; executors never delete one.
- **Night shift**: a stall check, `W<n>_watch` items every 30 minutes (and for a question blocked 15), backlog draws within a nightly budget, autopilot off / night / always gating draws only; in a digest routine watches are one count line, the watch's tells and answers listed (`docs/night-shift.md`).
- **Requests and messages** (58): requests apply in write order, wait for late targets and 10s for a copy identical to HEAD; a folder with no `request.md` moves to `requests/stale/` after an hour (60); a tell never answers a question; queue receipts give the start and why (`docs/control-folder.md`, `questions.md`, `receipts.md`).
- **Parked edits and retros** (59): `parked\<repo>.md` lines go to the next serial item (`[rules]` only to `rules: true`) and skipped ones come back (`docs/parked.md`); reports add `friction:`; the digest marks a workflow retro due after 6 non-template items, on the item with most friction (`docs/inbox.md`).
- **Around it**: `conductor rename-repo` rekeys a repo everywhere, history included, and can move its checkout (57; `docs/control-folder.md`); self-restart on a code change and "restart all" (`docs/restart.md`); auto-push of repos and a control folder with a remote, this repo after each commit (`docs/push.md`); `open:` / `notify:` (`docs/open.md`); the app, whose Repos tab follows an item end to end and whose title and tray icon show live status (`docs/app.md`).

The planner docs: `planner.md` (its *Start*, then read whole at step 5 with `session.md`: the only two files a start stages), `planner-vocab.md` (rare ops only), `claude-ai-new-project.md` (only new / park / resume and setup); README is an overview plus an index into `docs/`.

A next prompt should know:
- A behaviour change updates `planner.md` (and `claude-ai-new-project.md` where it covers the topic) and the matching `docs/` file, not README's body.
- Every doc and code file has a size budget (`CLEANUP.md`, *Size budgets*; `uv run python tools/sizes.py`, 1 KB = 1,000 bytes). `planner.md` and `CLAUDE.md` hold theirs: an addition to `planner.md` pays for itself by folding a rule `docs/` already holds into a pointer. `Daemon` is `daemon.py` plus one `daemon_*` mixin per concern; the app's tabs are one `app/*_panel.py` each.
- Tests wait up to `WAIT_SECONDS` (180s, `tests/conftest.py`) for the daemon, so the full lane passes with a project's executor holding the CPU (`docs/development.md`).

## Open

- **58–59, first live use**: `identical to HEAD` after a real edit: raise `SAME_WAIT_SECONDS` (10s). Did a cut-short `ask` get its `resume`? Do queue receipts' start estimates hold? `kind: many` rows in the browser. Were parked edits answered (`parked P<n>: …`) and skipped ones returned?
- **Audit the audit** (59): after the first 3 workflow retros, the planner reviews them with Matt. Which findings became fixes, and which recurred? Did `friction:` lines foresee them? Is the trigger (6 items) too frequent or too rare? Adjust the questions or the trigger, then close this line.
- **60, first live use**: a picker round's retirement (`pick_retired`: did it delete only what Matt was done with?); aicomplete's stray `requests/20261030-0814-p027/` should move to `requests/stale/` an hour after its daemon runs 60's code.
- **Part-way fast-forwards** (aicomplete 035, fixed after 60): watch the first live `warning` "main checkout restored" and any `checkout_dirty`. Did the retry (4 tries, 0.5s doubling) outlast the Windows lock, or does the backoff need to be longer?
- **Executors can't see outside load on the box** (from aicomplete, event #1298): 030 (a speed benchmark) lost its "quiet" timings to a claude-conductor `pytest -m ""` run from another session, which took about half the machine with nothing in the inbox. A machine-load line on the inbox's *Now* (CPU busy, and from what), or a `quiet` request an item can make that holds other runs, would let timing work wait or say so.
- **Machine-load discipline for executors** (Matt, 2026-10-30; event #1412): 034 (a simulation gate with ultracode) beside two art items made the box lag badly for Matt. The preamble should say: heavy compute (simulation sweeps, builds, test runs, subagent fan-out) runs at below-normal process priority and leaves at least 4 hardware threads free; at most 2 heavy subagents at once; when another item is running alongside, halve thread counts. The machine-load line above would let the planner and watch see it.
- **Executors edit global Claude config without saying so up front** (event #1298): 030 made two shell edits to `~/.claude/CLAUDE.md`. Matt (2026-10-30): editing global Claude config is fine when it's a good idea, since conductor and its projects are the main thing run there. The preamble should say so, and ask that any such edit be named in the report with its reason (030's only surfaced as an aside).
- **A self-written question default may override an explicit prompt line silently** (event #1324): 009's unanswered 1am ask reversed the prompt's "skip it". The preamble should say a default may not contradict the prompt unless flagged in the question's header, or the receipt marks `default overrode prompt`.
- **Preamble: name a scratch location** (event #1324): "outside the working directory" was breached by habit in 009 (%TEMP%) and again in 032; say "use `out/` or another git-ignored dir in the worktree".
- **Poll helper for executors** (event #1324): 109 of 009's 281 turns were 60-second polls, about a third of its cost. A conductor `wait_for(path|pid, every=50s)` returning on any message would save ~100 turns a long run.
- **ETA drift warning** (event #1324): 009's `eta` stayed 6:20am until 3:11am while it finished 3:38am; `progress` could warn when outline times and `eta` disagree.
- **Watch `quiet` flags during healthy background runs** (event #1324): fired twice on 009 while its experiment log advanced; suppress when a known process is advancing its log.
- **Templates: a leftover-budget line** (event #1324): "over 1h left: run the next most useful item and say what"; 009 stopped with 3h22 unused.
- **A skip written in a prompt binds** (Matt, 2026-10-30; event #1332): goal-as-authority covers only routes the prompt did not rule out, and a skip Matt sends by tell overrides it too. An executor that thinks a skipped route is best says so in its report or asks, but its own default never un-skips it. `planner.md` *The goal is the authority* and the preamble should say so; planners write "lower priority", not "skip", unless Matt meant a skip.
- The light preamble (55): did small items still ask with a default, background long calls and answer messages by number? One that turned out big and ran blind (no outline) means lower `LIGHT_WORDS` or add a signal word.
- Planner habits (55): Corelight's still sit under `state.md` *Pending fixes* (Word Warriors' moved); `planner.md` tells its planner to move them. Check Corelight's next session start no longer lists them to Matt.
- A Claude Docs tab exported into a prompt costs double tokens (base64 inline, re-emitted into a shell command); an export saved straight to a file the bridge commits would be cheaper.
- Tamper is attributed (30) only to an item whose hook saw it name a changed control path; a change made by a script that never names the path, or by a stage or merge executor (no hook), still ends every active item red.
- Messages reach an executor only after one of the lead's own tool calls ends (an `ask` hands back for one, 58); during a subagent or Workflow call, an idle wait or at the run's end they are logged undelivered. Fallback: stream them into `-p --input-format stream-json` stdin (`docs/executors.md`).
- `alongside` is proven in the sandbox with real executors (one pair, one tell during a helper); its first real use is worth a look: did both reports say whether they touched the other's area?
- Each code repo's `CLAUDE.md` must say "commit to the branch you are on", not "commit to `main`". The sandbox's was changed; a real repo's first prompt carries it.
- Clicking a toast does not open the app. Toasts use Command Prompt's app id; a registered `conductor-app:` URI plus `launch_action` would likely work but needs a human click to prove.
- Reports finished before `reviewed:` existed have no `reviewed` event: on Corelight they list as unreviewed until marked (**process all**).
- A command the cap moves to the background is stopped by Claude Code after 10 minutes. The preamble's *Servers* paragraph (54) says to start long-lived servers detached; did the next item with a preview server keep it past 10 minutes?
- Receipts: a heavy tick delays one 2.5–3.2s (sandbox 0.6–0.8s since 50). Measure live, bridge write to receipt; if ~1s can't be met, `planner.md` makes wait-or-re-stage the normal path.
- `planner.md` *Start* step 3 assumes a workspace shell and `TZ=America/Los_Angeles`; Matt's saved preference must carry steps 1–5 (the file is first read at step 5).
- Job objects (53), not yet live: at the next bug hunt, does the memory line's biggest item match Task Manager, little `chrome.exe`/`node.exe` "outside"? A child refused under a job shows as a failed launch or dead browser.
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
- Night shift (36) and autopilot (40): fake executor only. First real nights: are a watch's tells useful, does fitting a whole `max_hours` in the budget leave enough drawn?
- Repos tab (33), 1250px window: Note ~140px; three buttons push Terminal off the edge.
- Window focus needs a human look: an `open:` window, a forwarded app launch or a picker from an app-started daemon may come up behind others.
- Corelight's `.gitignore` still lacks `picks/`, `session.md` and `review.md`, so it logs a startup `warning` until its planner's request adds them.
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
- Run logs are compacted, then gzipped (54); nothing is dropped. If the gzipped total grows too fast, drop logs of items reviewed long ago.
- The preamble (54) says to leave a step whose question default can't be undone until last in the item (wordwarriors 152 lost shots a tell asked to keep). Worth a look at the next delete-with-default.
- Daemon restarts and stage chatter in a digest's *Other* still pile up after the last `state.md` request.
- 31's planner action rules (signals only from Matt's words, `reviewed:` only inside processing) are rules, not checks: worth a look in Word Warriors' next sessions.
