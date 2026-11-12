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
So `ultracode: true` pays off on genuinely large work. Nor on sim-heavy work: aicomplete
201 (`ultracode: true`, a rules item whose work was sims) ran no Workflow, since a sim is one
process to wait on, not parts to fan out; `planner.md` says to leave it off such items (110).
An item with `ultracode: true` front-matter
launches (or resumes, after staging) with the key in its `--settings` file; a stage
or merge executor never has it. Switching *mid-run* has no route (a `tell:` is an
ordinary message): queue the work, or a follow-up, with `ultracode: true`. A
Workflow call needs no approval in an executor (`--dangerously-skip-permissions`), and
the Workflow tool is present in every `-p` session; only the opt-in differs.

**Fan-out.** The preamble's subagent rule wins over Workflow's and Ultracode's own notes: at most
2 heavy subagents at once, light reviewers batched, and at most 10 agents per Workflow, finders and
verifiers both, unless the prompt gives another number (`preambles.md`, *Load budget*). "Every
substantive task" decides when to use a workflow, never how big one is (aicomplete 068 ran 22
agents against "about 10", ~800k tokens). The count is a preamble rule; conductor doesn't enforce it.
A helper's brief quotes the prompt's rulings word for word, never a paraphrase (79: aicomplete
111's helpers read a paraphrased price ceiling as a target band).

**No `isolation: worktree`.** Claude Code puts an Agent or Workflow helper's worktree in
the main checkout's `.claude/worktrees`, even from an item's worktree (aicomplete 040),
and the preamble forbids touching the main checkout. So the full preamble says to leave
it off and split helpers by file; there is no setting for the folder, only a `WorktreeCreate`
hook that would take over creating the worktree.
