# Worktrees, merges and staging

## Worktrees and merges

Unless its repo sets `worktrees = false`, each item runs in its own git worktree,
`<worktree_root>/<repo>/<NNN_name>`, on a branch `conductor/<NNN_name>` cut from `main`
at launch. Executors never touch each other's files or Matt's checkout. The preamble
tells the executor to commit to its branch, leave nothing uncommitted, and never touch
`main` or the main checkout.

- **Untracked state.** A worktree holds only tracked files. Each `shared` folder of the
  repo is linked into the worktree at the same path (a directory junction on Windows,
  a symlink elsewhere), so data, a venv or build output is shared, not copied; a change
  there is a change everywhere. A shared folder that does not exist yet in the main
  checkout is created there first (noted on the `worktree` event), so every worktree
  links the same one; a *file* at that path is a `warning` and is not linked. A shared
  folder should be git-ignored in the repo —
  conductor logs a `warning` if it is not, since an executor could commit it. Then the
  `setup` commands run in the worktree, off the daemon loop (`executors.md`, *Setup*);
  a failing or timed-out command is a launch failure (red). Links are always removed before a worktree is deleted, so
  removing a worktree never reaches into a shared folder.
- **Merging.** When an item finishes `done` or `partial`, it waits in `running/` (phase
  `merge`) and is merged serially per repo, in finish order: **squash** the branch to
  one commit on its fork point from `main` (subject `<repo>/<NNN_name>: <title>`, the
  title being the item's first heading; the body lists the branch's commit subjects),
  rebase that one commit onto current `main` inside its worktree, fast-forward the main
  checkout, remove the worktree and the branch (`merge` with `squashed`, `worktree`
  events), then log `finish`. So `finish` means merged, a `depends_on` item never
  starts before its dependency's changes are on `main`, and a conflict is only a real
  one against `main`: never a replay of the branch's own commits, or of branches merged
  into it, one by one (Word Warriors 099). The squash is one ref move, skipped when the
  branch already is that commit, so a restart mid-merge repeats nothing; a branch whose
  changes net to none lands nothing. A worktree removed meanwhile is made again from
  the branch.
  - *Prose conflict:* when every conflicted file is prose (the repo's `prose` globs,
    see `items.md`), the rebase is left stopped and a short **merge executor** (headless
    Claude Code, no ask tool, no messages, at most `merge_minutes`) runs in the item's
    worktree, logged `resolve`. Its prompt names the files, says which side of the
    markers is `main` and which is the item, lists the commits that landed on `main`
    and the item's commit and prompt (for intent only), and tells it to keep both
    sides' intent, touch nothing else and run no git command. When it exits, conductor
    checks its work: a result, no conflict marker (`<<<<<<<`/`>>>>>>>` lines) left,
    the stopped rebase where it was, no other tracked or untracked file changed, no
    control-folder change. Then conductor stages the files and continues the rebase
    (`--skip` if the resolution leaves the replayed commit empty); a later stop does
    the same again, and the merge goes on as usual. The `merge` event lists
    `resolved` files. Output: `reports/<repo>__NNN_name.merge.log` (and
    `.merge.prompt.md`, `.merge.stderr.log`). A merge executor that fails any check
    is a *rebase conflict* below: the rebase is aborted, so the branch keeps the
    item's version and `main` its own, and the `conflict` event carries `resolver`
    (its log) and the failed check as `error`. While it runs, later merges in that
    repo wait, and the tamper guard counts it as a running executor. A restart adopts
    it by pid. A conflict touching any non-prose file is never handed to it.
  - *Rebase conflict:* the rebase is aborted, a `conflict` event names the files, the
    item ends red; branch and worktree are kept, `main` is untouched. A rebase that
    fails with no conflicted file (a stale lock, an untracked file in the way, a file
    held open), a merge executor that cannot start, or a stopped rebase that vanished
    is conductor's machinery, not the item: a hold, retried each tick.
  - *Left its branch:* an executor whose worktree HEAD is no longer on
    `conductor/<NNN_name>` (another branch, detached) ends red, worktree kept — its
    commits are not on the branch conductor would merge.
  - *Main checkout not clean* (tracked changes) or not on `main`: the merge waits,
    logged once as a `hold` (with `merge: true`), and proceeds once it is. Merges
    behind it in the same repo wait too. Untracked files never hold a merge unless the
    fast-forward would overwrite one.
  - *Uncommitted changes* left in the worktree make the item red (they would be lost).
  - *Red item:* never merged; branch and worktree kept for inspection (links removed).
    A report saying `done`/`partial` gets a `conductor:` line under its header (`red
    (…); NOT on main: branch … kept`), so nobody reads it as merged.
  - *Red of conductor's own making:* a control-folder change the guard pins on
    conductor's own git work (only ref moves, during or after a control push since the
    snapshot: a `drift` with `own`, accepted, never reverted) is no red of the
    executor's. The item lands if its own result was `done`/`partial` with a clean
    tree; its `finish` carries `conductor` (the reasons) and its report a `conductor:
    landed on main as … despite …` line. No `red`, so no queue hold.
  - *readonly item:* never merged; its worktree and branch are discarded.

  **Landing a kept branch: `land: <repo>/<NNN_name>`** (`signals.md`). It takes a
  finished item's kept branch through the same squash path (a merge executor for prose
  conflicts, holds while `main` is dirty), from `.conductor/landing/` so a restart
  resumes it. `land` events: `state: started`, then `landed` (sha, commits, squashed);
  the worktree and branch go, the report's `conductor:` line says it landed later, and
  the queue hold, if that item's red holds it, is released (`clear`, by `conductor`). A
  conflict is a `conflict` with `land: true`; branch and worktree stay. Refused for an
  item already on `main`, without a branch, or whose worktree has uncommitted changes.
  This is how a red item's work is recovered: never a hand-written item merging the
  branch.

  *Leftover worktrees.* Removing a merged, readonly or discarded item's worktree can
  fail while another process holds a file in it (a dev server an executor left
  running, an editor): `PermissionError 13` on Windows. The failure is a `warning` and
  a row in `.conductor/leftovers.json`; the item concludes as usual. Conductor retries
  each row at daemon start, before a `disk_cleanup` launches, and whenever that repo's
  queue empties (`queue_empty`, but
  not the row of the item that just emptied it, since whatever held its files a
  moment ago still does). A retry that works logs the `worktree` event the first try
  would have (`removed`/`discarded`, note `leftover removed on retry (...)`) and drops
  the row; one that still fails logs a `warning` and keeps it. A row whose path a live
  item has since taken over (a stage discarded, then the item launched fresh there) is
  dropped without touching the path.

  To remove a kept worktree, delete its folder, then `git worktree prune` and
  `git branch -D conductor/<NNN_name>`. Never `git worktree remove --force` a tree
  that still holds a junction: Git for Windows follows it and empties the target
  (proven on this box). Conductor itself never calls it: it unlinks every directory
  link in the tree (whatever `shared` says now), deletes the files, then prunes.

  A `conductor.toml` request that drops a repo with items in `running/` is refused
  (`invalid`); the previous config stays in force.

## Staging

When a repo's queue head is blocked **only** by a busy lock (the repo's or a named one)
or by a dependency that is still running, and the repo uses worktrees with `stage` on
(an item's `stage:` overrides), conductor stages it: it creates the item's worktree and
launches a session with a stage preamble — read the prompt and what it points at, run
read-only commands only, nothing long or heavy, no edits, no asks (no `--mcp-config`),
write a plan as the final message, stop. The item stays in `queue/`; its record is
`running/<repo>/NNN_name.stage.json` (`stage` event, then `staged` with the plan's
first 500 characters). At most one staged item per repo. A `template: cleanup` or
`template: disk_cleanup` item never stages: what it is handed (audit notes, sizes) is
fixed at launch, and the item it waits on may still change it. Nor does a `resume:` item
(`resume.md`): a stage is a new session, and its point is the original's.

When the head can run, conductor checks the worktree is still exactly as cut (HEAD,
tracked files, and no untracked files beyond what setup made), moves it to current
`main` (`git reset --hard`), reruns setup, and resumes the **same session in the same
worktree** with `claude -p --resume <session id>`. The resume message says the lock
is held, how many commits landed since staging (`git log <old>..HEAD`), and then gives
the full execution preamble. The `start` event carries `resumed: true` and `landed`.

A stage runs setup too, before its session (untracked files are recorded after setup,
so setup output is allowed in a stage). A stage is discarded — `warning` event,
worktree removed, the item launches fresh later — if its setup failed (`stage
discarded: could not stage: <error>; the item launches fresh`), it changed its
worktree, left no result or errored, ran past `stage_minutes`, tampered with the
control folder, or the item left the queue. If the item file changed (during setup,
while staging, or once staged), the stage is discarded the same way but the item may
stage again, from its new text (`… it starts again from the new text`; `items.md`,
*Rewriting a queued item*). The head waits for a stage still running when it could
otherwise start.

**Resume and working directory.** Claude Code stores sessions per working directory
(`~/.claude/projects/<cwd>/<session>.jsonl`). In 2.1.301 `--resume <id>` also found a
session started in a different directory, but conductor resumes in the stage's own
worktree path anyway, so it never depends on that.
