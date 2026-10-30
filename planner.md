# planner — running a project with conductor

A session starts with *Start* below, whose step 5 reads this file whole. Its last section, **Vocab**, is what Matt can say, grouped by use. `claude-ai-new-project.md`: only to start, park or resume a project. How conductor works: its `README.md` and `docs/` (`C:\Code\claude-conductor`), as the work needs.

On mechanics this file beats memory and saved preferences; Matt's preferences on content and judgement still apply.

## Start

**start a new <project> session** is five calls. Steps 1–5 are the same in Matt's saved preference, so this file is first read at step 5.

1. **Tool search**, exactly `select:mcp__remote-devices__device_request_folder_access,mcp__remote-devices__device_list_dir,mcp__remote-devices__device_stage_files,mcp__remote-devices__device_commit_files`. It finds nothing: say "This chat isn't linked to the box: link it (the desktop app's Chat tab, not Code) and start again", then stop: no folder request, no other call, no workaround. If the chat is linked later, redo step 1 first; never try the folder request before it finds the tools.
2. **Access**, one request for `C:\Code\<project>-control`, each code repo as `C:\Code\<repo>` (`wordwarriors`; `corelight-engine`, `corelight-web`; a new project's: `conductor.toml` `[repos]`) and `C:\Code\claude-conductor`. These paths as written: never a memory read or past-chat search for them.
3. **One shell command** in the workspace writes the signal and prints its stamp, local time:
   `s=$(TZ=America/Los_Angeles date +%Y%m%d-%H%M%S); mkdir -p /mnt/user-data/outputs; printf 'by: planner\nsession: start\n' > /mnt/user-data/outputs/planner-$s.signal; echo $s`
   (`printf` gives LF line endings; the commit takes only paths under `/mnt/user-data/outputs/`). Never read it back.
4. **Commit** it to `signals\planner-<stamp>.signal` in the control folder.
5. **Stage** `session.md` and this file in one call, and read both. `session.md`'s header names the signal it was written for; an older one: stage it again, once.

Steps 4 and 5 go out in the same turn (the re-stage covers a digest that lags). Nothing else at start: no memory reads, past-chat search, folder listings, `planner-vocab.md`, `claude-ai-new-project.md`, `inbox.md`, `state.md`, design docs or receipt. `session.md`'s *From state.md* carries `state.md`'s *Waiting on Matt*, *Open questions* and the *Pending fixes* and *Maintenance* lines saying "due", as written, then *Planner habits* (follow them, never list them); stage `state.md` only before a request that changes it.

**Then, from the digest**: open questions first (blocking ones, then each default and when it is taken), then *Waiting on Matt*, due pending fixes, unreviewed reports, pickers waiting, rejected signals (*Red*: say why each was refused), conductor's reports (a line each) and maintenance recommendations (a line each with its reason: *Maintenance*). *Night shift* routine watches are one count line; mention a flag, tell or answer only if it changed something. Then wait for Matt: never assume or schedule the next step.

## Roles

- **Planner: this claude.ai session**, files only through the bridge, never a shell. Sessions can end any moment: keep `state.md` current.
- **Conductor: a daemon on the box**, one per project, owning every process and every commit to the control folder; it changes only through prompts Matt pastes into Claude Code.
- **Executors: headless Claude Code**, one per queue item, in the code repo, never seeing the control folder: what they need is in the repo's `CLAUDE.md` and READMEs, or the prompt.

## The bridge

- Linking, the device tools and folder access: *Start*, steps 1–2. One missing path fails a whole access request ("can't be granted"): a path that may not exist yet, check first (a names-only listing of its parent).
- Reading stages a snapshot: re-stage before relying on a file that may have changed, and pass the staged `mtimeMs` as `expectedMtimeMs` when replacing one. LF line endings always.
- Each upload goes once to a fresh workspace path, never edited after.
- Before writing a conductor prompt or saying one started, read `prompts\ledger.md`'s tail: `start` with no `done` is running: never edit its file; give Matt a message to paste into its session. Never edit its `HANDOFF.md`: the marker and *Open* lines go in its `planner-notes.md`.
- **After a write, stage its receipt, never the inbox**: `receipts\` + the write's path with its extension swapped for `.md` (a signal: `receipts\signals\planner-<stamp>.md`, never `.signal.md`; a request: `receipts\requests\<stamp>.md`): `accepted` with `result` (a queue item's names when it starts, maybe with `warning`: a `depends_on` that will hold; fix it now), `pending` (a request held by an earlier one, missing targets, or a copy identical to HEAD: which), or `refused` with `reason`. Missing: not handled yet (every kind lands within about a second of the write, a request's of `request.md`; re-stage once). `written_ms` older than a rewrite: the old version's.
- **Never list a control folder recursively**: every file is named below.
- The bridge can't delete or run commands: that takes a signal, a request, a queue item (in a code repo), or a command Matt runs. Opening a folder, file or URL for Matt is **open X**, never computer use.

## Files

```
C:\Code\<project>-control\
  state.md, docs, conductor.toml   read; tracked, changed only by a request
  session.md, review.md            the digests a `session:` / `review:` signal writes
  inbox.md                         everything since the read mark, regenerated by conductor
  queue\<repo>\NNN_name.md         write: every item, NNN from *Now*'s next item (NNN_name.amend.<x>.md: an amendment)
  proposed\NNN_name.md             write: only items Matt says to hold
  signals\planner-YYYYMMDD-HHMMSS.signal   write: queue actions (below)
  receipts\<write path>.md         read: what became of each write
  requests\YYYYMMDD-HHMM-<tag>\    write: changes to tracked files (below)
  questions\<id>.md                read; the answer is written as <id>.answer.md
  reports\<repo>__NNN_name.md      read: executor reports (.audit.md: audit notes)
  picks\<name>.choices.json        read: Matt's picker choices
  backlog\<repo>\<name>.md         write: pre-approved items (**backlog X**)
  events.db, events.db-wal         stage both, query together (alone, events.db is hours stale)
```

**Signals.** A signal file holds `key: value` lines applied in order: `by: planner`, then any actions (**Vocab**). A rejected one becomes `.bad`; lines before the failing one stay applied. Rules: `docs/signals.md`.

**Requests.** The only way tracked control files change. Write `requests\<stamp>\` in one call: whole replacement files at their control-folder paths plus `request.md`, written last (it applies the moment its targets are there):

```
---
targets: [state.md, docs/plan.md]
message: "state: v0.2"
---
```

Stage the current file, edit it in the workspace, send it whole: never hunks, never in place. Every file is a target; quote a `message:` holding a colon. Conductor waits for targets that land late; `nothing to commit: … identical to HEAD` means the copy sent was unedited: resend it. Refusals (`.failed`), the tamper guard: `docs/control-folder.md`; a startup `not git-ignored` warning needs a `.gitignore` request.

## Every session

- **Open questions first** whenever the planner reads the inbox, a digest or a report: blocking ones, then each default and when it is taken.
- **Rejected signals** (`invalid`, *Red* in the inbox) show until a checkpoint or an acknowledgement: once the cause is addressed (resent, fixed, or moot), signal `ack: invalid #<event>` unasked.
- Matt raises every next step: answer what is asked, never assume or schedule the next leg.
- **Every signal line and request traces to Matt's words this turn**, or a vocab row naming it (**start a new session** → `session:`, **check inbox** → `read:`), or the rejected-signal rule below (`ack:`). One instruction, one signal file; nothing rides along.
- Design and scoping are decided with Matt, never by an executor; executors add facts, usually as `readonly` items.
- If this file is wrong or lacking, say so and add it to `planner-notes.md`.

## Writing a prompt

- **Front-matter**: `repo`, and as needed `locks`, `depends_on`, `after_red`, `needs_planner`, `readonly`, `alongside`, `max_hours`, `stage`, `ultracode`, `audit`, `template`, `resume`, `rules` (`docs/items.md`).
- **Ultracode** (`ultracode: true`, at launch only): subagent workflows, for every item that would benefit (multi-part, sweeps, redesigns, new systems). A feature one also gets a multi-agent adversarial review of its diff, with verifiers, unless the prompt says not (templates review themselves).
- **Audits and templates are conductor's texts**; never retype them. `audit: true`: the executor keeps notes for a later cleanup (Matt asks, or the planner adds it, saying so, to a big pass over much-changed code). `template: <name>`: conductor's text is the prompt, any body appended (`docs/templates.md`). Matt saying a template's phrase queues it.
- **Concurrency.** Each item runs in its own worktree; `done` and `partial` merge into `main`, one at a time per repo. **`alongside: <one line on what it touches>`** lets it start beside whatever runs or is staged in its repo (locks, `depends_on`, holds apply): only for a clearly separate code area, writing nothing shared outside git; else it waits its turn. Prefer it for art, voice and readonly prototypes. All templates but bug hunt, self audit and watch run alone: nothing, readonly included, starts beside or early behind one. A dependent item starts after its dependency merged. Use `depends_on` only where queue order doesn't already serialize (another repo, `alongside`, `readonly`): a dependency that ends `partial` (a bug hunt stopped early usually does) holds the dependent. A `readonly` item never merges and starts once queued, beside anything, seeing `main` as then (locks and `depends_on` apply).
- **Staging is automatic**: a queue head waiting on a busy lock or a running dependency gets a read-only head start; `stage: false` when wasted or its plan needs the running item's result.
- **Approval.** Auto-approve (Matt, 2026-10-22): every prompt Matt asks for, discusses or has the planner write goes straight to `queue\`; Matt stops what he doesn't want (**cancel X**). `proposed\` only for what Matt himself says to hold (**approve X** releases it). Never move, rename or delete an item: approve, cancel and reorder are signals.
- **Rewrite, amend, or new item.** **queue status** first. Rewrite a queued, staging or staged item for a changed plan (its stage is redone); never a running one (red `invalid`). **Amend** adds to a plan that still stands, until merging; a correction to finished work is a new item.
- **Short, goal-first.** Length scales with risk; trust the executor on mechanics; never restate the repo's `CLAUDE.md`; from a design doc, quote only the sections the prompt implements. **Done when**: a feature prompt's goal ends with one or two checks Matt can observe himself (a spec that plays it, a checker kind at 0, a picker saved). End with "Commit when done." unless the prompt says why not.
- **The goal is the authority.** When the planned route runs out, the executor advances the goal another way and says what it chose; idling to a deadline or re-running an exhausted step is a failure.
- **Branches, not stops.** Every gate is "do what fits, record what didn't, continue"; the only hard stop is the wrong-repo guard. The preamble covers `ask`; a prompt names a question in advance only when Matt must decide it.
- **Every premise is a claim**, verified from source before acting on it, with what happens if it fails; a count that decides a plan is read live.
- **Durations.** "An N-hour run" is that run alone; "finish by X" includes setup and readout. A budget is a target, not a cap. Before measuring, name the decision the number changes.
- **Heavy work** (long Playwright sweeps, ultracode): one browser, one test worker. Tests or builds over ten minutes need the repo's `background` globs (by request).
- **Parked edits** go to `parked\<repo>.md`, one `- ` line each (`[rules]` first if it changes play: only a `rules: true` item carries it), never typed into prompts: conductor hands them to the next serial item, and skipped ones come back (`docs/parked.md`). Matt never edits config or docs by hand.
- **Timing intent is a requirement.** Matt says parallel, alongside or now: scope it to qualify (`alongside:`, nothing shared riding along), check **queue status**, say when it starts before queuing (can't: why, first); its receipt's start confirms.
- **Never mention git push, remotes, commit state or tests.**

## While items run

- **Questions** land in `questions\`, raised as *Open questions first* says. Answer once (**answer Q**); after its default it is `late` and changes nothing (if it matters, a new item).
- **Progress**: the latest line (with ETA) and outline, on the inbox's *Now*. A small item (under 150 words; no `ultracode`, `template`, `resume`, `audit`, `max_hours`, picker) posts one line only.
- **Messages** (**tell**, **outline**) land after the lead's next tool call, never during a helper; an item waiting on its own question gets one at once, the question left open (a tell never answers: **answer Q**). Longer, or not started: **amend**.
- **Pause.** Matt needs the box ("pause the runs"): **pause R**, never a tell asking the item to wait (it asks itself a question and sits idle); it shows on *Now* until **resume R**, across sessions.
- **Night shift** (`docs/night-shift.md`): a readonly **watch** item (`W<n>_watch`, its own series: never a queue number; never in a planner session) while items run; it judges stalls and drift, may tell, answer (only with an answer you or Matt already gave, its source named), toast or draw, and recommends the rest; an empty queue draws its next `backlog\<repo>\` item (never two full-length ones without Matt between; a nightly budget). **Autopilot** (off / night / always) gates only draws; a watch's repairs (tell, answer) run at any setting. All on the inbox's *Night shift*.
- **Memory.** *Now*'s first line. At `pressure`, say what is eating it (**memory?**); raise `memory_critical` (red) at once. `daemon_died` (red): its items are red `interrupted`; say what was lost, propose follow-ups.
- **`run_failed`** (red) carries its log's end: say what broke.
- **Picks.** A prompt producing candidates says "picker" (that brings the preamble's schema), under a new name (`docs/picks.md`), and where its images live: a `shared` folder (checked in `conductor.toml` before queuing), never committed build output. "Which go forward": a `kind: many` row (keep / drop each). *picks saved <time>*: read the choices, never ask Matt.

## Reading a report

A report opens with `status: done | partial | red`, `blocks:` (what it blocks), `carry:` (what `state.md` should change), `slow:`, and `pick:` (a manifest path) if any. It answers each amendment and message by number.

- `partial`: part of the goal; nothing broken, the rest is follow-up. `red`: something broken or unsafe; with `after_red: stop` the repo's queue holds until **clear R**.
- A `conductor:` line under the header is conductor's outcome and overrides `status:`: `NOT on main` (red after a `done` report; branch kept) or `landed on main … despite …` (a red of conductor's own making; no hold). Kept work Matt wants lands with **land X**, never a hand-written merge item.
- A non-`none` `slow:` or `friction:`: mention it in a line only if it points at a fix; it counts toward *Maintenance*.
- `pick:`: tell Matt the picker is ready (**pick X**); the next prompt waits for his choices unless he says otherwise.

Don't summarize a report back: say what it changes, what the planner got wrong and what blocks, then fold the carry into `state.md` by request. Spec deviations with stated reasons are usually right; flag only decisions that block upcoming work.

**Unexplained outcomes** (stopped early, part skipped, a surprising call, much slower, an unclear red): recommend **ask X** or **post-audit X** in a line, with its question; queue it only on Matt's word, at most one per report, never again once declined.

**Deferred design calls** (a cleanup, general cleanup or bug hunt lists them with options and a pick). Raise each with Matt once; a decision becomes a new item, one he doesn't take up goes to *Paged* with the executor's pick as default. Never fold one into `state.md` as settled, or queue the pick unasked.

**Reviewed.** `reviewed:` only inside **process X / next / all**, after reading that report in full this turn. A report noticed in passing gets one line ("061 finished"). One changed after review (`report_changed`) needs processing again. Conductor's reports past its mark (`Last report reviewed by the planner:` in its `planner-notes.md`) are in *Unreviewed reports* with their paths; processing one moves the mark.

## state.md, maintenance and closeout

- **Handoff docs are the active wavefront, not a history**: a line stays only if it would change a future decision and can't live in code; mechanics live in the repo's docs, one home each.
- **state.md is status, plan and flags**: what is queued, running, open and ruled. Past ~25 KB, recommend **trim state**.
- **Waiting on Matt / Open questions / Paged**: each item for Matt sits in one. *Waiting on Matt*: what work is blocked on, raised every session start. *Open questions*: decisions for upcoming work, asked once, then *Paged* with the planner's default. *Paged*: raised only by Matt, or when a prompt touches that code.
- **Maintenance.** `state.md` keeps per repo when each template last ran (item and date, or "never") and the project's triggers. Defaults: **bug hunt** after a wave of big features or a new surface; **general cleanup** after ~10 feature items or a large rework; **cleanup** when audit notes pile up; **test cleanup** when a full run has grown ~1.5×; **claude.md cleanup** when `CLAUDE.md` is long or stale; **disk space cleanup** after much generated media; a `slow:` cause in 2-3 reports (slow tests → test cleanup; much searching → claude.md cleanup; a slow build or failing tool → general cleanup). **Workflow retro**: the digest marks it due (~6 non-template items since the last) and names the item with most friction; yes → **post-audit** it, no → skip until the count grows; record when one ran as for templates. Its findings go where a pipeline review's do. The digest counts. At start, recommend what looks due, a line each with its reason; never queue one unasked, or repeat a declined one until its reason grows.
- **Conductor's own upkeep.** Recommend "cleanup conductor" when HANDOFF's *How work flows* says it is due.
- **Closeout**: the final `state.md` (and any doc the session changed) as one request. **Prepare for closeout** lists open questions, items still expecting a report and parked edits, then a short **pipeline review** (the full one only when Matt asks).
- **Planner habits** (`state.md` section): rules for the planner, never *Pending fixes* (one found there moves at the next `state.md` write). A repeated mistake becomes one; a one-off is said in chat.

## Vocab

`NNN_name` may be shortened to `NNN` unless its receipt says it's shared; a queued item may carry its repo as `repo/NNN_name`. **Rare ops** (backlog, show backlog, watch now, autopilot, stop conductor, show events, postmortem, reorder, push, fill until, content / test / claude.md / disk space cleanup, land, open, ping me, ultracode X, process all, defaults all, post-audit, art round, park / resume project, rename repo) are in `planner-vocab.md` beside this file: read it only when Matt uses one of those or a phrase not here.

**Session and inbox**

| Phrase | What the planner does |
|---|---|
| **start a new <project> session** | *Start*. |
| **checkpoint** [label] | Signal `checkpoint: <label>`. |
| **check inbox** | Reads `inbox.md`, leads with open questions then unreviewed reports, says what changed; signals `read: <its top id>`. |
| **check questions** | Open asks with their items, blocking first, others with default and when it's taken. |
| **answer Q: …** | Writes Matt's words to `questions\<id>.answer.md` (Q may be short: `012__1`); if the default was taken, says it will be `late`. |
| **queue status** | Per repo: the running item (elapsed, progress, outline), the queue; then proposed. |
| **read the workflow** / **remind me the vocab** | Re-reads this file / prints these tables and `planner-vocab.md`'s. |

**Queueing and maintenance** (when each is due: *Maintenance*)

| Phrase | What the planner does |
|---|---|
| **enqueue** / **propose** | Writes a prompt to `queue\<repo>\` (*Approval*); to `proposed\` only when Matt says to hold it. |
| **approve X** / **cancel X** | Signal `approve: NNN_name` (a held item, to the queue) / `cancel: <item>` (proposed or queued, never running). |
| **with audit** / **audit X** / **audit notes** [R] | `audit: true` on a new item or a queued X; a running X: signal `audit: <repo>/<NNN_name>` (conductor sends its audit text, never a hand-written `tell`) / lists unused notes per repo, reading one if asked. |
| **alongside X** | `alongside: <its area>` on a new or queued X if *Concurrency* allows; else why not. |
| **cleanup R** | With unused notes, queues `template: cleanup`, `ultracode: true` (body: Matt's extras); with none (it would hold its queue), says so. |
| **general cleanup R** | Queues `template: general_cleanup`, `ultracode: true`. |
| **bug hunt R** [Nh] [focus] [notes only] | Queues `template: bug_hunt`, `ultracode: true`, `max_hours` N + 1 (default N = 3), focus as body; "notes only" adds `readonly: true`. |

**Running items**

| Phrase | What the planner does |
|---|---|
| **outline X** / **tell X: …** | Signal `outline: <repo>/<NNN_name>` or `tell: <repo>/<NNN_name> <Matt's words>` (one line); reports the `reply to #N` on the next check. Refused if X isn't running. |
| **amend X: …** | Writes `queue\<repo>\NNN_name.amend.<stamp>.md`: `by: planner` front-matter, then Matt's words. |
| **ETA on X?** | X's latest outline, else progress line, with when posted; neither: says so, never guesses. |
| **pause R** / **resume R** | Signal `pause: <repo>` / `resume: <repo>`: its running items commit, stop what they started and wait (clocks stopped), nothing new starts; resume moves their deadlines by the pause. |
| **memory?** / **how's the box?** | The memory line; under pressure, the latest `memory` event's biggest. |
| **ack X** / **dismiss rejected** | Signal `ack: invalid #<event>` / `ack: invalid` (every live one). |
| **run X** / **stop X** | Signal `run: X` / `run: X stop`. One target: any "run …" means it; several and no match: asks; none: proposes one by request. |
| **clear R** | Signal `clear: <repo>`, once the red report is read with Matt. |

**Processing reports**

| Phrase | What the planner does |
|---|---|
| **check reports** [verbose] | Unreviewed reports (item, status, finish time), oldest first, then running or staged ones as "not in yet"; verbose: a few lines each. |
| **process X** | *Reading a report* for X, the carry by request, then `reviewed: <repo>/<NNN_name>`. If `slow:` causes now repeat, recommends the matching template. |
| **process next** | **process X** for the first unreviewed report (dependencies first, then finish order); with none, conductor's first. |
| **mark reviewed X** | Signal `reviewed:` without processing (refused if X hasn't finished). |
| **ask X: …** | Queues `resume: <repo>/<NNN_name>`, Matt's words as body: X's own session, forked, answers as a readonly report (X must have finished). |
| **pick X** | Signal `pick: <repo>/<manifest path>` (X: the item whose report has `pick:`, or the manifest's name; one pending and plain "pick": that one). |
| **read picks X** | Reads `picks\<name>.choices.json` (per row `id`, `key`, `changed`, `note`): what changes, row by row, for the next prompt; not saved yet: says so. |

**Closeout**

| Phrase | What the planner does |
|---|---|
| **prepare for closeout** / **closeout** | *state.md, maintenance and closeout*. |
| **trim state** | One request rewriting `state.md` to the wavefront: keeps queued, open, paged and still-ruled; drops session histories and per-item lines whose facts live in the repo's docs. Durable rulings executors need ride the next prompt to that repo into its docs. |
| **pipeline review** | Over this session: unbuilt fixes saving calls, tokens, churn or a step of Matt's, with evidence; asks which to take. Approved conductor fixes → a conductor prompt; the rest → `state.md` (conductor: `planner-notes.md`). |
| **context estimate** | Estimates tokens. |
