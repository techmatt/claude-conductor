# Remote workers

Opt-in and removable: a project's seeded simulation jobs can be spread over other
machines on the LAN. A browser tab is the main worker kind (nothing to install); the
main box keeps working and runs whatever no worker takes. All of it lives in
`src/conductor/remote/` (tests in `src/conductor/remote/tests/`).

## Off unless both opt-ins are present

- The project's `conductor.toml` has a `[remote]` section, and
- the item's front-matter says `remote: true`, or says nothing under `[remote] default =
  true` (105: aicomplete's rule is every mechanics item farms, and 175-178 forgot the flag).
  The default skips readonly and template items; `remote: false` opts one out.

Without the section: no broker, no port, no build, and an item's prompt, environment,
inbox and digest are the same text as before (`test_remote_hooks.py` checks this).
`remote: true` in a project without `[remote]` is ignored silently, for the same reason.
`[remote]` without the flag (or `default`) starts the broker but changes no item. An item
that farmed has `remote: true` on its `start` event, however it got it.

## Shape

- **The broker** (`broker.py`, its state in `board.py`) is its own process, started by the
  daemon at its start for a project with `[remote]`, and kept across a daemon restart
  unless the section changed. It exits once the daemon has been gone a minute. If it
  dies, the daemon starts it again within 10 s (one warning); its jobs carry on (`remote-url.md`).
  By hand: `python -m conductor.remote.broker <control> [--stay] [--after <pid>]`; `--stay`
  serves with no daemon (tests, a manual run), `--after` is a handover.
- **LAN only, one shared token.** It binds `bind:port` (default `0.0.0.0:8770`) and serves
  every path under `/w/<token>/`: the worker page, the wasm bundles and the job API.
  Workers pull: they poll for shards and post results; a worker never listens. The main
  box's own calls (jobs, claims, bundles, status) are taken from loopback only, without an
  `Origin` header, and so is a local-remote worker; every POST must be `application/json`,
  so a web page open on the main box can't forge one. Bodies are capped at 16 MB and a
  stalled connection drops after 30 s. The native command is the section's, never a
  request's. The token is `token` in the section (`remote-url.md`). The join URL is `url`
  in `.conductor/remote/broker.json`.
- **Trust.** Anyone holding the token is trusted: the fingerprint gate catches a worker
  that computes differently, not one that lies (it is served the bundle, so it can pass the
  gate and post anything). Share the URL only on a LAN you trust.
- **Firewall.** The first time the broker binds, Windows asks whether to allow conductor's
  `python.exe` on networks. Allow **Private networks** only. Conductor never changes
  firewall settings itself.

## The project contract (`[remote]`)

| Key | Meaning |
|---|---|
| `native` | The native command for a seed range (a list): `{first}`, `{count}`, `{out}` filled in, an element `{args}` replaced by the item's arguments. It writes its output file at `{out}`. |
| `native_build` | Optional, run once per job before any native shard (e.g. `cargo build --release`). |
| `build` | The wasm bundle build; it writes the bundle into `{bundle}`. |
| `bundle_js` | The bundle's ES module (default `bundle.js`): default export `init()`, plus `fingerprint() -> string` and `run(first, count, args) -> string`, the shard's output text. |
| `fingerprint` | A command whose stdout (stripped) is the native reference: what a native worker must reproduce before it gets shards. The wasm reference is the bundle's own `fingerprint()` (*Trust gate*). |
| `merge` | `concat` (the default and only one): shard outputs in seed order. |
| `dir` | The commands' folder inside the repo (relative paths in commands resolve there). |
| `port`, `bind`, `token` | The broker's address and token (above). A missing `token` is pinned by conductor (`remote-url.md`). |
| `host` | The join URL's host: this PC's name unless set (an IP, when other machines can't resolve the name). |
| `shard_seeds` (25), `seed_seconds` (0.05), `local_below_seconds` (10) | Shard size, and the size check: a job of `count × seed_seconds` under `local_below_seconds` stays local. |
| `default` (false) | `true`: an item without `remote:` farms unless readonly or a template (*Off unless…*). Not part of the broker's settings digest: changing it restarts nothing. |
| `start_cores` (4) | A browser worker's core count when its name is new; a known name keeps its learned plan (103; `remote-cores.md`). Still under the tab's cores and its slider cap. |
| `accent` (`#7c9cff`) | The worker page's accent colour (`#rgb` or `#rrggbb`), so each project's tabs are told apart. The page's title is the top-level `project`. |
| `lease_seconds` (sized to the shard), `fresh_seconds` (10), `main_slots` (1) | How long a worker may hold a shard without renewing it (*Leases*); a worker not heard from in this long is gone; shards the main box runs at once, over every open job (`remote-placement.md`). A project whose native command is single-threaded sets `main_slots` to its usual thread budget; a multi-threaded one keeps 1. |

## The executor interface

A `remote: true` item gets `farm-seeds` first on its PATH (`.conductor/remote/bin/`,
a `sh` shim and a `.cmd` one), `CONDUCTOR_REMOTE_CONTROL` and `CONDUCTOR_REMOTE_ITEM`
(its id, which the worker page shows) in its environment, and one
paragraph at the end of its prompt naming the command:

    farm-seeds <first seed> <count> <out file> [args…]

It leaves at `<out file>` exactly what the native command leaves for that range (bytes,
line endings included), and says on its last stderr line where the shards ran. The
executor never sees placement. It runs locally (and says why) outside a git repo, when
an argument holds a space (the wasm side gets the arguments joined by spaces), or when
the fingerprint command fails. The shims run Python with `-P`, so a file in the item's
folder never shadows a module; the `.cmd` one mangles `%`, `^` and `&` in arguments.
The paragraph (111) also says to commit before farming a change (a project's build may take
only committed code: aicomplete 204 ran every read natively and starved the box) and that
`farm-seeds: error: the broker went away` (exit 3) is a failure, not a slow run
(`remote-placement.md`). Each run that exits 0 appends `{item, first, count, args, at}` to
`.conductor/remote/seeds.jsonl`, which the *Kept reads* rows read (`worktrees.md`).

The commit it runs from, its dirty-tree warning and `farm-seeds --cancel`: `farm-seeds.md`.

## Placement

`remote-placement.md`: a job under `local_below_seconds` (or with no broker) runs
locally; otherwise its shards go each to the worker expected to finish it soonest, the
main box running at most `main_slots` at once over every job; leases, the tail re-run,
dropped shards and a broker that goes away.

## Workers

- **The browser page** (`page/`): one Web Worker per core, the broker choosing how many
  (`remote-cores.md`). Closing the tab costs nothing but the shards it held, which run elsewhere. It
  is named in its Name box, else `Chrome on Win32` and the like; it compiles a bundle once
  for all its cores. What it shows: `remote-status.md`.
- **On a real laptop over Wi-Fi** (88: Chrome, 7 of 8 cores): it works in the background,
  minimised and locked (Web Workers aren't throttled): wasm 58 ms a seed in front, 64
  behind a tab, 66 minimised, 81 locked, against native 61. A closed lid sleeps it (its
  shards go to the main box); it rejoins on open, no new gate. 5,000 seeds: 34–50 s against
  303 s local, byte-identical.
- **The local-remote worker** (`python -m conductor.remote.worker <control> [--cores N] [--id ID]`, id `lr<pid>` by default): a
  second process on the main box speaking the same protocol with the native command, for
  tests and failure injection (`--bad-fingerprint`, `--die-after N`, `--fail-every N`,
  `--hang-after N`). A native worker for other PCs is not built; the protocol allows one
  (it would fetch the code instead of using the main box's folder).

## One URL for good

The join URL never changes and an open tab survives restarts, commits and new page
versions: `remote-url.md`.

## Status views

The page, the *Now* line, the app's chip and Workers tab: `remote-status.md`; the
planner's digest and the deadlines: `remote-diag.md`.

## Trust, builds, provenance

- **Trust gate.** Before a worker gets shards for a commit it must reproduce that commit's
  reference for its own kind. Two references a commit, both computed on the main box:
  - **native** (local-remote and any native worker): the `fingerprint` command's stdout;
  - **wasm** (browsers): the bundle's own `fingerprint()`, run in Node
    (`wasm_fingerprint.mjs`; init gets the bundle's one `.wasm` file as bytes, as
    `{module_or_path}`, then bare (an `init(input)` that wraps its argument itself, 95), then
    nothing, each try on a fresh import of the module: aicomplete's `init` keeps its first
    promise, so a second call on the same module only repeated the first try's rejection, 96)
    once the bundle is built, cached as `bundles/<commit>/wasm.fingerprint` (2 min a try,
    two tries; then `wasm.fallback`: `remote-diag.md`).
    The bundle may be `.js`, `.mjs` or `.cjs`: all are served as `text/javascript`.

  So a project that keeps its fastest math (Matt, 2026-11-09; wasm never reproducing
  native) still gets browsers. Without Node, or if the bundle's
  fingerprint fails there, browsers are gated against the native reference (and
  `farm-seeds` says so). A worker that fails gets no shards for the commit; the failure,
  with the reference it missed, is in `.conductor/remote/gates.log`, the job's record and
  `farm-seeds`' stderr, and the inbox's workers line counts it. A failure is not for
  good (95): the worker re-gates under the same id when its page reloads or a
  local-remote worker restarts (the first hello says `fresh`), when a new broker takes up
  the board (a code change), or when the commit's reference changes.
- **References that differ** mean native and wasm shards may differ in their last bits.
  `job.json`'s `references` says so (`differ: true`); a merged output that mixed both gets
  `mixed_note`, and `farm-seeds`' summary ends "whose references differ (not what a local
  run gives)". Sandbox: `sim` built with `--features std-math` (std is its default math and
  fingerprint) has native `13e72e7b465277cf` against wasm `ba4ec8b15eb335ba`; the plain
  libm build has `ba4ec8b15eb335ba` for both.
- **The commit** is the repo's HEAD, with a hash of `git diff HEAD` and of every
  untracked, unignored file added when there are any.
- **Builds.** The wasm bundle is built the first time a job needs it for a commit while a
  browser is connected (a job no browser joins never shares the main box with a build), in its
  own detached process (a job or item that ends first never cuts it short), one per commit at a time, and
  cached in `.conductor/remote/bundles/<commit>/`. Native shards start at once; browsers
  join when the bundle is ready and its wasm reference known. A commit outside a job builds
  nothing. A failed build (with its log's error line: `client.failure_line`) or one over 20
  minutes (killed, tried once more: `remote-diag.md`) is the browsers' `error` state.
- **Provenance.** Who ran the kept copy of each shard is noted beside it
  (`shards/<first>.who.json`: the broker's, or `farm-seeds`' for a shard it ran after the
  broker went away), and `.conductor/remote/jobs/<job>/job.json` is built from those notes,
  so it matches the output's engines even without the broker (97: 162's read "main box 8"
  for 6 native and 2 wasm shards). The summary names a worker `<kind> <name> (<id>)`. The record also has the engines used,
  whether native and wasm mixed, and both references (`references`: `native`, `wasm`, `differ`); `jobs/<job>/main.log` has a `rerun shard N` line for each
  shard the main box ran while a worker still held it. Workers' logs come back with their results
  (`jobs/<job>/logs/<worker>.log`).
- **Determinism is the project's.** Native and wasm agree bit for bit only where the
  project's math does: in the `sim` test workload, the `libm` crate agrees and Rust's
  standard-library math on native Windows does not (the MSVC runtime differs by 1-2 ulp).

## Runtime files (`.conductor/remote/`)

`broker.json` (pid, port, `url`, `url_ip`, `local`, settings digest, `handover`; just its pid and
`starting` until it serves), `status.json` (the app's view, every 2 s), `board.json` (the board,
every 2 s, for the next broker), `broker.log` (a port already taken
shows here: give each project its own `port`), `token` (the generated one, before it is pinned), `bin/`, `bundles/<commit>/` (with `wasm.fingerprint`; and
`<commit>.build.log`), `jobs/<job>/` (shards, logs capped at 2 MB per worker, `job.json`;
removed after 7 days), `gates.log`, `steps.log` and `workers/` (`remote-diag.md`).

## Removing it

Delete `src/conductor/remote/`, the three hooks (the end of `Daemon.startup` and the
`keep_broker` lines in `Daemon.tick`, in `daemon.py`; the `remote.wants` lines in
`daemon_setup._start_exec`, and `rec.get("remote")`'s in `daemon_machine._continue_interrupted`), the app's
optional import of `remote.app` in `app/window.py` (with `self.remote` and its refresh line), `remote` from
`items.FIELDS` and `Item`, the `src/conductor/remote/tests` entry of `pytest.ini`'s
`testpaths`, every project's `[remote]` section, and these docs with their index lines.
`inbox.now_files`, `ReposPanel.note_suffix` and `remote-diag.md` in `config.RUNTIME` may stay. The suite passes
without the module (checked when it landed, prompt 87).

## The test project

`C:\Code\conductor-sandbox\sim` (a Rust crate, native and wasm) with `[remote]` in the
sandbox's control folder; cost per seed is `--cost N` (270000 ≈ 60 ms native). Built with
`--features std-math` (in `native_build`, and after `--` in `build`) it is the project that
keeps its fastest math: references differ (*Trust gate*).
