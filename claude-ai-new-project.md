# claude-ai-new-project — starting and running a project with conductor

Read this at the start of a claude.ai session that opens a NEW project, or when Matt (the user) says "read the workflow". It is the whole method; nothing from earlier projects is assumed. Every rule here would still be true if the project were about something else. How conductor itself works is in its `README.md`; the phrases Matt uses are in **Vocab** at the end.

**This workflow replaces an older one.** It uses no Google Drive relay (Drive is only a home for some project files: see *Storage*), no prompt or report folders outside the control folder, no "CLOSEOUT apply" prompts, and no files for Matt to place by hand. If memory or saved preferences describe Drive delivery, folder-resolution rules, apply-scripts or checkpoint numbering, those belong to the earlier fractal project and do not apply here. Conductor's own checkpoints (see *Every session*) are numbered by conductor, never by the planner. Where a saved preference and this file disagree about mechanics, this file wins. Matt's own stated preferences about content and judgement still apply.

## The three roles

- **Planner — this claude.ai session**, linked to Matt's Windows box through the desktop app. It works on files only: it reads and writes the project's control folder through the bridge (stage a file to read it, commit a file to write it). It has no shell on the box and never needs one. Sessions are bounded (~200k tokens is the warn point; Matt decides when to close out) and every session can end at any moment, because the planner keeps `state.md` current as it goes.
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
  picks\                           Matt's saved picker choices (<name>.choices.json)
  requests\                        planner → conductor (apply + commit)
  signals\                         planner/app → conductor: read, approve, cancel, reorder, clear, reviewed, tell, checkpoint, run, pick, stop
  inbox.md                         generated digest since the planner's last read
  events.db                        append-only event log (SQLite)
C:\Code\<repo>\                    code repos; executors work here
```

Everything except the tracked docs and `conductor.toml` is git-ignored runtime state.

**The tracked control files change only through conductor.** The planner writes a request (`requests\<stamp>\` holding whole replacement files at their paths relative to the control folder, plus `request.md`, **written last**); conductor copies them over, commits, and logs it. `request.md` is YAML front-matter with a required `targets:` list naming every file in the request, and an optional `message:`, then the commit message as its body:

```
---
targets: [state.md, docs/plan.md]
message: "state: v0.2"
---
Commit message (used when `message:` is absent).
```

Every file in the folder must be a target and every target present, or the request lands `.failed`. Quote `message:` when it contains a colon; an unquoted colon fails the YAML, and the request lands `.failed`. Conductor snapshots the control folder around every executor run and reverts any change it finds to a tracked file there, logging a red event — so an errant prompt cannot damage the docs. The planner never hand-patches a tracked file in place, and never uses hunks: it stages the current file, edits in its workspace, and sends the whole file.

## The bridge

- The planner must be in a claude.ai chat **linked to the box**: Matt opens the chat in the Claude desktop app's Chat tab and sends a message. The Code tab is a separate thing. If the device tools are missing, say so and ask Matt to link the chat; don't work around it.
- Folder access can be requested only for a folder that already exists. Ask once, for exactly the folders needed.
- Reading a file means staging a snapshot of it into the workspace. Re-stage before relying on a file that may have changed. Writing means committing a file from the workspace; pass the staged `mtimeMs` as `expectedMtimeMs` when replacing a file someone else may have touched.
- The bridge cannot delete files or run commands on the box. Anything that needs either is a signal (queue changes; `cancel` keeps the file), a request (adds or replaces tracked control files, never deletes one), a queue item (anything in a code repo), or one command Matt runs himself (including deleting a tracked control file).
- Write every file with LF line endings.

## Storage

`C:\Code\gdrive-sync` is a Google Drive sync folder. Every project file has exactly **one home**:

- **git:** source, data, small chosen outputs, and anything hand-made (recordings).
- **Drive:** `C:\Code\gdrive-sync\<project>\<folder>`, for files that are expensive, irreplaceable or private but don't belong in git: full-res generated originals, reference photos, slow or fragile generated media.
- **Nowhere:** anything a command rebuilds cheaply.

A Drive-homed folder appears in the repo's main checkout as a directory junction to its Drive folder, git-ignored in the repo. The repo's `shared` setting in `conductor.toml` links it into every worktree, so there is one copy, not two; Drive's own version history is the backup.

A new Drive-homed folder starts with the queue item that creates it. That item also makes the main-checkout junction, and its prompt says so explicitly, because conductor's preamble otherwise forbids touching the main checkout. The `shared` entry is added by request only after that item merges; added earlier, conductor would create an empty real folder at that path in the main checkout first.

## Starting a new project

1. Matt names the project and its code repos. Nothing is authored before every name is fixed.
2. The planner gives Matt the one command that creates the control folder, and Matt runs it in `C:\Code\claude-conductor`:
   `uv run conductor init C:\Code\<project>-control --project <project> --repo <name>=C:\Code\<repo> …`
   It writes the layout, `conductor.toml`, a stub `state.md` and the `.gitignore`, then makes the first commit. It refuses a folder that already has files in it. Matt creates any code repo that doesn't exist yet.
3. The planner asks for folder access to the control folder, to each repo, and to `C:\Code\gdrive-sync` (see *Storage*). Access can only be granted to a folder that exists.
4. The planner writes the first real `state.md`, plus any `conductor.toml` change (locks, `max_hours`, `ask_default_minutes`, and per repo the `shared` untracked folders and `setup` commands its worktrees need, its `prose` globs if the default `CLAUDE.md`/`README.md`/`docs/**/*.md` doesn't fit, its `background` globs for test or build commands that can run over ten minutes, or `worktrees = false`; and a `[run.<name>]` target once there is something to run), as one request. It also writes each code repo's `CLAUDE.md` standing contract as a first queued prompt for that repo. The contract holds:
   - the report: path and header from conductor's preamble, a ~60-line soft target, findings, decisions and surprises only, tests mentioned only for an unfixed red, and the `Report written to <path>` sign-off;
   - "commit to the branch you are on";
   - local 12-hour clock times;
   - steps over ~30 seconds backgrounded, with a completion waiter on the real process, never on a launcher wrapper;
   - LF line endings (`.gitattributes` `* text=auto eol=lf`);
   - operational facts learned while working promoted into the repo's README, never left only in a report.
5. Matt starts conductor for the project (`uv run conductor run C:\Code\<project>-control`). From then on, work flows through the queue.

## Every session

- Start (Matt says **start a new <project> session**): if folder access isn't granted yet, request access to the control folder, the code repos and `C:\Code\gdrive-sync` first. Read this file (the **Vocab** section included), then `C:\Code\<project>-control\state.md` and the control folder's other tracked docs. Send `checkpoint: session start` (`by: planner`), then do **check inbox**. Open executor questions (in `questions\`, not `state.md`'s *Paged* items) lead the inbox; raise blocking ones first, then any report the checkpoint's warnings name as still unreviewed.
- **Checkpoints.** A checkpoint is a session boundary. Finished items reviewed before the latest one drop out of the inbox's counts, the app's Repos tab (into its collapsed *Earlier checkpoints* group, below *In flight* and *Finished since checkpoint*) and its Events tab (which starts at the checkpoint). Unreviewed reports, red holds, open questions and anything queued, staged, running or proposed always carry forward, and nothing is deleted. Conductor numbers checkpoints by their event id, so the planner never keeps a counter. `inbox.md` names the current one at the top.
- Matt raises every next step. The planner answers what is asked and lists open questions; it never assumes or schedules the next leg.
- `remind me the vocab` prints the **Vocab** table.
- Design and scoping decisions are made in conversation with Matt, not handed to an executor. What an executor adds is facts: comparisons, audits, prototypes to look at. Those are usually `readonly` items, which the planner may queue without approval when they take no shared lock (see *Approval*).
- For a trivial outcome, two sentences and no write-up.
- If this file turns out to be wrong or missing something during a project, tell Matt in a line. Then, when no conductor prompt is running, add a line to *Open* in `C:\Code\claude-conductor\HANDOFF.md` so the next conductor prompt fixes it.
- Matt can also watch and act without the planner in the desktop app (`uv run conductor app C:\Code\<project>-control`, run in `C:\Code\claude-conductor`): repos (with finished items, a Mark reviewed button, and a Pick… button for a report that left a picker), questions, the event trail (Activity since the latest checkpoint by default; Advanced for every event), proposed items, and a **New checkpoint…** button. There is only ever one app: launching it again with another control folder adds that folder as a tab in the running app (and brings it forward) instead of opening a second copy. Its actions are the same `signals/` verbs the planner uses, so the inbox shows them like any other event.

## Writing a prompt

- **Front-matter first.** `repo` (required), and as needed: `locks` (named locks beyond the repo's own), `depends_on`, `after_red` (`stop`, the default, or `continue`), `needs_planner` (the next item can't be written until this report is read), `readonly`, `max_hours`, `paths` (globs the item may change), `stage` (`false` to skip staging this item), `ultracode` (`true` to run the executor with Claude Code's ultracode on: it orchestrates subagents through workflows on every substantive task, which is more thorough and costs much more; worth it for broad audits, large refactors and hard debugging, not for routine items), `audit` (`true`: keep audit notes) and `template` (`cleanup`, `general_audit`, `test_perf`, `disk_audit`, `claude_md_cleanup` or `bug_hunt`: conductor's standard prompt; see below).
- **Ultracode is set at launch only.** `ultracode: true` is the one route that works. A `tell:` saying "use ultracode" is delivered as an ordinary message and changes nothing, so a running item can't be switched. For a queued item that hasn't started, rewrite its front-matter. For a running one, tell Matt, and queue the remaining work as a new item with `ultracode: true`.
- **Audits are conductor's texts, not the planner's.** Never retype audit instructions into a prompt. `audit: true` (on any item, only when Matt asks: never by default) makes the executor keep running notes beside its report on what was badly structured, what took longer than expected and why, what to refactor, and what the repo's `CLAUDE.md` or README should say. Nobody reviews those notes per item; they wait for a cleanup. `template: <name>` makes conductor's standard text the prompt, and the body is optional: anything the planner writes is added after it as "The planner adds, for this item". The templates are `cleanup` (acts on every audit note of that repo that no earlier cleanup used), `general_audit` (audits the whole repo itself, then acts) `test_perf` (a fast/slow test lane split and faster tests; its report gives timings), `disk_audit` (measures everything the project occupies, conductor's control folder included, frees what is cheaply rebuilt or obsolete, moves what is expensive but idle into `archive\` under the project's Drive home, defers history rewrites; its report gives a before/after size table) and `claude_md_cleanup` (a quick pass that cuts, fixes and tightens the repo's `CLAUDE.md`, keeping the standing contract; changes to how executors work are deferred) and `bug_hunt` (runs the project as its users would through a reusable checker it builds in the repo, fixes clear bugs, keeps a findings list in the repo and defers the rest; with `readonly: true` it only finds and records, in its report). A cleanup, general audit or bug hunt carries out what is clear and defers every design call to Matt (see *Reading a report*). A cleanup that ends red uses none of its notes, so the next one gets them again. A cleanup with no unused notes holds its queue until one appears or it is cancelled, so check **audit notes** before queueing one. Template items are queued like any other. Matt saying a template's phrase (**cleanup R**, **general audit R**, **test perf audit R**, **disk space audit R**, **claude.md cleanup R**, **bug hunt R**) is his approval, so that item goes straight to `queue\`; a template item the planner suggests itself goes to `proposed\`.
- **Concurrency is declared, not judged.** Every item runs in its own worktree on `conductor/<item>` and is merged into `main` when it finishes `done` or `partial`. Two items in one repo run at the same time only when both declare `paths`, the globs are disjoint, and they share no named lock; anything else waits its turn. Declare `paths` only when the work really is confined. Touching a file outside them makes the item red and unmerged, and the check is conservative, so a doubtful pair runs serially. A dependent item starts only after its dependency has merged. A `readonly` item is the exception: it never merges, so it takes no repo lock. Once it is next in its queue it starts beside whatever is running, and the items after it don't wait for it. It still waits for its named locks and `depends_on`, and it sees `main` as it is when it starts, so a readonly item meant to look at an earlier item's work (a notes-only bug hunt after a feature, say) declares `depends_on` on that item. Every executor gets its own port for any server it starts (`CONDUCTOR_PORT`; the preamble says so), so one that starts the app never collides with **run game** or with another item. Prompts don't hard-code a dev port.
- **Prose files are outside `paths`.** Each repo's `prose` setting in `conductor.toml` (default `["CLAUDE.md", "README.md", "docs/**/*.md"]`) names the shared docs nearly every item touches. An item may edit them without declaring them, and two items that both edit them still run together when their code `paths` are disjoint. Leave prose out of `paths`. At merge, the two items' prose edits merge textually. If they conflict, conductor runs a short merge executor that keeps both sides' intent. If that fails, the item ends red like any conflict, with both versions kept: the branch has the item's version and `main` has the other. A repo whose docs are generated, or must never be merged automatically, sets `prose = []` through a `conductor.toml` request; a project adds its own prose globs the same way.
- **Staging is automatic.** A queue head that is waiting only on a busy lock or a running dependency gets a read-only head start in its worktree, then resumes in the same session when it gets the lock. Set `stage: false` when that reading would be wasted, or when the item's plan depends on what the running item produces.
- **A code repo's `CLAUDE.md` says "commit to the branch you are on"**, never "commit to `main`" and never names a branch. Executors work on `conductor/<item>`, and conductor does the merge.
- **Approval.** An item goes into `queue\` only when Matt has approved it in the conversation; otherwise it goes to `proposed\`. A `readonly` item (writes nothing tracked, no commit, no shared lock) may go straight to the queue — Matt sees it but doesn't approve it. Once an item exists, the planner never moves, renames or deletes it: approving, cancelling and reordering are `signals\` verbs conductor carries out and logs (see **Vocab**).
- **Rewrite, amend, or a new item.** A rewrite is for a changed plan: queued items only, and it discards a stage (the item launches fresh). An amendment (**amend X**) adds to a plan that still stands, in any state but finished: queued, staged (the stage is kept) or running. A correction to finished work is a new item. After every write, check for its event in the inbox or Activity — `edited` for a rewrite, `amended` (or a red `amend_refused`) for an amendment — since a write that didn't land shows none: then check the file.
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

## Progress and messages

Every executor posts short **progress lines** with an ETA: at its start, at real milestones, and whenever its estimate changes. The latest one for each running item shows in `inbox.md` (*Now*), in `conductor status`, and in the app; the full run of them is in `events.db` (`progress` events). "ETA on X?" is answered from X's latest progress line and when it was sent, never from a guess.

Matt or the planner can **message a running executor**: signal `tell: <repo>/<NNN_name> <message>` (one line; `by: planner`). The executor gets it after its next tool call, treats it as an instruction from Matt that outranks the prompt, and answers through a progress line. A message to an item that isn't running is refused. It normally lands within about a minute: conductor keeps every shell call an executor makes to a minute or so, moving a longer one to the background. It can't reach an executor waiting on a blocking question until that ends, and one sent after its last tool call is never delivered (the inbox shows a warning). A repo whose test or build commands run long can list them in its `background` globs in `conductor.toml` (e.g. `background = ["npm test*", "*playwright test*"]`, through a request), so executors always start them in the background rather than having them moved there after a minute, which Claude Code stops after 10 minutes. A correction to work already finished is still a new queue item. Matt can also use **Tell…** in the app or `conductor tell <item> <text>`. For anything longer than a line, or for an item that hasn't started yet, **amend** it instead: the planner writes the addition as `queue\<repo>\NNN_name.amend.md` (front-matter `by: planner`, then the text; a second one is `NNN_name.amend.2.md`), whatever the item's state. Conductor logs `amended` with the item's state and how it delivers it: a queued item gets it appended to its prompt at launch, a staged one in its resume message (the stage is kept, and the amendment overrides the plan it made), a running one after its next tool call, like a tell. Every amendment delivered is kept beside the report as `reports\<repo>__NNN_name.amend.md`. One for a merging or finished item is refused (`amend_refused`, red) and the file stays. Matt can also use **Amend…** in the app or `conductor amend <item> <text|file>`. In the app or with `conductor open <item>`, he can open a finished item's session in a terminal (`claude --resume`) to ask it things.

## Run targets

A project's runnable thing (a dev server, the game) is a **run target**: a `[run.<name>]` table in `conductor.toml` with the repo (or folder) it runs in, the server command, the URL to open, and optionally a different URL to wait on and how long to wait (conductor's README, *Run targets*). The planner sets a project's targets through a `conductor.toml` request, with the command and URL the repo's README gives. A repo's main checkout has no installed dependencies, because executors install them in worktrees that are then deleted. So a target's `command` installs them first (e.g. `npm install && npm run dev -- --port 5173`). "run X" is the signal `run: X`. Conductor starts the server if it isn't already answering, waits for it, and opens the URL in Matt's browser. A server that never comes up is a red `run_failed` event carrying the end of its log; the planner reads it and says what broke. `run: X stop` stops it, e.g. to free files a dev server holds before an item that moves them. Matt can also use the app's **Run X** button, which shows whether X is up, and the **Stop X** button beside it while X is up. Matt never types the commands.

## Picks

When work produces candidates for Matt to choose between (art variants, layouts, anything visual), he picks in his browser: one row per thing, a click on his favourite in each row, a note where none is right, then **Save**. A prompt that produces candidates says "write a picker manifest". Conductor's preamble gives the executor the schema, and the executor names the manifest in its report's `pick:` line, so the prompt need not repeat either. It does say where the manifest and its images go: they must survive the merge (committed, or in a `shared` folder; a `readonly` item can only use a `shared` folder), and an item with `paths` must cover them. When the report is in, the planner sends `pick: <repo>/<manifest path>` (the ref is in the report's finish line and in the inbox's **Picks** section, which lists every picker still "waiting for Matt" or opened and not saved, across checkpoints, until Matt saves it). Conductor opens the page in Matt's browser. After Matt saves, the planner reads `picks\<name>.choices.json` (per row: `id`, the chosen `key`, `changed`, and Matt's `note`) and writes the chosen keys and his notes into the next prompt, since executors never see the control folder. Matt can also open one from the app's **Pick…** button. A bad manifest is refused with the reason (an `invalid` event; `pick_failed` from `conductor pick`), and the planner reports it; that includes a manifest whose `name` another manifest already uses, so a prompt that makes a new picker gives it a new name (e.g. with the item number). If conductor restarted while a picker tab was open, that tab can't save: send `pick:` again, and Matt saves from the new tab. A control folder made before pickers existed lacks `picks/` in its `.gitignore`, and conductor logs a startup `warning` until a request adds the line.

## Reading a report

Every CC session that writes a report ends its final message with `Report written to <full path>` (each repo's `CLAUDE.md` carries this; conductor's executor preamble repeats it). Every report opens with a header conductor can parse:

```
status: done | partial | red
blocks: <what this blocks, or none>
carry: <what state.md should change, or none>
pick: <manifest path relative to the repo root>     (only when the executor wrote a picker manifest)
```

- `done`: the goal was met.
- `partial`: part of the goal was met, nothing is broken, and the rest is follow-up work. This includes an executor declining an instruction for a stated reason.
- `red`: something is broken or unsafe, and this repo's queue should not continue without a look. With `after_red: stop` (the default) conductor holds the queue until the planner clears it.
- `pick:`: the report left candidates for Matt (see *Picks*). Processing it means telling Matt the picker is ready (**pick X**); the next prompt waits for his choices unless he says otherwise.

The planner does not summarize a report back. It says what the report changes, what the planner got wrong, and what (if anything) blocks — then folds the carry into `state.md` through a request. Spec deviations with stated reasons are usually right; read before overriding. Trivial outcomes get two sentences. Only decisions that block upcoming work are flagged. A micro-task is assumed handled once queued.

**Deferred design calls.** A cleanup, general audit or bug hunt never decides a design question: its report lists each one under **Deferred**, with the options it sees and the one it would pick. Those go to Matt through the planner. On processing such a report, the planner raises each deferred call with Matt in the conversation, once; a decision becomes a new queue item, and one Matt doesn't take up moves to *Paged* with the executor's pick as the default. The planner never folds one into `state.md` as settled, and never queues the executor's pick unasked.

**Reviewed reports.** Conductor tracks which reports the planner has acted on; the planner keeps no list of its own. Once the planner has acted on a report (said what it changes, flagged blockers, sent its carry through a request), it sends the signal `reviewed: <repo>/<NNN_name>` for that item (several lines may share a file, with `by: planner`). Until then the item is listed under **Unreviewed reports** at the top of every `inbox.md` (after open questions), whatever the read mark says, and in the app's Repos tab. Marking the inbox read never marks a report reviewed. `reviewed:` is refused for an item that hasn't finished, and does nothing (logging a warning) for one already reviewed. Matt can also mark one reviewed in the app. Conductor's own reports (`C:\Code\claude-conductor\reports\NN_name_report.md`, from prompts Matt pastes by hand) have no `reviewed` event. Their mark is the line `Last report reviewed by the planner: NN_name` in *Status* of conductor's `HANDOFF.md`: **process all** covers the ones numbered past it, and the planner then rewrites the line directly (see **Vocab**).

## state.md and closeout

- **The handoff docs are the active wavefront, not a history.** A line survives only if it would change a future decision and cannot live in code: undecided questions, caveats that would cause a misread, true-but-unenforceable facts. A mechanic lives in its repo's README; the docs point to it. One home per fact.
- **state.md is status, plan and flags** — what is queued, running, open, ruled — and holds no facts that belong elsewhere.
- **Waiting on Matt / Open questions / Paged.** Every item for Matt sits in exactly one of three `state.md` sections. *Waiting on Matt* holds only what queued or next work is blocked on, and is raised at every session start until resolved. *Open questions* are decisions the planner needs to author upcoming work; each is asked once, in the reply where it arises, and if Matt doesn't take it up it moves to *Paged* with the planner's default. *Paged* is everything else that is Matt's to do or decide whenever he gets to it: nothing waits on it, so the planner never asks about it, never lists it at session start or closeout, and never asks when. It comes up only when Matt raises it, or when a prompt already touching the same code can carry a paged polish item (said in one line).
- **Closeout** = the planner writes the final `state.md` (and any doc the session changed) as one request, conductor commits it, and the next session starts from it. No apply scripts, no files for Matt to place. "Prepare for closeout" = list open questions for Matt, items still expecting a report, and parked edits that should ride the next prompt.
- A mistake the planner repeats becomes one line in the project's docs; a one-off mistake is said in the chat and not carried.

**Park / resume a project.** Parking puts a project away whole (to free the box, or to move its folders), with nothing left running or half-done.

1. Nothing is in flight. No item is running, staged or merging, and no question is open. Cancel or let finish what's queued; anything still queued stays queued and runs on resume.
2. **Closeout** (above) as a request, **while the daemon still runs**: requests apply only through the daemon.
3. `run: <target> stop` for every run target that is up.
4. `stop: daemon` (**stop conductor**).
5. Matt clears what's left in `C:\Code\<project>-worktrees\`. A worktree's git link is an absolute path, so a worktree can't move with the folders. A kept red worktree gives its branch `conductor/<NNN_name>` to the repo, so deleting the folder loses no commit. A leftover that conductor couldn't remove (a dev server held it) goes too. The steps are in conductor's README, *Worktrees and merges*: delete the folder, then run `git worktree prune` in the repo.
6. In the app, **Remove project**. This only forgets the folder; nothing on disk changes.

The control folder and the code repos may then move, as a set, keeping their relative layout: `conductor.toml` names the repos and `worktree_root` by relative path. To resume: move them back (or keep the same layout elsewhere), start the daemon (`uv run conductor run <control folder>` in `C:\Code\claude-conductor`, or the app's Start), **Add project…** in the app, then **start a new <project> session**.

## Vocab

What Matt can say to the planner. The planner reads this at session start; "remind me the vocab" prints the table.

Queue actions are signals: the planner writes a small file into `signals\` with one `key: value` line per action (several lines may share a file), and conductor carries them out and logs an event for each. The planner never moves, renames or deletes a file in `queue\` or `proposed\` itself. `NNN_name` may be shortened to `NNN`; a queued item may carry its repo as `repo/NNN_name`. The exact rules for each verb are in conductor's `README.md`.

| Phrase | What the planner does |
|---|---|
| **start a new <project> session** | If folder access isn't granted yet, requests access to the control folder, the code repos and `C:\Code\gdrive-sync` first. Reads this file, then `C:\Code\<project>-control\state.md` and the control folder's other tracked docs. Signals `checkpoint: session start` (`by: planner`), then does **check inbox**. |
| **checkpoint** [label] | Signal `checkpoint: <label>` (`by: planner`) mid-session, when Matt asks: finished, reviewed items drop out of the current view. Its number is the event id `inbox.md` then names. |
| **check inbox** | Reads `inbox.md` (everything since the last read): what finished, what's red, what's asking. Leads with open questions, then the unreviewed reports. Says what changed, not a summary. Marks it read: signal `read: <id>`, the id printed at the top of `inbox.md`. Reading never marks a report reviewed. |
| **check reports** / **show reports** | One compact table and nothing else: every finished report not yet reviewed (item, status, finish time), oldest first, then every running or staged item with its elapsed time as "not in yet". In the same turn, the planner reviews each unreviewed report (as **process X**) and marks it reviewed. |
| **check reports verbose** | The same, plus a few lines per report on what it changes and anything blocking. |
| **process X** | Acts on report X: says what it changes, what the planner got wrong, and anything blocking; sends its carry through a request; then signals `reviewed: <repo>/<NNN_name>`. |
| **process next** | **process X** for the first unreviewed report in logical order (dependencies first, then finish order). |
| **process all** / **process all reports** / **review all** | **process X** for every unreviewed report, in logical order: dependencies first, then finish order. Then conductor's own reports: every `C:\Code\claude-conductor\reports\NN_name_report.md` whose prompt number is past the `Last report reviewed by the planner: NN_name` line in *Status* of `C:\Code\claude-conductor\HANDOFF.md`, in number order (hand-pasted prompts have no `reviewed` event, so that line is their mark). For each: what it changes for this project (vocab, front-matter, behaviour) and its *Deferred* calls, raised with Matt as for a cleanup. Afterwards the planner rewrites that line to the newest one processed, directly over the bridge: conductor's repo has no request step. |
| **mark reviewed X** | Signal `reviewed: <repo>/<NNN_name>` without processing, e.g. for a report Matt and the planner already went through. Refused if X hasn't finished. |
| **check questions** | Lists open asks in `questions\` (they also lead `inbox.md`): the asking item, which ones block, and for the others the default and when it is taken. Blocking ones come first. |
| **answer Q: …** | Writes Matt's words as plain text to `questions\<id>.answer.md` (Q may be the short form, e.g. `012__1`). It is never edited afterwards. Once the default has been taken, says so: the answer will be logged `late` and changes nothing, so if it matters it needs a new queue item. |
| **queue status** | Every repo: running item with its elapsed time and latest progress line (note, ETA, when sent), queued items in order, proposed items awaiting approval. |
| **ETA on X?** | X's latest progress line from `inbox.md` (*Now*) or its `progress` events: the ETA and when it was given. If X has posted none, says so. |
| **tell X: …** | Signal `tell: <repo>/<NNN_name> <Matt's words>` (one line, `by: planner`): a message to running item X, delivered after its next tool call (normally within a minute). The answer comes back as a progress line; the planner reports it on the next check. Refused if X isn't running. |
| **amend X: …** | Writes `queue\<repo>\NNN_name.amend.md` (`.amend.2.md`, … if one is already waiting): front-matter `by: planner`, then Matt's words, any length. Works while X is queued, staged or running. Then checks the `amended` event and tells Matt which delivery path applied from its state: appended to the prompt at launch (queued), added to the resume message with the stage kept (staged), or delivered after the next tool call (running). Refused for a merging or finished X (`amend_refused`; the file stays, so a correction becomes a new item). No event means the write didn't land. |
| **show events** [since X / for Y] | Stages `events.db` and queries it: a timeline, filtered by time, repo or item. "since checkpoint N" means events with id ≥ N (checkpoint N is event #N); plain "since checkpoint" means the latest, the one `inbox.md` names. |
| **postmortem Y** | Walks item Y's event trail, prompt and report, and says where it went wrong. |
| **propose** / **enqueue** | Writes a new prompt into `proposed\` (needs approval) or `queue\<repo>\` (Matt approved it here). |
| **approve X** | Signal `approve: NNN_name`. Conductor moves `proposed\NNN_name.md` into the queue of the repo its front-matter names. |
| **cancel X** | Signal `cancel: <item>` for a proposed or queued item. Conductor moves the file to `.conductor\cancelled\` (kept, not deleted). A running item cannot be cancelled; a staged one loses its stage. |
| **reorder R: X Y Z** | Signal `reorder: <repo or proposed> X Y Z`. The listed items swap their own numbers into the listed order; unlisted items keep theirs. Refused if any item is running, if something `depends_on` a renumbered item, if a renumbered item would come before an item it depends on, or if a new name is the id of an item that already ran. |
| **clear R** | Signal `clear: <repo>`: releases a red hold on that repo's queue, after the planner has read the red report with Matt. |
| **run X** / **run game** | Signal `run: X`: conductor starts target X if it isn't up, waits until it answers, and opens it in Matt's browser. With one target, plain "run game" (or "run it") means that one; with several and no match, the planner asks which. If no target exists yet, the planner proposes one as a `conductor.toml` request. On a `run_failed` event, it reports the error and the log tail. |
| **stop X** | Signal `run: X stop`: conductor kills run target X's process tree. Matt can also click **Stop X** in the app, shown beside **Run X** while X is up. |
| **pick X** | Signal `pick: <repo>/<manifest path>` for X's manifest (X is the item whose report has the `pick:` line, or the manifest's name): conductor opens the picker in Matt's browser. With one picker pending in the inbox's **Picks** section, plain "pick" means that one; with several, the planner asks which. |
| **read picks X** | Reads `picks\<name>.choices.json` for X and says what the chosen keys and notes change, row by row (unchanged rows in one line). The next prompt carries them, since the executor can't read the control folder. If Matt hasn't saved yet, says so. |
| **stop conductor** | Signal `stop: daemon`: the daemon exits cleanly; running executors carry on and are picked up when it restarts. Matt restarts it with `uv run conductor run <control folder>` or the app's Start button. |
| **ultracode X** | For a queued item X that hasn't started: rewrite its front-matter with `ultracode: true`. For a running X: says that it can't be switched mid-run, and offers the rest of the work as a new item with `ultracode: true`. |
| **with audit** / **audit X** | "with audit" on a new item: writes it with `audit: true`. "audit X" for a queued item X that hasn't started: rewrites its front-matter with `audit: true`. For a running or finished X: says it is too late, since audit notes are kept only from the start. |
| **audit notes** [R] | Lists the unused audit notes per repo (or for repo R): the repo's *Now* line in `inbox.md` names them as `audit pending: N audit notes (NNN_x, …)`, and each is `reports\<repo>__NNN_name.audit.md`. Read one only if Matt asks. |
| **cleanup R** | If R has unused audit notes: queues `template: cleanup`, `ultracode: true` for repo R (body empty, or Matt's extra instructions). If it has none, says so and queues nothing. |
| **general audit R** | Queues `template: general_audit`, `ultracode: true` for repo R. |
| **test perf audit R** | Queues `template: test_perf` for repo R. |
| **disk space audit R** | Queues `template: disk_audit` for repo R (body empty, or Matt's extra instructions). |
| **claude.md cleanup R** | Queues `template: claude_md_cleanup` for repo R. |
| **bug hunt R** [Nh] [focus] [notes only] | Queues `template: bug_hunt`, `ultracode: true` for repo R, with `max_hours` N + 1 (default N = 3: N is the hunt's target, the extra hour covers setup and report) and the focus, if any, as the body. "notes only" adds `readonly: true`: the hunt records findings in its report, changes nothing, and runs beside the repo's other items. |
| **park project** | Follows *Park / resume a project*: checks nothing is in flight, does **closeout** while the daemon runs, stops every run target, signals `stop: daemon`, then tells Matt which worktree folders are left to clear and to use **Remove project** in the app. |
| **resume project** | Tells Matt to start the daemon and **Add project…** in the app, then does **start a new <project> session**. |
| **context estimate** | Token estimate for this session; ~200k is the warn point. |
| **prepare for closeout** | Open questions for Matt (never paged items), items still expecting a report, parked edits that should ride the next prompt. |
| **closeout** | Writes the final `state.md` (and any changed doc) as one request; conductor commits it. |
| **read the workflow** | Re-reads `claude-ai-new-project.md` (in the conductor repo). |
| **remind me the vocab** | Prints this table. |
