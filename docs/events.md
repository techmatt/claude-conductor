# Event log

`events.db`, table `events(id, ts, kind, item, repo, paths, detail)`. `ts` is local
ISO time with offset; `paths` is a JSON list (control-folder-relative where inside
it); `detail` is a JSON object. Triggers reject UPDATE and DELETE.

| kind | when | notable detail |
|---|---|---|
| `daemon_start` / `daemon_stop` | daemon lifecycle | pid, control, code (start: its code version, `restart.md`); running and staging items at stop, note; stop `reason` (`signal`: a `stop: daemon` signal, with its paths and by; `interrupt`: Ctrl+C; `error`: three ticks in a row failed on an `OSError`, with error) |
| `daemon_restart` | the old daemon restarts itself on a code change or a `restart: daemon` (`restart.md`); the new one logs `daemon_start` | reason (`code changed: <old sha> → <new sha> <subject>`, or `asked by <by> (restart: daemon)` with paths), from, to (`<src tree id> <pyproject blob id>`), pid, new_pid, running, staging, note |
| `daemon_died` | at start: the previous daemon never stopped cleanly (red) | pid, started, last_beat, last_beat_clock, gap, note, memory (its last sample: at, level, commit, limit, free), running |
| `load_high` | under `load_warn_free_threads` (2) free for 3 samples and `load_warn_seconds` (180), again every `load_tell_minutes` (15) while it lasts (`memory.md`); inbox only, no toast (70); messages only an item that is a real share of the load (80) | free, keep, items (told now), causes (items that are a real share), load, caution |
| `memory` / `memory_critical` | the memory level changed (`memory_critical`, red: to critical) | level, was, commit, limit (GiB), percent, free (GiB), top (item, kind, count, private_mb), others (name, count, private_mb), load (cpu, top: who, cpu, item, of; `memory.md`), action |
| `reaped` | processes an item left in its worktree were killed | context, processes (pid, name, kind, private_mb), private_mb |
| `enqueue` | an item is first seen in `queue/` | its front-matter fields (`paths` as `paths_declared`, `resume` as `resume_of`); `shares_number` (the other items with its number), if any; starts (when it will start and why: `receipts.md`); parked (the parked edits it will carry as the queue stands); warning (a `depends_on` that can't be met) |
| `edited` | a queued item's file changed before it launched (it runs the new text) | staged (its stage is discarded and redone); amendments (ids still standing); launching (the write landed mid-launch, which was undone); starts (only when the rewrite moved its start); warning |
| `invalid` | (red) bad item, signal, stray queue folder, bad config, a queue file written for an item that already launched (set aside as `.md.bad`) | error, path; `applied` (lines already applied) for a part-applied signal |
| `hold` | a queue head cannot run, an item is set aside (its dependency cancelled), or a merge waits (reason changed) | reason; merge; alone (only this item holds) |
| `setup` | a setup runner spawned (`executors.md`, *Setup*); paths: the setup log | commands, pid, stage (true for a stage's setup) |
| `start` | executor launched | session_id, pid, deadline, port, worktree, branch; resumed, landed (a staged item); setup_seconds; template, template_version, notes (a template item; a cleanup's notes); alongside (its line), beside (ids executing or in setup in its repo then; `scheduling.md`); resume_of, resume_session (`resume.md`); parked (the parked edits it carries: `parked.md`); error (launch failed, setup included) |
| `stage` | a blocked queue head launched read-only | session_id, pid, worktree, deadline; setup_seconds |
| `staged` | the stage finished cleanly | session_id, worktree, plan (first 500 chars), cost_usd, questions (its plan's `Questions:` or `## Questions`, `questions.md`), questions_parsed (their count) |
| `worktree` | created, moved (a staged worktree to current main), removed (merged), kept (red), discarded; a leftover removed on retry | action, branch, base, note (a shared folder created; `leftover removed on retry`) |
| `merge` | an item's branch landed on main | branch, sha (main after), commits (the item's own: pre-merge main to sha), note; resolved (prose files a merge executor resolved); squashed (commits folded in) |
| `land` | a `land:` began, then landed | state, branch; sha, commits, note, resolved, squashed, hashes |
| `resolve` | the rebase stopped on prose files only; a merge executor resolves them | files, step, commit (the one being replayed), session_id, pid, deadline, note |
| `conflict` | the rebase onto main conflicted, or a merge executor failed (red) | branch, files, error, note; resolver (the merge executor's log); land (a `land:`) |
| `checkout_dirty` | a fast-forward of the main checkout failed part-way and left changes that are not the branch's, so nothing was put back (red; the merge holds: `worktrees.md`) | branch, files (the paths not the branch's), error, note |
| `paths_violation` | the branch changed files outside `paths` (red) | files, paths_declared |
| `adopt` | restart found the executor (or stage: `stage: true`) alive | pid, session_id |
| `recover` | restart found it finished while the daemon was down, a merge to resume, or a cut-off run resumed (`restart.md`) | note; continued, pid, cut, why, deadline |
| `requeue` | restart found it never launched, or mid-setup | reason |
| `kill` | max_hours reached (red) | pid, reason |
| `finish` | item done (after its merge, when it had one) | status, report_status, reasons, blocks, carry, slow and friction (as written, or null), pick (`<repo>/<path>`, or null; picks: all of them, when several), parked_done, parked_back (its parked edits), session_id, exit_code, cost_usd, turns, branch, merged, needs_planner, after_red; conductor (its own red, landed anyway); audit (it kept notes), template, resume_of (a resume item) |
| `audit_used` | a cleanup (or general cleanup) concluded, not red: the audit notes it acted on; or an `audit_used:` signal (no item, `by`) | notes (item ids); paths (their files) |
| `red` | the item's status is red | reasons, after_red; external_data (its kept branch) |
| `tamper` | control folder changed during an executor's window (red) | changes, concurrent, found (the check that saw it); attributed (with several running: the ones whose own writing tool calls named a changed path, the only ones that end red; empty: all do; `control-folder.md`) |
| `drift` | control folder changed with no executor running, or by conductor's own git | changes, note; own |
| `repo_renamed` | `conductor rename-repo` gave a repo a new key; every earlier row now names the new one (`rename-repo.md`) | old, new, path, moved, sha, by |
| `request` / `request_failed` | a request applied or rejected (none while it waits for targets) | request, message, targets, no_change (it committed nothing), identical (every target matched HEAD after the wait), state_kb + fold (`state.md` left past budget) / error |
| `request_stale` | a request folder with no `request.md`, untouched an hour, moved to `requests/stale/`; paths: from, to | request, files |
| `commit` | conductor committed the control folder, or an item committed in its repo; an item's is logged as it ends, dated by the commit's own time, so ids, not times, give log order | scope, sha, subject; happened (the commit's own time), branch (in a worktree) |
| `read` / `clear` | signals, or a `closeout:` request | through; session (a `session:` signal's checkpoint id); closeout (the request's name) / repo; note (a `land:`) |
| `reviewed` | a `reviewed:` signal marked a finished item's report as acted on | finish (its event id), status, by; hashes (`report`, `audit`: sha256 or null) |
| `report_changed` | a reviewed report or its `.audit.md` changed since its `reviewed` (or last `report_changed`): unreviewed again (`inbox.md`) | files (`report`, `audit`: what differs from the review), reviewed (its id), hashes; opened, opened_by (the latest `session_opened` since it finished) |
| `session_opened` | a `session_opened:` signal: a finished item's session opened by the app's Terminal or `conductor open` | session_id, by |
| `review` | a `review:` signal wrote `review.md`; paths: the signal, `review.md`; item: the ref's (none for `all`) | which (`all` or the item id), items (inlined, oldest first), conductor (conductor reports inlined), by |
| `checkpoint` | a `checkpoint:` (or `session:`) signal started a new session view (its id is its number) | label, by; session (`session.md`, from a `session:` signal) |
| `progress` | the executor called `progress` | seq, at, note (absent on an outline-only line), eta, done, remaining, outline ([{title, eta, done}]); reply_to (the tell or running amendment it answers) |
| `tell` | a `tell:` (or `outline:`) signal queued a message for a running executor (event id = message id); `by: conductor` for a memory, load or started-beside message | text, by; outline_request (an `outline:` signal's fixed request); beside; waiting_on (the open question its `ask` hands back for: `questions.md`; before 58, answers: the question it answered); pause, resume |
| `delivered` | the message or amendment reached the executor | tell or amend, via (`hook`; an amendment also `prompt`/`resume`; `progress` in older logs), text, waited_seconds, after_seq (progress lines by then); kept (an amendment's `.amend.md`) |
| `amended` | an amend file was taken (the event id is the amendment's number) | text, by, state (`queued`/`staging`/`staged`/`running`), delivery |
| `amend_refused` | an amend file for a merging or finished item, or empty or unreadable (red; the file stays) | state, reason, path, hash, by |
| `run_start` / `run_ready` / `run_open` / `run_stop` | a run target or tool (`tool: true`) started (or was already starting: `reused`), answered, had its URL opened, was stopped | target; pid, command, cwd (`reused`: note) / waited_seconds / url, opened / note |
| `run_failed` | a run target or tool (`tool: true`) never came up, or its URL could not be opened (red; holds no queue) | target, error, log_tail |
| `pick_open` | a `pick:` signal (or `conductor pick`) served a manifest and opened its page; item: the latest finish that named it, if any | name, manifest, url, opened (false: the browser reported it did not open); by |
| `pick_saved` | Matt saved a picker; paths: its choices file; no `by` (the page has no identity) | name, manifest, changed, notes, rows |
| `pick_failed` | the browser raised, `conductor pick` refused the manifest, or its server could not start (red; holds no queue) | manifest, name, url, error; by |
| `pick_retired` | a picker's files were deleted: a later round of its series was saved, or a `retire:` signal (`picks.md`, *Retiring*) | name, manifest, deleted, kept (tracked, or another live picker's image); later (the round saved) or by |
| `opened` | an `open:` signal (or `conductor open-path`) launched its action | verb, path or url; by |
| `open_refused` | an `open:` target failed its check (not red; the signal file goes on) | verb, target (as given), reason; by |
| `notify` | a `notify:` signal or a watch's action raised a toast | text, by; item (a watch's) |
| `approve` | a `proposed/` item moved into its queue | item, from |
| `cancel` | a queued or proposed item set aside in `.conductor/cancelled/` | from (`queue`/`proposed`), kept; dependents and warning (queued items that depend on it) |
| `reorder` | items of a queue (repo) or of `proposed` renumbered | before, after (names in queue order); note (finished after a restart) |
| any signal-caused event | a signal file had `by:` | by |
| `ask` | a question file appeared | question (id), text, block, default, deadline, deadline_clock; design (held for Matt, `questions.md`); overrides_prompt (its first line's flag, here and on the question's later events: `questions.md`) |
| `answer` | answered before it resolved | question, text, outcome, reason, waited_seconds, deadline (moved, when blocking); by (the answer file's `by:`) |
| `default` | the default deadline passed unanswered | question, text (the default), outcome, reason, waited_seconds |
| `late` | an answer file after a default or close | question, text, outcome, note; by |
| `question_closed` | resolved with no answer: item finished, the tool call cancelled, or a message answered it (`by_message`) | question, text, outcome, reason, waited_seconds |
| `queue_empty` | an item finished and its repo has nothing queued or running | last |
| `watch_flag` | the night shift's check flagged something (`night-shift.md`) | flag (`quiet`, `eta`, `question`, `blocked`, `backlog`, `memory`, `run_failed`), key, text; watch (`skipped: <why>` for a stall no watch looked at) |
| `watch_answer` | a watch's `answer:` action answered a question | question, text, source, watch |
| `pause` / `resume` | a `pause:` / `resume:` signal (`night-shift.md`, *Pause*) | repo; items, by; paused_seconds (resume) |
| `watch` | conductor queued a watch item | reason, flags (their keys), file, by |
| `backlog_draw` | a backlog item moved into its repo's empty queue; paths: the queue file, the backlog file | from, full_length, night, by |
| `autopilot` | an `autopilot:` signal set the mode (`night-shift.md`); the last one holds | mode (`off`, `night`, `always`), was, by |
| `ack` | `ack: invalid` (`signals.md`) | what, events (`invalid` ids), by |
| `backlog_sleep` | at night, draws stop: the budget is spent, or nothing is eligible (once a night per reason) | key, reason, until, spent_hours, budget_hours |
| `push` | main was pushed to the remote (`push.md`); repo: none for the control folder | target (repo or `.control`), remote, branch, sha, why (`merge`, `start`, `signal`, `closeout`, `commit`), up_to_date, recovered (the first success after a failure) |
| `push_failed` | a push failed, once per failure (an automatic retry of the same one logs nothing) | target, remote, branch, why, error, signature, output (git's last lines), rejected (the remote moved on: never forced) |
| `closeout_push` | every push a `closeout:` request started is done | request, pushed, failed |
| `config_reload`, `warning` | config applied / worth a note, not red; the note says what (a runtime path or shared folder not ignored; a toast, `open:` launch, self-restart, tick, memory sample, log compaction or night shift that failed; a stage discarded; a worktree not removed; a main checkout restored after a part-way fast-forward; a report unreviewed at a checkpoint or reviewed twice; a message never delivered or a `tell:` for no live item; an amend file naming no item; a signal or reorder cut short by a crash; the daemon under a Claude Code session; a watch's action not carried out; a remote broker that did not start or was restarted after dying, or a bad `[remote]`, `remote-workers.md`) | note; checkpoint, finish (unreviewed at a checkpoint); applied (a part-applied signal); amend, tell (never delivered); verb, target (an `open:` launch); action (a watch's); item, branch, files (a restored checkout) |
