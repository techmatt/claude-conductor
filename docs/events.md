# Event log

`events.db`, table `events(id, ts, kind, item, repo, paths, detail)`. `ts` is local
ISO time with offset; `paths` is a JSON list (control-folder-relative where inside
it); `detail` is a JSON object. Triggers reject UPDATE and DELETE.

| kind | when | notable detail |
|---|---|---|
| `daemon_start` / `daemon_stop` | daemon lifecycle | pid, control; running items at stop; stop `reason` (`signal`: a `stop: daemon` signal, `interrupt`: Ctrl+C, `error`: three ticks in a row failed on an `OSError`) |
| `daemon_died` | at start: the previous daemon left `daemon.json` and is gone, so it never stopped cleanly (red) | pid, started, last_beat, gap, memory (its last sample: at, level, commit, limit, free), running |
| `memory` / `memory_critical` | the memory level changed (`memory_critical`, red: to critical) | level, was, commit, limit (GiB), percent, free (GiB), top (item, kind, count, private_mb), others (name, count, private_mb), action |
| `reaped` | processes an item left in its worktree were killed | context, processes (pid, name, kind, private_mb), private_mb |
| `enqueue` | an item is first seen in `queue/` | its front-matter fields |
| `edited` | a queued item's file changed before it launched (it runs the new text) | staged (its stage is discarded) |
| `invalid` | bad item, signal, stray queue folder, bad config | error, path; `applied` (lines already applied) for a part-applied signal |
| `hold` | a queue head cannot run, or a merge waits (reason changed) | reason; merge |
| `start` | executor launched | session_id, pid, deadline, port, worktree, branch; resumed, landed (a staged item); template, template_version, notes (a template item; a cleanup's notes); error (launch failed) |
| `stage` | a blocked queue head launched read-only | session_id, pid, worktree, deadline |
| `staged` | the stage finished cleanly | session_id, plan (first 500 chars), cost_usd |
| `worktree` | created, moved (a staged worktree to current main), removed (merged), kept (red), discarded; a leftover removed on retry | action, branch, base, note (a shared folder created; `leftover removed on retry`) |
| `merge` | an item's branch landed on main | branch, sha (main after), commits; resolved (prose files a merge executor resolved) |
| `resolve` | the rebase stopped on prose files only; a merge executor was launched to resolve them | files, step, commit (the one being replayed), session_id, pid, deadline |
| `conflict` | the rebase onto main conflicted, or a merge executor failed (red) | branch, files, error; resolver (the merge executor's log) |
| `paths_violation` | the branch changed files outside `paths` (red) | files, paths_declared |
| `adopt` | restart found the executor (or stage: `stage: true`) alive | pid, session_id |
| `recover` | restart found it finished while the daemon was down, or a merge to resume | note |
| `requeue` | restart found it never launched | reason |
| `kill` | max_hours reached | pid, reason |
| `finish` | item done (after its merge, when it had one) | status, report_status, reasons, blocks, carry, slow (as written, or null), pick (`<repo>/<path>`, or null), session_id, exit_code, cost_usd, turns, branch, merged, needs_planner, after_red; audit (it kept notes), template |
| `audit_used` | a cleanup (or general audit) concluded, not red: the audit notes it acted on | notes (item ids); paths (their files) |
| `red` | the item's status is red | reasons, after_red |
| `tamper` | control folder changed during an executor's window | changes, concurrent, found (the check that saw it) |
| `drift` | control folder changed with no executor running | changes |
| `request` / `request_failed` | a request applied or rejected | request, message, targets / error |
| `commit` | conductor committed the control folder, or an item committed in its repo | scope, sha, subject |
| `read` / `clear` | signals | through; session (a `session:` signal's checkpoint id) / repo |
| `reviewed` | a `reviewed:` signal marked a finished item's report as acted on | finish (its event id), status, by |
| `checkpoint` | a `checkpoint:` (or `session:`) signal started a new session view (its id is its number) | label, by; session (`session.md`, from a `session:` signal) |
| `progress` | the executor called `progress` | seq, at, note (absent on an outline-only line), eta, done, remaining, outline ([{title, eta, done}]); reply_to (the tell or running amendment it answers) |
| `tell` | a `tell:` (or `outline:`) signal queued a message for a running executor (the event id is the message id); `by: conductor` for a memory-level message | text, by; outline_request (an `outline:` signal's fixed request) |
| `delivered` | the message or amendment reached the executor | tell or amend, via (`hook`/`progress`; an amendment also `prompt`/`resume`), text, waited_seconds, after_seq (progress lines by then); kept (an amendment's `.amend.md`) |
| `amended` | an amend file was taken (the event id is the amendment's number) | text, by, state (`queued`/`staging`/`staged`/`running`), delivery |
| `amend_refused` | an amend file for a merging or finished item, or empty or unreadable (red; the file stays) | state, reason, path, hash, by |
| `run_start` / `run_ready` / `run_open` / `run_stop` | a run target started (or was already starting: `reused`), answered, had its URL opened, was stopped | target; pid, command, cwd / waited_seconds / url / note |
| `run_failed` | a run target never came up, or its URL could not be opened (red; holds no queue) | target, error, log_tail |
| `pick_open` | a `pick:` signal (or `conductor pick`) served a manifest and opened its page; item: the latest finish that named it, if any | name, manifest, url, opened (false: the browser reported it did not open); by |
| `pick_saved` | Matt saved a picker; paths: its choices file; no `by` (the page has no identity) | name, manifest, changed, notes, rows |
| `pick_failed` | opening the browser raised, `conductor pick` refused the manifest, or its own server could not start (red; holds no queue) | manifest, name, url, error; by |
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
| `config_reload`, `warning` | config applied; runtime path not ignored; a toast failed; a stage discarded; a shared path that is a file, or not ignored; a worktree not removed (recorded for retry) or still not removed on a retry; a report still unreviewed at a checkpoint | note; checkpoint, finish (the unreviewed-at-checkpoint warning) |
