# Hooks and the rule ledger

The executor's PreToolUse hook (`talk.pre_hook`, matcher `talk.PRE_TOOLS`: the shell, file,
Agent/Task and Workflow tools) denies a call that breaks a rule a hook can check cheaply and
reliably, with a one-line `permissionDecisionReason` starting `conductor: refused:` that says
what to do instead. The checks live in `rules.py` (the agent cap in `fanout.py`); the hook also
caps shell timeouts (`progress.md`, *Short calls*). It runs as
`<python> -m conductor.talk pre --cap <s> --control <folder> [--readonly] [--background=<glob> …]`,
one Python start per matched call. A hook error lets the call through. Checked live (122): a
real `claude -p` in the sandbox had its `sed -i` and `%TEMP%` Write refused and its heredoc
commit message allowed.

Enforcement beats text (Anthropic's Claude Code team cut its system prompt ~80% for frontier
models: short guidance plus enforcement beat long rule lists). Since 123 the preambles
(`preambles.md`) are short principles; a hooked rule keeps at most a clause there.

**A dropped rule comes back** when a retro or a report's `friction:` line shows its failure
again: preferably as a hook, else as a line in the preamble, with its row here updated.

## The ledger

Every executor rule, one row each: its gist, its origin (conductor item numbers, — where
unrecorded; in brackets the project incident that prompted it), and where it lives now. *Full* and *light* are the
preambles, *both* the two; *hook* is `rules.py`/`fanout.py`; *dropped* is gone from the text
and not enforced.

### Enforced by the hook

| Rule | Origin | Lives |
|---|---|---|
| Edit with Write/Edit, never `sed -i`, a heredoc to a file or a file-writing `-c` string | 72, 82 (wordwarriors 174, aicomplete 074, fractals 025, website 003); 128 (helium-hustle 029, wordwarriors 181 still tried); 133 (aicomplete 267, 270 each lost a call) | hook; since 128 a sentence in full and a clause in light naming each refused form; since 133 a plain "Never …: the hooks refuse each", and "Write a script, then run it" in both; since 142 (aicomplete 292: 20 edit scripts, one failing unseen mid background chain) "one script for the whole change (every edit checked before any is written); run it alone, never mid-chain" in full, "run it alone" in light |
| No `%TEMP%`, `$TMP`, `/tmp`, `mktemp` or temp-file APIs | 61 | hook; a clause in both |
| Never `isolation: worktree` (it lands in the main checkout) | — (aicomplete 040) | hook only |
| Helpers never `git checkout`/`restore`/`switch`/`stash`/`clean -f`/`reset --hard` | 86 (aicomplete 144), 122 | hook only |
| Any fan-out ≤ 10 agents; a Workflow declares `const MAX_AGENTS` | 68, 117 (aicomplete 234), 120 | hook; a clause in full |
| No elevation (`sudo`, `runas`, `-Verb RunAs`) | 76 (aicomplete 103) | hook; a word in both |
| No Bash `&`, `nohup`, `disown`, `setsid` jobs: `start` | 106 (aicomplete 188) | hook; `start` in *Short calls* |
| No `sleep` ≥ 5 s or poll loop: `wait_for` | — (wordwarriors 034), 122 | hook; `wait_for` in *Short calls* |
| Every `run_in_background` sets a `timeout` | 82 (fractals 026, website 003) | hook; in *Short calls* |
| `~/.claude`: config only, never memory or a `CLAUDE.md`; lessons in `carry:` | 74 (aicomplete 087) | hook; "lessons go in `carry:`" in full |
| Only the report (and `write_roots`) in the control folder | —, 114 (`write_roots`) | hook (file tools) and the tamper guard; one line in each |
| Readonly items commit nothing | 79 | hook; the readonly line |
| A `remote` item farms seeded runs, not the native sim directly | 124 (aicomplete 249) | the message hook: one reminder, never a denial (a native run is the fallback; `remote-workers.md`); since 133 it names `farm-seeds --at <commit>` for an older revision (aicomplete 270) |
| Revisit a taken design default with time left | 133 (aicomplete 271) | the message hook: one nudge at the first report write with over an hour left (`questions.md`, *Revisit*) |

### In the preamble text

| Rule | Origin | Lives |
|---|---|---|
| Unattended: decide what you can, record it, keep going | —, 123 | full (first line) |
| `ask` only when a wrong guess wastes work or is hard to undo, with a default; a call holds the session; block only when every default is worse | 74 | both |
| A skip binds; a default never un-skips or overrides the prompt without `default overrides prompt:` | 61 (aicomplete 009, 030) | both |
| **Design asks** (`design: true`): design principle, reversed direction, ~30% split, a reading that removes what it names, departing from Matt's staging answer, a fix changing play | 79, 111 | full word for word; light the split and play-fix only |
| A `design: true` default changing the shipped build is confirmed afresh before regen and docs | 144 (aicomplete 299 regenerated twice on a default it reverted) | full word for word |
| An undoable step last | 74 | full |
| Latitude: rulings, goal, bars, method; say what gave way, never ask | 102 (aicomplete 167) | both |
| Method is a route; required lines and CLAUDE.md *Working rules* bind | 102, 111 (aicomplete 206) | full |
| Bars strict, not chased; a numeric target's reachability first | 102 | both (the "~30 min, strongest lever" recipe dropped) |
| **Fix loops**: three failed attempts at one failure → change approach, `design: true` if design changes | 123 | both |
| Only hard stop: a prompt's wrong-repo guard | 102 | full |
| Fix the obvious in place, inside `paths:` | 113 | both |
| Leftover budget: over an hour left, the next step on an unmet bar | 75 (aicomplete 090), 102 | both (`LEFTOVER_FULL` / `_LIGHT`) |
| **Measurement**: paired seeds, fresh seeds for a final read, timing on a quiet box, only ratios beside others, re-base only when alone | 74, 102, 117 (aicomplete 215) | full word for word |
| Re-read a quoted number from source | 74 | full |
| `progress` at start, milestones, a >15 min job, an estimate moving a third | 74, 103 | full; light start and long jobs |
| `outline` with a row per done-when item; staged: post it first | — | full |
| An `eta` names its source | 68 | full |
| Plan from the last tool return's clock | 86, 114 (aicomplete 143, 213) | both |
| Calibration freeze line, SE gap, seeds taken | 74, 86, 117 (aicomplete 223, 234) | *Calibrate* block |
| Servers on `CONDUCTOR_PORT`, detached, stopped; `npm ci` first | 74, 84 (aicomplete 135-138) | *Servers* block; light a port line |
| Scratch in `out/<item>/`; the rest of `out/` is not fresh | 61, 116 (fractals 008, 010) | both |
| Working papers under the results folder, committed | 73 (aicomplete 079) | full |
| Reusable reads in `out/keep/`, named in the report | 110, 117 | full |
| Scripts in files (read-only one-liners pass the hook) | 72 | full (a clause) |
| Heavy compute below normal priority, at most `{threads}` threads in all, children counted, half beside others (143: one budget; "leave 4 free" read as a second) | 72, 73, 143 | full; light `nice` and threads |
| A full test suite only between sims | 86 (aicomplete 142) | both |
| A load message is information: finish the unit, go smaller | 80, 82 | full |
| At most 2 heavy subagents, over any Workflow or Ultracode note | 73 | full |
| Helper briefs: rulings word for word, own scratch path; read-only helpers answer inline | 79 (aicomplete 111), 113 (website 006), 118 (aicomplete 239) | full |
| Messages and amendments outrank the prompt; answer by `progress`; helpers pass them up; `resume`/`answered` | —, 110 | full; light one line |
| *Short calls*: under `foreground_seconds`, background longer, `wait_for`, never end the turn to wait | — (wordwarriors 034), 82 | both (`CALLS` / `CALLS_LIGHT`) |
| A background subagent: `wait_for` with `minutes` near its length or check its output, never a path nothing writes | 128 (helium-hustle 027, 030) | both (*Short calls*); a subagent finishing also ends a `wait_for` (`executors.md`) |
| ETA: by rate once there is a plan; `(to the cap)` only for the whole limit | 68, 128 (helium-hustle 028) | full; the `eta` field's description |
| Worktree: commit to the branch, never touch `main` or the main checkout | — | both (`WORKDIR_TREE` / `_LIGHT`); hooking it needs the shared-link list |
| `paths:` only | — | both; the merge's branch check enforces it |
| `write_roots` writable | 114 (fractals 050) | both |
| Readonly: nothing merges, so ledger entries go in `carry:` | 79 (aicomplete 109) | both |
| *Running beside you*; the latest return rules | 74 (aicomplete 080, 081) | both |
| **Diff review** before the report: one read-only subagent, the diff, rulings, done-when, *What failure looks like*; one more pass at most; a `Diff review:` line; a rules change gets it before fresh seeds, a config self-check is not it (144, from *Calibrate*: aicomplete 300) | 123, 144 | full, writable, not template; Ultracode's adversarial review counts |
| Report header, statuses (`done` / `partial` / `red`, a soft-band miss is `done`), the report-path line | 74, 109, 114, 117 | both word for word |
| `warnings:` from the repo's *Warning checks*; `unchanged from main`; never changes status | 109, 113, 121 | full; light the header line |
| `partial:` when partial: each unmet bar and why, `matt: ` on one only his call is left on, naming its question or `## For Matt` choice (all such: no retro; conductor checks the naming, 132) | 131, 132 (aicomplete 264) | full only (light at its word cap) |
| Report: what changes for the planner first, ~60 lines, answer every message, flag a two-change comparison, tests only for an unfixed red | 74 | full; light answers by number |
| Pickers: manifest schema, `pick:` line, never delete a round | 79, 121 | *Pickers* block |
| Audit notes under fixed headings | 75 | *Audit notes* block (`templates/audit.md`) |

### Dropped in 123

| Rule | Origin | Why |
|---|---|---|
| Ask when the result rests on your reading of a scope line | 74 | Covered by "a wrong guess would waste real work" and the design asks. |
| Estimate in round trips (~15 s) plus measured run times | 84 | `progress` and `wait_for` returns carry the clock and time left. |
| Never kill and relaunch work for load alone | 82 | Folded into "finish the unit in flight, start the next one smaller". |
| Ultracode's "every substantive task" says when, never how big | 68 | The 10-agent cap is hooked. |
| Split helpers by file | — (aicomplete 040) | `isolation: worktree` is hooked. |
| Blocked time doesn't count toward the limit (in *Asking*) | — | The time-limit line says it. |
| Light: 2 heavy subagents, `~/.claude`, elevation spelled out | 73, 74, 76 | A light item rarely fans out; the rest is hooked. |
