# planner vocab — the rare ops

The rest of `planner.md`'s **Vocab**: read this file only when Matt uses one of these phrases (or **remind me the vocab**). A normal session (queue work, process reports, closeout) never needs it. `planner.md`'s rules and its table's note on item names apply here too.

| Phrase | What the planner does |
|---|---|
| **backlog X** | Only on Matt's word: his prompt as `backlog\<repo>\<name>.md` (queue front-matter, no number); drawn when that queue empties. |
| **show backlog** | The inbox's *Night shift*. |
| **watch now** | Signal `watch: now` (refused while one is live). |
| **autopilot off / night / on** | Signal `autopilot: off\|night\|always`: whether the night shift may start work itself (draws, a watch's included); the app's header shows it and what is next. |
| **fill until T** | Queues enough useful work to run until T (due maintenance, then backlog, then anything agreed with Matt), the last item finishing by T; says what it chose. |
| **stop conductor** | Signal `stop: daemon`; executors are adopted on restart. |
| **show events** [since X / for Y] | Stages `events.db` and queries it. "since checkpoint N": event id ≥ N; plain "since checkpoint": the latest. Kinds: `docs/events.md`. |
| **postmortem Y** | Y's events, prompt and report: where it went wrong. |
| **reorder R: X Y** | Only when Matt asks. Signal `reorder: <repo or proposed> X Y` (only the items that swap, listed in their **new** order; the current order is a no-op): numbers swap, each renamed item is a new id. Fix old names in `state.md`. |
| **push R** | Signal `push: <repo>` (retry; `push: control` for the control folder). |
| **park project** / **resume project** | *Park / resume* in `claude-ai-new-project.md`. |
