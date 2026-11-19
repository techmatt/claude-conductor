Bug hunt in `{{repo}}`. Run the project as its intended users would (whoever the repo's
docs say they are), find what is broken, confusing, ugly or slow, fix what is clearly a
bug, and leave Matt a clear list of the rest.

## Scope and the clock

**Scope: recent changes**, unless the planner's note below asks for a **full bug hunt**.
Read the git log since the last hunt (the findings list or the last hunt's commit says
when; no earlier hunt: the last two weeks) and drive each changed area hard, as its user
would; recent code is where bugs live. Run the checker sweep across everything too.
Another lens from the list below runs only when a round on recent changes points at it
(a bug whose cause reaches past the change). A **full bug hunt** gives every lens below
one real round first.

**Stop on yield.** Work in rounds; end each with a progress line: what it found and the
next round's plan. A round is *thin* when it found nothing a user would hit in normal
use: only wording, stale lines or polish. **After two thin rounds in a row, the hunt is
over**: finish the fixes in hand, make them committed and green, write the report. The
time limit (the hunt plus an hour for setup, merge and the report) is a cap, not a
target: never widen the hunt to fill it. Stop at the cap with whatever is committed and
green; never start a fix you can't finish and test before then.

The lenses (a full bug hunt's rounds; otherwise only where a round points):

- **Recent changes:** read the git log since the last hunt and drive each changed area
  hard (the default scope above).
- **Paths not yet driven:** every screen, mode, setting and URL/dev flag the docs or code
  name; each first-run, returning-user and edge state (empty, full, maxed, reset).
- **Odd input:** mashing, holding, pressing during transitions, pausing, resizing,
  switching tabs or focus, reloading mid-flow, a slow or failing network or disk.
- **Long runs:** many sessions back to back, at real speed and at test speed; memory,
  frame time and repeats across sessions.
- **Content sweep:** every piece of text, audio and art the project ships, read, heard
  and looked at as its user would, against the repo's own rules (findings list
  categories, style rules, rulings in its docs).
- **Code reading for bugs:** races, timers, state that survives a scene or session it
  shouldn't, error paths, places the tests don't reach. Only where it can produce a bug
  a user would see.
- **The checker itself:** add the checks this hunt taught you to want, then run them
  across everything.

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
still checked against the repo's findings list so nothing known is re-reported. Scope
and stop on yield apply in full.

## The report

After its header:
- **Time**: start, scope (recent changes since <commit or date>, or full), actual end
  and why it ended (`stopped on yield` after rounds N and N+1, or `at the cap`), then
  one line per round: the lens or area, how long, what it found (counts of user-facing
  bugs and of wording or polish), so the stop can be judged.
- **Checker**: what it covers and how to run it (or, notes only, where it is).
- **Fixed**: each bug fixed, briefly (notes only: **Found**, each bug with where it shows).
- **Deferred**: everything left for Matt, most important first, each with the options
  you see and which you would pick and why; mark a call that is Matt's own (taste, scope, a rules term) `(Matt's call)`.

The status is `done` when the hunt stopped on yield or at the cap with every clear bug
found fixed or (notes only) recorded; ending early is never `partial`, and deferred
items do not make it partial. `partial` only when clear bugs were left unfixed for lack
of time. `red` only when something is broken.
