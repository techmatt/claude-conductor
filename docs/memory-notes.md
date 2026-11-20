# Executor memory notes

Claude Code keeps a memory per working directory under `~/.claude/projects/<folder>/memory/`
(`<folder>`: the repo's path, each character not a letter or digit as `-`, so
`C:\Code\fractal-wallpapers` is `C--Code-fractal-wallpapers`), indexed by its `MEMORY.md`,
one `- [Title](note.md) — hook` line a note. Executors read it; an item's worktree session
shares its repo's.

No executor may write there: the PreToolUse hook refuses a file tool under `~/.claude`
that is memory or a `CLAUDE.md` (`rules.home_claude`, `hooks.md`). Matt doesn't edit by
hand either, so before 143 a note an audit ruled wrong had no route:
`fractal-wallpapers`' `final-statement-ends-with-report-filename` was flagged by 060, 062
and 063 and stayed. 143 fixed that note by hand (now `reports-copied-to-drive`, its
last-line rule gone, the Drive copy kept) and gave the planner a route.

## `memory_note`

The planner's MCP tool (`mcp.md`): `memory_note(project, repo, note, op, …)` writes
`memory-edits/<stamp>-<n>.md` in the control folder, and the daemon carries it out
(`memory_notes.py`, `process_memory_edits` each tick), writes the receipt and deletes the
request. `memory-edits/` is runtime and ignores itself.

| `op` | takes | does |
|---|---|---|
| `write` | `body`: the whole note; `description`: its index line (needed for a new note) | writes the note; sets its `MEMORY.md` line when `description` is given |
| `edit` | `edits`: `[{old, new}]`, each `old` a piece of the note found exactly once | applies them in order, all or none |
| `delete` | — | removes the note and its `MEMORY.md` line |

A refusal (no such repo or note, `old` missing or found several times, a new note with no
`description`) writes nothing; the receipt says why. Events: `memory_note` (`note`, `op`,
`path`, `result`, `via`) or `memory_refused` (`reason`). `status` lists a `memory_note`
among the session's planner writes; `stats` counts it under `other`.

A prompt or amendment that asks an executor to write under `~/.claude` gets a `WARNING`
line on its receipt naming this tool (`planner_mcp.warn`).
