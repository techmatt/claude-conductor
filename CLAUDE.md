# Working in claude-conductor

Conductor is a daemon that runs queued Claude Code prompts for a project: per-repo queues, locks, headless executors, an event log, an inbox, questions, and every commit to the project's control folder. `claude-ai-new-project.md` is how a project uses it (and, as its last section, the planner's vocab); `README.md` is how it works. Prompts for this repo are pasted into a Claude Code session by hand — conductor never runs its own queue.

## Conventions

- Python 3.12+, managed with `uv`. Windows-first, cross-platform by construction: `pathlib` only, no absolute paths in tracked code. Process handling specific to Windows lives in one module.
- Every file conductor writes is UTF-8 with LF line endings (`newline="\n"`); `.gitattributes` holds `* text=auto eol=lf`.
- `README.md` is the canon for how conductor works. Facts learned while working — commands, file formats, front-matter fields, event kinds — are promoted there as you pass them, never left only in a report.
- Never touch a real project's control folder or code repo. Testing uses the sandbox (`C:\Code\conductor-sandbox` and `C:\Code\conductor-sandbox-control`) only.

## "Launch the conductor"

When Matt says "launch the conductor" (or "launch conductor", "start the conductor app", "open conductor" and the like), run `uv run conductor app <control>` from this repo, backgrounded, where `<control>` is the most recent control folder: the `C:\Code\*-control` folder holding a `conductor.toml` whose first commit is newest (today `C:\Code\wordwarriors-control`). The app also reopens every remembered project, so naming the newest one is enough. It is a viewer only — it does not start the daemon (`uv run conductor run <control>`), and launching it is not a test, so the sandbox-only rule below does not apply.

## Standing prompt contract

- Write the final report to `reports/<prompt_name>_report.md` (git-ignored). ~60 lines is a soft target; write it once. Findings, decisions and surprises only; no process narration. Mention tests only for a red you could not fix.
- Clock times are local 12-hour (`5:25pm`).
- A step estimated over ~30 seconds is backgrounded, with a completion waiter on the real process, never on a launcher wrapper.
- A prompt that changes conductor's user-facing behaviour (commands, signals, front-matter, files the planner reads or writes, the app) updates `claude-ai-new-project.md` — method and **Vocab** — in the same commit.
- Before committing, update `HANDOFF.md` *Status* and *Open* to match what the prompt left behind, and commit it with the rest.
- Commit to `main` only, by explicit path. "Commit when done." in a prompt means one commit at the end unless the prompt asks for one per phase.
- Every prompt names this repo; if the working directory is not `claude-conductor`, stop and say so.
