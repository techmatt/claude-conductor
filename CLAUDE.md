# Working in claude-conductor

Conductor is a daemon that runs queued Claude Code prompts for a project: per-repo queues, locks, headless executors, an event log, an inbox, questions, and every commit to the project's control folder. `matt-controller-workflow.md` is how a project uses it; `README.md` is how it works. Prompts for this repo are pasted into a Claude Code session by hand — conductor never runs its own queue.

## Conventions

- Python 3.12+, managed with `uv`. Windows-first, cross-platform by construction: `pathlib` only, no absolute paths in tracked code. Process handling specific to Windows lives in one module.
- Every file conductor writes is UTF-8 with LF line endings (`newline="\n"`); `.gitattributes` holds `* text=auto eol=lf`.
- `README.md` is the canon for how conductor works. Facts learned while working — commands, file formats, front-matter fields, event kinds — are promoted there as you pass them, never left only in a report.
- Never touch a real project's control folder or code repo. Testing uses the sandbox (`C:\Code\conductor-sandbox` and `C:\Code\conductor-sandbox-control`) only.

## Standing prompt contract

- Write the final report to `reports/<prompt_name>_report.md` (git-ignored). ~60 lines is a soft target; write it once. Findings, decisions and surprises only; no process narration. Mention tests only for a red you could not fix.
- Clock times are local 12-hour (`5:25pm`).
- A step estimated over ~30 seconds is backgrounded, with a completion waiter on the real process, never on a launcher wrapper.
- Commit to `main` only, by explicit path. "Commit when done." in a prompt means one commit at the end unless the prompt asks for one per phase.
- Every prompt names this repo; if the working directory is not `claude-conductor`, stop and say so.
