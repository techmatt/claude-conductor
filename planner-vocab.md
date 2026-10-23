# planner vocab — the rarer phrases

The rest of `planner.md`'s **Vocab**: read this file only when Matt uses one of these phrases (or **remind me the vocab**). `planner.md`'s rules and its table's note on item names apply here too.

| Phrase | What the planner does |
|---|---|
| **show events** [since X / for Y] | Stages `events.db` and queries it. "since checkpoint N": event id ≥ N; plain "since checkpoint": the latest. Kinds: `docs/events.md`. |
| **postmortem Y** | Y's events, prompt and report: where it went wrong. |
| **reorder R: X Y** | Only when Matt asks. Signal `reorder: <repo or proposed> X Y` (only the items that swap, listed in their **new** order; the current order is a no-op): numbers swap, each renamed item is a new id. Fix old names in `state.md`. |
| **push R** | Signal `push: <repo>` (retry; `push: control` for the control folder). |
| **pick X** | Signal `pick: <repo>/<manifest path>` (X: the item whose report has `pick:`, or the manifest's name; one pending and plain "pick": that one). |
| **read picks X** | Reads `picks\<name>.choices.json` (per row `id`, `key`, `changed`, `note`): what changes, row by row, for the next prompt; not saved yet: says so. |
| **ultracode X** | Queued: rewrites its front-matter. Running: offers the rest as a new item with `ultracode: true`. |
| **alongside X** | `alongside: <its area>` on a new or queued X if *Concurrency*'s rule allows; else says why not. |
| **with audit** / **audit X** / **audit notes** [R] | `audit: true` on a new item or a queued X; a running X: signal `audit: <repo>/<NNN_name>` (conductor sends its audit text, never a hand-written `tell`) / lists unused notes per repo, reading one if asked. |
| **cleanup R** | With unused notes, queues `template: cleanup`, `ultracode: true` (body: Matt's extras); with none (it would hold its queue), says so. |
| **general cleanup R** | Queues `template: general_cleanup`, `ultracode: true`. |
| **test cleanup R** / **claude.md cleanup R** / **disk space audit R** | Queues `template: test_cleanup` / `claude_md_cleanup` / `disk_audit` (body: Matt's extras). |
| **bug hunt R** [Nh] [focus] [notes only] | Queues `template: bug_hunt`, `ultracode: true`, `max_hours` N + 1 (default N = 3), focus as body; "notes only" adds `readonly: true`. |
| **content audit R** [extras] | Queues `template: content_audit` (body: Matt's extras); never a maintenance default. |
| **art round X: ids** | Writes the candidate item: `art:alts` for those ids, a picker manifest under a new name, ending at `pick:`. Picks reach the next prompt to that repo, as usual. |
| **trim state** | One request rewriting `state.md` to the wavefront: keeps queued, open, paged and still-ruled; drops session histories and per-item lines whose facts live in the repo's docs. Durable rulings executors need ride the next prompt to that repo into its docs. |
| **fill until T** | Queues enough useful work to run until T (due maintenance, then backlog, then anything agreed with Matt), the last item finishing by T; says what it chose. |
| **ask X: …** | Queues `resume: <repo>/<NNN_name>`, Matt's words as body: X's own session, forked, answers as a readonly report (X must have finished). |
| **post-audit X** [extras] | Queues `resume: <repo>/<NNN_name>`, `template: self_audit` (body: extras): X's session audits its own run into notes for the next cleanup; its **About conductor** goes to `planner-notes.md`. |
| **park project** / **resume project** | *Park / resume* in `claude-ai-new-project.md`. |
| **pipeline review** | Over this session: unbuilt fixes saving calls, tokens, churn or a step of Matt's, with evidence; asks which to take. Approved conductor fixes → a conductor prompt; the rest → `state.md` (conductor: `planner-notes.md`). |
| **context estimate** | Estimates tokens. |
