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
`parked/<repo>.done.md` is conductor's: each edit handed out, as
`- <edit> — <item name> (<launch time>)`.

**Who carries them.** At launch (`_start_exec`, the prompt's second half) a **serial**
item, meaning not `alongside`, not `readonly`, not a template and not a `resume:` item,
takes every untagged line. It takes the `[rules]` lines only when its front-matter says
`rules: true` (`items.md`), the item that bumps the rules revision. So an item that must
keep identical results, such as a cleanup, never gets one (aicomplete's 025 held its
node-cut fix back for exactly that reason). Taken lines move out of `parked/<repo>.md`
into the done file at once, and the run record keeps them (`parked: [{n, text}]`, so a
restart never takes them twice). The `start` event lists them (`parked`).

**In the prompt.** After the body (and in a staged item's resume message), a section
`## Parked edits (also do these)` lists them as `P1.`, `P2.`, …, asking the executor to
do each unless it conflicts or is unsafe, and to answer each in its report as `parked
P<n>: done` or `parked P<n>: skipped (why)`.

**At finish.** Before the `finish` event (`_settle_parked`, once per item, saved before it
acts): if the item's work landed (not red), the edits its report answered `done` stay
done, and every other one, whether skipped or unanswered, goes back to the top of
`parked/<repo>.md`. A red item lands nothing, so all of them go back. The finish carries
`parked_done` and `parked_back`, and the inbox line says `parked edits: N done, M back`.

**Visibility.** The inbox's *Now* line per repo shows `N parked edit(s) waiting (K
`[rules]`)`, and the digest's *Maintenance facts* has `parked edits waiting: N`. A queue
receipt names what the item will carry as the queue stands (`carries 2 parked edit(s)
as things stand: …`, after 58's start estimate). An earlier serial item in the same
queue takes them first, so one behind it carries nothing. It is an estimate: a line
added before the launch rides too.
