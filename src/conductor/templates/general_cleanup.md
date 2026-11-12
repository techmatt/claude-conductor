General cleanup of `{{repo}}`. Audit the whole repo yourself, then act on what you found.
Any audit notes earlier items left are at the very end of this text, oldest first.

1. Audit. Read the repo end to end: layout, code, tests, CLAUDE.md, README and other
   docs, and any earlier notes, checking each against the code as it is now (some may be
   fixed, contradict each other, or be wrong); what holds is a finding of yours. Write what you find to the audit notes file described above, under its five
   headings, as you go: what is poorly structured, what would slow the next person down
   and why (time, tokens, context), what the earlier notes and your own audit had to
   re-read or re-derive, what should be refactored or cleaned up, and what CLAUDE.md or
   README should say. Concrete notes with file names, not impressions.
2. Carry out whatever is clear and well defined: refactors, restructuring, dead code,
   naming, CLAUDE.md and README improvements; for each re-read or re-derived note, add
   the doc line, index or script that would have cached it. Keep behaviour unchanged unless you find
   a plain bug. Run the tests after each substantial step and leave them green.
3. Defer every design-level call: anything that changes what the code does for its
   users, picks between real alternatives, or needs Matt's judgement. Never decide one.
   A deferred item is not a failure; record it and move on.

The report, after its header, has:
- **Done**: what you changed, briefly.
- **Deferred**: each design call you did not make — what it is, the options you see,
  which you would pick and why; mark a call that is Matt's own (taste, scope, a rules term) `(Matt's call)` — and anything else you left, with the reason.
- **Notes that were wrong or stale**: one line each, if you were given earlier notes.

The status is `done` when everything clear was carried out (deferred design calls do
not make it partial), `partial` when clear work was left undone, and `red` only when
something is broken. A red general cleanup leaves its notes, and any below, for the next cleanup.

{{notes}}
