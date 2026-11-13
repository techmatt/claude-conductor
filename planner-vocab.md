# planner vocab — the rare ops

The rest of `planner.md`'s **Vocab**: read only when Matt uses one of these phrases (or **remind me the vocab**); a normal session never needs it. `planner.md`'s rules and its note on item names apply here too.

| Phrase | What the planner does |
|---|---|
| **backlog X** | Only on Matt's word: his prompt as `backlog\<repo>\<name>.md` (queue front-matter, no number); drawn when that queue empties. |
| **show backlog** | The inbox's *Night shift*. |
| **watch now** | Signal `watch: now` (refused while one is live). |
| **autopilot off / night / on** | Signal `autopilot: off\|night\|always`: whether the night shift may start work itself (draws, watches); the app's header shows it. |
| **fill until T** | Queues enough useful work to run until T (due maintenance, then backlog, then anything agreed with Matt), the last item finishing by T; says what it chose. |
| **stop conductor** | Signal `stop: daemon`; executors are adopted on restart (`restart: daemon`: in place). |
| **show events** [since X / for Y] | Stages `events.db` and queries it. "since checkpoint N": event id ≥ N; plain "since checkpoint": the latest. Kinds: `docs/events.md`. |
| **postmortem Y** | Y's events, prompt, report: what went wrong. |
| **reorder R: X Y** | Only when Matt asks. Signal `reorder: <repo or proposed> X Y` (only the items that swap, listed in their **new** order; the current order is a no-op): numbers swap, each renamed item is a new id. Fix old names in `state.md`. |
| **test cleanup R** / **claude.md cleanup R** / **disk space cleanup R** | Queues `template: test_cleanup` / `claude_md_cleanup` / `disk_cleanup` (body: Matt's extras). |
| **process all** / **review all** | Signal `review: all`; stage `review.md` once its header names that signal. **process X** for each report in order, then conductor's past the marker (what each changes here, *Deferred* calls ruled as for a cleanup); then rewrite the marker in `planner-notes.md`. |
| **defaults all** [except …] | Every open question but the exceptions takes its default: those needing work → one follow-up item, the rest → `state.md` rulings; flags any Matt should really look at. |
| **open X** / **show me X** | Signal `open: folder\|reveal\|file <path>` (a path Matt names or the planner just wrote; `text`: a script unrun; `url`: a page). **ping me**: `notify: <text>`. |
| **ultracode X** | Queued: rewrites its front-matter. Running: offers the rest as a new item with `ultracode: true`. |
| **retry X** | Signal `retry: <repo>/<NNN_name>`: a finished red item's kept session resumes in its kept worktree, then finishes and merges as usual, releasing its red's hold (`docs/api-retry.md`). For a red the API caused (`API error` reason) once it is back; an outage's API failure is retried by itself first. |
| **land X** | Signal `land: <repo>/<NNN_name>`: a finished red item's kept branch lands on `main` as one commit, releasing its red's hold. Only after its report is read with Matt. |
| **content cleanup R** [extras] | Queues `template: content_cleanup` (body: Matt's extras); never a maintenance default. |
| **post-audit X** [extras] | Queues `resume: <repo>/<NNN_name>`, `template: self_audit` (body: extras): X's session audits its own run, fixes the obvious in place and lands it (serial, waits behind a red hold), noting the rest for the next cleanup; its **About conductor** goes to `planner-notes.md`. |
| **art round X: ids** | Writes the candidate item: `art:alts` for those ids, a picker manifest under a new name, ending at `pick:`. |
| **review cards** / **tool X** | Signal `tool: <name>`: starts that `[tools.<name>]` if needed and opens it (`tool: <name> stop` stops it; `docs/app.md`, *Project tools*). |
| **use remote workers** | `[remote]` only: default since 105 under `[remote] default = true` (`remote: false` opts out), else `remote: true`; seeded batches via `farm-seeds` (`docs/remote-workers.md`). |
| **push R** | Signal `push: <repo>` (retry; `push: control` for the control folder). |
| **park project** / **resume project** | *Park / resume* in `claude-ai-new-project.md`. |
| **rename repo R to S** [at P] | Only when nothing of R is queued, staged or running: **stop conductor**, then tells Matt to run `uv run conductor rename-repo R S [--path P] --control <control>` in `C:\Code\claude-conductor` and start the daemon. History carries over; `state.md`'s prose about R is fixed by request. |
