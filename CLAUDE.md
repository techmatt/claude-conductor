# Working in claude-conductor

Conductor is a daemon that runs queued Claude Code prompts for a project and makes every commit to its control folder. `planner.md` is how a planner session uses it (from its *Start* to its vocab); `claude-ai-new-project.md` is starting, parking and resuming a project; `README.md` and `docs/` are how it works. Prompts for this repo are pasted into a Claude Code session by hand — conductor never runs its own queue.

## Conventions

- Python 3.12+, managed with `uv`. Windows-first, cross-platform by construction: `pathlib` only, no absolute paths in tracked code. Process handling specific to Windows lives in one module.
- Every file conductor writes is UTF-8 with LF line endings (`newline="\n"`); `.gitattributes` holds `* text=auto eol=lf`.
- `README.md` + `docs/` are the canon for how conductor works: README is an overview and an index, each `docs/` file one topic small enough to read whole. Facts learned while working — commands, file formats, front-matter fields, event kinds — are promoted to the matching topic file as you pass them, never left only in a report. A new topic gets its own file and an index line.
- Tests: `uv run pytest` is the fast lane for after each change; `uv run pytest -m ""` runs everything and is what must be green before a commit (timings: `docs/development.md`). A test that takes 2.5s or more serially gets `@pytest.mark.slow` (`docs/development.md`).
- Never touch a real project's control folder or code repo. Testing uses the sandbox (`C:\Code\conductor-sandbox` and `C:\Code\conductor-sandbox-control`) only.

## "Launch the conductor"

When Matt says "launch the conductor" (or "open conductor" and the like), run `uv run conductor app <control>` from this repo, backgrounded, where `<control>` is the most recent control folder: the `C:\Code\*-control` folder holding a `conductor.toml` whose first commit is newest (today `C:\Code\workbench-control`). The app also reopens every remembered project, so naming the newest one is enough. It doesn't start the daemon (*Start daemon* or `uv run conductor run <control>` does), and launching it is not a test, so the sandbox-only rule below does not apply.

## "Restart all"

When Matt says "restart all" (or "restart everything" and the like), restart the app and every live daemon: run `uv run python tools/restart_all.py`, with no check and no go first. Running items are adopted, never interrupted: each daemon restarts at its own safe point, which can take up to 10 minutes, so background it with a waiter. Exit 1 means a daemon still waits after that (the output names what on); report it, and run `--force` (stop and start now) only on Matt's word. Never restart either by hand as a Claude Code background command; the tool starts both detached (`docs/restart.md`). Say you are restarting before you run it: Matt times a restart.

## "Run next prompt"

Planners write prompts to `prompts/inbox/<YYYYMMDD-HHMMSS>-<project>-<slug>.md`, unnumbered; a prompt gets its number when it runs (`tools/prompts.py`). Files a prompt reads live in `prompts/support/`.

- "Run next prompt" (and the like): `uv run python tools/prompts.py next`. It lists what's waiting, oldest first, takes the oldest, numbers it (`prompts/NN_<slug>.md`) and writes the ledger's `start` line. "Run <slug>" / "run <file>": `... run <name>`, out of order (an old numbered prompt too).
- It refuses while the ledger has a `start` with no `done`: relay the name and start time. Only on Matt's word that it's stale ("run anyway") add `--anyway`, which records it.
- Then say "Running NN_slug", run exactly that file as if pasted, end with `Report written to <path>`, and never chain on. Say how many still wait from a fresh `list` at the end.
- Nothing waiting: say so in one line and do nothing else.

There is no "run all prompts" phrase.

## "Cleanup conductor"

When Matt says "cleanup conductor" (or "conductor cleanup" and the like), follow `CLEANUP.md` as written: a maintenance pass with its report in `reports/` (named in `CLEANUP.md`). It is not a numbered prompt, so "run next prompt" never picks it, and it never runs unasked. `uv run python tools/sizes.py` shows any file over its size budget at a glance, in any session.

## Standing prompt contract

- **Run ledger** (`prompts/ledger.md`, git-ignored, append-only): a prompt's `start` line comes from `tools/prompts.py`, a cleanup pass's first action appends `<time> start <name>`; the last action of either is `uv run python tools/prompts.py done <name> <report path>` (or `stopped`).
- Write the final report to `reports/<prompt_name>_report.md` (git-ignored), starting with a `status: done | partial | red` line (the planner's `session.md` digest shows it). ~60 lines is a soft target; write it once. Findings, decisions and surprises only; no process narration. Mention tests only for a red you could not fix.
- Clock times are local 12-hour (`5:25pm`).
- A step estimated over ~30 seconds is backgrounded, with a completion waiter on the real process, never on a launcher wrapper.
- A prompt that changes conductor's user-facing behaviour (commands, signals, front-matter, files the planner reads or writes, the app) updates `planner.md` — method and **Vocab** — in the same commit, and `claude-ai-new-project.md` where it covers the topic (new / park / resume, memory, run targets, picks). Keep `planner.md` compressed: it is read whole each session.
- Before committing, update `HANDOFF.md` *Status* and *Open* to match what the prompt left behind, moving in the lines under *For HANDOFF Open* in `planner-notes.md` (then delete them there), and commit it with the rest.
- Commit to `main` only, by explicit path, then `git push`. "Commit when done." in a prompt means one commit at the end unless it asks per phase.
- Every prompt names this repo; if the working directory is not `claude-conductor`, stop and say so.
