# Working in claude-conductor

Conductor is a daemon that runs queued Claude Code prompts for a project and makes every commit to its control folder. `planner.md` is how a planner session uses it (from its *Start* to its vocab); `claude-ai-new-project.md` is starting, parking and resuming a project; `README.md` and `docs/` are how it works. Prompts for this repo are pasted into a Claude Code session by hand — conductor never runs its own queue.

## Conventions

- Python 3.12+, managed with `uv`. Windows-first, cross-platform by construction: `pathlib` only, no absolute paths in tracked code. Process handling specific to Windows lives in one module.
- Every file conductor writes is UTF-8 with LF line endings (`newline="\n"`); `.gitattributes` holds `* text=auto eol=lf`.
- `README.md` + `docs/` are the canon for how conductor works: README is an overview and an index, each `docs/` file one topic small enough to read whole. Facts learned while working — commands, file formats, front-matter fields, event kinds — are promoted to the matching topic file as you pass them, never left only in a report. A new topic gets its own file and an index line.
- Tests: `uv run pytest` is the fast lane for after each change; `uv run pytest -m ""` runs everything and is what must be green before a commit (timings: `docs/development.md`). A test that takes 2.5s or more serially gets `@pytest.mark.slow` (`docs/development.md`).
- Never touch a real project's control folder or code repo. Testing uses the sandbox (`C:\Code\conductor-sandbox` and `C:\Code\conductor-sandbox-control`) only.

## "Launch the conductor"

When Matt says "launch the conductor" (or "open conductor" and the like), run `uv run conductor app <control>` from this repo, backgrounded, where `<control>` is the most recent control folder: the `C:\Code\*-control` folder holding a `conductor.toml` whose first commit is newest (today `C:\Code\wordwarriors-control`). The app also reopens every remembered project, so naming the newest one is enough. It doesn't start the daemon (*Start daemon* or `uv run conductor run <control>` does), and launching it is not a test, so the sandbox-only rule below does not apply.

## "Restart all"

When Matt says "restart all" (or "restart everything" and the like), restart the app and every live daemon: run `uv run python tools/restart_all.py --check` first. Exit 1 means something is active (a running or staged item, an open question): list it and wait for Matt's go before restarting. Exit 0 means run `uv run python tools/restart_all.py`. Never restart either by hand as a Claude Code background command; the tool starts both detached (`docs/restart.md`). The warning comes first: Matt times a restart.

## "Run next prompt"

When Matt says "run next prompt" (or "run the next prompt" and the like):

- **Next** is the lowest-numbered `prompts/NN_name.md` after the newest one with a `done` line in `prompts/ledger.md` (before the ledger: a report in `reports/`), in natural order: 12 < 12a < 13. Files a prompt reads live in `prompts/support/`.
- Name it first ("Running 19_run_next_and_maintenance"), so a wrong pick is obvious, then run exactly that one as if pasted, ending with `Report written to <path>` as usual. Never chain on to the next one.
- With several unrun, run only the lowest and say how many remain.
- An **older** prompt with no report (skipped or superseded) is never run by this phrase; mention it in one line in case it was missed.
- **None:** say so in one line ("No unrun prompt; the latest is 18_x, and its report is in") and do nothing else: no invented work, no reruns.
- **What's left is checked at the end:** "that was the last unrun prompt" comes from a fresh look at `prompts/` against the ledger after the run, never from the start.

There is no "run all prompts" phrase.

## "Cleanup conductor"

When Matt says "cleanup conductor" (or "conductor cleanup" and the like), follow `CLEANUP.md` as written: a maintenance pass with its report in `reports/` (named in `CLEANUP.md`). It is not a numbered prompt, so "run next prompt" never picks it, and it never runs unasked. `uv run python tools/sizes.py` shows any file over its size budget at a glance, in any session.

## Standing prompt contract

- **Run ledger** (`prompts/ledger.md`, git-ignored, append-only): the very first action of every prompt or cleanup pass appends `<time> start <NN_name>`, and the last `<time> done <NN_name> → <report path>` (or `stopped`).
- Write the final report to `reports/<prompt_name>_report.md` (git-ignored), starting with a `status: done | partial | red` line (the planner's `session.md` digest shows it). ~60 lines is a soft target; write it once. Findings, decisions and surprises only; no process narration. Mention tests only for a red you could not fix.
- Clock times are local 12-hour (`5:25pm`).
- A step estimated over ~30 seconds is backgrounded, with a completion waiter on the real process, never on a launcher wrapper.
- A prompt that changes conductor's user-facing behaviour (commands, signals, front-matter, files the planner reads or writes, the app) updates `planner.md` — method and **Vocab** — in the same commit, and `claude-ai-new-project.md` where it covers the topic (new / park / resume, memory, run targets, picks). Keep `planner.md` compressed: it is read whole each session.
- Before committing, update `HANDOFF.md` *Status* and *Open* to match what the prompt left behind, moving in the lines under *For HANDOFF Open* in `planner-notes.md` (then delete them there), and commit it with the rest.
- Commit to `main` only, by explicit path, then `git push`. "Commit when done." in a prompt means one commit at the end unless it asks per phase.
- Every prompt names this repo; if the working directory is not `claude-conductor`, stop and say so.
