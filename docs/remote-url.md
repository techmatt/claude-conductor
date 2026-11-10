# One URL for good: remote workers across restarts

Open the worker page once, bookmark it, leave the tab open (94). The URL is
`http://<this PC's name>:<port>/w/<token>/`, and an open tab rides out everything below
with no action from Matt.

- **The name, not the IP.** Other machines resolve it through the router's DNS (it
  registers DHCP names: here `MattAurora2020` → `192.168.1.88` from `192.168.1.1`) or
  LLMNR/NetBIOS, so a new IP keeps the URL. Where a machine can't open it, set `host` to the
  IP in `[remote]` and reserve that IP in the router. `url_ip` in `broker.json` and
  `status.json` is the same URL by IP. A name that resolves to no LAN address here falls back
  to the IP.
- **The token is pinned.** A `[remote]` without `token` gets the one in effect (the old
  `.conductor/remote/token`, so a URL already shared keeps working) written into the
  section by a request the daemon applies and commits at its start (`remote.pin_token`:
  an edit after the `[remote]` line, commit "remote: pin the worker page's token…"; a failed
  one leaves its receipt and the file still serves). Clearing `.conductor/remote/` then
  changes nothing.
- **A broker restart** (killed, crashed, or a handover): the page and each core back off
  (2 s, 4, 8, 16, then every 30 s, jittered) and show `reconnecting`; a core's result is
  retried ~45 s so it lands on the new broker. The board (jobs, shards done and leased,
  workers and their gates) is saved to `board.json` every 2 s and taken up at start, so a
  gated worker goes straight on (one that failed its gate is gated again, 95) and `farm-seeds` carries on after its wait (`remote-workers.md`, *Placement*).
- **A daemon restart** leaves the broker alone (same settings).
- **A new commit**: the next job's key asks for the new bundle and a new gate; the tab
  loads it and re-gates (sandbox, 94: `c46fe7d` re-gated, 44 of 60 shards on the tab).
- **A new page version** (the page's files as served, hashed: `page.version` in the hello
  reply): each core finishes its shard and takes no other, then the tab reloads. The core
  slider, pause and *Keep screen awake* are in the page's `localStorage`, its worker id in
  `sessionStorage`, so it comes back as the same worker on the same settings.
- **Conductor's own code changes**: the broker hands over as the daemon restarts itself
  (`CodeWatch`, every 10 s: a committed change to `src/` or `pyproject.toml`, a clean
  checkout, and the new code importing). It starts the next broker with `--after <pid>`,
  stops taking calls (503: callers retry) and waits for those inside, saves the board, names
  the new pid in `broker.json` and exits; the new one waits for it, then takes the port and
  board. Sandbox (94): a 3000-seed job ran through a handover in 42.9 s (tab 92 shards,
  main box 28, one re-run), byte-identical to a local run. A handover is not amber in the app
  (`status.json` `handover: true`).
