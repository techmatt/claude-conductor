# CLEANUP — the "cleanup conductor" pass

When Matt says **cleanup conductor**, a Claude Code session in this repo follows this file as written. It is a maintenance pass between prompts, not a numbered prompt: it never counts for "run next prompt". It keeps behaviour unchanged: a fix that changes behaviour is named in the report, and every design call goes to **Deferred** in the report with its options and a pick, never acted on. If following this file shows it is wrong or missing something, fix this file first, then go on, and say so in the report.

The report is `reports/cleanup_<YYYY-MM-DD>_report.md` (a second pass the same day adds `b`, `c`: `cleanup_<YYYY-MM-DD>b_report.md`, and its screenshot likewise), under the standing report contract in `CLAUDE.md` (a `status:` line first). Steps over ~30 seconds run in the background.

## 0. Baseline, before touching anything

- First of all, append `<time> start cleanup_<YYYY-MM-DD>[b]` to `prompts/ledger.md`; the pass ends by appending its `done` line (`CLAUDE.md`, *Run ledger*).
- `uv run python tools/sizes.py --all`: every doc and code file against its budget (below). Keep the output for the report's before column.
- Test timings: `uv run pytest -q` (the fast lane's wall time), `uv run pytest -m "" -q --durations=25` (everything), and `uv run pytest -n0 -q --durations=15` (the fast lane serially: the `slow` rule is serial time). Run them in the background while reading; any failure is a flake or a bug to fix, never to retry past.
- `HANDOFF.md` *Status* `Last cleanup:` names the last pass. Skim the reports since it (`reports/NN_*_report.md`): their surprises and *Deferred* lines are leads.

## 1. Size budgets

Every file over budget gets cut, deduplicated or split. A split doc gets its own `docs/` file and a README index line. `tools/sizes.py` reads this table: a file takes the first row whose glob matches, so specific rows come first. A cleanup may revise a budget, with the reason in the row and the report.

| Path | Budget | Why |
|---|---|---|
| `planner.md` | 20 KB | a planner reads it whole every session |
| `HANDOFF.md` | 16 KB | starts every claude.ai session on conductor; *Open* is a wavefront, not a log |
| `CLAUDE.md` | 6 KB | read at every Claude Code session here |
| `README.md` | 7 KB | an overview and an index into `docs/`, nothing more |
| `CLEANUP.md` | 9 KB | read whole by every cleanup |
| `claude-ai-new-project.md` | 10 KB | read with `planner.md` to start, park or resume a project |
| `docs/*.md` | 12 KB | one topic each, small enough to read whole |
| `src/conductor/daemon.py` | 1100 lines | the loop, launch, stage and finish; four `daemon_*` mixins hold the rest |
| `src/**/*.py` | 900 lines | a module that grows past this has more than one concern |
| `tests/*.py` | 900 lines | one area per file |
| `tools/*.py` | 300 lines | small scripts |
| `**/*.md` | 20 KB | anything else tracked |

## 2. Docs

- **Facts against code.** Every command and flag (`cli.py`), signal verb (`daemon_signals.py`), event kind (`log.append("…"` across `src/`) and its detail, front-matter field (`items.py`), `conductor.toml` key and default (`config.py`), control-folder path (`config.RUNTIME`, `executor.RunPaths`), toast kind (`notify.py`), template name, and app button named in `docs/`. A list a doc presents as complete must be complete. A read-only subagent can sweep this and report quoted snippets; apply the fixes yourself.
- **One home per fact.** `docs/` holds mechanics; `planner.md` what a planner does; `claude-ai-new-project.md` new / park / resume and project setup; README the overview, commands and index. A fact explained in two places keeps one and the other points to it.
- **`planner.md`**: every line must change what a planner does. Its vocab matches the signal verbs.
- **`CLAUDE.md`**: every line must change what a Claude Code session here does; mechanics it repeats from `docs/` become a pointer. Everything it names exists and is current: commands, tools and their flags (`tools/restart_all.py --check`, `tools/sizes.py`), `docs/` files, the sandbox paths, the newest control folder in *Launch the conductor* (re-derive it by its rule), and the phrases' examples (prompt names, report paths).
- **`HANDOFF.md`**: *Status* is current state only. *Open* drops lines that are resolved, stale or proven, and says each remaining one in as few lines as it can.
- **Test counts and timings** live in `docs/development.md` only; update them from the step 4 numbers.

## 3. Code

- **Dead code**: `uvx vulture src/conductor --min-confidence 60` (Qt overrides and `http.server` handler methods are false positives) and `uvx ruff check --select F src tests tools`. Every `add_argument` in `cli.py` is read somewhere.
- **Duplicated logic** is consolidated where it is the same rule, not merely similar code.
- **Modules over budget** split along their concerns (prompt 25: `daemon.py` into `daemon_*` mixins, `app/panels.py` into one module per tab plus `trail.py` and `widgets.py`). Before a split, `grep -rn "monkeypatch.setattr(" tests` for module attributes the tests patch, and move each patch to the new module. Ruff's import fixer writes one name per line; collapse those to the repo's 120-column style.
- **Bugs** found on the way are fixed; one that changes behaviour is named in the report.

## 4. Tests

- The fast lane stays fast: a test at 2.5s or more serially gets `@pytest.mark.slow` or gets faster. Redundant tests (same path, same assertion) go.
- A flaky test is a race to fix in the test or the code (poll for the condition; `env.hold()` instead of a sleep), never a retry or a skip.
- `uv run pytest -m ""` must be green. Measure both lanes again for the report's after column.

## 5. Prove nothing user-facing broke

- Stop the sandbox's daemon if it is running (`state.daemon_info`), then `uv run python tools/sandbox_proof.py C:\Code\conductor-sandbox-control --shot reports/cleanup_<date>_app.png`. It drives the sandbox with the fake executor (an item through queue and merge, `session: start`, a request) and opens the app's window offscreen on it. Look at the screenshot.
- Never a real project's control folder or repo.

## 6. Close

- `HANDOFF.md` *Status*: `Last cleanup: <YYYY-MM-DD>, after prompt NN` (the newest prompt with a report).
- The report holds: before/after sizes for every doc over 5 KB; the largest code modules (lines); test timings before/after (fast, full, and the serial fast-lane top); what was cut or moved, and where to; behaviour-changing fixes; **Deferred**.
- `planner.md` changes only if a vocab or method fact was wrong; it is otherwise not the cleanup's to grow.
- One commit to `main` by explicit path, `Cleanup conductor <date>: …`, after `uv run pytest -m ""` is green.
