# Working in claude-conductor

Conductor is a daemon that runs queued Claude Code prompts for a project: per-repo queues, locks, headless executors, an event log, an inbox, questions, and every commit to the project's control folder. `claude-ai-new-project.md` is how a project uses it (and, as its last section, the planner's vocab); `README.md` is how it works. Prompts for this repo are pasted into a Claude Code session by hand — conductor never runs its own queue.

## Conventions

- Python 3.12+, managed with `uv`. Windows-first, cross-platform by construction: `pathlib` only, no absolute paths in tracked code. Process handling specific to Windows lives in one module.
- Every file conductor writes is UTF-8 with LF line endings (`newline="\n"`); `.gitattributes` holds `* text=auto eol=lf`.
- `README.md` is the canon for how conductor works. Facts learned while working — commands, file formats, front-matter fields, event kinds — are promoted there as you pass them, never left only in a report.
- Tests: `uv run pytest` is the fast lane (~30s) for after each change; `uv run pytest -m ""` runs everything (~65s) and is what must be green before a commit. A test that takes 2.5s or more serially gets `@pytest.mark.slow` (README *Development*).
- Never touch a real project's control folder or code repo. Testing uses the sandbox (`C:\Code\conductor-sandbox` and `C:\Code\conductor-sandbox-control`) only.

## "Launch the conductor"

When Matt says "launch the conductor" (or "launch conductor", "start the conductor app", "open conductor" and the like), run `uv run conductor app <control>` from this repo, backgrounded, where `<control>` is the most recent control folder: the `C:\Code\*-control` folder holding a `conductor.toml` whose first commit is newest (today `C:\Code\wordwarriors-control`). The app also reopens every remembered project, so naming the newest one is enough. It is a viewer only — it does not start the daemon (`uv run conductor run <control>`), and launching it is not a test, so the sandbox-only rule below does not apply.

## "Run next prompt"

When Matt says "run next prompt" (or "run the next prompt" and the like):

- **Next** is the lowest-numbered prompt in `prompts/` after the newest one that has a report in `reports/` (`NN_name.md` ↔ `NN_name_report.md`), in natural order: 12 < 12a < 13.
- Name it first ("Running 19_run_next_and_maintenance"), so a wrong pick is obvious, then run exactly that one as if pasted, ending with `Report written to <path>` as usual. Never chain on to the next one.
- With several unrun, run only the lowest and say how many remain.
- An **older** prompt with no report (skipped or superseded) is never run by this phrase; mention it in one line in case it was missed.
- **None:** say so in one line ("No unrun prompt; the latest is 18_readonly_concurrency_and_report_marker, and its report is in") and do nothing else: no invented work, no reruns.

There is no "run all prompts" phrase.

## Standing prompt contract

- Write the final report to `reports/<prompt_name>_report.md` (git-ignored). ~60 lines is a soft target; write it once. Findings, decisions and surprises only; no process narration. Mention tests only for a red you could not fix.
- Clock times are local 12-hour (`5:25pm`).
- A step estimated over ~30 seconds is backgrounded, with a completion waiter on the real process, never on a launcher wrapper.
- A prompt that changes conductor's user-facing behaviour (commands, signals, front-matter, files the planner reads or writes, the app) updates `claude-ai-new-project.md` — method and **Vocab** — in the same commit.
- Before committing, update `HANDOFF.md` *Status* and *Open* to match what the prompt left behind, and commit it with the rest.
- Commit to `main` only, by explicit path. "Commit when done." in a prompt means one commit at the end unless the prompt asks for one per phase.
- Every prompt names this repo; if the working directory is not `claude-conductor`, stop and say so.
