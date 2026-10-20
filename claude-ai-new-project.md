# claude-ai-new-project — starting, parking and resuming a project

**Read `planner.md` first.** It is the method for every session and the vocab. This file adds what only a new, parked or resumed project needs, plus detail on memory, run targets and picks beyond what `planner.md` needs to act. How conductor itself works is in its `README.md` and `docs/`.

## Layout (per project)

```
C:\Code\<project>-control\        git; the planner's world
  state.md, <other handoff docs>   tracked — changed ONLY through a request
  conductor.toml                   tracked — repos, named locks, defaults, run targets
  queue\<repo>\NNN_name.md         approved work, run in NNN order
  proposed\NNN_name.md             awaiting Matt's approval
  running\ done\                   conductor moves items through these
  reports\<repo>__NNN_name.md      executor reports
  questions\                       open asks and their answers
  picks\                           Matt's saved picker choices (<name>.choices.json)
  requests\                        planner → conductor (apply + commit)
  signals\                         planner/app → conductor (the verbs are in planner.md, Vocab)
  inbox.md                         generated digest since the planner's last read
  session.md                       the start digest of the latest `session:` signal
  events.db                        append-only event log (SQLite)
  .conductor\                      conductor's own state (never read it whole)
C:\Code\<repo>\                    code repos; executors work here
C:\Code\<project>-worktrees\       one git worktree per running item
```

Everything except the tracked docs and `conductor.toml` is git-ignored runtime state. A control folder made before a runtime path existed (`picks/`, `session.md`) lacks it in its `.gitignore`, and conductor logs a startup `warning` until a request adds the line.

## Storage

`C:\Code\gdrive-sync` is a Google Drive sync folder. Every project file has exactly **one home**:

- **git:** source, data, small chosen outputs, and anything hand-made (recordings).
- **Drive:** `C:\Code\gdrive-sync\<project>\<folder>`, for files that are expensive, irreplaceable or private but don't belong in git: full-res generated originals, reference photos, slow or fragile generated media.
- **Nowhere:** anything a command rebuilds cheaply.

A Drive-homed folder appears in the repo's main checkout as a directory junction to its Drive folder, git-ignored in the repo. The repo's `shared` setting in `conductor.toml` links it into every worktree, so there is one copy, not two; Drive's own version history is the backup. The planner asks for access to `C:\Code\gdrive-sync` only when a task needs it.

A new Drive-homed folder starts with the queue item that creates it. That item also makes the main-checkout junction, and its prompt says so explicitly, because conductor's preamble otherwise forbids touching the main checkout. The `shared` entry is added by request only after that item merges; added earlier, conductor would create an empty real folder at that path in the main checkout first.

## Starting a new project

1. Matt names the project and its code repos. Nothing is authored before every name is fixed.
2. The planner gives Matt the one command that creates the control folder, and Matt runs it in `C:\Code\claude-conductor`:
   `uv run conductor init C:\Code\<project>-control --project <project> --repo <name>=C:\Code\<repo> …`
   It writes the layout, `conductor.toml`, a stub `state.md` and the `.gitignore`, then makes the first commit. It refuses a folder that already has files in it. Matt creates any code repo that doesn't exist yet.
3. The planner asks for folder access to the control folder and each repo (and to `C:\Code\gdrive-sync` if the project will home files on Drive: *Storage*). Access can only be granted to a folder that exists.
4. The planner writes the first real `state.md` (with *Waiting on Matt*, *Open questions*, *Paged* and *Maintenance* sections: `planner.md`), plus any `conductor.toml` change, as one request. That covers locks, `max_hours` and `ask_default_minutes`, and per repo: the `shared` untracked folders and `setup` commands its worktrees need; its `prose` globs if the default `CLAUDE.md`/`README.md`/`docs/**/*.md` doesn't fit; its `background` globs for test or build commands that can run over ten minutes; or `worktrees = false`. A `[run.<name>]` target comes once there is something to run (*Run targets*). It also writes each code repo's `CLAUDE.md` standing contract as a first queued prompt for that repo. The contract holds:
   - the report: path and header from conductor's preamble, a ~60-line soft target, findings, decisions and surprises only, tests mentioned only for an unfixed red, and the `Report written to <path>` sign-off;
   - "commit to the branch you are on";
   - local 12-hour clock times;
   - steps over ~30 seconds backgrounded, with a completion waiter on the real process, never on a launcher wrapper;
   - LF line endings (`.gitattributes` `* text=auto eol=lf`);
   - operational facts learned while working promoted into the repo's README, never left only in a report.
5. Matt starts conductor for the project: the app's **Start daemon**, or `uv run conductor run C:\Code\<project>-control` in a plain terminal. Never as a Claude Code background command: when that session ends or reaps its background commands, the daemon and every executor die with it (the daemon logs a startup `warning` when it finds itself there). From then on, work flows through the queue, and each session starts with **start a new <project> session**.

Matt can watch and act without the planner in the desktop app (`uv run conductor app C:\Code\<project>-control`, run in `C:\Code\claude-conductor`). There is only ever one app: launching it again with another control folder adds that folder as a tab in the running app.

## Park / resume a project

Parking puts a project away whole (to free the box, or to move its folders), with nothing left running or half-done.

1. Nothing is in flight. No item is running, staged or merging, and no question is open. Cancel or let finish what's queued; anything still queued stays queued and runs on resume.
2. **Closeout** as a request, **while the daemon still runs**: requests apply only through the daemon.
3. `run: <target> stop` for every run target that is up.
4. `stop: daemon` (**stop conductor**).
5. Matt clears what's left in `C:\Code\<project>-worktrees\`. A worktree's git link is an absolute path, so a worktree can't move with the folders. A kept red worktree gives its branch `conductor/<NNN_name>` to the repo, so deleting the folder loses no commit. A leftover that conductor couldn't remove (a dev server held it) goes too. Delete the folder, then run `git worktree prune` in the repo (`docs/worktrees.md`).
6. In the app, **Remove project**. This only forgets the folder; nothing on disk changes.

The control folder and the code repos may then move, as a set, keeping their relative layout: `conductor.toml` names the repos and `worktree_root` by relative path. To resume: move them back (or keep the same layout elsewhere), start the daemon (`uv run conductor run <control folder>` in `C:\Code\claude-conductor`, or the app's Start), **Add project…** in the app, then **start a new <project> session**.

**park project** checks nothing is in flight, does **closeout** while the daemon runs, stops every run target, signals `stop: daemon`, then tells Matt which worktree folders are left to clear and to use **Remove project**. **resume project** tells Matt to start the daemon and **Add project…**, then does **start a new <project> session**.

## Memory

The memory line (first on `inbox.md`'s *Now*, the app header's second line, and `conductor status`) reads `memory 29.4 of 36.6 GiB committed (80%), 10.9 GiB free · biggest <repo>/<NNN> 2.1 GiB · ok`. "Committed" against its limit is what runs out on Windows (RAM plus page file). Conductor samples it every 30 seconds, with each running item's processes by kind (the executor and its subagents, browsers, Vite, node, shells), into `.conductor\memory.jsonl`. Thresholds are in `conductor.toml` `[defaults]` (`memory_pressure_free_gb` 3, `memory_critical_free_gb` 1.5, and the commit backstops `memory_pressure_percent` 90, `memory_critical_percent` 95) and change through a request.

- **`memory`, level `pressure`** (toasted): no launch, resume or stage (the *Now* line says *launches and stages paused*), and each running item gets a `tell` from conductor (`by: conductor`): one browser and one test worker, close idle servers, no new parallel subagents or workflows. A `memory` event back at `ok` comes only after five minutes clear, so a swinging box doesn't flap; it resumes starts, and told items hear they may go back to normal.
- **`memory_critical`** (red, toasted): the same, harder (one process at a time, commit work so far).
- **`reaped`**: when an item finishes, conductor kills what it left running in its worktree (a Vite server, a browser, a waiter loop) and names each process and the MiB freed. Run targets are never touched.
- **`daemon_died`** (red, toasted, logged at the next start): the previous daemon was killed without stopping, with its last heartbeat, how long nothing ran, memory then, and what was running. The app's header says **died** (or **not answering** for a stuck daemon) rather than *not running*.

Mechanics: `docs/memory.md`.

## Run targets

A project's runnable thing (a dev server, the game) is a **run target**: a `[run.<name>]` table in `conductor.toml` with the repo (or folder) it runs in, the server command, the URL to open, and optionally a different URL to wait on and how long to wait (`docs/run-targets.md`). The planner sets targets through a `conductor.toml` request, with the command and URL the repo's README gives. A repo's main checkout has no installed dependencies, because executors install them in worktrees that are then deleted, so a target's `command` installs them first (e.g. `npm install && npm run dev -- --port 5173`). `run: X stop` is also how to free files a dev server holds before an item that moves them. Matt has the app's **Run X** button, which shows whether X is up, and **Stop X** beside it while X is up. Matt never types the commands.

## Picks

When work produces candidates (art variants, layouts, anything visual), Matt picks in his browser: one row per thing, a click on his favourite in each row, a note where none is right, then **Save**. Conductor's preamble gives the executor the manifest schema and the `pick:` report line, so a prompt need not repeat either. The inbox's **Picks** section lists every picker still waiting for Matt, or opened and not saved, across checkpoints, until he saves it. Matt can also open one from the app's **Pick…** button. A bad manifest is refused with the reason (an `invalid` event; `pick_failed` from `conductor pick`), and the planner reports it. That includes a manifest whose `name` another manifest already uses. If conductor restarted while a picker tab was open, that tab can't save: send `pick:` again, and Matt saves from the new tab. Mechanics: `docs/picks.md`.
