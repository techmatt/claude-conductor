# planner over the file bridge (fallback)

Read this only when the chat lacks conductor's tools (`planner.md`, *Start*): the planner then works through the desktop app's file bridge, staging and committing files itself. Everything else in `planner.md` holds; where it names a tool, do what the tool does, as below. How each write is handled: `docs/receipts.md`, `docs/requests.md`, `docs/signals.md`.

## Start over the bridge

Six calls. Steps 1–5 are the same in Matt's saved preference.

1. **Tool search**, exactly `select:mcp__remote-devices__device_request_folder_access,mcp__remote-devices__device_list_dir,mcp__remote-devices__device_stage_files,mcp__remote-devices__device_commit_files`. It finds nothing: say "This chat isn't linked to the box: link it (the desktop app's Chat tab, not Code) and start again", then stop. Only some of the four: load the rest in the same turn, before step 2.
2. **Access**, one request for `C:\Code\<project>-control`, `C:\Code\claude-conductor` and the project's repos, as written, in the same turn as step 3: `C:\Code\<project>` unless Matt's saved preference lists others for it. Refused (a missing path): the first two alone.
3. **One shell command** in the workspace writes the signal and prints its stamp, local time:
   `s=$(TZ=America/Los_Angeles date +%Y%m%d-%H%M%S); mkdir -p /mnt/user-data/outputs; printf 'by: planner\nsession: start\n' > /mnt/user-data/outputs/planner-$s.signal; echo $s`
   (`printf`: LF endings; the commit takes only `/mnt/user-data/outputs/` paths). Never read it back.
4. **Commit** it to `signals\planner-<stamp>.signal` in the control folder.
5. **Stage** `session.md` and `planner.md` in one call, and read both. `session.md`'s header names the signal it was written for; an older one: stage it again, once.

Steps 4 and 5 go out in the same turn. A refused guess or an ungranted repo: request `session.md`'s `Repos:` paths, copied. Stage `state.md` only before a request that changes it.

## The bridge

- One missing path fails a whole access request: list a maybe-missing path's parent first (names only).
- Reading stages a snapshot. Re-stage a changed file with its staged `mtimeMs` as `expectedMtimeMs`; a re-stage can briefly show the old copy ("dispatched"): check `mtimeMs`, else read again. Upload once, to a fresh path (one written twice can send its first copy). LF line endings always.
- **After a write, stage its receipt in a later call** (same batch is too soon): `receipts\` + the write's path, extension `.md` (`receipts\requests\<stamp>.md`; a signal's `receipts\signals\planner-<stamp>.md`). None for a conductor prompt or a planner-notes line. It reads `accepted` with `result` (a `warning`: act on it now), `pending` (what holds it) or `refused` with `reason`. No receipt: re-stage once, then tell Matt the daemon may be off.
- **Never list a control folder, repo root or `reports\` recursively**: files are named below; stage a report by name.
- The bridge can't delete or run commands: that takes a signal, a request, a queue item or a command Matt runs.

## Files

```
C:\Code\<project>-control\
  state.md, paged.md, habits.md, conductor.toml   read; tracked, changed only by a request
  session.md, review.md, inbox.md  the digests and the inbox conductor writes
  queue\<repo>\NNN_name.md         write: an item, NNN from *Now*'s next item (NNN_name.amend.<stamp>.md: an amendment, `by: planner` front-matter)
  proposed\NNN_name.md             write: an item Matt says to hold
  signals\planner-YYYYMMDD-HHMMSS.signal   write: `by: planner`, then the verb lines
  receipts\<write path>.md         read: what became of each write
  requests\YYYYMMDD-HHMM-<tag>\    write: changes to tracked files (below)
  questions\<id>.md                read; the answer is written as <id>.answer.md
  reports\<repo>__NNN_name.md      read: executor reports (.audit.md: audit notes)
  picks\<name>.choices.json        read: Matt's picker choices
  parked\<repo>.md                 write: parked edits, one `- ` line each
  backlog\<repo>\<name>.md         write: pre-approved items
  events.db, events.db-wal         stage both, query together (alone, hours stale)
C:\Code\claude-conductor\
  prompts\inbox\<YYYYMMDD-HHMMSS>-<project>-<slug>.md   a conductor prompt (read prompts\ledger.md's tail first)
  planner-notes.md, planner-ideas.md                    the planner's lines for conductor
```

**Signals**: `key: value` lines applied in order, `by: planner` first. A rejected one becomes `.bad`, lines before the failing one still applied.

**Requests** are the only way tracked control files change. Write `requests\<stamp>\` in one call: `request.md` with edits in its body, or with whole replacement files at their control-folder paths, listed last (it waits 60s for a late one):

```
---
targets: [state.md]
message: "state: v0.2"
---
<<<<<<< state.md
- 004 queued
=======
- 004 done
>>>>>>>
```

Edits (the default for `state.md`) are whole lines of the staged file, each old side unique; an empty new side deletes; one miss refuses it all. Every changed file is a target; quote a `message:` holding a colon. `nothing to commit: … identical to HEAD` means the copy sent was unedited: resend from a fresh path.
