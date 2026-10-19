Disk space audit of `{{repo}}` (project `{{project}}`). The project takes too much disk.
Find where the space goes and free what can safely be freed.

This item may act outside its working directory, overriding the preamble only as this
text says: it may delete cheaply rebuilt, untracked files in the main checkout (never a
tracked file there, never its branch or index), and move files into the project's Drive
archive. Everything else in the preamble stands.

Links. A Drive-homed folder in the main checkout is a directory junction into Drive, and
every `shared` folder in a worktree is a link. A recursive delete that follows one
empties the real folder. Before any recursive delete, find every junction and symlink
under the target without following them (Python: `os.scandir` with
`entry.is_junction()` / `entry.is_symlink()`), and remove each link itself first
(`os.rmdir` on a junction, `os.unlink` on a symlink), or leave the target alone. Measure
the same way: count a link's target once, at its real home.

1. Measure everything the project occupies, by location: the main checkout's working
   tree; `.git` (`git count-objects -vH`, and the largest blobs in history with the
   commits that added them); dependency installs and caches (`node_modules`, `.venv`,
   tool caches); build output; `scratch/` and other untracked folders; each Drive-homed
   folder at its real home; and the worktrees folder below, leftovers included. Use one
   method throughout (a short Python walk that never follows links is fine) and keep
   the numbers: the report compares before and after.
2. Act under the project's storage policy: every file has one home, git, Drive
   (the project's folder under the `gdrive-sync` folder beside the code repos, as the
   repo's CLAUDE.md or README names it) or nowhere, for anything a command rebuilds
   cheaply.
   - Delete what is cheaply rebuilt or clearly obsolete: stale build output, caches,
     dependency installs nothing uses any more, old scratch output with no other use.
     Say how each comes back (`npm ci`, `uv sync`, a build command).
   - Move what is expensive or irreplaceable but no longer needed day to day into an
     `archive\` folder under the project's Drive home, keeping its relative path.
   - Never delete anything hand-made or irreplaceable (recordings, reference photos,
     originals, anything no command can rebuild): move it to the archive instead.
   - `git gc` and `git worktree prune` are fine. A file that is in use (a dev server,
     an editor) and will not delete is left; say what held it.
   - Never touch a worktree marked live below, and never delete a worktree folder
     yourself: conductor removes its own. List the others under **Deferred** with their
     sizes.
3. Defer anything destructive beyond that: rewriting git history to drop big old blobs,
   deleting a tracked file that is not clearly obsolete, changing where a kind of file
   lives, removing a red item's kept worktree. Never do one; record it under
   **Deferred** with its size and the options.

Conductor measured what you cannot see (the control folder is never yours to touch).
It already shrinks a finished item's run logs by dropping the image bytes and file
copies they repeat; say if what is left still looks too big.

{{usage}}

The report, after its header, has:
- **Sizes**: a before/after table by location (the main checkout and its parts, `.git`,
  each Drive-homed folder, the archive, the worktrees folder, and conductor's control
  folder as measured above), with a total row and the amount freed.
- **Done**: what was deleted or moved, with sizes, and how anything deleted is rebuilt.
- **Deferred**: each larger saving you did not make, with its size, the options, which
  you would pick and why.

The status is `done` when everything safe was freed (deferred items do not make it
partial), `partial` when safe work was left undone, and `red` only when something is
broken (a build or the tests no longer work after a delete).
