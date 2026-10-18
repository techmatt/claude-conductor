Test performance in `{{repo}}`. The tests are too slow. Make them fast to run while
working, without losing what they protect.

1. Measure first. Time the whole suite and find where the time goes (per-test
   durations, collection and import time, fixtures, setup, sleeps and polling, I/O,
   subprocesses, network). Use the test runner's own timing output where it has one.
2. Set up a fast/slow lane split, or tighten the one that exists. The fast lane is
   what someone runs after every change and should take seconds; the slow lane holds
   the rest and still runs before a commit. Make the default test command run the fast
   lane, make the full run one documented command, and say both in the README and
   CLAUDE.md.
3. Speed tests up generally: shared or cheaper fixtures, shorter waits and polls,
   smaller data, parallelism where the runner supports it and the tests are
   independent.
   - Lowering thoroughness slightly is acceptable when it buys real speed (fewer
     parameter combinations, smaller inputs). Say what was given up.
   - Moving a slow test to the slow lane is acceptable where that makes sense. Never
     delete a test only because it is slow.
   - Never weaken an assertion to make a test pass, and leave no test flaky.

This is the one report where test detail belongs. After its header, it has:
- **Timings**: before and after, wall clock, for each lane and for the full run, measured
  the same way on the same machine, with the command used for each.
- **What changed**: each speed-up with its rough saving, and anything moved between lanes.
- **Given up**: any thoroughness traded for speed, and why it was worth it.
- **Left**: the slowest remaining tests and what would speed them up.
