# Toasts

The daemon raises a Windows toast (library: `windows-toasts`, WinRT in-process) only for
what Matt acts on (115, `notify.note`); everything else is panel only:

- **An item finishing**, one toast per item with its repo's queue state merged in:
  `fractals: 051_drop_wording_checks done · queue now empty` (or `· 052_x started`,
  `· 052_x running`, `· 052_x next (held)`). `partial` and `red` are in the title; a red
  that holds its queue ends `· queue <repo> HELD (clear: <repo>)`; the body is the reasons
  and `picker ready: <ref>` when the report named one. A watch's finish toasts only red.
  `queue_empty` (logged when nothing is queued or running in the repo) no longer toasts.
- **What waits on Matt**: an `ask` that blocks (`block`) or has no default, a `design: true` ask (146),
  and a picker opened by
  someone else (`pick_open` not `by: app` / `cli`); a `notify:` signal or a watch's action.
- **Errors (something stopped)**: a `finish` red (the `red` event itself does not toast),
  `tamper`, `hold`, `checkout_dirty`, `run_failed`, `pick_failed`, `amend_refused`,
  `request_failed`, `invalid`, `push_failed`, `memory_critical`, `daemon_died`, a
  `daemon_stop` on an error, and the watch's stuck-item flag (`watch_flag` `quiet`).
- **A daemon down now**, from the app, not the daemon (139): one toast when a project's
  daemon dies or stops answering for 3 minutes (`memory.md`, *Death made visible*); since
  149 a death the app restarted reads `<project>: the daemon died; restarted`.

Panel only: report `warnings:`, non-blocking questions with a default (not `design` ones), progress
and outlines, ETA overruns, `memory` pressure and `load_high`, retros queued, parked
edits applied, watch reports, the empty queue, conductor's own maintenance.

**Coalescing** (`notify.Toasts`): a finish waits `SETTLE_SECONDS` (10) so its repo's next
launch can be named; anything else goes at once; and nothing shows within
`WINDOW_SECONDS` (60) of the last toast: those wait and go out as one,
`fractals: 2 done, 1 question`, a line each in the body. Pending toasts go out when the
daemon stops. In a one-repo project items are named bare (`051_x`), else `repo/051_x`.

The toast's tag is `<project>#<event ids>`, which is what the Action Center history
records. Toasts use Command Prompt's registered app id, with "Conductor" as the
attribution line, so nothing has to be installed. A toast that fails is logged as a
`warning` and never stops the daemon. `toasts = false` in `[defaults]` turns them off
(every project, no per-project choice); on other platforms each would fail, so turn them
off there.
