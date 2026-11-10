# Remote workers: diagnostics and deadlines

When a remote worker misbehaves, the planner diagnoses it from one file, with no Claude
Code on the worker's machine; and no preparing step sits open-ended (98). The code is
`src/conductor/remote/diag.py`, with the deadlines in `board.py` (the gate), `client.py`
(build, Node reference) and the worker page. How remote workers work: `remote-workers.md`;
what each view shows: `remote-status.md`.

## `remote-diag.md` (the planner's read)

At the control folder's root, beside `inbox.md`, for a project with `[remote]`. The broker's
2-second loop rewrites it when its text changes (times are clock times, so an idle broker
leaves it alone); it stays after the broker exits. It holds:

- **Workers**: each worker's one state (`remote-status.md`, *One state*), the main box first;
  under a browser, its core count, its cap, and the last change and why (`remote-cores.md`).
- **Past deadline or failed**: in the last 6 hours, any step started with no end past its
  deadline, or ended `failed`, `timeout` or `fallback`, with its stderr tail and that
  worker's page lines up to that moment.
- **Bundle steps (main box)**: the last 8 build, Node reference and fallback steps.
- **Per worker**: its last 30 events, the page's lines and the broker's steps interleaved
  by time (source `page`, `coreN`, or `broker`).

It is git-ignored like `inbox.md` (`config.RUNTIME`); the daemon's startup check names it
only where `.conductor/remote/` exists. A project that adds `[remote]` adds the line by
request. "Copy diagnostics" on the page stays the fallback for a tab that can't reach the
broker (its lines not yet uploaded are in it, `notUploaded`).

## The steps log (`.conductor/remote/steps.log`)

JSON lines, rolled to `steps.log.1` past 512 KB. A step is a `start` and an `end` with the
same `id` (a `note` is a moment): `at`, `step`, `key`, `worker`, `job`, `try`, and on an end
`result` (`ok`, `failed`, `timeout`, `fallback`), `exit`, `stderr` (the tail), `detail`.
**A stall is a start with no end.**

| Step | Written by | Start | End |
|---|---|---|---|
| `build` | the build process (`client.build_bundle`) | it starts | exit code, log tail; `timeout` when killed |
| `reference` | `farm-seeds` (`client.wasm_reference`) | Node starts | the reference, or exit code and stderr tail |
| `fallback` | `farm-seeds` | (a note) browsers gate against the native reference, and why | |
| `gate` | the broker | handed to a worker | `ok`, `failed` (the value got), `timeout` |
| `handout` | the broker | (a note) a worker's first shard of a job | |

## Page logs (`.conductor/remote/workers/<name>-<id>.log`)

The worker page keeps a rolling log and sends the lines not yet taken (up to 200) with each
hello (`log`, and its own `clock`, so the broker puts them on its own clock). One file a
worker, named by its Name box and id (a rename renames it), the newest 500 lines kept;
files untouched for 7 days are deleted. What it logs:

- every event-log line, and each change of its state as the broker reports it;
- each core's steps: gate handed out, asking the page for the module and its arrival (ms),
  each init try with its time or error, `fingerprint()` time, the gate's answer;
- the bundle's fetch and compile times; a core replaced; results that failed to post;
- once a minute: hellos and polls (count, median and max ms, failures), the page's heap
  (Chrome's `performance.memory`) and device memory, hidden or visible;
- `console.error` / `console.warn`, uncaught exceptions and rejections, from the page and
  from every Web Worker, and a core that fails to load (`onerror`).

## Deadlines and recovery

| Step | Deadline | Past it |
|---|---|---|
| build | 20 min | killed (its process tree) and run once more; twice: the build failed (the browsers' `error`) |
| Node wasm reference | 2 min | killed and run once more (a failed try too); twice: browsers are gated against the native reference |
| gate | 1 min | the worker's `error` (`passed its 60 s deadline (try 1 of 2)`); handed out again on its next poll; the second late one fails the gate until the page reloads |
| page: the compiled module | 60 s | the core loads the bundle itself |
| page: a core preparing (gate or bundle) | 75 s | the core's Web Worker is replaced (the broker's gate deadline comes first) |
| page: the `.wasm` fetch, a poll, a gate post | 60 s, 30 s, 30 s | fails, logged, retried as before (a result post gets 120 s) |

The deadlines are `diag.DEADLINES`. **The native fallback** is said in `remote-diag.md` (a
`fallback` step), in the job's `job.json` (`references.fallback`) and `farm-seeds`'
summary; the key's state carries it (`note`). It is kept as `bundles/<key>/wasm.fallback`,
so the next job on that bundle doesn't wait 4 minutes again; a new commit is a new key.
Native and wasm have matched on every aicomplete game so far (160: 0 of 192 differ, 161: 0
of 2304). While Node runs the key is `referencing`: browsers are `preparing` (`the broker is
computing the wasm reference`), and `error` once a try passed its deadline.

**Fault injection**: `CONDUCTOR_REMOTE_FAULT=node-hang` in `farm-seeds`' environment makes
the Node reference hang (tests, the sandbox walk).

## Why (aicomplete, Nov 9, 10:05-10:31pm)

Matt's laptop tab passed no gate after the 97 page landed. From the broker's board: each
new key's gate was handed to a core (10:17:17, 10:25:35) and never answered; the Node
reference was fine (cached 0.15 s before the 7e494f4 gate went out). The board waited on a
pending gate for `lease_seconds` (600 on aicomplete), and a reload didn't clear it, so after
Matt's 10:27:23 reload all 7 cores were told "idle" until 10:35:35. Fixed: a gate's own
1-minute deadline, a reload drops pending gates. Why the laptop's cores never answered was
not visible: the 97 page left a core waiting on the page's compiled module with no deadline,
and a failed hand-over threw where nothing answered the core. Both now have deadlines and
logging; on this box Chrome and Edge gated aicomplete's bundle in ~8 s over the LAN URL,
and the same laptop tab passed the next two keys' gates (10:38, 10:48pm) on the old code.
