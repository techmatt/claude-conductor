Cleanup of `{{repo}}`. Earlier items kept the audit notes below while they worked: what
was poorly structured, what took longer than expected, what should be refactored, and
what the repo's CLAUDE.md or README should say. Act on them.

1. Read every note, then check each one against the code as it is now: some may already
   be fixed, contradict each other, or be wrong.
2. Carry out whatever is clear and well defined: refactors, restructuring, dead code,
   naming, CLAUDE.md and README improvements. Keep behaviour unchanged unless a note
   names a plain bug. Run the tests after each substantial step and leave them green.
3. Defer every design-level call: anything that changes what the code does for its
   users, picks between real alternatives, or needs Matt's judgement. Never decide one.
   A deferred item is not a failure; record it and move on.

The report, after its header, has:
- **Done**: what you changed, grouped by note, briefly.
- **Deferred**: each design call you did not make — what it is, the options you see,
  which you would pick and why — and anything else you left, with the reason.
- **Notes that were wrong or stale**: one line each.

The status is `done` when everything clear was carried out (deferred design calls do
not make it partial), `partial` when clear work was left undone, and `red` only when
something is broken. A red cleanup leaves these notes unused for the next one.

The audit notes follow, oldest first, each headed by the item that wrote it.

{{notes}}
