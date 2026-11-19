# The planner's MCP server

`conductor mcp` (`src/conductor/planner_mcp.py`, reads in `planner_read.py`) is a stdio MCP
server named `conductor` that a claude.ai planner chat linked to this machine calls through
the Claude desktop app (as `mcp__remote-devices__conductor__<tool>`). It replaces the file
bridge's stage / commit / later receipt stage with typed calls that return their result
(136 trial, 138 surface). The bridge still works: `planner-bridge.md`.

## The rule for every write

A tool that changes something writes the very file the planner wrote over the bridge (a
signal, request, queue or proposed item, amendment, answer file, parked line, backlog item),
`by: planner`, as `.tmp` then renamed, LF endings, local-time stamps; then waits up to 30s
(`RECEIPT_SECONDS`) for its receipt (`receipts.md`) and returns it, lean: `accepted: <result>`
(and `WARNING: …`), `pending (<write>): <what holds it>` or `REFUSED: <reason>` (ending with
the accepted form), then up to four event lines. A rewritable file (queue, proposed, parked,
backlog) waits for a receipt whose `written_ms` is at least the write's. No receipt in time:
it says so and names `read(what="receipt")` for later. The daemon, the event log and the app
are unchanged: the server is one more writer of the planner's files.

**A daemon that takes no writes** (no `daemon.json` process, or a stale heartbeat:
`state.daemon_info`) refuses every project write before anything is written:
`<project>'s daemon is DOWN: it is stopped. Start it: … Nothing was written.` Reads and
`status` still answer.

## The tools

| tool | writes / reads | returns |
|---|---|---|
| `status(project?)` | `state.scan`, `daemon.json`, `events.db` | per repo the running item (elapsed, kill time, the newest progress line *with a note*, the outline), staged, queue (six names, why the head waits), red hold; proposed; open questions; ≤1.5 KB. No project: a line each |
| `start_session(project, label?)` | signal `session: <label>` | `planner.md` whole, `=====`, then `session.md` (paged) once its header names the signal |
| `read(project?, what, name?, section?, part?)` | see below | the text, ≤8 KB a part (`CAP`), cut at a line, `[part 1 of 3; next: read(…, part=2)]` |
| `queue_item(project, repo, name, body, meta?, held?)` | `queue/<repo>/NNN_name.md` or `proposed/` | the receipt: `queued as <id>; <start>`, or a rewrite's `rewritten: … (what changed)` |
| `amend(project, item, text)` | `queue/<repo>/NNN_name.amend[.N].md` (`actions.amend`) | the receipt |
| `signal(project, lines)` | `signals/planner-<stamp>-<n>.signal` (`actions.write_signal`) | the receipt |
| `edit_state(project, file, edits? \| content?, message?)` | `requests/<YYYYMMDD-HHMM>-mcp<SS><n>/` | the receipt (`committed <sha>`) |
| `answer(project, question, text)` | `questions/<id>.answer.md` | the receipt |
| `park(project, repo, line)` | a `- ` line appended to `parked/<repo>.md` | the receipt |
| `note(kind, …)` | below | the receipt, or what was written |

**`queue_item`**: `name` is a slug (`fix_menu`: numbered as the repo's next item,
`nightshift.queue_name`, unless one queued or proposed item already ends in that slug, which
is then rewritten) or `NNN_slug` (written as given; an existing one is rewritten; a taken
number is renumbered by the daemon, its receipt saying so). `meta` is the front-matter
beside `repo`. An item queued while the same name is proposed (or the reverse) is refused.

**`edit_state`**: `file` is a tracked control file, or `state.md`, `paged.md`, `habits.md`
before they are tracked. `edits` (old/new, whole lines) are first applied to the file on
disk with `request.apply_edits`; a miss writes nothing and returns the error plus the
file's lines around the best match for the missed edit's first old line (`near`, numbered),
so a retry needs no read. The request carries edit blocks (applied by the daemon against
the live copy); text holding a conflict marker, or `content`, goes as a whole file written
before `request.md`. An edit that changes nothing writes nothing.

**`read`** `what`:
- control files: `state`, `paged`, `habits` (an unsplit project's `state.md` section when the
  file doesn't exist), `inbox`, `review`, `session`, `remote-diag`; `section` picks a `## `
  (else `### `) heading by its start, case-free;
- `report` (`name`: `NNN`, `NNN_name`, `repo/NNN_name`): first its header lines, title and
  section list with sizes; `section` reads one, `"all"` the whole; `audit`: its notes;
- `question` (an id or suffix), `picks` (a picker's saved choices, pretty-printed),
  `receipt` (a write's path), `stats` (`weeks`, default 4: `conductor stats`' text, `stats.md`);
- conductor's own: `planner`, `vocab`, `bridge` (`planner-bridge.md`), `new-project`,
  `planner-notes`, `planner-ideas`, `ledger` (its last 12 lines and what waits in
  `prompts/inbox`), `doc` (`name`: a `docs/` topic; none names them all).

**`note`** kinds: `backlog` (`backlog/<repo>/<name>.md`, receipt) and, in conductor's own
folder (no receipt): `conductor_prompt` (`prompts/inbox/<YYYYMMDD-HHMMSS>-<project>-<name>.md`),
`prompt_support` (`prompts/support/<name>`), `planner_note` (a line under *For HANDOFF Open*
in `planner-notes.md`), `reviewed_mark` (its *Last report reviewed* line), `planner_idea` (a
line under *Waiting on evidence* in `planner-ideas.md`).

**Reach.** Projects are the `--control` folders, else the app's remembered list (QSettings
`conductor/conductor-app`, key `controls`), else `*-control` folders beside this repo. Paths
are built from names checked against the project (repos, item names, tracked files); nothing
in a code repo is read or written.

## Registration

The Claude desktop app (`%LOCALAPPDATA%\AnthropicClaude`) reads local MCP servers from
`%APPDATA%\Claude\claude_desktop_config.json`, `mcpServers`, at start:

```json
"conductor": {"command": "C:\\Code\\claude-conductor\\.venv\\Scripts\\python.exe",
              "args": ["-m", "conductor", "mcp"]}
```

The venv's python, not `uv run`: the app's PATH may lack `uv`. The app starts the server
once and keeps it, so a change to the server's code or tool list takes a full quit and
restart of the app. The other route, a desktop extension (`.mcpb`), was not taken.

## Testing

`tests/mcp_client.py --planner <tool> '<json>' [--control <folder>]` calls a tool as the app
would; `--status [project]` for `status`. `tests/test_planner_mcp.py` drives each tool against
a test control folder, ticking the daemon while the call waits for its receipt.
