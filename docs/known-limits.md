# Known limits

Gaps nobody plans to fix unless they bite. Each says what would change that. A line that starts biting moves to `HANDOFF.md` *Open* as a bug, or a prompt.

## Queue, items and merges

- **Staged items and late amendments.** A staged item sees amendments only in its resume message (worded as overriding the plan); a stage started after an amendment was written still doesn't see it.
- **Leftover worktrees.** One is retried only at daemon start, before a `disk_cleanup` launches, and when its repo's queue empties (never for the item that just emptied it). If a stray dev server keeps one around, add a periodic retry.
- **`resolve` checks structure, not intent.** The merge executor is checked for markers gone and nothing else touched; whether it kept both sides' intent shows only by reading (in the sandbox it joined two rewrites of one sentence with a semicolon).
- **Hard cases for a code repo's `CLAUDE.md`.** A repo asking for one-line commits conflicts with Claude Code's `Co-Authored-By` trailer.

## Control folder and requests

- **Tamper attribution.** A change is attributed (30) only to an item whose hook saw it name a changed control path. A change made by a script that never names the path, or by a stage or merge executor (no hook), still ends every active item red.
- **Half-written request targets.** Requests trust the planner to write `request.md` last: a target still being written by a writer that allows shared reads could be read half-done (the readable check catches only a writer holding it locked).
- **Receipts can lag a heavy tick** by 2.5–3.2s. On 2026-10-30 the planner's first stage missed one at least six times (once over a minute under load); on 2026-10-31 never. If it recurs: stage once after a short wait, or a write returns when its receipt exists.

## Executors

- **Messages during a subagent.** A message reaches an executor only after one of the lead's own tool calls ends (an `ask` hands back for one, 58). During a subagent or Workflow call, an idle wait or at the run's end it is logged undelivered. Fallback: stream into `-p --input-format stream-json` stdin (`executors.md`).
- **`ask X` after transcript pruning.** It needs the original's transcript, which Claude Code prunes after `cleanupPeriodDays` (30 by default); then it fails red at launch. `report_changed` checks only reports reviewed since 32 (no hashes before).
- **A reply is positional.** It is the first `progress` line after a delivery, so a milestone posted first shows as the reply. If that misfires, the preamble could ask for `re #N`.
- **`quiet` counts pipes.** `Tree.writes` counts pipe writes, so a chatty tool looks like work and holds a `quiet` off.
- **No `quiet` for timing work.** A request that holds other runs while one times something was not built; the load line only shows the contention.

## Processes and the daemon

- **Executors are the daemon's own children** (exit codes kept), so a tree kill of the daemon takes them; only an app-started daemon is detached.
- **Reaper matching.** An orphan is matched by its executor's job (53), else by working folder or command line inside the item's run window. A leftover started while staging is matched only from the launch on, and `worktrees = false` items are never reaped, job or not.
- **Self-restart.** A second commit during a blocked wait is not re-checked (the new daemon runs what is on disk), and the handshake wait (up to 20s) holds the old daemon's loop.
- **What Claude Code's reaper calls "critically low" is unknown.** After a reap, compare the `daemon_died` event's memory with `memory.jsonl` around it. The one died daemon since memory sampling (aicomplete, 2026-10-30 5:00pm) had 14.7 GB free, so it was no reap.
- **`OCControl.Service`** (Alienware OC Controls) swings free RAM by ~10 GiB every ~3 minutes on this box, so the default `memory_pressure_free_gb` (3) trips at its peaks under real work. The fix is outside conductor: stop the service or update AWCC.

## Digest and planner rules

- **A hidden red.** A red the planner never saw but wrote `state.md` after is hidden from the digest (46); *Now* and the app still show its effect.
- **Digest *Other*** still piles up daemon restarts and stage chatter after the last `state.md` request.
- **Reports finished before `reviewed:` existed** have no `reviewed` event: on Corelight they list as unreviewed until marked (**process all**).
- **Deferred design calls** live only in a cleanup report's **Deferred** and, once raised, `state.md`'s *Paged*. Conductor tracks none; a standing inbox section could, if they get lost.
- **Audit notes are uncapped.** A cleanup template inlines every unused audit note into its prompt. If a repo collects dozens, cap the count per cleanup (oldest first).
- **31's planner action rules** (signals only from Matt's words, `reviewed:` only inside processing) are rules, not checks.
- **`planner.md` *Start* step 3** assumes a workspace shell and `TZ=America/Los_Angeles`. Matt's saved preference must carry steps 1–5, since the file is first read at step 5.

## App and pickers

- **The app's refresh runs on the UI thread.** It probes run targets there (0.3s timeout each) and re-reads every `finish`, `red`, `clear` and `reviewed` event. Move it to a worker if it gets sluggish.
- **Only the latest checkpoint shapes the views.** The Repos tab's earlier group shows the 10 newest settled items per repo; older ones are in Events (*since checkpoint* off), `conductor events --since checkpoint N` or `conductor open`.
- **`uv run conductor app` locks `.venv\Scripts\conductor.exe`** while the app is open, so any `pyproject.toml` change makes every `uv run` fail until it closes. `uv run python -m conductor app` would avoid it.
- **A restart loses the picker server's port and token,** so an open picker tab can't save until `pick:` is resent. A fixed port and a token in `.conductor/` would fix it.

## Costs

- **Run logs** are compacted, then gzipped (54); nothing is dropped. If the gzipped total grows too fast, drop logs of items reviewed long ago.
- **A Claude Docs tab exported into a prompt costs double tokens** (base64 inline, re-emitted into a shell command). An export saved straight to a file the bridge commits would be cheaper.
