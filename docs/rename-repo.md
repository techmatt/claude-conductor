# Renaming a repo

```
conductor rename-repo <old> <new> [--path <new folder>] [--control <folder>]
```

gives a repo a new key (and, with `--path`, a new checkout folder) in one step
(`rename.py`). It refuses while the daemon runs, while `queue/<old>` or `running/<old>`
holds anything, or while a worktree is left under `<worktree_root>/<old>`. With
`--path`, a checkout still at the old folder is moved there; either way its worktrees are
repaired and pruned. Then:

- **Renamed:** `queue/ backlog/ running/ done/<old>`, any `receipts/` folder named
  `<old>`, `<worktree_root>/<old>`, every file or folder named `<old>__…` or `…__<old>__…`
  in `reports/`, `questions/`, `picks/` and `.conductor/` (mail, progress, touch,
  landing, setup, cancelled), and `.conductor/push/<old>.log`. Reports keep no old
  names, so every reader finds them under the new key.
- **Rewritten:** run records, receipts, questions, picks, `.conductor/` journals (memory
  samples included) and `inbox.md` / `session.md` / `review.md`; items' `repo:`
  front-matter (not their bodies); and in events.db every row's repo, item, paths and
  detail. Only conductor's spellings of the key change (`<old>/NNN_x`, `<old>__`,
  `queue/<old>`, `` `<old>` ``, the checkout's and worktree folder's paths), never the
  bare word in prose. events.db is append-only; this is the one writer that edits rows
  (its triggers dropped and restored inside one transaction), and it logs
  `repo_renamed` (old, new, path, moved, sha) after.
- **Committed:** `conductor.toml` (the table, `repo =` lines, a tool's `cwd`; `path` when it
  moved) and `state.md` (the same spellings), as `rename repo <old> -> <new>`, with the
  guard re-snapshotted so the next start sees no drift.

Executors' own files (reports, logs, prompts) and item bodies are history and keep
their text. Transcripts are found by session id, so `open` and `resume:` are
unaffected; Claude Code's `~/.claude/projects/C--Code-<old>` is renamed by hand if wanted.
