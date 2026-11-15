Content cleanup of `{{repo}}` (project `{{project}}`). Check the content its audience
meets against who that audience is, then act on what you found. The content is whatever
the body below names: player-facing content (words, pictures, lines of text or audio) by
default, or docs (a docs trim: what the project's readers, people or agents, read).

1. Read who the audience is from the project's docs (README, design docs, CLAUDE.md):
   for players, age, reading level, language, setting; for docs, who reads each one
   and what for. If the docs don't say, use the closest they come and say so in the report.
2. Go through all that content, set by set. A *set* is a word list or other pool, a
   kind of line (prompts, dialogue, UI text, captions, voice lines), or a doc or section.
   Flag what is risky for that audience: ambiguous (two readings, a picture that could
   be something else), too rare or too hard, wrong for them (tone, topic, a regional or
   dated term), or, in docs, stale, duplicated, or in the wrong place.
   Content seen through the running app takes one browser at a time, a workflow or
   subagent's included; reading files and finished screenshots runs alongside.
3. Act on each finding: store it (move it out of play, keeping it where the repo keeps
   retired content, or beside the set if there is no such place; in docs, move it to the
   doc it belongs in, or cut it where git history keeps it), reword it, or flag it when
   neither fits. Keep each set workable: no pool drops below what the code or its docs
   need to run, a level or category keeps its spread, and a doc keeps every fact a
   reader needs. Run the tests and leave them green.
4. Defer every design-level call: a set that needs new content, a change to who it is
   for, a rule about what content may contain. Never decide one.

The report, after its header, has:
- **Moved**: each item stored, moved, cut, reworded or flagged, grouped by set, with the
  reason in a few words (reworded: old → new; moved: from → to).
- **Pools**: each set's size before and after (items in a pool, KB or lines in a doc).
- **Deferred**: each design call you did not make, with the options, which you would
  pick and why; mark a call that is Matt's own (taste, scope, a rules term) `(Matt's call)`.

The status is `done` when every finding was acted on or flagged (deferred design calls
do not make it partial), `partial` when content was left unchecked, and `red` only when
something is broken.
