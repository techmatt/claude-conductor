# Ultracode

Claude Code's ultracode is a session setting: once it is on, the session
is told "Ultracode is on" and uses the Workflow tool (multi-agent orchestration) on
every substantive task. In 2.1.302, probed with headless `-p` runs in the sandbox
(Opus 5.5) that quoted their own context:

| route | ultracode on? |
|---|---|
| `"ultracode": true` in the `--settings` file | **yes** |
| `--effort ultracode` | **yes** (also sets effort `xhigh`) |
| the word `ultracode` in the `-p` prompt | no: the keyword triggers only in typed input, never in `-p` |
| a message (`tell:`) saying "use ultracode", delivered by the PostToolUse hook | no: hook context is not the user's prompt |
| the `--settings` file rewritten mid-run to add the key | no: read once at launch |

So conductor uses the first route. Proven through conductor in the sandbox: an
`ultracode: true` item's session carried the "Ultracode is on" notice. Ultracode does
not force a workflow: the notice leaves trivial turns solo, and the executor judges
what counts. A 23-file doc audit (`sandbox/043`) was done solo, without a workflow.
So `ultracode: true` pays off on genuinely large work. An item with `ultracode: true` front-matter
launches (or resumes, after staging) with the key in its `--settings` file; a stage
or merge executor never has it. Switching *mid-run* has no route (a `tell:` is an
ordinary message): queue the work, or a follow-up, with `ultracode: true`. A
Workflow call needs no approval in an executor (`--dangerously-skip-permissions`), and
the Workflow tool is present in every `-p` session; only the opt-in differs.

**Fan-out.** The preamble's subagent rule wins over Workflow's and Ultracode's own notes: at most
2 heavy subagents at once, light reviewers batched, about 10 in all (`executors.md`, *Load budget*).

**No `isolation: worktree`.** Claude Code puts an Agent or Workflow helper's worktree in
the main checkout's `.claude/worktrees`, even from an item's worktree (aicomplete 040),
and the preamble forbids touching the main checkout. So the full preamble says to leave
it off and split helpers by file; there is no setting for the folder, only a `WorktreeCreate`
hook that would take over creating the worktree.
