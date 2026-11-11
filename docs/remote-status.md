# Remote workers: status views

What shows how a project's remote workers are doing (`remote-workers.md` is how they
work): the worker page in each browser tab, the inbox's *Now* line, and the app's chip and
Workers tab. All of it only for a project with `[remote]`.

## One state, every view (97)

Every view of a worker shows one state, computed in one place (`Board._state`) from what the
broker sees and what the page reports in its hello (`report`: draining, paused, a core's
error, or downloading / compiling / starting a bundle, with seconds in):

| State | When |
|---|---|
| `offline` | not heard from in `fresh_seconds` |
| `draining` | the page is finishing its shards before a reload (a new page version) |
| `working` | it holds a live lease (`N shards out`) |
| `error` | it failed the gate for the open job's commit (a wrong value, or no answer twice); its gate or the Node wasm reference passed its deadline (`remote-diag.md`); that commit's wasm build failed (with the build log's error line) or has run 20+ min; or a core reports an error |
| `preparing` | the broker is building the bundle (or waits to) or computing its wasm reference, the gate is pending, or the page is downloading, compiling or starting it; with the step, the commit and the elapsed time |
| `idle` | none of these (`paused` when its cores are paused) |

The main box is `working` while it holds a shard, else `idle`. `view.me`, `view.workers[]` and
`view.main` carry it to the page, `status.json`'s rows to the app (`state`, `detail`, `since`
on the broker's clock, `key`). A core's tile has the same words (idle, preparing, working,
error, draining), and each change replaces the tile's text: 97's "loading bundle" under
"idle" was a label from a finished shard's first load, kept for 45 minutes.

## Loading a bundle (97)

The tab fetches and compiles the bundle's one `.wasm` once (the poll's `wasm`:
`Board._wasm_file`) and hands each core the `WebAssembly.Module`; init gets it bare, then as
`{module_or_path}`, then nothing (init fetches its own), each on a fresh import. Bare first
(100): aicomplete's `farm.mjs` wraps its input in `{module_or_path}` itself, so the object
form failed every core's first try; plain wasm-bindgen takes a bare Module too (its
deprecation warning is not logged). Bundle files are served `immutable` (a key's folder never
changes), so a reload or a replaced core costs no download. On aicomplete's 3.3 MB bundle
(headless Edge on the main box, 7 cores) every core was ready in 0.12-0.35 s fetching its
own and 0.08-0.42 s compiled once: loading was never the wait locally, and over Wi-Fi it is
now one fetch, not seven. The waits are the build (27-144 s a new key in 162) and the gate
(~6 s: aicomplete's fingerprint plays two games).

## In the app

`src/conductor/remote/app.py` (91), imported by `app/window.py` when the module is there.
It reads `.conductor/remote/status.json`, which the broker's 2-second loop writes
(`broker.write_status`: its address and start, `main_slots`, and `Board.panel`), at the
app's own refresh; nothing new polls. `[remote]` is re-read when `conductor.toml` changes:
adding the section brings the chip and tab, removing it takes them.

- **The chip**, on the header after `daemon up since …`:
  `● remote: 1 worker · 7 cores · idle` (the workers' most telling state: error, preparing,
  draining, working, idle); `● remote: farming 18/36 shards · 2 workers · 1 preparing · ETA 4 min`
  while a job runs (workers that failed its gate aren't counted; none left reads `main box only`);
  `● remote: no workers`; `● remote: broker down`. A click opens the Workers tab; the
  tooltip lists any problem. **Copy worker link** beside it copies `url` (by this PC's name;
  `remote-url.md`).
- **The dot**: red when the broker isn't serving (no `status.json`, its pid gone, or the
  file over 10 s old); amber when a connected worker failed its gate, or in the last
  5 minutes a gate failed, the broker restarted under a running daemon (it started over a
  minute after the daemon: one started with its daemon, or a handover for a code change,
  doesn't count), or the main box
  re-ran a shard still out on a worker (`Board.reruns`); grey with no worker connected;
  green otherwise. The Workers tab's label goes amber or red with it.
- **The Workers tab** (after Proposed): the broker (up since, port, Copy worker link); what
  the main box is doing, as the page's Cluster panel says it (`No item is running.`;
  `aicomplete/163_remote_reread running on the main box; no farm work out yet.`; or the job:
  its item, `done · out · pending of N`, ETA, the bundle building with its elapsed time or
  failed with its error line, and one bar: done, out, pending); one amber line for what's wrong (`Edge on Mac failed the gate at 4:31pm (wasm
  reference)`, `3 shards re-run on the main box since 4:30pm`, `the broker restarted at
  …`, a worker's error); a table, the main box first: worker (its name from the page), kind and engine, cores (the main box's
  `main_slots` as slots), state (*One state*: `preparing · compiling the bundle for 810ce37 · 12 s`,
  `working · 2 shards out`), last seen (`local` for the main box: the broker's own machine), shards today (won, by local date). Workers the broker has
  forgotten (an hour unheard) drop out. The broker learns the running items from
  `running/<repo>/<name>.run.json` every 2 s (`broker.running_items`).
- **The item row**: a farming item's Note starts with `farming 18/36` (first, so a long
  progress line can't elide it).

## The worker page

A status console (90), dark unless the system asks for light, in the project's `accent`.
Broker facts ride the hello it already sends every 2 s (`"view": true`; the reply's `page`
and `view`, from `Board.view`); its own come from its Web Workers' messages.

- **Header:** live / reconnecting (backing off while the broker is away) / paused /
  reloading (a new page version: `remote-url.md`), `project · item` (the job's item, else
  the running items `· no farm work yet`, else `no item running`), the state badge and its words
  (*One state*), the **Name** box (below), the bundle this tab's cores have (`bundle 810ce37`;
  `bundle 810ce37 → 99d3086` while another is prepared; `+changes` when the key carries a
  diff hash), and the gate with its kind
  (`✓ wasm reference ba4ec8…`). Live means the page's hello or any core's poll got an
  answer in the last 7 s; the gate is the broker's view, else the last one this tab's
  cores ran, so an idle tab shows both truthfully (96). A hello gives up after 10 s and is
  tried again: a stalled one had left a tab at `no broker` with no gate while its cores
  worked (161, and once on the sandbox in 96).
  **Name:** a text box, kept in the browser's `localStorage`, sent with every hello and
  poll; the Workers tab, other tabs' lists and the shards it runs from then on carry it
  (existing records stay as written). Empty: `<browser> on <platform>`.
- **Cores:** a tile per Web Worker (its state, its seed range or its step: downloading,
  compiling or starting the bundle, the fingerprint check; elapsed; its last ms a seed only
  while that was this job's; working tiles pulse softly), the broker's core count with its last reason, the cap slider
  (`remote-cores.md`), Pause, and *Keep screen awake*.
- **Throughput:** seeds a minute (the last 60 s; `—` while nothing is out here) with a 2-minute sparkline; ms a seed per
  core (the median of the last six shards for this commit and arguments) against the main
  box's native rate in this job (`0.95× the main box per core`; per main slot, so per core
  only for a single-threaded native command). **Inferred slowdown:** at 25% or more over
  the tab's own best for the same commit and arguments (six shards in), it says
  `~35% slower than its best: background tab, power saving or other load?`. The page says
  that other programs' CPU use isn't visible to it.
  An older job's figure reads `last job: 95 ms a seed per core`, muted.
- **Cluster:** no item running; an item running with no farm work out yet (named); or the
  oldest open job as one bar (this tab / others / out / pending) with an
  ETA from its rate so far and the bundle building (elapsed) or failed (its error line), the other workers (name, state, cores, shards, ms a seed in this job),
  and the main box's state and load: CPU % and top consumers from the daemon's latest memory sample
  while fresh (what the inbox's load line shows; none while no daemon samples).
- **Event log** (collapsed; with each core's steps it is also uploaded to the broker: `remote-diag.md`): joined, renamed, a bundle fetched and compiled (MB, seconds; the
  first core ready), gated, shard N done, lost the race, shard N dropped
  (its job closed or another copy is in: `remote-placement.md`, *The tail*), tab hidden /
  visible, lost / rejoined the broker, errors. **Copy diagnostics** copies a JSON snapshot
  (browser, cores, timings, the broker's last view, recent shards and events; never the
  token), shown selected in a text box too, where the clipboard API is missing.
- **Tab title** is the same state: `▶ 4/4 · 980/min · sandbox`, `◌ preparing 12 s · sandbox`,
  `✗ error · sandbox`, `⏏ draining · sandbox`, `◦ idle · 4 cores · sandbox`,
  `… reconnecting · sandbox`. Worker messages redraw it, so it stays current in a hidden tab.
- **Keep screen awake** uses the Screen Wake Lock API, off by default and remembered, retaken when the tab
  shows again. It needs a secure context: on `http://127.0.0.1` it works; on the LAN URL
  it is hidden. On a laptop, use a power plan that doesn't lock while plugged in, or allow
  the URL in `chrome://flags/#unsafely-treat-insecure-origin-as-secure` (Edge alike).

## The *Now* line

The inbox's *Now* section gets one line while a worker is connected:
`remote workers: 2 connected (1 browser, 1 local-remote), 13 cores`. The broker writes it
to `.conductor/now/remote.line` every 2 s; the inbox shows such a line only while it is
under 30 s old (`inbox.now_files`).
