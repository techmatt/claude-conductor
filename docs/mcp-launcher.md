# The MCP launcher

The app starts the server once and keeps it, so `conductor mcp` is a thin launcher
(`mcp_launcher.py`, 140) holding no tool code: it relays each line to a worker (`conductor
mcp --worker`, the server itself, `planner_mcp.main`) and, before an `initialize` or
`tools/call`, starts a fresh worker when any `.py` under `src/conductor` changed (path,
mtime, size) since the running one started, or it died, replaying the client's
`initialize`. So a prompt that changes a tool's behaviour is live at the next call; a
new or renamed tool, a changed description or schema, or a change to the launcher itself
still takes a full quit and restart of the app, which lists tools once.

**Calls sent together** (141). A chat sends several calls in one turn by default. The
launcher relays each as it arrives and routes each reply by its id (a thread per worker
reads its output); a worker started on a code change takes the new calls while the one
before it finishes its own. In the worker (`PlannerServer.submit`), `status` and `read` run
at once on a pool; every write runs on its project's lane, one thread, so writes to one
project apply in the order they came; conductor's own notes share a lane. A call never ends
unanswered: a worker that exits answers each call it had with an error (a write may have
landed: `status`, a receipt), and a call over `CALL_SECONDS` (75) gets an error and its late
reply is dropped. A JSON-RPC batch (an array) is answered as one array; values run together
on a line are split. The worker reads the protocol from private copies of its pipes, fds 0
and 1 pointed at NUL: on Windows a child (git) started on a call's thread blocks while
another thread reads an inheritable stdin pipe (seen in 141's tests: a `status` never ended).

**The traffic log**: every message in and out, a line each (time, launcher pid, `in` /
`out`, id, tool and `what`, seconds taken, `ERROR`), never arguments or results, in
`out/mcp-traffic.log` (git-ignored; past 1 MB it moves to `.1`). It shows what the app
actually sent when a call goes unanswered.

**141's lost results.** From a linked chat after 140, calls sent together got one answer.
The old launcher took one call at a time and dropped a batch array silently; Claude Code's
own client, sending three reads at once, got all three through it, so the app's path does
something those don't, unseen without the log. A lost `edit_state` (aicomplete, 2:07pm) left
no request, mark or commit: it never ran.

**142's 80 s timeouts.** Around 2:17-2:25pm on 2026-11-19, `edit_state`, `status`, `note`
and `read` calls timed out at the bridge's 80 s in aicomplete and fractals with nothing
written: no request, receipt or `planner_write` event in either project in that window, both
daemons alive. The app's launcher then was still 140's (one call at a time; the traffic log
starts with 141's own tests at 2:17pm and the app's new launcher at 2:29pm, after the
restart), so this is 141's lost-calls case, fixed there. What 142 adds:

- **A call budget.** The bridge gives up at 80 s, and an answer after it is lost. The worker
  answers every call within `CALL_BUDGET` (65 s) of its arrival: a receipt wait shrinks to
  what is left, and a write still queued behind its project's other writes with under
  `CALL_MIN_LEFT` (8 s) left is refused with "not written: … Nothing was written; send it
  again", never written late. The launcher's own limit (`CALL_SECONDS`, 75) stays under 80.
  Three writes sent together, each waiting 30 s for a slow daemon, would otherwise pass 80.
- **Writes findable afterwards.** `read(what="receipt")` with no `name` lists the server's
  writes to the project in the last 2 h, newest first (`planner_read.recent_writes`): each
  receipt marked `via: mcp` with its outcome, and each mark no receipt has taken yet
  ("written, no receipt yet").

**Two chats on one project** (142: aicomplete session 36 learned only from `status` that
another chat had split state.md and queued two items). One launcher serves every linked chat
(pid 11284 served aicomplete and fractals all afternoon), so the server can't say whose a
write was. Instead `status` names the planner writes since the newest `session:` checkpoint
(`planner_read.session_writes`: requests, queued and rewritten items, the planner's
amendments, answers and reviews, `planner_write`s, memory notes; the newest 4, each with
its route; not conductor's own queue writes or an `approve:`'s move, 143), and
every write's receipt ends with their count. A chat that sees one it didn't make, or a
session number past its own digest's, has another chat writing beside it.
