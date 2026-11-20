# The planner's MCP server

`conductor mcp` (`src/conductor/planner_mcp.py`, reads in `planner_read.py`) is a stdio MCP
server named `conductor` that a claude.ai planner chat linked to this machine calls through
the Claude desktop app (as `mcp__remote-devices__conductor__<tool>`). It replaces the file
bridge's stage / commit / receipt round with typed calls that return their result (136,
138, 140). The bridge (`planner-bridge.md`) is the exception: images, or a note saying why.

## The rule for every write

A tool that changes something writes the very file the planner wrote over the bridge (a
signal, request, queue or proposed item, amendment, answer file, parked line, backlog item),
`by: planner`, as `.tmp` then renamed, LF endings, local-time stamps; then waits up to 30s
(`RECEIPT_SECONDS`) for its receipt (`receipts.md`) and returns it, lean: `accepted: <result>`
(and `WARNING: …`), `pending (<write>): <what holds it>` or `REFUSED: <reason>` (ending with
the accepted form), then up to four event lines. A rewritable file (queue, proposed, parked,
backlog) waits for a receipt whose `written_ms` is at least the write's. No receipt in time:
it says so and names `read(what="receipt")` for later. The daemon tells its writes apart
(*Attribution*).

## Attribution

Before each write lands the server writes a mark, `receipts/.mcp/<the write's path, / as ~>`
(`daemon_receipts.mark`; for a signal or amendment, just before its rename). The daemon's
`EventLog.before_append` hook adds `via: mcp` to every event of a marked write: one whose
paths hold it, every event of a marked signal file or request (`_receipt` context), and an
item renumbered from the marked name. The write's receipt says `via: "mcp"` and takes the
mark away; a mark no receipt took is pruned with the receipts (3 days). Proposed, parked
and backlog writes, which log no event of their own, log `planner_write` (`what`, the path)
when marked, so they show too. A bridge write is one without `via`; `conductor stats`
counts both per session (`stats.md`).

**A daemon that takes no writes** (no `daemon.json` process, or a stale heartbeat:
`state.daemon_info`) refuses every project write before anything is written:
`<project>'s daemon is DOWN: it is stopped. Start it: … Nothing was written.` Reads and
`status` still answer.

## The tools

| tool | writes / reads | returns |
|---|---|---|
| `status(project?)` | `state.scan`, `daemon.json`, `events.db` | the planner writes since the newest session start (count, the newest 4: *Two chats*); per repo the running item (elapsed, kill time, the newest progress line *with a note*, the outline), staged, queue (six names, why the head waits), red hold; proposed; open questions; ≤1.5 KB. No project: a line each |
| `start_session(project, label?)` | signal `session: <label>` | `planner.md` whole, `=====`, then `session.md` (paged) once its header names the signal |
| `read(project?, what, name?, section?, part?)` | see below | the text, ≤8 KB a part (`CAP`), cut at a line, `[part 1 of 3; next: read(…, part=2)]` |
| `queue_item(project, repo, name, body, meta?, held?)` | `queue/<repo>/NNN_name.md` or `proposed/` | the receipt: `queued as <id>; <start>`, or a rewrite's `rewritten: … (what changed)` |
| `amend(project, item, text)` | `queue/<repo>/NNN_name.amend[.N].md` (`actions.amend`) | the receipt |
| `signal(project, lines)` | `signals/planner-<stamp>-<n>.signal` (`actions.write_signal`) | the receipt |
| `edit_state(project, file, edits? \| sections? \| content?, message?)` | `requests/<YYYYMMDD-HHMM>-mcp<SS><n>/` | the receipt (`committed <sha>`) |
| `answer(project, question, text)` | `questions/<id>.answer.md` | the receipt |
| `park(project, repo, line?, op?, match?)` | add: a line appended to `parked/<repo>.md`; remove / replace: a `park:` signal (144) | the receipt, with what waits |
| `memory_note(project, repo, note, op, body?, description?, edits?)` | `memory-edits/<stamp>-<n>.md`, applied by the daemon (`memory-notes.md`) | the receipt |
| `note(kind, …)` | below | the receipt, or what was written |

**`queue_item`**: `name` is a slug (`fix_menu`: numbered as the repo's next item,
`nightshift.queue_name`, unless one queued or proposed item already ends in that slug, which
is then rewritten) or `NNN_slug` (written as given; an existing one is rewritten; a taken
number is renumbered by the daemon, its receipt saying so). `meta` is the front-matter
beside `repo`. An item queued while the same name is proposed (or the reverse) is refused.
`warn` (143) adds a `WARNING` for a body asking an executor to write under `~/.claude`
(`memory_note` instead; `amend` too) or a `readonly` one naming `farm-seeds` or remote
workers without `remote: true`.

**`edit_state`**: `file` is a tracked control file, or `state.md`, `paged.md`, `habits.md`
before they are tracked. `edits` (old/new) are first applied to the file on disk: `old`
as whole lines when those occur once, else any piece of the text that occurs once, widened
to the lines around it (`widen`, 143: state.md lines run to 2 KB, and a few words cost the
line typed twice). A piece's `new` replaces it alone (the receipt says so); an empty one
for less than whole lines is refused, showing the line (144: fractals' left a line's tail). None or several writes nothing and returns the error plus the
file's lines around the best match for the missed edit's first old line (`near`, numbered),
so a retry needs no read. The request carries edit blocks (applied by the daemon against
the live copy); text holding a conflict marker, or `content`, goes as a whole file written
before `request.md`. An edit that changes nothing writes nothing.

`sections` (140) moves text without retyping it: in order, `{op, section, to?, body?}` on
`file`'s `## ` sections (`section`: the heading, else the one heading it starts, case,
`*` and a leading `## ` free, 142), `delete`, `replace` (the heading kept, `body` under it) or `move` (appended to
`to`, a tracked file or a new `.md`, created). One request carries it all: the source as
edit blocks (whole when they wouldn't apply the same way), a move's destination whole; the
default message names each op (`state: move *Paged* to paged.md`). A section not found, or
several, refuses with the file's section list, nothing written.

**`read`** `what`:
- control files: `state`, `paged`, `habits` (an unsplit project's `state.md` section when the
  file doesn't exist), `inbox`, `review`, `session`, `remote-diag`; `section` picks a `## `
  (else `### `) heading by its start, case-free;
- `report` (`name`: `NNN`, `NNN_name`, `repo/NNN_name`): first its header lines, title and
  section list with sizes; `section` reads one, `"all"` the whole; `audit`: its notes. A
  resume's prompt (`.api2.md`) never makes a name ambiguous; a missing report says why
  (`missing; red: <reasons>`, 143);
- `question` (an id or suffix), `picks` (a picker's saved choices, pretty-printed),
  `receipt` (a write's path; none: the server's writes of the last 2 h, 142), `stats` (`weeks`, default 4: `conductor stats`' text, `stats.md`);
- `repo` (`name`: `<repo>/<path>`): a tracked text file of a project's code repo, read-only,
  paged and `section`ed like the rest; a markdown file with `## ` sections comes header-first
  as a report does (141; `section`, or `"all"`, for the text); `<repo>` or a folder lists its tracked names. Refused
  with the reason: a repo not the project's, a path outside it (`..`, `.git`), untracked,
  git-ignored, binary, not UTF-8, or an image (`.png`, `.jpg`, … `.svg`, `.pdf`: the
  planner looks at those itself, over the bridge);
- conductor's own: `planner`, `vocab`, `bridge` (`planner-bridge.md`), `new-project`,
  `planner-notes`, `planner-ideas`, `ledger` (its last 12 lines and what waits in
  `prompts/inbox`), `doc` (`name`: a `docs/` topic; none names them all), `conductor-report`
  (`name`: `139`, a name or its start: header and sections first, as `report`; none: what is
  past the planner's *Last report reviewed* mark, the line every such read ends with).

**`note`** kinds: `backlog` (`backlog/<repo>/<name>.md`, receipt) and, in conductor's own
folder (no receipt): `conductor_prompt` (`prompts/inbox/<YYYYMMDD-HHMMSS>-<project>-<name>.md`),
`prompt_support` (`prompts/support/<name>`), `planner_note` (a line under *For HANDOFF Open*
in `planner-notes.md`), `reviewed_mark` (its *Last report reviewed* line), `planner_idea` (a
line under *Waiting on evidence* in `planner-ideas.md`).

**Reach.** Projects are the `--control` folders, else the app's remembered list (QSettings
`conductor/conductor-app`, key `controls`), else `*-control` folders beside this repo. Paths
are built from names checked against the project (repos, item names, tracked files); in a
code repo only tracked files are read (`read` `repo`), and nothing is written.

## Registration

The Claude desktop app (`%LOCALAPPDATA%\AnthropicClaude`) reads local MCP servers from
`%APPDATA%\Claude\claude_desktop_config.json`, `mcpServers`, at start:

```json
"conductor": {"command": "C:\\Code\\claude-conductor\\.venv\\Scripts\\python.exe",
              "args": ["-m", "conductor", "mcp"]}
```

The venv's python, not `uv run`: the app's PATH may lack `uv`. The other route, a desktop
extension (`.mcpb`), was not taken.

## The launcher

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

## Testing

`tests/mcp_client.py --planner <tool> '<json>' [--control <folder>]` calls a tool as the app
would (through the launcher); `--status [project]` for `status`. `tests/test_planner_mcp.py`
drives each tool against a test control folder, ticking the daemon while the call waits for
its receipt.

`conductor mcp --selftest [--control <folder>]`, after any prompt that touches the server,
calls every tool through the launcher against the sandbox (`conductor-sandbox-control`
beside this checkout), one `ok` / `FAIL` line each, exit 1 on a failure. Its writes stay
harmless: a held probe item (`selftest_probe`) then a `cancel:` of it, a session start; the
other writes check the refusal that names what is there. Then calls sent together:
`reads-at-once` (three reads) and `write+read` (the probe again beside `status`, cancelled
after), each failing if any goes unanswered. The sandbox daemon must be up.
`tests/test_mcp_launcher.py` sends five calls at once, a batch, and calls to a worker that
dies or never answers.
