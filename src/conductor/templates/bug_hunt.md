Bug hunt in `{{repo}}`. Run the project as its intended users would (whoever the repo's
docs say they are), find what is broken, confusing, ugly or slow, fix what is clearly a
bug, and leave Matt a clear list of the rest.

## The clock

The time limit above is the hunt's length plus one hour for setup, merge and the report.
**The hunt's length is a target to use, not a cap to stay under.** Matt chose it; a hunt
that ends hours early has wasted the time he set aside, however good its findings. Write
down your start time and your hunt-end time (the limit minus one hour) in your first
progress line. Work in rounds; at the end of each round, post a progress line with the
time left and the next round's plan.

Running out of findings is not the end of the hunt; it is the signal to widen it. When a
round comes up dry, take the next lens from this list (in any order that fits the repo)
and keep going:

- **Paths not yet driven:** every screen, mode, setting and URL/dev flag the docs or code
  name; each first-run, returning-user and edge state (empty, full, maxed, reset).
- **Odd input:** mashing, holding, pressing during transitions, pausing, resizing,
  switching tabs or focus, reloading mid-flow, a slow or failing network or disk.
- **Long runs:** many sessions back to back, at real speed and at test speed; memory,
  frame time and repeats across sessions.
- **Content sweep:** every piece of text, audio and art the project ships, read, heard
  and looked at as its user would, against the repo's own rules (findings list
  categories, style rules, rulings in its docs).
- **Recent changes:** read the git log since the last hunt and drive each changed area
  hard; recent code is where bugs live.
- **Code reading for bugs:** races, timers, state that survives a scene or session it
  shouldn't, error paths, places the tests don't reach. Only where it can produce a bug
  a user would see.
- **The checker itself:** add the checks this hunt taught you to want, then run them
  across everything.

Stop before the hunt-end time only if every lens above has had a real round and turned
up nothing new in its last pass. If so, say so in the report, round by round. Never stop
early because the findings so far feel like enough, because the fixes are in, or because
the fast lane is green.

Spend the last stretch before the hunt-end time, not after it, on making sure what you
fixed is committed and green: never start a fix you can't finish and test before then.

## The work

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

**Browsers: at most one browser test lane plus one checker sweep at a time**, whatever
else the hunt wants to run. Workflows and subagents that drive a browser (a
contact-sheet run, a real-speed session) count toward that cap. Code reads, unit tests
and reading finished screenshots can run alongside.

**Notes only.** If this item is readonly (the preamble says so), find and record but
change nothing tracked: no fixes, no tests, no findings-list edits, no commit. Build the
checker and keep its screenshots in one of the repo's `shared` folders if the preamble
names one, otherwise outside the repo or nowhere; the findings go in the report only,
still checked against the repo's findings list so nothing known is re-reported. The
clock rules above apply in full.

## The report

After its header:
- **Time**: start, hunt-end target, actual end, and the rounds in between (one line
  each: the lens, how long, what it found). If it ended more than 15 minutes before the
  target, say why, lens by lens.
- **Checker**: what it covers and how to run it (or, notes only, where it is).
- **Fixed**: each bug fixed, briefly (notes only: **Found**, each bug with where it shows).
- **Deferred**: everything left for Matt, most important first, each with the options
  you see and which you would pick and why.

The status is `done` when the hunt used its time (or exhausted every lens, shown under
**Time**) and every clear bug found was fixed or (notes only) recorded; deferred items
do not make it partial. `partial` when it ended early without exhausting the lenses, or
when clear bugs were left unfixed for lack of time. `red` only when something is broken.
