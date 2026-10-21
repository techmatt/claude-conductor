# Resume items: ask a finished item, or have it audit itself

A resume item puts questions to a finished item's **own session**, so the answer comes
from what that executor actually saw and decided, and comes back as a report. The
planner's **ask X: …** queues one with Matt's words as the body; **post-audit X** queues
one with `template: self_audit` (`templates.md`). Before this, the only way to ask was
the app's **Terminal** button or `conductor open` (`progress.md`), by hand.

## The item

```
---
repo: game
resume: game/069_bug_hunt     # <repo>/<NNN_name>: a finished item in this same repo
template: self_audit          # optional; the only template a resume item may use
---
1. Why did the hunt stop after 1.6 of its 6 hours?
```

- **Checked when it is parsed** (`items._resume`): the value is `<repo>/<NNN_name>` and
  names this item's own repo; `readonly: false` is refused (a resume item is always
  readonly); any template but `self_audit` is refused, and `template: self_audit`
  without `resume:` is refused. A body is required unless it is a self-audit.
- **Checked against the queue** (`resume.problem`, from `state.scan` every tick): the
  original has a `finish` event with status `done`, `partial` or `red`; it is not
  queued, staged or running; and it has a session (its finish names one, and it did not
  fail to launch). Otherwise the item is **invalid** with the reason (logged once per
  version of the file and reason, the queue held as for any invalid item). It becomes
  valid by itself once the original finishes.
- **Scheduling.** It runs like any readonly item: in a repo with worktrees, no repo lock,
  starting once queued, beside anything, early behind the head (with `worktrees = false`
  it waits for the repo like any readonly item there, though it still gets its own
  worktree); its named locks and `depends_on` apply. A reorder that would renumber the
  original of a queued or proposed resume item is refused, as for `depends_on`. It
  **never stages** (a stage is a new session). Unlike other items it is **not held by
  a red**: asking a red item why it went red is the point. The red hold still stands for
  everything else in the queue.

## The launch

- **A fresh worktree at current `main`**, always, even in a repo with `worktrees = false`;
  discarded at the end like any readonly item's. Setup runs as usual.
- **A fork, not a resume**: `claude -p … --resume <original session> --fork-session
  --session-id <new uuid>`. Verified on Claude Code 2.1.306: the fork's init and result
  lines carry the new id, it remembers the original, and a second fork of the original
  sees nothing the first added, so the original stays as it was and can be resumed (or
  asked) again. Passing `--session-id` keeps conductor's usual marker: the ask server,
  restart adoption (`procs.find_by_marker`) and the `finish` event all use the new id.
  `--resume` finds a session from any folder; the fork's transcript is written under
  the new working directory's project folder.
- **The transcript must exist.** With `--session-id`, a fork of a session Claude Code
  does not have exits 0 and silently starts a *fresh* session that would answer from
  nothing. So at launch conductor looks for `<config>/projects/*/<session>.jsonl`
  (`<config>` is `CLAUDE_CONFIG_DIR`, else `~/.claude`; `resume.session_file`), and a
  missing one fails the launch red: `resume: <orig>'s session <id> has no transcript
  in Claude Code's projects folder`.
- The run record keeps `resume_of`, `resume_session` (the original's session id),
  `resume_since` (where the original's code stood: its merge commit; with
  `worktrees = false`, its last `commit` event, it having committed to `main`; else,
  never merged, its run record's `base`) and `resume_merged`.

## The preamble

The usual preamble, readonly paragraph included, plus a *Resumed* paragraph
(`resume.RESUMED`): the session is its own run of the original, forked; this is a new
readonly item whose report goes to the new path; the code may have moved on, with
**what landed on `main` since** the original merged (or, never merged, since it
started) as one line per commit (`git log` from `resume_since` to the new worktree's
base, the newest 50); and **its own record** it may read, the one exception to the
preamble's rule against reading the control folder, as absolute paths: the original's
`.prompt.md`, report, `.audit.md`, run log and stage log (each only if it exists), and
an extract of the original's events, written at launch beside the resume item's
prompt as `reports/<repo>__NNN_name.original.md` (one line per event: id, time, kind,
detail). It answers each question by number. It writes only its report (and, as a
self-audit, its notes).

## The result

Its `start` event carries `resume_of` and `resume_session`; its `finish` carries
`resume_of` (and the new session id as `session_id`, so it can itself be resumed).
Every view names it the same way (`resume.label`): `resume of <orig>`, or
`post-audit of <orig>` for a self-audit.

- **Inbox** (and `conductor events`): the `enqueue`, `start` and `finish` lines; the
  *Unreviewed reports* line (also in `session.md` and `review.md`); its *Now* entry,
  queued or running.
- **`review.md`**: its section adds `- resume of <orig>: its report <path>`.
- **`session.md`** *Maintenance*: resume items are not counted as work finished since a
  template ran, nor in the `slow:` window.
- **`conductor status`**: `, resume of <orig>` on its running line (in place of
  `, readonly`), `(resume of <orig>)` on its queued line.
- **App**: Repos *In flight* reads `running · resume` with the label first in the note;
  a queued or finished row's note carries the label; the trail's title shows it with
  the original as a link (in the Events tab it opens the original's trail in place; in
  Repos, whose detail follows the tree, it opens it in Events); the Conversation's
  finish turn adds it.

A resume item arrives **unreviewed** like any item; the original's reviewed state does
not change. A session resumed *by hand* is the other way to reach an original: if it
edits the original's report after review, `report_changed` makes it unreviewed again
(`inbox.md`, *Reviewed reports that change*).
