# Pushing to a remote

Merged work goes to the repo's remote without a hand push. Per repo, off by default:

```toml
[repos.app]
path = "../app"
push = true                   # push main to the remote after each merge (conductor init writes false)
```

## When conductor pushes

- **After each merge** into the repo's main branch (`main = …`, default `main`).
- **At daemon start**, when main is ahead of its remote-tracking ref (`refs/remotes/<remote>/main`):
  a push lost to a restart, or merges made while pushing was off.
- **On `push: <repo>`** (a signal; any configured repo, `push = true` or not): a retry after a failure.
- **On a `closeout:` request**: the control folder (only when it has a remote) and every
  `push = true` repo, after the closeout's own commit. `closeout_push` reports the lot once
  every one is done: what was pushed, what failed.

## The control folder

A control folder with a remote is pushed the same way, with no setting: the remote is all
it takes. Without one it is skipped quietly (no `push_failed`), except on `push: control`.

- **After conductor's own commits** (requests), batched: one push 30s after the last
  commit of a burst (`CONTROL_QUIET_SECONDS`), or 5 minutes after the first unpushed one
  however busy the burst (`CONTROL_MAX_WAIT_SECONDS`). `why: ["commit"]`.
- **At daemon start**, when its main is ahead of the remote-tracking ref.
- **On `push: control`** (a retry; the name means the control folder unless a repo is called
  `control`), and **on a `closeout:`**, at once, which carries any batch waiting.

Its target name in events and logs is `.control`. Generated and runtime files never reach
the remote: receipts, `session.md`, `review.md`, `inbox.md`, `events.db` and `.conductor/`
are git-ignored (the startup check warns when one is not).

The remote is main's upstream (`branch.main.remote`), else `origin`, else the only remote.
A `push = true` repo with none fails with "no remote".

## What a push is

`git push <remote> refs/heads/<main>:refs/heads/<main>`: main only, never a `conductor/*`
branch, never `--force`. Credentials come from git's own config (on Matt's box, HTTPS
remotes and `gh auth git-credential`); `GIT_TERMINAL_PROMPT=0` and `GCM_INTERACTIVE=never`
mean a missing login fails instead of waiting on a prompt nobody sees.

A push is a process the daemon polls each tick (`daemon_push.py`), so a slow network
never holds a tick or a merge. One push per target at a time: a merge during a push
queues the next. A push still running after 5 minutes is killed and reported. Its
output is `.conductor/push/<target>.log` (target: the repo name, or `.control`).

## Failures

A failure is one `push_failed` event (the inbox and the app's Events tab always show
it) with git's last lines, never hints. It never blocks work: merges go on. The same failure
again from an automatic retry (the next merge, a daemon start) logs nothing more; a
`push:` or a closeout always reports. The next success after a failure carries
`recovered`.

**Rejected** (`rejected: true`): the remote has commits main lacks, because someone pushed
from elsewhere. Conductor never force-pushes. Matt pulls them into the repo's main
checkout while nothing is merging there (`git pull` on main), then signals `push: <repo>`.

## conductor itself

`claude-conductor` is not a conductor project: its `CLAUDE.md` has each prompt push
`main` after committing.
