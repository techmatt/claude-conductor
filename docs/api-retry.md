# API outages: retry, not red

A transient Claude API failure costs a few minutes, not a red item, a queue hold and a lost
night (111, `daemon_api.py`). Found live: aicomplete 211 was six hours in, its fresh read
farming, when its next API call got `Failed to authenticate. API Error: 403 Attention
Required! | Cloudflare` and the executor exited 1 at 1:48am. It went red, held the queue, and
queued a post-audit (216) that launched 2 s later into the same outage, went red on its first
turn and held the queue again. Nothing moved until Matt resumed 211 by hand at 7:40am.

## What counts

`daemon_api.api_error`: the executor left no result or an error result, and its result text,
the last five lines of its run log or the end of its stderr match `API_ERROR`: `API Error:`
with 401, 403, 408, 429 or any 5xx, `Connection error`, `Request timed out` or `Overloaded`;
`Failed to authenticate`; Cloudflare's `Attention Required!`; `overloaded_error`;
`rate_limit_error`. Never a 400 (a request too long fails the same way again). A run with
a tamper, a kill at `max_hours`, a launch failure, or a watch is judged as before.

## The retry

At the exit, before anything is reaped or judged (`finish` → `_api_wait`):

- The run stays in `running/` (its lock, its worktree, its repo's place) with `api_wait`
  (`try`, `at`, `since`, `error`) in its record, and an `api_retry` event (`state:
  waiting`, `attempt`, `of`, `at`, `error`). Jobs it launched with `start` keep running:
  nothing is reaped.
- After the wait (`[defaults] api_retry_minutes`, default `[2, 10, 30]`: one per try) the
  same session resumes in the same worktree (`claude -p --resume <session id>`), told that the
  API dropped it, when, and to check what was in flight and carry on
  (`reports/<repo>__NNN_name.apiN.md`), plus any amendments sent meanwhile. Its deadline moves
  by the time it waited (the clock is paused), leaving it at least 30 minutes
  (`RETRY_FLOOR`; a `retry:` of an item killed at `max_hours` would die at once otherwise).
  `api_retry` with `state: resumed`.
- A daemon restart meanwhile keeps the wait (`_recover_spawned`).
- When the tries are spent, or the session can't be resumed (no transcript, the worktree
  gone or off its branch), the item is red with one reason: `API error (<the line>); N
  retries spent` (or `could not resume: …`). A missing report is not added: it is the API's
  doing too. `api_retry_minutes = []` reds at once, as before 111.
- A run's `total_cost_usd` counts its whole session, so the resumed run's total is the
  item's. The `finish` carries `api_tries`.

**Seen while it waits**: the inbox and `session.md` show each `api_retry` line; *Now* reads
`<item> — **waiting on the API** (<error>): try N at <time>`; the app's Repos row reads
`waiting on API` with the try and error. The night shift's stall check skips it (it has
exited) and, once it resumes, counts quiet time from the resume.

## `retry: <repo>/<NNN_name>`

The same resume on demand, for a finished **red** item whose worktree is kept (not a readonly
item, whose worktree is discarded) and whose session transcript exists. Refused (an `invalid`
event) for any other status, an item running or landing, one already landed, or while
another non-readonly item runs in its repo. Its record and file move back from `done/` to
`running/`, it gets a free port if its own is taken, and its session resumes with
`.retryN.md`: who asked and the red's reasons. It then finishes, merges and reports as
usual; a non-red finish clears the red hold it left (a `clear` by conductor). `api_retry`
with `state: retry` and `by`; the `finish` carries `retried`.

## Around it

- **Retros.** A red whose every reason is an API error or a launch failure (`api_only`)
  triggers no workflow retro and is never the one picked (`retros.md`).
- **Resume items** (ask, self-audit) never hold their queue on a red: they are readonly,
  there is nothing to protect (`resume.md`).
- **A fork's cost** is its own: the original's `cost_usd` at launch (`resume_cost`) is taken
  off its total (`own_cost`); 216 reported 211's $9.48.
