# Event log

`events.db`, table `events(id, ts, kind, item, repo, paths, detail)`. `ts` is local
ISO time with offset; `paths` is a JSON list (control-folder-relative where inside
it); `detail` is a JSON object. Triggers reject UPDATE and DELETE.

| kind | when | notable detail |
|---|---|---|
| `daemon_start` / `daemon_stop` | daemon lifecycle | pid, control; running and staging items at stop, note; stop `reason` (`signal`: a `stop: daemon` signal, with its paths and by; `interrupt`: Ctrl+C; `error`: three ticks in a row failed on an `OSError`, with error) |
| `daemon_restart` | the old daemon restarts itself on a code change (`restart.md`); the new one logs `daemon_start` | reason (`code changed: <old sha> → <new sha> <subject>`), from, to (`<src tree id> <pyproject blob id>`), pid, new_pid, running, staging, note |
| `daemon_died` | at start: the previous daemon never stopped cleanly (red) | pid, started, last_beat, last_beat_clock, gap, note, memory (its last sample: at, level, commit, limit, free), running |
| `memory` / `memory_critical` | the memory level changed (`memory_critical`, red: to critical) | level, was, commit, limit (GiB), percent, free (GiB), top (item, kind, count, private_mb), others (name, count, private_mb), action |
| `reaped` | processes an item left in its worktree were killed | context, processes (pid, name, kind, private_mb), private_mb |
| `enqueue` | an item is first seen in `queue/` | its front-matter fields (`paths` as `paths_declared`, `resume` as `resume_of`); `shares_number` (the other items with its number), if any |
| `edited` | a queued item's file changed before it launched (it runs the new text) | staged (its stage is discarded and redone); launching (the write landed mid-launch, which was undone) |
| `invalid` | (red) bad item, signal, stray queue folder, bad config, a queue file written for an item that already launched (set aside as `.md.bad`) | error, path; `applied` (lines already applied) for a part-applied signal |
| `hold` | a queue head cannot run, or a merge waits (reason changed) | reason; merge |
| `setup` | a setup runner spawned (`executors.md`, *Setup*); paths: the setup log | commands, pid, stage (true for a stage's setup) |
| `start` | executor launched | session_id, pid, deadline, port, worktree, branch; resumed, landed (a staged item); setup_seconds; template, template_version, notes (a template item; a cleanup's notes); alongside (its line), beside (ids executing or in setup in its repo then; `scheduling.md`); resume_of, resume_session (`resume.md`); error (launch failed, setup included) |
| `stage` | a blocked queue head launched read-only | session_id, pid, worktree, deadline; setup_seconds |
| `staged` | the stage finished cleanly | session_id, worktree, plan (first 500 chars), cost_usd |
| `worktree` | created, moved (a staged worktree to current main), removed (merged), kept (red), discarded; a leftover removed on retry | action, branch, base, note (a shared folder created; `leftover removed on retry`) |
| `merge` | an item's branch landed on main | branch, sha (main after), commits (the item's own: pre-merge main to sha), note; resolved (prose files a merge executor resolved); squashed (commits folded in) |
| `land` | a `land:` began, then landed | state, branch; sha, commits, squashed, hashes |
| `resolve` | the rebase stopped on prose files only; a merge executor resolves them | files, step, commit (the one being replayed), session_id, pid, deadline |
| `conflict` | the rebase onto main conflicted, or a merge executor failed (red) | branch, files, error, note; resolver (the merge executor's log); land (a `land:`) |
| `paths_violation` | the branch changed files outside `paths` (red) | files, paths_declared |
| `adopt` | restart found the executor (or stage: `stage: true`) alive | pid, session_id |
| `recover` | restart found it finished while the daemon was down, or a merge to resume | note |
| `requeue` | restart found it never launched, or mid-setup | reason |
| `kill` | max_hours reached (red) | pid, reason |
| `finish` | item done (after its merge, when it had one) | status, report_status, reasons, blocks, carry, slow (as written, or null), pick (`<repo>/<path>`, or null), session_id, exit_code, cost_usd, turns, branch, merged, needs_planner, after_red; conductor (its own red, landed anyway); audit (it kept notes), template, resume_of (a resume item) |
| `audit_used` | a cleanup (or general cleanup) concluded, not red: the audit notes it acted on | notes (item ids); paths (their files) |
| `red` | the item's status is red | reasons, after_red |
| `tamper` | control folder changed during an executor's window (red) | changes, concurrent, found (the check that saw it); attributed (with several running: the ones whose own writing tool calls named a changed path, the only ones that end red; empty: all do; `control-folder.md`) |
| `drift` | control folder changed with no executor running, or by conductor's own git | changes; own |
| `request` / `request_failed` | a request applied or rejected (none while it waits for targets) | request, message, targets, no_change (it committed nothing) / error |
| `commit` | conductor committed the control folder, or an item committed in its repo | scope, sha, subject |
| `read` / `clear` | signals, or a `closeout:` request | through; session (a `session:` signal's checkpoint id); closeout (the request's name) / repo; note (a `land:`) |
| `reviewed` | a `reviewed:` signal marked a finished item's report as acted on | finish (its event id), status, by; hashes (`report`, `audit`: sha256 or null) |
| `report_changed` | a reviewed report or its `.audit.md` changed since its `reviewed` (or last `report_changed`): unreviewed again (`inbox.md`) | files (`report`, `audit`: what differs from the review), reviewed (its id), hashes; opened, opened_by (the latest `session_opened` since it finished) |
| `session_opened` | a `session_opened:` signal: a finished item's session opened by the app's Terminal or `conductor open` | session_id, by |
| `review` | a `review:` signal wrote `review.md`; paths: the signal, `review.md`; item: the ref's (none for `all`) | which (`all` or the item id), items (inlined, oldest first), conductor (conductor reports inlined), by |
| `checkpoint` | a `checkpoint:` (or `session:`) signal started a new session view (its id is its number) | label, by; session (`session.md`, from a `session:` signal) |
| `progress` | the executor called `progress` | seq, at, note (absent on an outline-only line), eta, done, remaining, outline ([{title, eta, done}]); reply_to (the tell or running amendment it answers) |
| `tell` | a `tell:` (or `outline:`) signal queued a message for a running executor (event id = message id); `by: conductor` for a memory or started-beside message | text, by; outline_request (an `outline:` signal's fixed request); beside; answers (the question it answered: `questions.md`); pause, resume |
| `delivered` | the message or amendment reached the executor | tell or amend, via (`hook`; an amendment also `prompt`/`resume`; `progress` in older logs), text, waited_seconds, after_seq (progress lines by then); kept (an amendment's `.amend.md`) |
| `amended` | an amend file was taken (the event id is the amendment's number) | text, by, state (`queued`/`staging`/`staged`/`running`), delivery |
| `amend_refused` | an amend file for a merging or finished item, or empty or unreadable (red; the file stays) | state, reason, path, hash, by |
| `run_start` / `run_ready` / `run_open` / `run_stop` | a run target started (or was already starting: `reused`), answered, had its URL opened, was stopped | target; pid, command, cwd / waited_seconds / url, opened / note |
| `run_failed` | a run target never came up, or its URL could not be opened (red; holds no queue) | target, error, log_tail |
| `pick_open` | a `pick:` signal (or `conductor pick`) served a manifest and opened its page; item: the latest finish that named it, if any | name, manifest, url, opened (false: the browser reported it did not open); by |
| `pick_saved` | Matt saved a picker; paths: its choices file; no `by` (the page has no identity) | name, manifest, changed, notes, rows |
| `pick_failed` | the browser raised, `conductor pick` refused the manifest, or its server could not start (red; holds no queue) | manifest, name, url, error; by |
| `opened` | an `open:` signal (or `conductor open-path`) launched its action | verb, path or url; by |
| `open_refused` | an `open:` target failed its check (not red; the signal file goes on) | verb, target (as given), reason; by |
| `notify` | a `notify:` signal or a watch's action raised a toast | text, by; item (a watch's) |
| `approve` | a `proposed/` item moved into its queue | item, from |
| `cancel` | a queued or proposed item set aside in `.conductor/cancelled/` | from (`queue`/`proposed`), kept |
| `reorder` | items of a queue (repo) or of `proposed` renumbered | before, after (names in queue order); note (finished after a restart) |
| any signal-caused event | a signal file had `by:` | by |
| `ask` | a question file appeared | question (id), text, block, default, deadline |
| `answer` | answered before it resolved | question, text, waited_seconds, deadline (moved, when blocking); by (the answer file's `by:`) |
| `default` | the default deadline passed unanswered | question, text (the default) |
| `late` | an answer file after a default or close | question, text, outcome; by |
| `question_closed` | resolved with no answer: item finished, or the tool call was cancelled | question, outcome, reason |
| `queue_empty` | an item finished and its repo has nothing queued or running | last |
| `watch_flag` | the night shift's check flagged something (`night-shift.md`) | flag (`quiet`, `eta`, `question`, `blocked`, `backlog`, `memory`, `run_failed`), key, text; watch (`skipped: <why>` for a stall no watch looked at) |
| `watch_answer` | a watch's `answer:` action answered a question | question, text, source, watch |
| `pause` / `resume` | a `pause:` / `resume:` signal (`night-shift.md`, *Pause*) | repo; items, by; paused_seconds (resume) |
| `watch` | conductor queued a watch item | reason, flags (their keys), file, by |
| `backlog_draw` | a backlog item moved into its repo's empty queue; paths: the queue file, the backlog file | from, full_length, night, by |
| `autopilot` | an `autopilot:` signal set the mode (`night-shift.md`); the last one holds | mode (`off`, `night`, `always`), was, by |
| `ack` | `ack: invalid` (`signals.md`) | what, events (`invalid` ids), by |
| `backlog_sleep` | at night, draws stop: the budget is spent, or nothing is eligible (once a night per reason) | key, reason, until, spent_hours, budget_hours |
| `push` | main was pushed to the remote (`push.md`); repo: none for the control folder | target (repo or `.control`), remote, branch, sha, why (`merge`, `start`, `signal`, `closeout`), up_to_date, recovered (the first success after a failure) |
| `push_failed` | a push failed, once per failure (an automatic retry of the same one logs nothing) | target, remote, branch, why, error, signature, output (git's last lines), rejected (the remote moved on: never forced) |
| `closeout_push` | every push a `closeout:` request started is done | request, pushed, failed |
| `config_reload`, `warning` | config applied / worth a note, not red; the note says what (a runtime path or shared folder not ignored; a toast, `open:` launch, self-restart, tick, memory sample, log compaction or night shift that failed; a stage discarded; a worktree not removed; a report unreviewed at a checkpoint or reviewed twice; a message never delivered or a `tell:` for no live item; a signal or reorder cut short by a crash; the daemon under a Claude Code session; a watch's action not carried out) | note; checkpoint, finish (unreviewed at a checkpoint); applied (a part-applied signal); amend, tell (never delivered); verb, target (an `open:` launch); action (a watch's) |
