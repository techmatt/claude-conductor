# Remote workers

Opt-in and removable: a project's seeded simulation jobs can be spread over other
machines on the LAN. A browser tab is the main worker kind (nothing to install); the
main box keeps working and runs whatever no worker takes. All of it lives in
`src/conductor/remote/` (tests in `src/conductor/remote/tests/`).

## Off unless both opt-ins are present

- The project's `conductor.toml` has a `[remote]` section, and
- the item's front-matter says `remote: true`.

Without the section: no broker, no port, no build, and an item's prompt, environment,
inbox and digest are the same text as before (`test_remote_hooks.py` checks this).
`remote: true` in a project without `[remote]` is ignored silently, for the same reason.
`[remote]` without the flag starts the broker but changes no item.

## Shape

- **The broker** (`broker.py`, its state in `board.py`) is its own process, started by the
  daemon at its start for a project with `[remote]`, and kept across a daemon restart
  unless the section changed. It exits once the daemon has been gone a minute. If it
  crashes, the daemon and its items carry on and jobs run locally.
- **LAN only, one shared token.** It binds `bind:port` (default `0.0.0.0:8770`) and serves
  every path under `/w/<token>/`: the worker page, the wasm bundles and the job API.
  Workers pull: they poll for shards and post results; a worker never listens. The main
  box's own calls (jobs, claims, bundles, status) are taken from loopback only, without an
  `Origin` header, and so is a local-remote worker; every POST must be `application/json`,
  so a web page open on the main box can't forge one. Bodies are capped at 16 MB and a
  stalled connection drops after 30 s. The native command is the section's, never a
  request's. The token is `token` in the section, else generated once into
  `.conductor/remote/token`. The join URL is `url` in `.conductor/remote/broker.json`.
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
| `port`, `bind`, `token` | The broker's address and token (above). |
| `shard_seeds` (25), `seed_seconds` (0.05), `local_below_seconds` (10) | Shard size, and the size check: a job of `count × seed_seconds` under `local_below_seconds` stays local. |
| `lease_seconds` (60), `fresh_seconds` (10), `main_slots` (1) | A shard not back in this long is leased again; a worker not heard from in this long is gone; shards the main box runs at once. A project whose native command is single-threaded sets `main_slots` to its usual thread budget; a multi-threaded one keeps 1. |

## The executor interface

A `remote: true` item gets `farm-seeds` first on its PATH (`.conductor/remote/bin/`,
a `sh` shim and a `.cmd` one), `CONDUCTOR_REMOTE_CONTROL` in its environment, and one
paragraph at the end of its prompt naming the command:

    farm-seeds <first seed> <count> <out file> [args…]

It leaves at `<out file>` exactly what the native command leaves for that range (bytes,
line endings included), and says on its last stderr line where the shards ran. The
executor never sees placement. It runs locally (and says why) outside a git repo, when
an argument holds a space (the wasm side gets the arguments joined by spaces), or when
the fingerprint command fails. The shims run Python with `-P`, so a file in the item's
folder never shadows a module; the `.cmd` one mangles `%`, `^` and `&` in arguments.

## Placement

- No broker (or one not answering within half a second): the native command runs at once
  over the whole range. A lease is never waited out.
- A job estimated under `local_below_seconds` stays local.
- Otherwise the range is split into shards of `shard_seeds`, **whether or not a worker is
  connected**: the main box works the shards (`main_slots` at once), and a worker that joins
  mid-job takes those still pending. Once none is left unleased, the main box re-runs the
  oldest still out on a worker. The first copy back wins, and a shard the main box is
  running is killed when a worker's copy lands.
- Sharding costs the main box little: on the sandbox (500 seeds, 20 shards, no worker,
  `main_slots` 1) a sharded `farm-seeds` took 28.5 s against 27.3 s for one bare native run
  over the range (1.045; prompt 89). That is ~0.5 s fixed (Python, `native_build`, git, the
  fingerprint) plus ~30 ms a shard, 17 ms of it Windows building a hidden console for the
  child (`CREATE_NO_WINDOW`). `DETACHED_PROCESS` would save that but lets a native command's
  own console children open visible windows, so it is not used.
- A worker that joins mid-job is used at once (89: 600 seeds begun with none, a local-remote
  worker joining 8 s in took 15 of 24 shards; 15.1 s against 33.2 s local, identical).
- A lease past `lease_seconds`, or held by a worker no longer heard from, is leased again.
- A broker that stops answering mid-job, or a main-box slot that fails: the job is closed
  and the shards not yet in run locally.
- `farm-seeds` calls the broker every 2 s while its job runs; a job not heard from in
  `fresh_seconds` is no longer served (a killed item's job never starves the next) and is
  dropped after `lease_seconds`.

## Workers

- **The browser page** (`page/`): one Web Worker per core, a core slider defaulting to all
  but one, the fingerprint result and the shards it ran. Closing the tab costs nothing but
  the shards it held, which run elsewhere. It reports itself as `<browser> on <platform>`
  (`Chrome on Win32`), not the full user agent.
- **On a real laptop over Wi-Fi** (88: Chrome, 8 logical cores, the tab at 7): the tab
  keeps working in the background, minimised and on a locked screen. Chrome doesn't
  throttle Web Workers. Per core it ran wasm at 58 ms a seed in front, 64 behind another
  tab, 66 minimised and a flat 81 locked (power throttling), against the main box's
  native 61. A closed lid sleeps it: it is dropped `fresh_seconds` later and its leased
  shards run on the main box. On lid open it rejoins within seconds, with no new gate
  for the same commit. A 5,000-seed job took 34–46 s (laptop in front or hidden) and about
  50 s (locked), against 303 s local-only. Every output was byte-identical.
- **The local-remote worker** (`python -m conductor.remote.worker <control> [--cores N]`): a
  second process on the main box speaking the same protocol with the native command, for
  tests and failure injection (`--bad-fingerprint`, `--die-after N`, `--fail-every N`,
  `--hang-after N`). A native worker for other PCs is not built; the protocol allows one
  (it would fetch the code instead of using the main box's folder).

## Trust, builds, provenance

- **Trust gate.** Before a worker gets shards for a commit it must reproduce that commit's
  reference for its own kind. Two references a commit, both computed on the main box:
  - **native** (local-remote and any native worker): the `fingerprint` command's stdout;
  - **wasm** (browsers): the bundle's own `fingerprint()`, run in Node
    (`wasm_fingerprint.mjs`; init gets the bundle's one `.wasm` file as bytes) once the
    bundle is built, cached as `bundles/<commit>/wasm.fingerprint`.

  So a project that keeps its fastest math (Matt, 2026-11-09), whose wasm build never
  reproduces its native answer, still gets browsers. Without Node, or if the bundle's
  fingerprint fails there, browsers are gated against the native reference (and
  `farm-seeds` says so). A worker that fails gets no shards for the commit; the failure,
  with the reference it missed, is in `.conductor/remote/gates.log`, the job's record and
  `farm-seeds`' stderr, and the inbox's workers line counts it.
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
  join when the bundle is ready and its wasm reference known.
- **Provenance.** Each shard's result carries the worker's id and kind;
  `.conductor/remote/jobs/<job>/job.json` records who ran each shard, the engines used,
  whether native and wasm mixed, and both references (`references`: `native`, `wasm`, `differ`); `jobs/<job>/main.log` has a `rerun shard N` line for each
  shard the main box ran while a worker still held it. Workers' logs come back with their results
  (`jobs/<job>/logs/<worker>.log`).
- **Determinism is the project's.** Native and wasm agree bit for bit only where the
  project's math does: in the `sim` test workload, the `libm` crate agrees and Rust's
  standard-library math on native Windows does not (the MSVC runtime differs by 1-2 ulp).

## Visibility

The inbox's *Now* section gets one line while a worker is connected:
`remote workers: 2 connected (1 browser, 1 local-remote), 13 cores`. The broker writes it
to `.conductor/now/remote.line` every 2 s; the inbox shows such a line only while it is
under 30 s old (`inbox.now_files`).

## Runtime files (`.conductor/remote/`)

`broker.json` (pid, port, URL, settings digest), `broker.log` (a port already taken
shows here: give each project its own `port`), `token`, `bin/`, `bundles/<commit>/` (with `wasm.fingerprint`; and
`<commit>.build.log`), `jobs/<job>/` (shards, logs capped at 2 MB per worker, `job.json`;
removed after 7 days), `gates.log`.

## Removing it

Delete `src/conductor/remote/`, the two hooks (the end of `Daemon.startup` in
`daemon.py`; the `if item.remote:` lines in `daemon_setup._start_exec`), `remote` from
`items.FIELDS` and `Item`, the `src/conductor/remote/tests` entry of `pytest.ini`'s
`testpaths`, every project's `[remote]` section, and this file with its index lines.
`inbox.now_files` reads a folder nothing else writes and may stay. The suite passes
without the module (checked when it landed, prompt 87).

## The test project

`C:\Code\conductor-sandbox\sim` (a Rust crate, native and wasm) with `[remote]` in the
sandbox's control folder; cost per seed is `--cost N` (270000 ≈ 60 ms native). Built with
`--features std-math` (in `native_build`, and after `--` in `build`) it is the project that
keeps its fastest math: references differ (*Trust gate*).
