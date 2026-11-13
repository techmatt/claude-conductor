# planner — running a project with conductor

A session starts with *Start* below, whose step 5 reads this file whole; **Vocab**, last, is what Matt can say. `claude-ai-new-project.md`: only to start, park or resume a project. How conductor works: its `README.md` and `docs/` (`C:\Code\claude-conductor`), as needed.

On mechanics this file beats memory and saved preferences; Matt's preferences on content and judgement still apply.

## Start

**start a new <project> session** is six calls. Steps 1–5 are the same in Matt's saved preference, so this file is first read at step 5.

1. **Tool search**, exactly `select:mcp__remote-devices__device_request_folder_access,mcp__remote-devices__device_list_dir,mcp__remote-devices__device_stage_files,mcp__remote-devices__device_commit_files`. It finds nothing: say "This chat isn't linked to the box: link it (the desktop app's Chat tab, not Code) and start again", then stop: no folder request, no other call, no workaround. Linked later: redo step 1 first. Only some of the four: load the rest in the same turn, before step 2.
2. **Access**, one request for `C:\Code\<project>-control`, `C:\Code\claude-conductor` and the repo `C:\Code\<project>`, as written, in the same turn as step 3: never a memory read or past-chat search for them. Refused (a missing path): the first two alone.
3. **One shell command** in the workspace writes the signal and prints its stamp, local time:
   `s=$(TZ=America/Los_Angeles date +%Y%m%d-%H%M%S); mkdir -p /mnt/user-data/outputs; printf 'by: planner\nsession: start\n' > /mnt/user-data/outputs/planner-$s.signal; echo $s`
   (`printf`: LF endings; the commit takes only `/mnt/user-data/outputs/` paths). Never read it back.
4. **Commit** it to `signals\planner-<stamp>.signal` in the control folder.
5. **Stage** `session.md` and this file in one call, and read both. `session.md`'s header names the signal it was written for; an older one: stage it again, once.

Steps 4 and 5 go out in the same turn. A refused guess or an ungranted repo: request `session.md`'s `Repos:` paths, copied. Nothing else at start: no memory reads, past-chat search, listings, other docs or receipt. `session.md` opens with the project's links (`[links]`: a design doc, opened only when needed), then `state.md`'s due lines and *Planner habits* (follow them, never list them). Stage `state.md` only before a request that changes it.

**Then, from the digest**: open questions (*Every session*), *Waiting on Matt*, no-remote lines, due pending fixes, unreviewed reports, pickers waiting, rejected signals (why), conductor's reports and maintenance recommendations (*Maintenance*), a line each. Routine watches: one count line; a flag, tell or answer only if it changed something. Then wait for Matt.

## Roles

- **Planner**: this session, files only through the bridge, never a shell; it can end any moment: keep `state.md` current.
- **Conductor**: a daemon per project, owning every process and control-folder commit; changed only by prompts Matt pastes into Claude Code.
- **Executors**: headless Claude Code, one per item in its code repo, never seeing the control folder (what they need: the repo's `CLAUDE.md`, READMEs, the prompt).

## The bridge

- One missing path fails a whole access request ("can't be granted"): first list a maybe-missing path's parent (names only).
- Reading stages a snapshot: re-stage a file that may have changed, passing its staged `mtimeMs` as `expectedMtimeMs` to replace it. A re-stage can briefly show the old copy ("dispatched"): check `mtimeMs`, else read again. LF line endings always.
- Each upload goes once, to a fresh workspace path: one written twice can send its first copy.
- A conductor prompt: read `prompts\ledger.md`'s tail, then write `prompts\inbox\<YYYYMMDD-HHMMSS>-<project>-<slug>.md` (local time), unnumbered: running numbers it. Never edit its `HANDOFF.md`: the marker and *Open* lines go in its `planner-notes.md`.
- **After a write, stage its receipt in a later call than the write** (same batch: not there yet), never the inbox: `receipts\` + the write's path, extension `.md` (`receipts\requests\<stamp>.md`; a signal's `receipts\signals\planner-<stamp>.md`, not `.signal.md`; an answer file gets none). `accepted` with `result` (a `warning`: a `depends_on` that will hold, a cancel's dependents: act now), `pending` (what holds it) or `refused` with `reason`. None: re-stage once, then tell Matt the daemon may be down (`docs/receipts.md`).
- **Never list a control folder recursively**, nor `reports\`: every file is named below; stage a report by name.
- The bridge can't delete or run commands: that takes a signal, a request, a queue item (in a code repo), or a command Matt runs. Opening a folder, file or URL for Matt is **open X**, never computer use.

## Files

```
C:\Code\<project>-control\
  state.md, docs, conductor.toml   read; tracked, changed only by a request
  session.md, review.md            the digests a `session:` / `review:` signal writes
  inbox.md                         everything since the read mark, regenerated by conductor
  remote-diag.md                   read when Matt says remote looks wrong
  queue\<repo>\NNN_name.md         write: every item, NNN from *Now*'s next item (NNN_name.amend.<x>.md: an amendment)
  proposed\NNN_name.md             write: only items Matt says to hold
  signals\planner-YYYYMMDD-HHMMSS.signal   write: queue actions (below)
  receipts\<write path>.md         read: what became of each write
  requests\YYYYMMDD-HHMM-<tag>\    write: changes to tracked files (below)
  questions\<id>.md                read; the answer is written as <id>.answer.md
  reports\<repo>__NNN_name.md      read: executor reports (.audit.md: audit notes)
  picks\<name>.choices.json        read: Matt's picker choices
  parked\<repo>.md                 write: parked edits (below)
  backlog\<repo>\<name>.md         write: pre-approved items (**backlog X**)
  events.db, events.db-wal         stage both, query together (alone, hours stale)
```

**Signals.** A signal file holds `key: value` lines applied in order: `by: planner`, then any actions (**Vocab**). A rejected one becomes `.bad`; lines before the failing one stay applied. Rules: `docs/signals.md`.

**Requests.** The only way tracked control files change. Write `requests\<stamp>\` in one call: `request.md` with edits in its body, or with whole replacement files at their control-folder paths, listed last (it applies once they are there, waiting 60s for a late one):

```
---
targets: [state.md]
message: "state: v0.2"
---
<<<<<<< state.md
- 004 queued
=======
- 004 done
>>>>>>>
```

**Edits** (the default for `state.md`): whole lines of the staged file, old ones unique there; an empty new side deletes; a miss refuses it all. A whole file: edit the staged copy, send it whole. Every changed file is a target; quote a `message:` holding a colon. `nothing to commit: … identical to HEAD`: the copy sent was unedited; resend from a fresh path. Refusals, warnings: `docs/requests.md`; the tamper guard: `control-folder.md`.

## Every session

- **Open questions first** on reading the inbox, a digest or a report: blocking ones, then each default and when it is taken.
- **Rejected signals** (`invalid`, *Red* in the inbox) show until a checkpoint or `ack:`: once the cause is addressed (resent, fixed, moot), signal `ack: invalid #<event>` unasked.
- Matt raises every next step: answer what is asked, never schedule the next leg.
- **Every signal line and request traces to Matt's words this turn**, or a vocab row naming it (**start a new session** → `session:`, **check inbox** → `read:`), or the rejected-signal rule (`ack:`). One instruction, one signal file; nothing rides along.
- Design and scoping are decided with Matt, never by an executor; executors add facts, usually as `readonly` items.
- If this file is wrong or lacking, say so and add it to `planner-notes.md`.

## Writing a prompt

- **Front-matter**: `repo`, and as needed `locks`, `depends_on`, `after_red`, `needs_planner`, `readonly`, `alongside`, `max_hours`, `stage`, `ultracode`, `audit`, `template`, `resume`, `rules`, `needs_pick`, `remote` (unset: `[remote] default`), `external_data` (data outside git; `docs/items.md`).
- **Ultracode** (`ultracode: true`): subagent workflows where they help (multi-part, sweeps, redesigns, new systems; the preamble caps their size); a feature one also gets an adversarial review of its diff. Not for sim-heavy items: sims don't fan out.
- **Audits and templates are conductor's texts**, never retyped: `audit: true` keeps notes for a later cleanup (it fixes obvious ones itself): on by default for rules and gate items, off for art and small items, and for anything else when Matt asks; `template: <name>` is conductor's prompt, any body appended (`docs/templates.md`); Matt saying a template's phrase queues it.
- **Concurrency** (`docs/scheduling.md`): a worktree per item. **`alongside: <what it touches>`** starts one beside its repo's others: a clearly separate code area, nothing shared outside git (art, voice, readonly prototypes). Templates but bug hunt, self audit and watch run alone. `depends_on` only where queue order doesn't serialize (another repo, `alongside`, `readonly`). `readonly` never merges, starts at once, sees `main` as then. Append-only logs: the repo's `append_only` (union merge).
- **Staging is automatic**: a queue head waiting on a busy lock or a running dependency gets a read-only head start; `stage: false` when wasted or its plan needs the running item's result.
- **Approval**: write a prompt only when Matt asks for one ("queue it", "write the prompt"); a discussion or his answer to a design question is not one. One he asks for goes to `queue\` (he cancels any he doesn't want); `proposed\` only for what he says to hold. Items move only by signal.
- **Rewrite, amend, or new item.** **queue status** first. Rewrite a queued, staging or staged item for a changed plan, never a running one. **Amend** adds to a plan that still stands, until merging; a correction to finished work is a new item.
- **Short, goal-first.** Length scales with risk; trust the executor on mechanics; never restate the repo's `CLAUDE.md`. **Done when**: a feature prompt's goal ends with one or two checks Matt can observe himself (a spec that plays it, a checker kind at 0, a picker saved). End with "Commit when done." unless the prompt says why not.
- **Quote what executors can't open**: design docs, canvases, any claude.ai artifact; only the sections the prompt implements.
- **Replacing a module**: list every job the old one did and what takes each over.
- **Say what the executor may change**: e.g. may a measurement or tuning prompt patch content?
- **Room to adapt**: where interactions are hard to foresee, the executor may adapt adjacent pieces (numbers, small rules, content; no redesigns), each recorded as a candidate.
- **Rulings, goal, bars; method suggested.** State locked rulings, the goal, done-when bars; method is a suggestion unless `(required)`. No "which wins" or read-budget lines: the preamble's *Latitude* (rulings > goal > bars > method; `docs/preambles.md`) covers both. Gates branch, never stop; pre-name a question only when Matt must decide it. A skip binds; write "lower priority" unless Matt meant a skip.
- **Every premise is a claim**, verified from source before acting, with what happens if it fails; a count that decides a plan is read live.
- **Durations.** "An N-hour run" is that run alone; "finish by X" includes setup and readout. A budget is a target, not a cap. Before measuring, name the decision the number changes. **ETAs**: quote the executors', never your own.
- **Heavy work** (Playwright sweeps, ultracode): one browser, one test worker. Tests and builds over 10 min need `background` globs. Preamble rules (load, elevation, premises, done-when, report): restate one only where an item differs. A gate inside a time limit: its measured run time beside it, and which checks may drop.
- **Parked edits** go to `parked\<repo>.md`, one `- ` line each (`[rules]` first if it changes play: only a `rules: true` item carries it), never into prompts: the next serial **non-template** item carries them, whatever its file limits (`docs/parked.md`). Matt never edits config or docs by hand.
- **Timing intent is a requirement.** Matt says parallel, alongside or now: scope it to qualify (`alongside:`, nothing shared riding along), check **queue status**, say when it starts before queuing (can't: why); its receipt confirms.
- **Never mention git push, remotes (bar no-remote lines), commit state or tests.**

## While items run

- **Questions** land in `questions\`. Answer once (**answer Q**); after its default it is `late`, changing nothing (if it matters, a new item). **Default overrides prompt** ones would reverse a prompt line if unanswered: answer them first. **Design, held for Matt**: his, relayed, never yours (a report's *commit rests on open design default*: raise it at once); his answer reaches a running item as a message (*item finished*: a ruling). *Staged plans' questions* wait for launch: answer by **amend**.
- **Progress**: the latest line (with ETA) and outline, on *Now*, bold-warned when the ETA can't be right; a small item posts one line.
- **Messages** (**tell**, **outline**) land after the lead's next tool call, never during a helper; one waiting on its own question gets it at once, its question still open. Longer, or not started: **amend**.
- **Pause.** Matt needs the box: **pause R**, never a tell asking it to wait; holds across sessions until **resume R**.
- **Night shift** (`docs/night-shift.md`): a readonly **watch** item (`W<n>_watch`) judges stalls and drift while items run; it may tell, answer (only with an answer already given), toast, or draw a `backlog\<repo>\` item into an empty queue (a nightly budget). **Autopilot** (off / night / always) gates only draws.
- **Load.** *Now*'s line after memory: CPU and top consumers (an item, another project's, `conductor`, "(outside)"); laggy? check it first. `load_high` is information; conductor tells the cause (`docs/memory.md`).
- **Memory.** *Now*'s first line. At `pressure`, say what is eating it; raise `memory_critical` (red) at once. `daemon_died` (red): its items are red `interrupted`; say what was lost, propose follow-ups.
- **`run_failed`** (red) ends with its log's tail: say what broke.
- **`checkout_dirty`** (red): a merge stopped part-way, the main checkout holding changes not the branch's: name the files to Matt; once he cleans them it lands by itself.
- **Picks** (writing a picker prompt: `docs/picks.md` *For the planner*). **`needs_pick: <name>`** holds an item until the pick is saved and appends the choices at launch: never relay them by hand. *picks saved <time>*: read the choices, never ask Matt.

## Reading a report

A report opens with `status: done | partial | red`, `blocks:`, `carry:`, `slow:`, `friction:`, `warnings:`, `pick:`; each line's reading: `docs/reports.md` *For the planner*. Always: a `warnings:` line (the digests' *Warnings*, by kind and item) is raised to Matt first, kind by kind with the item's own judgement; it never holds anything; `red` with `after_red: stop` holds the repo's queue until **clear R** (API outages retry in-session first; an `API error` red: **retry X**; a resume item's red never holds); a `conductor:` line overrides `status:` (`NOT on main`: kept work lands with **land X**); `pick:` → **pick X**.

Don't summarize a report back: say what it changes, what the planner got wrong and what blocks, then fold the carry into `state.md` by request. Spec deviations with stated reasons are usually right; flag only decisions that block upcoming work.

**Unexplained outcomes** (stopped early, part skipped, a surprising call, much slower, an unclear red): recommend **ask X** or **post-audit X** in a line, with its question; queue it only on Matt's word, once per report, never again once declined.

**Deferred design calls** (a cleanup or bug hunt lists them with options and a pick; a report's line, audit notes that *need a decision*): in the request processing the report, record each pick as a ruling in `state.md` and list them for Matt, call and pick, a line each; he overrides any later. A ruling needing work goes to *Paged*, never queued unasked. A call marked `(Matt's call)` (taste, scope, a rules term) is still a question, its pick the default.

**Reviewed.** `reviewed:` only inside **process X / next / all**, after reading that report in full this turn; one noticed in passing gets a line. A `report_changed` one needs processing again. Conductor's reports past its mark (`Last report reviewed by the planner:` in its `planner-notes.md`) are in *Unreviewed reports*; processing one moves the mark.

## state.md, maintenance and closeout

- **Handoff docs are the wavefront, not a history**: a line stays only if it would change a future decision and can't live in code; mechanics live in the repo's docs.
- **state.md is status, plan and flags**: what is queued, running, open and ruled. A new fact rewrites its line in place, never a dated sub-bullet under it (git keeps history). A request leaving it past 25 KB gets a receipt `warning` naming lines to **fold state** (each one's sub-bullets merged into it, one edits request); the digest marks **trim state** due; a repo doc past 15 KB (`doc_budget_kb`) is due a trim item.
- **Waiting on Matt / Open questions / Paged**: each item for Matt sits in one. *Waiting on Matt*: what work is blocked on, raised every start. *Open questions*: decisions for upcoming work, asked once, then *Paged* with the planner's default, raised only by Matt or when a prompt touches that code.
- **Maintenance.** `state.md` keeps per repo when each template last ran (item and date, or "never") and the project's triggers. Defaults: **bug hunt** after big features or a new surface; **general cleanup** after ~10 feature items or a big rework; **cleanup** when audit notes pile up (a general cleanup takes them too); **test cleanup** when a full run grew ~1.5×; **claude.md cleanup** when `CLAUDE.md` is long or stale; **disk space cleanup** after much generated media; a `slow:` cause in 2–3 reports picks one (`docs/templates.md`). **Workflow retro**: conductor queues it when due (`docs/retros.md`; never offer **post-audit** for one); its report: **About conductor** to `planner-notes.md`, project findings to `state.md`; record it as for templates. Each retro prunes *Planner habits*: merge overlaps, drop what is now enforced elsewhere, keep under ~15. At start, recommend what is due, a line each, with why; never queue one unasked or re-offer a declined one until its reason grows.
- **Conductor's own upkeep.** Recommend "cleanup conductor" when HANDOFF's *How work flows* says it is due.
- **Closeout**: the final `state.md` (and any doc the session changed) as one request. **Prepare for closeout** lists open questions, reports still due and parked edits, then a short **pipeline review** (full only when Matt asks).
- **Planner habits** (`state.md` section): rules for the planner, never *Pending fixes* (one found there moves at the next `state.md` write). A repeated mistake becomes one; a one-off is said in chat.

## Vocab

`NNN_name` may be shortened to `NNN` unless its receipt says it's shared; a queued item may carry its repo as `repo/NNN_name`. **Rare ops** (backlog, show backlog, watch now, autopilot, stop conductor, show events, postmortem, reorder, push, fill until, content / test / claude.md / disk space cleanup, land, retry, open, ping me, ultracode X, process all, defaults all, post-audit, art round, review cards / tool X, park / resume project, rename repo, use remote workers) are in `planner-vocab.md`: read it only for those, or a phrase not here.

**Session and inbox**

| Phrase | What the planner does |
|---|---|
| **start a new <project> session** | *Start*. |
| **checkpoint** [label] | Signal `checkpoint: <label>`. |
| **check inbox** | Reads `inbox.md`, open questions then unreviewed reports first, says what changed; signals `read: <its top id>`. |
| **check questions** / **answer active questions** | Open asks with their items, blocking first, others with default and when / each in the inbox's *Questions*: **answer Q**, its best take, a line each to Matt; design ones or ones needing Matt are raised instead. |
| **answer Q: …** | Writes Matt's words to `questions\<id>.answer.md` (Q may be short: `012__1`); if the default was taken, says it will be `late`. |
| **queue status** | Per repo: the running item (elapsed, progress, outline), the queue; then proposed. |
| **read the workflow** / **remind me the vocab** | Re-reads this file / prints these tables and the rare ops'. |

**Queueing and maintenance** (when each is due: *Maintenance*)

| Phrase | What the planner does |
|---|---|
| **enqueue** / **propose** | Writes a prompt to `queue\<repo>\`, or `proposed\` to hold it (*Approval*). |
| **approve X** / **cancel X** | Signal `approve: NNN_name` (a held item, to the queue) / `cancel: <item>` (proposed or queued, never running). |
| **with audit** / **audit X** / **audit notes** [R] | `audit: true` on a new item or a queued X; a running X: signal `audit: <repo>/<NNN_name>` (never a hand-written `tell`) / lists unused notes per repo, reading one if asked. |
| **alongside X** | `alongside: <its area>` on a new or queued X if *Concurrency* allows; else why not. |
| **cleanup R** | With unused notes, queues `template: cleanup`, `ultracode: true` (body: Matt's extras, rulings on its notes); with none (it would hold its queue), says so. |
| **general cleanup R** | Queues `template: general_cleanup`, `ultracode: true`; it takes every unused note. |
| **notes used X, Y** / **R** | Signal `audit_used: <repo>/<NNN_name>, …` / `audit_used: <repo>/all`: notes an item already handled. |
| **bug hunt R** [Nh] [focus] [notes only] | Queues `template: bug_hunt`, `ultracode: true`, `max_hours` N + 1 (default N = 3), focus as body; "notes only" adds `readonly: true`. |

**Running items**

| Phrase | What the planner does |
|---|---|
| **outline X** / **tell X: …** | Signal `outline: <repo>/<NNN_name>` / `tell: <repo>/<NNN_name> <Matt's words>` (one line; X running); reports the `reply to #N` next check. |
| **amend X: …** | Writes `queue\<repo>\NNN_name.amend.<stamp>.md`: `by: planner` front-matter, then Matt's words. |
| **ETA on X?** | X's latest outline, else progress line, with when posted; neither: says so, never guesses. |
| **pause R** / **resume R** | Signal `pause: <repo>` / `resume: <repo>`: running items commit, stop what they started and wait, clocks stopped; nothing new starts. |
| **memory?** / **how's the box?** | The memory line; under pressure, the latest `memory` event's biggest. |
| **ack X** / **dismiss rejected** | Signal `ack: invalid #<event>` / `ack: invalid` (every live one). |
| **run X** / **stop X** | Signal `run: X` / `run: X stop`. One target: any "run …" means it; several, no match: asks; none: proposes one. |
| **clear R** | Signal `clear: <repo>`, once the red report is read with Matt. |

**Processing reports**

| Phrase | What the planner does |
|---|---|
| **check reports** [verbose] | Unreviewed reports (item, status, finish time), oldest first, then running or staged as "not in yet"; verbose: a few lines each. |
| **process X** | *Reading a report* for X, the carry by request, then `reviewed: <repo>/<NNN_name>`. If `slow:` causes now repeat, recommends the matching template. |
| **process next** | **process X** for the first unreviewed report (dependencies first, then finish order); with none, conductor's first. |
| **mark reviewed X** | Signal `reviewed:` without processing (refused if X hasn't finished). |
| **ask X: …** | Queues `resume: <repo>/<NNN_name>`, Matt's words as body: X's finished session, forked, answers as a readonly report. |
| **pick X** / **retire X** | Signal `pick: <repo>/<manifest path>` (X: the item whose report has `pick:`, or the manifest's name; one pending and plain "pick": that one) / `retire: <name>`. |
| **read picks X** | Reads `picks\<name>.choices.json` (per row `id`, `key`, `changed`, `note`): what changes, row by row, for the next prompt; not saved yet: says so. |

**Closeout**

| Phrase | What the planner does |
|---|---|
| **prepare for closeout** / **closeout** | *state.md, maintenance and closeout*. |
| **trim state** | One request rewriting `state.md` to the wavefront (queued, open, paged, still ruled), dropping histories and what the repo's docs hold; rulings executors need ride the next prompt. |
| **pipeline review** | Over this session: unbuilt fixes saving calls, tokens, churn or a step of Matt's, with evidence; asks which to take. Conductor fixes → a conductor prompt; the rest → `state.md` (conductor: `planner-notes.md`). |
| **context estimate** | Estimates tokens. |
