# The app's panels

The four tabs of a project in the desktop app (`docs/app.md`): Repos with each item's
Conversation, Questions, Events and Proposed. Code: `src/conductor/app/*_panel.py`, with
an item's detail in `trail.py`.

- *Repos* — a tree: each repo row, then up to three group rows, each item under its
  group. The repo row's note is a summary in the same words as the items' State
  cells, each item counted once (`1 running · 1 staged · 2 queued · 1 unreviewed`,
  then `N to pick` and the *audit pending* marker; `held` / `held after red` first
  while held; `idle` when there is nothing). Group rows are muted bold labels with
  their count (`In flight (3)`, `Finished since checkpoint (1 unreviewed)` — plain
  `Finished` before any checkpoint — and `Earlier checkpoints (29)`), and nothing in
  State, Note or the button column; an empty group is not shown. Every repo row and
  group can be collapsed, and each keeps the state it was left in across refreshes
  (*In flight* and *Finished* start open, *Earlier checkpoints* closed). A hold row
  sits directly under the repo, before the groups (the next item then reads *blocked
  by hold*); a red hold has a **Clear hold** button (`clear: <repo>`).
  *In flight*: each running item with its phase (`setting up`, `running`, `merging`, `concluding`;
  a readonly one beside the others reads `running · readonly`, a resume item `running · resume`
  with `resume of <orig>` in its note (`resume.md`), an `alongside` one
  `running · alongside`), its elapsed time and, while it runs, its port in the note (an
  `alongside` one adds `beside <names>` and `alongside: <line>`), then the queue in order, the next one marked `· next` with its
  decision (`wait`, `stage`, …) in the note, and any invalid item. A staged item is
  still queued, so it is one row whose state carries both: `staging` (with elapsed) or
  `staged` (note: plan ready), then `· next` or `· blocked by hold`; other queued items
  read `queued`. Each queued item has **Cancel** (asks first;
  `cancel: <repo>/<NNN_name>`).
  *Finished* holds the repo's current finished items (those not settled by a
  checkpoint), newest first: the
  10 most recent plus every unreviewed one however old, each with its status, finish
  time, carry, slow (unless `none`) and a *reviewed* / *unreviewed* mark (unreviewed
  bold, coloured by status; reviewed muted); the note's tooltip holds it whole. An
  unreviewed row needs Matt, so its name is amber on an amber cell and its **Mark
  reviewed** has the amber border, until it is reviewed or its picker is saved. Double-click opens the trail, as for live items. Each
  unreviewed row has **Mark reviewed** (`reviewed: <repo>/<NNN_name>`, `by: app`; the
  button disables once clicked and the row turns *reviewed* when the daemon logs it).
  The tab label reads *Repos (N unreviewed)*, in amber, while any report is unreviewed.
  A running item's note shows its latest progress line (note, ETA, done / to go, and
  when it was sent); while it executes it has **Tell…**, a text box whose message goes
  out as a `tell:` signal (`by: app`). A queued row (beside **Cancel**) and an executing
  one (beside **Tell…**) have **Amend…**, a multi-line box written as an amend file
  (`by: app`; see `progress.md`, *Amendments*). Every finished row has **Terminal** (open in terminal:
  `claude --resume`, see `progress.md`), beside **Mark reviewed** while
  unreviewed. A finished row whose report named a picker has **Pick…** (`pick: <ref>`,
  `by: app`; amber until saved) and its note adds ` · pick: saved 4:52pm (6 changed, 2
  notes)`, ` · pick: opened, not saved` or ` · pick: waiting for Matt` (see `picks.md`,
  *Pending*); the tab label adds `N to pick` while any is unsaved. Items settled by the
  latest checkpoint sit in *Earlier checkpoints* (the 10 newest; the count is all of
  them).
  The tab has no help line: an item's name tooltip says double-click opens it in Events,
  and **Clear hold**, **Cancel**, **Mark reviewed** and **Pick…** each have a tooltip
  naming what it does and the signal it sends.
  The tab is a split: the tree on the left, the selected item's detail on the right,
  so Matt follows one prompt end to end here and leaves *Events* for investigating
  conductor. Selecting an item row shows it (a group or repo row changes nothing); the
  selection survives refreshes and rebuilds (the item's row is reselected as it moves
  from queued to running to finished). With nothing selected the detail shows the
  running item (the longest-running, with several), else the most recently finished
  one. Double-click still opens the item in *Events*. The detail is the trail *Events*
  shows (Events, Prompt, Report, Log, Stage log, Git, **Open in terminal**) with
  **Conversation** first and selected: the item as a chat thread, oldest first,
  following the newest entry unless Matt has scrolled up. Matt's messages and
  amendments sit on the right, the executor's progress lines (note, ETA, done / to go),
  questions and its staged plan line on the left, conductor's start, picker and finish
  lines across. Under a tell sits its delivery and the executor's **reply** (the
  `progress` event tagged `reply_to` it; see `progress.md`), else *waiting for
  a reply* (delivered), *waiting for delivery* (not yet) or the never-delivered warning
  (sent after the last tool call). An amendment shows how it is delivered for the state
  it was written in, when it was, and (a running one) its reply the same way. A
  question shows its answer, the default taken, a late answer or its close, else
  *waiting for an answer*. The finish shows its header (status with reasons, blocks,
  carry, slow, pick) and a **Report** link to the Report tab; other reds show as their
  Activity line. Plumbing (`delivered`, `merge`, `reaped`, `enqueue`, `edited`,
  `commit`, `worktree`, …) stays in the detail's Events tab. The item's latest outline
  (`progress.md`, *Outline*) is pinned above the thread, outside its scroll, with when it was posted:
  done rows ticked, the next undone row bold and marked *next*. Every outline also sits
  in the thread at its time as a compact one-line turn (*Outline*, or under a
  *Progress* note it came with), and an outline request is a turn of Matt's titled
  *Outline request #N* with the outline that answered it as its reply. **Tell…**,
  **Outline?** (sends `outline: <item>`) and **Amend…** at the bottom act on the item
  on screen, enabled by the tree's rules (Tell and Outline? while it executes; Amend
  queued, staged or executing). The thread is `model.conversation`.
- *Questions* — open asks, blocking ones first, then by soonest default; each shows
  *BLOCKING* or the default and a live countdown to it, its options as buttons that
  fill the answer box (the default's marked *(default)*), and **Answer**, which writes
  `questions/<id>.answer.md` through the same function as `conductor answer` (never
  overwriting one), with `by: app` front-matter so the `answer` event names the app. An answer to a question that resolved meanwhile (its default taken)
  is reported as late, as the CLI does. Resolved questions are listed beneath, newest
  first. Question files are re-parsed only when they or their result change.
- *Events* — by default the **Activity** view: workflow-level lines only, worded
  plainly — `prompt X started`, `report X written — done/partial/red (carry: …)`,
  `report X reviewed`, `X used N audit note(s)`, `X: <progress line>`, `message #n to X: …` and `message #n
  reached X`, `amendment #n to X, while <state> (…)` and `amendment #n reached X`, run targets starting / up / opened / stopped, pickers opened / saved,
  questions asked / answered / default taken (and late answers and
  closes), approved, cancelled, reordered, cleared, a queued item rewritten (`edited`), `doc update committed: <message>`
  (a `request`) or `request failed`, daemon started / stopped, and every red, `warning`
  or `invalid` event whatever its kind. A `finish` is the *report written* line, not a
  separate entry. Everything else (`worktree`, `stage`, `staged`, `merge`, `commit`,
  `hold`, `enqueue`, `queue_empty`, `read`, …) appears only with **Advanced** checked
  (off by default), which also shows each event in the log's own wording. The *Kind*
  list offers only the kinds the current mode shows. `new checkpoint #N "label"` is an
  Activity line. **since checkpoint #N** (on by default) starts the list at the latest
  checkpoint's own event; off, it is the whole log. In both modes: filterable by repo, item (`001`, `001_x`, `repo/001_x`, or
  any substring of the id), kind, a from/to time range and *reds only*. Selecting an
  event shows its item's trail beside it: every event of the item (the selected one
  expanded with its full detail; wrapped lines hang under the event's text, or under the
  detail indent when the pane is too narrow to leave ~20 characters there), the prompt as sent (`.prompt.md`, else the item
  file), the report, the executor log and the stage log rendered from stream-json (text,
  `→ tool: input`, `← result`, the final result line with turns, time and cost), and
  *Git*: the item's commits with diffs from `conductor/<NNN_name>` while that branch
  exists, else the last `commits` commits ending at the `merge` event's `sha` (the
  rebased commits on main), else (an item run without a worktree) `git show` of the
  shas in its `commit` events. Git runs on a worker thread, only when its tab is shown,
  and again only when the item has new events. Moving to another event of the same
  item only refocuses the Events tab; new events for the open item update each tab in
  place (scroll position kept; a log being read at its end follows it). Rendered logs
  are cached per file version. A cancelled item's prompt comes from its kept copy in
  `.conductor/cancelled/` (the `cancel` event's `kept`). Keys: ↑/↓, PgUp/PgDn,
  Home/End move (the trail follows after a 120ms pause); Ctrl+F item filter (Enter
  returns to the list); Ctrl+R reds only; Ctrl+L the list; Alt+1…6 the trail tabs.
  Double-clicking an item in any other panel opens its trail here. The trail's
  **Open in terminal** is enabled for an item that has finished (and is not queued or
  running again).
- *Proposed* — `proposed/*.md` with its repo, first line and any problem (bad
  front-matter, unknown repo); **Approve** (`approve: NNN_name`; enabled only for a
  selected item without problems), **Cancel** (asks
  first), **Move up / Move down** (`reorder: proposed <a> <b>` swapping the two
  neighbours' numbers; the selection follows the moved item to its new name, and a
  second move waits until the first has landed), **Open trail** (the proposal in Events).

The *Questions* and *Proposed* tab labels carry their counts and are coloured from any
panel: red with `N blocking` while a question blocks, amber while only defaults are
pending or items await approval. The app always draws light (Fusion style and an
explicit palette, so a dark Windows theme cannot flip it); its colours are named tokens
in `app/theme.py`, each text colour at least 4.5:1 on the backgrounds it is drawn on.
