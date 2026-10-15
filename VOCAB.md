# VOCAB — what Matt can say to the planner

The planner reads this at session start. "remind me the vocab" prints the table.

| Phrase | What the planner does |
|---|---|
| **check inbox** | Reads `inbox.md` (everything since the last read): what finished, what's red, what's asking. Says what changed, not a summary. Marks it read. |
| **check questions** | Lists open asks in `questions\` (they also lead `inbox.md`): the asking item, which ones block, and for the others the default and when it is taken. Blocking ones come first. |
| **answer Q: …** | Writes Matt's words as plain text to `questions\<id>.answer.md` (Q may be the short form, e.g. `012__1`). It is never edited afterwards. Once the default has been taken, says so: the answer will be logged `late` and changes nothing, so if it matters it needs a new queue item. |
| **queue status** | Every repo: running item and elapsed time, queued items in order, proposed items awaiting approval. |
| **show events** [since X / for Y] | Stages `events.db` and queries it: a timeline, filtered by time, repo or item. |
| **postmortem Y** | Walks item Y's event trail, prompt and report, and says where it went wrong. |
| **propose** / **enqueue** | Writes a prompt into `proposed\` (needs approval) or `queue\` (Matt approved it here). |
| **approve / cancel / reorder** | Acts on `proposed\` and `queue\`. |
| **context estimate** | Token estimate for this session; ~150k is the warn point. |
| **prepare for closeout** | Open questions for Matt, items still expecting a report, parked edits that should ride the next prompt. |
| **closeout** | Writes the final `state.md` (and any changed doc) as one request; conductor commits it. |
| **read the workflow** | Re-reads `matt-controller-workflow.md`. |
| **remind me the vocab** | Prints this table. |
