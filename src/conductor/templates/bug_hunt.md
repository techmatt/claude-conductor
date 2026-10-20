Bug hunt in `{{repo}}`. Run the project as its intended users would (whoever the repo's
docs say they are), find what is broken, confusing, ugly or slow, fix what is clearly a
bug, and leave Matt a clear list of the rest. The time limit above is the hunt's target
plus an hour for setup and the report; the target is not a cap. Work efficiently rather
than exhaustively:

1. Build or extend a reusable checker kept in the repo and documented in its README.
   It drives the app across many seeds and paths at speed, captures screenshots only at
   meaningful moments (screen changes, dialogue, choices, results), and runs automatic
   checks there: text overflowing its box or the screen, overlaps and hidden targets,
   missing or placeholder assets, console errors, missing audio, stretches where input
   is not accepted, frame-time spikes, memory growth. Instrument the app where that is
   the cheap route (a debug hook or flag, off in normal use).
2. Look as the user: review contact sheets of the captures, and use the app at real
   speed for pacing and clarity.
3. Fix clear bugs, and make each automatable check a test. Note bad code only where it
   caused or hid a real bug; structure is a general audit's job.
4. Keep a findings list in the repo (`docs/qa-findings.md` unless the repo has its own
   convention): each finding fixed, deferred to Matt with a recommendation, or won't-fix
   with a reason. Read it first, and never re-report an item already on it.

**Notes only.** If this item is readonly (the preamble says so), find and record but
change nothing tracked: no fixes, no tests, no findings-list edits, no commit. Build the
checker and keep its screenshots in one of the repo's `shared` folders if the preamble
names one, otherwise outside the repo or nowhere; the findings go in the report only,
still checked against the repo's findings list so nothing known is re-reported.

The report, after its header, has:
- **Checker**: what it covers and how to run it (or, notes only, where it is).
- **Fixed**: each bug fixed, briefly (notes only: **Found**, each bug with where it shows).
- **Deferred**: everything left for Matt, most important first, each with the options
  you see and which you would pick and why.

The status is `done` when the hunt ran its course and every clear bug found was fixed
or (notes only) recorded; deferred items do not make it partial. `partial` when clear
bugs were left unfixed for lack of time, and `red` only when something is broken.
