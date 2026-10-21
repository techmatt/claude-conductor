General cleanup of `{{repo}}`. There are no notes to go on: audit the whole repo yourself,
then act on what you found.

1. Audit. Read the repo end to end: layout, code, tests, CLAUDE.md, README and other
   docs. Write what you find to the audit notes file described above, under its four
   headings, as you go: what is poorly structured, what would slow the next person down
   and why, what should be refactored or cleaned up, and what CLAUDE.md or README should
   say. Concrete notes with file names, not impressions.
2. Carry out whatever is clear and well defined: refactors, restructuring, dead code,
   naming, CLAUDE.md and README improvements. Keep behaviour unchanged unless you find
   a plain bug. Run the tests after each substantial step and leave them green.
3. Defer every design-level call: anything that changes what the code does for its
   users, picks between real alternatives, or needs Matt's judgement. Never decide one.
   A deferred item is not a failure; record it and move on.

The report, after its header, has:
- **Done**: what you changed, briefly.
- **Deferred**: each design call you did not make — what it is, the options you see,
  which you would pick and why — and anything else you left, with the reason.

The status is `done` when everything clear was carried out (deferred design calls do
not make it partial), `partial` when clear work was left undone, and `red` only when
something is broken. A red general cleanup leaves its notes for the next cleanup.
