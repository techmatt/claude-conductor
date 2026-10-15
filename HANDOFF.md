# HANDOFF — claude-conductor

The one file that starts a claude.ai session for conductor work. Read it, then `README.md` (how conductor works) and `matt-controller-workflow.md` (how a project uses it). Every conductor prompt updates *Status* and *Open* before committing; nothing else in this file changes without a reason.

## How work on conductor flows

- Matt discusses with a claude.ai session linked to the box. The session reads and writes `C:\Code\claude-conductor` over the bridge. It writes each prompt to `prompts/NN_name.md` (git-ignored).
- Matt pastes or names the prompt in a Claude Code session in this repo. Conductor never runs its own queue: its prompts always go this way, by hand.
- CC writes `reports/NN_name_report.md` (git-ignored) and ends with `Report written to <path>`. Matt says "report's in", and the session reads it.
- Testing uses the sandbox only: `C:\Code\conductor-sandbox` and `C:\Code\conductor-sandbox-control`.

## Design decisions (settled 2026-10-15)

- **Three roles.** The claude.ai planner works files-only. The conductor daemon owns every process. Executors are headless CC run with `--dangerously-skip-permissions`.
- **One install, one instance per project.** Each project has its own control folder, which is a git repo. The handoff docs live there and never in a code repo.
- **Tracked control files change only through conductor's request/apply step.** The tamper guard reverts anything else that happens during an executor run.
- **The planner never deletes.** It writes `signals/` files (read mark, `clear: REPO`), and conductor consumes them.
- **The ask tool is the executor's judgment.** The executor decides whether a question blocks or falls back to a default after 10 minutes. There are no phone notifications; desktop toasts only.
- **Approval.** Matt approves what enters `queue/`. Items in `proposed/` wait for him. A `readonly` item may go straight to the queue.
- **Staging is a resumable session:** a read-only orient phase first, then the same session resumes when it holds the lock.
- **Concurrency within a repo is declared, not judged.** It uses disjoint `paths:` plus per-item worktrees, with serial merges.

## Phases

1. Core: queues, executors, event log, inbox, tamper guard, requests, restart rule. **Done** (`reports/01_core_report.md`).
2. The ask tool, toasts, `conductor init`, and status definitions. **Done** (`reports/02_ask_report.md`).
3. Worktrees and staging: per-item worktrees, serial merges, `paths:` concurrency, stage-then-resume. **Done** (`reports/03_worktrees_report.md`).
4. The app: a PySide6 tray app with Repos, Questions, Events and Proposed panels, reading `events.db`.
5. Anything still missing for the first real project.

## Status

Phases 1–3 done. Phase 4 (the app) is next.

## Open

- Tamper during concurrent runs ends every running item red, because conductor can't attribute it. This is acceptable while tamper never actually occurs. `paths:` concurrency makes concurrent runs in one repo more common.
- Each code repo's `CLAUDE.md` must say "commit to the branch you are on", not "commit to `main`". The sandbox's was changed. Any real repo's first prompt carries this.
- `setup` commands run inside the daemon loop, so a slow setup delays everything else for that long. This is fine for `uv sync`-sized steps. Move setup into a launcher if a project needs minutes.
- A fast-forward of the main checkout interrupted mid-write (the daemon killed during `git merge --ff-only`) leaves tracked files dirty. The merge then holds as "uncommitted changes" until Matt resets the checkout. It is rare and needs a person.
