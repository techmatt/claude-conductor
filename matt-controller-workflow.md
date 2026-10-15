# matt-controller-workflow — starting and running a project with conductor

Read this at the start of a claude.ai session that opens a NEW project, or when Matt says "read the workflow". It is the whole method; nothing from earlier projects is assumed. Every rule here would still be true if the project were about something else.

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
4. The planner writes the first real `state.md`, plus any `conductor.toml` change (locks, `max_hours`, `ask_default_minutes`), as one request. It also writes each code repo's `CLAUDE.md` standing contract (report shape, commit rules, how to background long steps, the `Report written to` sign-off) as a first queued prompt for that repo.
5. Matt starts conductor for the project (`uv run conductor run C:\Code\<project>-control`). From then on, work flows through the queue.

## Every session

- Start: read `state.md` and `VOCAB.md` (in the conductor repo), then "check inbox" unless Matt opens with something else. Open questions lead the inbox; raise blocking ones first.
- Matt raises every next step. The planner answers what is asked and lists open questions; it never assumes or schedules the next leg.
- `remind me the vocab` prints `VOCAB.md`.

## Writing a prompt

- **Front-matter first.** `repo` (required), and as needed: `locks` (named locks beyond the repo's own), `depends_on`, `after_red` (`stop`, the default, or `continue`), `needs_planner` (the next item can't be written until this report is read), `readonly`, `max_hours`.
- **Approval.** An item goes into `queue\` only when Matt has approved it in the conversation; otherwise it goes to `proposed\`. A `readonly` item (writes nothing tracked, no commit, no shared lock) may go straight to the queue — Matt sees it but doesn't approve it.
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
