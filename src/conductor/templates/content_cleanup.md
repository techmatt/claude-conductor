Content cleanup of `{{repo}}` (project `{{project}}`). Check every word, picture and line
of text or audio the user meets against who plays it, then act on what you found.

1. Read who plays it from the project's docs (README, design docs, CLAUDE.md): age,
   reading level, language, setting. If the docs don't say, use the closest they come
   and say so in the report.
2. Go through all user-facing content: word lists and other pools, prompts and
   dialogue, UI text, pictures and their captions, voice lines. Flag what is risky for
   that audience: ambiguous (two readings, a picture that could be something else),
   too rare or too hard, or wrong for them (tone, topic, a regional or dated term).
   Content seen through the running app takes one browser at a time, a workflow or
   subagent's included; reading files and finished screenshots runs alongside.
3. Act on each finding: store it (move it out of play, keeping it where the repo keeps
   retired content, or beside the pool if there is no such place), reword it, or flag it
   when neither fits. Keep each pool workable: no pool drops below what the code or
   its docs need to run, and a level or category keeps its spread. Run the tests and
   leave them green.
4. Defer every design-level call: a pool that needs new content, a change to who it is
   for, a rule about what content may contain. Never decide one.

The report, after its header, has:
- **Moved**: each item stored, reworded or flagged, grouped by pool, with the reason
  in a few words (reworded: old → new).
- **Pools**: each pool's size before and after.
- **Deferred**: each design call you did not make, with the options, which you would
  pick and why.

The status is `done` when every finding was acted on or flagged (deferred design calls
do not make it partial), `partial` when content was left unchecked, and `red` only when
something is broken.
