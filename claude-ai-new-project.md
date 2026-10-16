# claude-ai-new-project — starting and running a project with conductor

Read this at the start of a claude.ai session that opens a NEW project, or when Matt (the user) says "read the workflow". It is the whole method; nothing from earlier projects is assumed. Every rule here would still be true if the project were about something else. How conductor itself works is in its `README.md`; the phrases Matt uses are in **Vocab** at the end.

## The three roles

- **Planner — this claude.ai session**, linked to Matt's Windows box through the desktop app. It works on files only: it reads and writes the project's control folder through the bridge (stage a file to read it, commit a file to write it). It has no shell on the box and never needs one. Sessions are bounded (~150k tokens is the warn point; Matt decides when to close out) and every session can end at any moment, because the planner keeps `state.md` current as it goes.
- **Conductor — a daemon on the box** (`C:\Code\claude-conductor`, one install, one running instance per project). It owns the clock and every process: the queues, the locks, launching executors, waiting on them, collecting reports, the event log, the inbox, questions, and every commit to the control folder. Conductor itself is built and changed only by prompts Matt pastes into a Claude Code session by hand — it never runs its own queue.
- **Executors — headless Claude Code**, one per queue item, launched by conductor with `--dangerously-skip-permissions` in the target code repo. An executor never sees the control folder; anything it needs lives in the code repo's `CLAUDE.md` and READMEs.

## Layout (per project)

```
C:\Code\<project>-control\        git; the planner's world
  state.md, <other handoff docs>   tracked — changed ONLY through conductor's apply step
  conductor.toml                   tracked — repos, named locks, defaults
  queue\<repo>\NNN_name.md         approved work, run in NNN order
  proposed\NNN_name.md             awaiting Matt's approval
  running\ done\                   conductor moves items through these
  reports\<repo>__NNN_name.md      executor reports
  questions\                       open asks and their answers
  requests\                        planner → conductor (apply + commit)
  signals\                         planner/app → conductor: read, approve, cancel, reorder, clear, stop
  inbox.md                         generated digest since the planner's last read
  events.db                        append-only event log (SQLite)
C:\Code\<repo>\                    code repos; executors work here
```

Everything except the tracked docs and `conductor.toml` is git-ignored runtime state.

**The tracked control files change only through conductor.** The planner writes a request (`requests\<stamp>\` holding whole replacement files plus `request.md` with a commit message); conductor copies them over, commits, and logs it. Conductor snapshots the control folder around every executor run and reverts any change it finds to a tracked file there, logging a red event — so an errant prompt cannot damage the docs. The planner never hand-patches a tracked file in place, and never uses hunks: it stages the current file, edits in its workspace, and sends the whole file.

## Starting a new project

1. Matt names the project and its code repos. Nothing is authored before every name is fixed.
2. The planner gives Matt the one command that creates the control folder, and Matt runs it in `C:\Code\claude-conductor`:
   `uv run conductor init C:\Code\<project>-control --project <project> --repo <name>=C:\Code\<repo> …`
   It writes the layout, `conductor.toml`, a stub `state.md` and the `.gitignore`, then makes the first commit. It refuses a folder that already has files in it. Matt creates any code repo that doesn't exist yet.
3. The planner asks for folder access to the control folder and to each repo. Access can only be granted to a folder that exists.
4. The planner writes the first real `state.md`, plus any `conductor.toml` change (locks, `max_hours`, `ask_default_minutes`, and per repo the `shared` untracked folders and `setup` commands its worktrees need, or `worktrees = false`), as one request. It also writes each code repo's `CLAUDE.md` standing contract (report shape, commit rules — "commit to the branch you are on" — how to background long steps, the `Report written to` sign-off) as a first queued prompt for that repo.
5. Matt starts conductor for the project (`uv run conductor run C:\Code\<project>-control`). From then on, work flows through the queue.

## Every session

- Start: read `state.md` and the **Vocab** section below, then "check inbox" unless Matt opens with something else. Open questions lead the inbox; raise blocking ones first.
- Matt raises every next step. The planner answers what is asked and lists open questions; it never assumes or schedules the next leg.
- `remind me the vocab` prints the **Vocab** table.
- Matt can also watch and act without the planner in the desktop app (`uv run conductor app C:\Code\<project>-control`, run in `C:\Code\claude-conductor`): repos, questions, the event trail, and proposed items. Its actions are the same `signals/` verbs the planner uses, so the inbox shows them like any other event.

## Writing a prompt

- **Front-matter first.** `repo` (required), and as needed: `locks` (named locks beyond the repo's own), `depends_on`, `after_red` (`stop`, the default, or `continue`), `needs_planner` (the next item can't be written until this report is read), `readonly`, `max_hours`, `paths` (globs the item may change), `stage` (`false` to skip staging this item).
- **Concurrency is declared, not judged.** Every item runs in its own worktree on `conductor/<item>` and is merged into `main` when it finishes `done` or `partial`. Two items in one repo run at the same time only when both declare `paths`, the globs are disjoint, and they share no named lock; anything else waits its turn. Declare `paths` only when the work really is confined. Touching a file outside them makes the item red and unmerged, and the check is conservative, so a doubtful pair runs serially. A dependent item starts only after its dependency has merged.
- **Staging is automatic.** A queue head that is waiting only on a busy lock or a running dependency gets a read-only head start in its worktree, then resumes in the same session when it gets the lock. Set `stage: false` when that reading would be wasted, or when the item's plan depends on what the running item produces.
- **A code repo's `CLAUDE.md` says "commit to the branch you are on"**, never "commit to `main`" and never names a branch. Executors work on `conductor/<item>`, and conductor does the merge.
- **Approval.** An item goes into `queue\` only when Matt has approved it in the conversation; otherwise it goes to `proposed\`. A `readonly` item (writes nothing tracked, no commit, no shared lock) may go straight to the queue — Matt sees it but doesn't approve it. Once an item exists, the planner never moves, renames or deletes it: approving, cancelling and reordering are `signals\` verbs conductor carries out and logs (see **Vocab**).
- **A started item is never edited.** A correction or follow-up is a new queue item. An item not yet started may be rewritten freely.
- **Short, goal-first.** Length scales with risk; trust the executor on mechanics. Never restate what the repo's `CLAUDE.md` owns. End with "Commit when done." unless the prompt says why not.
- **State the goal as the authority.** When the planned route runs out, the executor is authorized to advance the goal another way and say what it chose. Idling to a deadline, or re-running an exhausted step, is a failure; listing only the fallbacks the planner happened to foresee is not enough.
- **Branches, not stops.** Every gate is "do what fits, record what didn't, continue." The only hard stop is the wrong-repo guard. Conductor's preamble gives every executor the `ask` tool and one rule for it. Ask only when a wrong guess would waste real work or be hard to undo. Give a default whenever a reasonable one exists; an unanswered question takes it after `ask_default_minutes` (10 by default). Block only when every default would be worse than waiting. The executor makes that call itself, so a prompt doesn't repeat the rule. A prompt names a question in advance only when the planner already knows Matt must decide it.
- **Every premise is a claim**, verified from source before acting on it; say what happens when one fails. A prompt's own numbers are its least trustworthy part — a count that decides a plan is read live.
- **Durations.** "An N-hour run" is N hours of that run alone; setup and readout sit outside it. "Finish by X" includes setup and readout — state the reservation. The budget is a target, not a cap. The planner gives no runtime estimate for compute-heavy work; the executor estimates from its first units and says so.
- **Weigh a measurement against the cost of the action it decides**, not against how informative it is. Before measuring, name the decision the number changes.
- **Small parked edits ride the very next prompt to that repo**, never held back or given a prompt of their own.
- **Matt never edits config or docs by hand** — he dictates values; a prompt or a request applies them.
- **Never mention git push, remotes or commit state.** Matt handles git beyond each prompt's commit.
- **Tests are not a subject.** Assume every prompt runs the right tests; a report mentions tests only for a red it could not fix.

## Questions from executors

An executor's `ask` lands in `questions\<repo>__<NNN_name>__<n>.md`. It leads the inbox under **Questions** until it is resolved, and Matt gets a desktop toast for it. The file says whether the question blocks, and if not, which default it takes and when.

- "check questions" lists the open ones. Matt answers in the conversation, and the planner writes the answer as plain text to `questions\<id>.answer.md`. That file is a runtime file, not a request: it is written directly and never edited afterwards. Matt can also answer from a terminal with `conductor answer <id> <text>`.
- An answer written after the default was taken is logged `late` and changes nothing. If the late answer matters, the fix is a new queue item.
- A blocking question holds its executor for as long as it stays open, and that time doesn't count toward `max_hours`. A blocking question is the first thing to raise when Matt opens a session.

## Reading a report

Every CC session that writes a report ends its final message with `Report written to <full path>` (each repo's `CLAUDE.md` carries this; conductor's executor preamble repeats it). Every report opens with a header conductor can parse:

```
status: done | partial | red
blocks: <what this blocks, or none>
carry: <what state.md should change, or none>
```

- `done`: the goal was met.
- `partial`: part of the goal was met, nothing is broken, and the rest is follow-up work. This includes an executor declining an instruction for a stated reason.
- `red`: something is broken or unsafe, and this repo's queue should not continue without a look. With `after_red: stop` (the default) conductor holds the queue until the planner clears it.

The planner does not summarize a report back. It says what the report changes, what the planner got wrong, and what (if anything) blocks — then folds the carry into `state.md` through a request. Spec deviations with stated reasons are usually right; read before overriding. Trivial outcomes get two sentences. No list of "unreviewed by Matt" items is kept; only decisions that block upcoming work are flagged. A micro-task is assumed handled once queued.

## state.md and closeout

- **The handoff docs are the active wavefront, not a history.** A line survives only if it would change a future decision and cannot live in code: undecided questions, caveats that would cause a misread, true-but-unenforceable facts. A mechanic lives in its repo's README; the docs point to it. One home per fact.
- **state.md is status, plan and flags** — what is queued, running, open, ruled — and holds no facts that belong elsewhere.
- **Closeout** = the planner writes the final `state.md` (and any doc the session changed) as one request, conductor commits it, and the next session starts from it. No apply scripts, no files for Matt to place. "Prepare for closeout" = list open questions for Matt, items still expecting a report, and parked edits that should ride the next prompt.
- A mistake the planner repeats becomes one line in the project's docs; a one-off mistake is said in the chat and not carried.

## Vocab

What Matt can say to the planner. The planner reads this at session start; "remind me the vocab" prints the table.

Queue actions are signals: the planner writes a small file into `signals\` with one `key: value` line per action (several lines may share a file), and conductor carries them out and logs an event for each. The planner never moves, renames or deletes a file in `queue\` or `proposed\` itself. `NNN_name` may be shortened to `NNN`; a queued item may carry its repo as `repo/NNN_name`. The exact rules for each verb are in conductor's `README.md`.

| Phrase | What the planner does |
|---|---|
| **check inbox** | Reads `inbox.md` (everything since the last read): what finished, what's red, what's asking. Says what changed, not a summary. Marks it read: signal `read: <id>`, the id printed at the top of `inbox.md`. |
| **check questions** | Lists open asks in `questions\` (they also lead `inbox.md`): the asking item, which ones block, and for the others the default and when it is taken. Blocking ones come first. |
| **answer Q: …** | Writes Matt's words as plain text to `questions\<id>.answer.md` (Q may be the short form, e.g. `012__1`). It is never edited afterwards. Once the default has been taken, says so: the answer will be logged `late` and changes nothing, so if it matters it needs a new queue item. |
| **queue status** | Every repo: running item and elapsed time, queued items in order, proposed items awaiting approval. |
| **show events** [since X / for Y] | Stages `events.db` and queries it: a timeline, filtered by time, repo or item. |
| **postmortem Y** | Walks item Y's event trail, prompt and report, and says where it went wrong. |
| **propose** / **enqueue** | Writes a new prompt into `proposed\` (needs approval) or `queue\<repo>\` (Matt approved it here). |
| **approve X** | Signal `approve: NNN_name`. Conductor moves `proposed\NNN_name.md` into the queue of the repo its front-matter names. |
| **cancel X** | Signal `cancel: <item>` for a proposed or queued item. Conductor moves the file to `.conductor\cancelled\` (kept, not deleted). A running item cannot be cancelled; a staged one loses its stage. |
| **reorder R: X Y Z** | Signal `reorder: <repo or proposed> X Y Z`. The listed items swap their own numbers into the listed order; unlisted items keep theirs. Refused if any item is running, if something `depends_on` a renumbered item, if a renumbered item would come before an item it depends on, or if a new name is the id of an item that already ran. |
| **clear R** | Signal `clear: <repo>`: releases a red hold on that repo's queue, after the planner has read the red report with Matt. |
| **stop conductor** | Signal `stop: daemon`: the daemon exits cleanly; running executors carry on and are picked up when it restarts. Matt restarts it with `uv run conductor run <control folder>` or the app's Start button. |
| **context estimate** | Token estimate for this session; ~150k is the warn point. |
| **prepare for closeout** | Open questions for Matt, items still expecting a report, parked edits that should ride the next prompt. |
| **closeout** | Writes the final `state.md` (and any changed doc) as one request; conductor commits it. |
| **read the workflow** | Re-reads `claude-ai-new-project.md` (in the conductor repo). |
| **remind me the vocab** | Prints this table. |
