# Remote workers: status views

What shows how a project's remote workers are doing (`remote-workers.md` is how they
work): the worker page in each browser tab, the inbox's *Now* line, and the app's chip and
Workers tab. All of it only for a project with `[remote]`.

## In the app

`src/conductor/remote/app.py` (91), imported by `app/window.py` when the module is there.
It reads `.conductor/remote/status.json`, which the broker's 2-second loop writes
(`broker.write_status`: its address and start, `main_slots`, and `Board.panel`), at the
app's own refresh; nothing new polls. `[remote]` is re-read when `conductor.toml` changes:
adding the section brings the chip and tab, removing it takes them.

- **The chip**, on the header after `daemon up since …`:
  `● remote: 1 worker · 7 cores · idle`;
  `● remote: farming 18/36 shards · 2 workers · ETA 4 min` while a job runs (workers that
  failed its gate aren't counted; none left reads `main box only`);
  `● remote: no workers`; `● remote: broker down`. A click opens the Workers tab; the
  tooltip lists any problem. **Copy worker link** beside it copies `url` (the LAN one).
- **The dot**: red when the broker isn't serving (no `status.json`, its pid gone, or the
  file over 10 s old); amber when a connected worker failed its gate, or in the last
  5 minutes a gate failed, the broker restarted under a running daemon (it started over a
  minute after the daemon: one started with its daemon doesn't count), or the main box
  re-ran a shard still out on a worker (`Board.reruns`); grey with no worker connected;
  green otherwise. The Workers tab's label goes amber or red with it.
- **The Workers tab** (after Proposed): the broker (up since, port, Copy worker link); the
  current job (its item, `done · out · pending of N`, ETA, and one bar: done, out,
  pending); one amber line for what's wrong (`Edge on Mac failed the gate at 4:31pm (wasm
  reference)`, `3 shards re-run on the main box since 4:30pm`, `the broker restarted at
  …`); a table, the main box first: worker, kind and engine, cores (the main box's
  `main_slots` as slots), state (`working · N out`, `idle`, `gate pending`, `gate failed`,
  `stale`), last seen, shards today (won, by local date). Workers the broker has
  forgotten (an hour unheard) drop out.
- **The item row**: a farming item's Note starts with `farming 18/36` (first, so a long
  progress line can't elide it).

## The worker page

A status console (90), dark unless the system asks for light, in the project's `accent`.
Broker facts ride the hello it already sends every 2 s (`"view": true`; the reply's `page`
and `view`, from `Board.view`); its own come from its Web Workers' messages.

- **Header:** live / reconnecting / paused, `project · item`, the short commit
  (`+changes` when the key carries a diff hash), and the gate with its kind
  (`✓ wasm reference ba4ec8…`).
- **Cores:** a tile per Web Worker (idle or running, its seed range, elapsed, its last ms a
  seed; running tiles pulse softly), the slider, Pause, and *Keep screen awake*.
- **Throughput:** seeds a minute (the last 60 s) with a 2-minute sparkline; ms a seed per
  core (the median of the last six shards for this commit and arguments) against the main
  box's native rate in this job (`0.95× the main box per core`; per main slot, so per core
  only for a single-threaded native command). **Inferred slowdown:** at 25% or more over
  the tab's own best for the same commit and arguments (six shards in), it says
  `~35% slower than its best: background tab, power saving or other load?`. The page says
  that other programs' CPU use isn't visible to it.
- **Cluster:** the oldest open job as one bar (this tab / others / out / pending) with an
  ETA from its rate so far, the other workers (kind, cores, shards, ms a seed in this job),
  and the main box's load: CPU % and top consumers from the daemon's latest memory sample
  while fresh (what the inbox's load line shows; none while no daemon samples).
- **Event log** (collapsed): joined, gated, shard N done, lost the race, tab hidden /
  visible, lost / rejoined the broker, errors. **Copy diagnostics** copies a JSON snapshot
  (browser, cores, timings, the broker's last view, recent shards and events; never the
  token), shown selected in a text box too, where the clipboard API is missing.
- **Tab title** is a status line: `▶ 4/4 · 980/min · sandbox`, `◦ idle · 4 cores · sandbox`,
  `… reconnecting · sandbox`. Worker messages redraw it, so it stays current in a hidden tab.
- **Keep screen awake** uses the Screen Wake Lock API, off by default, retaken when the tab
  shows again. It needs a secure context: on `http://127.0.0.1` it works; on the LAN URL
  it is hidden. On a laptop, use a power plan that doesn't lock while plugged in, or allow
  the URL in `chrome://flags/#unsafely-treat-insecure-origin-as-secure` (Edge alike).

## The *Now* line

The inbox's *Now* section gets one line while a worker is connected:
`remote workers: 2 connected (1 browser, 1 local-remote), 13 cores`. The broker writes it
to `.conductor/now/remote.line` every 2 s; the inbox shows such a line only while it is
under 30 s old (`inbox.now_files`).
