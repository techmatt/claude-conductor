# Remote workers: trust, builds, provenance

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
  `farm-seeds`' stderr, and the inbox's workers line counts it with the reason (116). A failure is not for
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
  (`shards/<first>.who.json`: the broker's, or `farm-seeds`' for a shard it finished after the
  broker dropped out), and `.conductor/remote/jobs/<job>/job.json` is built from those notes,
  so it matches the output's engines even without the broker (97: 162's read "main box 8"
  for 6 native and 2 wasm shards). The summary names a worker `<kind> <name> (<id>)`. The record also has the engines used,
  whether native and wasm mixed, and both references (`references`: `native`, `wasm`, `differ`); `jobs/<job>/main.log` has a `rerun shard N` line for each
  shard the main box ran while a worker still held it. Workers' logs come back with their results
  (`jobs/<job>/logs/<worker>.log`).
- **Determinism is the project's.** Native and wasm agree bit for bit only where the
  project's math does: in the `sim` test workload, the `libm` crate agrees and Rust's
  standard-library math on native Windows does not (the MSVC runtime differs by 1-2 ulp).
