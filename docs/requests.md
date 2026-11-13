# Requests

The only way a tracked control file changes (the guard that enforces it:
`control-folder.md`, *Control-folder guard*). The planner creates
`requests/<stamp>/` holding whole replacement files at their paths relative to the
control folder, plus `request.md`, all in one bridge call, `request.md` last:

```
---
targets: [state.md, docs/plan.md]
message: Optional one-line commit message
---
Commit message (used when `message:` is absent; first line is the subject).
```

The front-matter is YAML: a `message:` containing a colon must be quoted
(`message: "state: v0.2"`). Unquoted, the YAML fails and the request lands `.failed`.

## Applying

There is no settle timer: as soon as `request.md` and every target it lists are present
and readable (a file its writer still holds open is not), conductor checks the guard,
copies the files in (UTF-8 text is written with LF endings; anything else byte for byte),
stages and commits exactly the targets, logs `request` and `commit`, removes the request
folder and re-snapshots. A nap wakes within 0.25s of any planner write (`receipts.md`, *When*).

## Waits

The bridge writes asynchronously: a target can land after `request.md`, even one written
in an earlier call, so nothing is applied until every target is there. Targets not there
yet are waited for, with no event, until 60s (`REQUEST_WAIT_SECONDS`) after `request.md`'s
mtime (so the wait survives a restart; rewriting `request.md` starts it again); then the
request fails (`targets missing: state.md (waited 60s); nothing was applied`).

A request whose every target is byte for byte the control folder's copy (HEAD's) waits for
a changed copy to land, re-read each tick, until nothing in its folder has been written for
30s (`SAME_WAIT_SECONDS`: since 84 the newest of `request.md`'s and the targets' mtimes, so a
copy the bridge lands again restarts it), then applies as `identical` (the `request` event's
field; receipt `nothing to commit: every target is identical to HEAD`), distinct from
missing. The six seen 2026-11-06/07 (aicomplete, fractals) cleared only when the planner
re-sent from a fresh path: the bridge's stale copy, so edits (below) are the fix. Either
wait past 2s gets a `pending` receipt saying which.

## Failures and order

Every other error fails, at once when `request.md` is 2s old (`FRESH_SECONDS`; younger, it
may be half written and is looked at again): bad front-matter, no `targets:`, a target that
is absolute, uses `..` or points into the runtime paths or `.git`, no commit message. Every
file in the folder must be a target, checked once nothing is missing. A bad request is
logged `request_failed` (red) and renamed `requests/<stamp>.failed/`. A request that
changes nothing is logged with `no_change` and removed.

Requests keep their order where their targets overlap: a later one naming a target of an
earlier, waiting one waits too. Requests apply in write order (`request.md`'s mtime, then
folder name), never stamp order: a stamp is its writer's guess at the clock (57's
hand-written one ran 8 minutes ahead). The planner names them `YYYYMMDD-HHMM-<tag>`.

A folder with no `request.md` whose files are all an hour old (`STALE_SECONDS`) moves to
`requests/stale/<stamp>/` (`-2`, … if taken), logged `request_stale` with its files, so
a stray copy the planner can't delete never waits forever. One whose message starts
`closeout:` also moves the read mark (`inbox.md`). Applied or failed, a request's outcome
is `receipts/requests/<stamp>.md` (`receipts.md`).

## Edits

(84) A target may come as edit blocks in `request.md`'s body instead of a file
in the folder: `<<<<<<< <target>`, its old lines, `=======`, the new lines, `>>>>>>>`, each
marker on a line of its own (`request.parse_edits`). Both sides are whole lines; the old
ones must occur exactly once in the control folder's current copy, an empty new side
deletes them, and blocks for one file apply in order (`request.apply_edits`). A miss
refuses the whole request (`edit 2 for state.md: its old lines are not found (old text must
be whole lines) in the current file (first: '- 004'); nothing was applied`, or `found 3 times`;
113: a block whose old text was part of a line read only "not found"). The body outside the
blocks is the message when `message:` is absent. Edited targets are listed in `targets:`
like any other; one sent both as a file and as edits, or edits for a path not listed, is
refused. Edits are never `identical`-waited: they apply against the live copy. Why: a review
took 6-8 planner calls (stage, copy, edit, copy to a fresh path, commit, receipt), and the
whole-file form is where the bridge's stale copies came in.
