# Parked edits

Small edits the planner parks for a repo (a rename, a wording fix, a stale line) wait in
the control folder, and conductor hands them to the next item that can safely carry
them. Before 59 the planner typed them into prompts by hand. That turned items that could
have run `alongside` into serial ones: aicomplete's 026 lost its parallel start that way.
Code: `parked.py`.

**Files.** `parked/<repo>.md` is the planner's, written over the bridge like `backlog/`:
one edit per line starting `- `. `[rules]` first marks an edit that changes play. Any
other line is ignored, so a heading or a note is fine. `parked/` is runtime and ignores
itself (its `.gitignore` is `*`, written at daemon start), so no request is needed.
Each write gets a receipt, `receipts/parked/<repo>.md`, saying how many edits wait
(72; before it the planner staged one that never came: `receipts.md`).
`parked/<repo>.done.md` is conductor's: each edit handed out, as
`- <edit> — <item name> (<launch time>)`.

**Withdraw or replace** (144). A waiting line can be taken back or changed by a piece of
it found in exactly one waiting line (any case): the signal `park: <repo> remove <piece>`
or `park: <repo> replace <piece> => <new line>` (`park: <repo> <line>` adds one), or MCP
`park` with `op: remove | replace` and `match`, which writes that signal. The daemon makes
the change (`parked.apply`), logs `parked_edit` (`op`, `was`, `line`, `waiting`), and the
receipt names what changed and lists every line that waits after (`P1. … | P2. …`) and who
carries them. A piece in no line or several is refused, naming them; one already handed
out names its item from the done file (only a `tell:` reaches it now, or a parked undo).
Found live: aicomplete held a `[rules]` line moving the round band to 7-10 after Matt's
Nov 20 ruling (6-8, 10-11 exceptional) superseded it; riding, it would have reverted it.
Every park receipt (an add over the bridge too) lists what waits.

**Who carries them.** At launch (`_start_exec`, the prompt's second half) a **serial**
item, meaning not `alongside`, not `readonly`, not a template and not a `resume:` item,
takes every line, `[rules]` ones too. Until 143 a `[rules]` line rode only a `rules: true`
item, and none came: fractals' and aicomplete's waited across several items while every
receipt said the next serial item would take them. A `[rules]` edit now writes the repo's
`docs/rulings.md` like any other edit; a template (a cleanup that must keep identical
results) still never carries one. Taken lines move out of `parked/<repo>.md`
into the done file at once, and the run record keeps them (`parked: [{n, text}]`, so a
restart never takes them twice). The `start` event lists them (`parked`).

**In the prompt.** After the body (and in a staged item's resume message), a section
`## Parked edits (also do these)` lists them as `P1.`, `P2.`, …, asking the executor to
do each unless it conflicts or is unsafe, and to answer each in its report as `parked
P<n>: done` or `parked P<n>: skipped (why)`. They are in scope whatever the prompt's own
file limits say; only a declared `paths:` binds them (75: aicomplete 094 skipped all four
of its edits under "touch no other files", and they waited a cycle). Since 151 (helium-hustle
115) it adds that an edit changing play may turn a sim or loadout check red: patch the check
when the red is the edit's intended effect (an expected value moving), else leave it red and
report it, the `parked P<n>:` line saying which.

**"Accumulate" is `park`** (Matt, 2026-11-26). "Accumulate these" or "hold these for a batch"
means park them, nothing more: never a batch list kept in `state.md` (helium-hustle's
*Playtest batch 3* sat outside the parked edits and missed two carrying items).

**At finish.** Before the `finish` event (`_settle_parked`, once per item, saved before it
acts): if the item's work landed (not red), the edits its report answered `done` stay
done, and every other one, whether skipped or unanswered, goes back to the top of
`parked/<repo>.md`. A red item lands nothing, so all of them go back. The finish carries
`parked_done` and `parked_back`, and the inbox line says `parked edits: N done, M back`.

**Visibility.** The inbox's *Now* line per repo shows `N parked edit(s) waiting (K
`[rules]`)`, and the digest's *Maintenance facts* has `parked edits waiting: N`. A queue
receipt names what the item will carry as the queue stands (`carries 2 parked edit(s)
as things stand: …`, after 58's start estimate). While edits wait it always says (143):
one behind an earlier serial item reads `carries none of the N parked edit(s) waiting:
<item> ahead of it takes them`, one not serial `…: not a serial item`. It is an estimate:
a line added before the launch rides too. The park receipt names the queued item that
will carry them, by the same rule.
