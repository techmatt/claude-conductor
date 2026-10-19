CLAUDE.md cleanup of `{{repo}}`. A quick pass through the repo's `CLAUDE.md`: make it
short, right and clear. This is a small item; do not audit the rest of the repo.

1. Read `CLAUDE.md`, then check what it says against the code, the README and the repo
   as they are now.
2. Cut anything extraneous: history, narration, and anything a reader learns faster
   from the code. A fact that lives in the README gets a one-line pointer to its
   section, not a copy; a fact repeated within `CLAUDE.md` is said once.
3. Fix anything clearly wrong against the code as it is: commands, paths, file names,
   test lanes, tools. A fact that belongs in the README and is missing there moves
   there.
4. Tighten unclear wording. Keep each instruction's meaning.
5. Keep the standing contract whole, however it is worded: the report (path and header
   from conductor's preamble, a ~60-line soft target, findings, decisions and surprises
   only, tests mentioned only for an unfixed red, the `Report written to <path>`
   sign-off); "commit to the branch you are on"; local 12-hour clock times; steps over
   ~30 seconds backgrounded with a waiter on the real process; LF line endings; facts
   learned while working promoted into the README.

Make reasonable improvements directly and commit them. Anything that would change how
executors work in this repo (a new rule, a dropped rule, a different workflow) is not
yours to decide: list it under **Deferred**.

The report is short. After its header, it has:
- **Cut**: what was removed or replaced by a pointer, one line each.
- **Fixed**: what was wrong and what it says now.
- **Deferred**: each change to how executors work you did not make, with what you
  would do and why.

The status is `done` when the pass is complete (deferred items do not make it partial),
and `red` only when something is broken.
