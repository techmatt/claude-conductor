# Desktop actions: `open:` and `notify:`

Two signals (`signals.md`) that let the planner act on Matt's desktop without computer use.

## `open:`

`open: <verb> <path or URL>` is one named desktop action, never a command
runner (`desktop.py`).

- `folder <path>`: the folder in File Explorer. `reveal <path>`: Explorer at the
  file's folder, the file selected.
- `file <path>`: the file in its default app. Programs and scripts are refused by
  extension (`.exe .com .bat .cmd .ps1 .py .js .vbs .msi .lnk .url .hta .reg`, …), by
  `PATHEXT`, and by Windows' own test (`AssocIsDangerous`).
- `text <path>`: the file in Notepad, never run, so a script can be shown. It is the
  one verb beyond the first four: "show me that script" is common and `file` refuses it.
- `url <URL>`: the default browser; only `https://` and `http://` on `localhost`,
  `127.0.0.1` or `::1`, and no user name or password in it.

A path is absolute, or relative to the control folder, and must exist. After
following junctions, links and `..`, it must lie inside the control folder, a
configured repo, the worktree root, conductor's own checkout, or a folder in
`conductor.toml`'s `open_roots`. A ` # comment` after it is stripped. Anything else
is an `open_refused` event (verb, target as given, reason, `by`); it is not red and
does not reject the file, so later lines still apply. An unknown verb or a missing
target is a syntax error (`invalid`). A launch goes through the detached launcher
(output in `.conductor/open.log`) and logs `opened` (verb, `path` or `url`); one
that cannot start is a `warning`. `conductor open-path <verb> <path>` does the same
from a shell, with `by: cli`, no daemon needed.

## `notify:`

`notify: <text>` raises a desktop toast titled `<project>: from the <by>`,
the text as its body (cut at 250 characters; a ` # ` is part of it). Logs `notify`.
