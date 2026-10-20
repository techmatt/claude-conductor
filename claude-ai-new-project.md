# claude-ai-new-project — starting, parking and resuming a project

**Read `planner.md` first.** It is the method for every session and the vocab. This file adds what only a new, parked or resumed project needs, plus detail on memory, run targets and picks beyond what `planner.md` needs to act. How conductor itself works is in its `README.md` and `docs/`.

## Layout (per project)

Three sibling folders: `C:\Code\<project>-control\` (git; the planner's world: *Files* in `planner.md`, every path in conductor's `docs/control-folder.md`), the code repos `C:\Code\<repo>\` where executors work, and `C:\Code\<project>-worktrees\`, one git worktree per running item.

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
   It writes the layout, `conductor.toml`, a stub `state.md`, `.gitignore` and `.gitattributes`, then makes the first commit. It refuses a folder that already has files in it. Matt creates any code repo that doesn't exist yet.
3. The planner asks for folder access to the control folder and each repo (and to `C:\Code\gdrive-sync` if the project will home files on Drive: *Storage*). Access can only be granted to a folder that exists.
4. The planner writes the first real `state.md` (with *Waiting on Matt*, *Open questions*, *Paged* and *Maintenance* sections: `planner.md`), plus any `conductor.toml` change, as one request. That covers locks, `max_hours`, `ask_default_minutes`, `open_roots` (the project's Drive folder, so **open X** reaches it), and per repo: the `shared` untracked folders and `setup` commands its worktrees need; its `prose` globs if the default doesn't fit (`docs/items.md`); its `background` globs (`docs/progress.md`); or `worktrees = false`. A `[run.<name>]` target comes once there is something to run (*Run targets*). It also writes each code repo's `CLAUDE.md` standing contract as a first queued prompt for that repo. The contract holds:
   - the report: path and header from conductor's preamble, a ~60-line soft target, findings, decisions and surprises only, tests mentioned only for an unfixed red, and the `Report written to <path>` sign-off;
   - "commit to the branch you are on";
   - local 12-hour clock times;
   - steps over ~30 seconds backgrounded, with a completion waiter on the real process, never on a launcher wrapper;
   - LF line endings (`.gitattributes` `* text=auto eol=lf`);
   - operational facts learned while working promoted into the repo's README, never left only in a report.
5. Matt starts conductor for the project: the app's **Start daemon**, or `uv run conductor run C:\Code\<project>-control` in a plain terminal. Never as a Claude Code background command, which takes the daemon and every executor down with it (`docs/restart.md`). From then on, work flows through the queue, and each session starts with **start a new <project> session**.

Matt can watch and act without the planner in the desktop app (`uv run conductor app C:\Code\<project>-control`, run in `C:\Code\claude-conductor`).

## Park / resume a project

Parking puts a project away whole (to free the box, or to move its folders), with nothing left running or half-done.

1. Nothing is in flight. No item is running, staged or merging, and no question is open. Cancel or let finish what's queued; anything still queued stays queued and runs on resume.
2. **Closeout** as a request, **while the daemon still runs**: requests apply only through the daemon.
3. `run: <target> stop` for every run target that is up.
4. `stop: daemon` (**stop conductor**).
5. Matt clears what's left in `C:\Code\<project>-worktrees\`. A worktree's git link is an absolute path, so a worktree can't move with the folders. A kept red worktree gives its branch `conductor/<NNN_name>` to the repo, so deleting the folder loses no commit. A leftover that conductor couldn't remove (a dev server held it) goes too. Delete the folder, then run `git worktree prune` in the repo (`docs/worktrees.md`).
6. In the app, **Remove project**. This only forgets the folder; nothing on disk changes.

The control folder and the code repos may then move, as a set, keeping their relative layout: `conductor.toml` names the repos and `worktree_root` by relative path. To resume: move them back (or keep the same layout elsewhere), start the daemon (`uv run conductor run <control folder>` in `C:\Code\claude-conductor`, or the app's Start), **Add project…** in the app, then **start a new <project> session**.

**park project** runs steps 1–4 itself, then tells Matt which worktree folders are left to clear and to use **Remove project**. **resume project** tells Matt to start the daemon and **Add project…**, then does **start a new <project> session**.

## Memory

The memory line (*Now*'s first line, the app header, `conductor status`) reads `memory 29.4 of 36.6 GiB committed (80%), 10.9 GiB free · biggest <repo>/<NNN> 2.1 GiB · ok`; "committed" (RAM plus page file) is what runs out on Windows. Thresholds are `conductor.toml` `[defaults]` keys and change by request; what each level does, `reaped` and `daemon_died` are in `docs/memory.md`.

## Run targets

A project's runnable thing (a dev server, the game) is a `[run.<name>]` table in `conductor.toml` (`docs/run-targets.md`), set by request with the command and URL the repo's README gives. A main checkout has no installed dependencies, so a target's `command` installs them first. `run: X stop` is also how to free files a dev server holds before an item that moves them. Matt has the app's **Run X** / **Stop X** buttons and never types the commands.

## Picks

When work produces candidates (art variants, layouts), Matt picks in his browser, one row per thing, then **Save**. The preamble gives the executor the manifest schema and the `pick:` report line, so a prompt need not repeat either. The inbox's **Picks** lists every picker still waiting for Matt; Matt can also open one from the app's **Pick…**. A refused manifest (`invalid`, or `pick_failed`) gets reported with its reason. Mechanics, including a restart losing an open tab: `docs/picks.md`.
