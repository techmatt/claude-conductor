Audit notes. While you do this item, keep a running notes file at exactly this path:
    {{notes_path}}
A later cleanup acts on it. Write it as you go, so it survives a kill, covering every task
you perform, under these headings:
- Poorly structured: what was badly organised in the code or the repo layout.
- Slower than expected: what took longer than it should have, and why, in tokens and
  context as well as wall-clock time, by activity where it's clear (locating code,
  understanding, editing, running and verifying).
- Re-derived or re-read: files re-read, searches repeated, facts established more than
  once, and what would have cached it (a CLAUDE.md or README line, an index, a script).
- Refactor or clean up: what should be refactored, restructured or tidied, with the
  files involved.
- Docs that would have saved time: what the repo's CLAUDE.md or README should say.
- Needs a decision: a note only the planner can settle (a ruling, scope, a trade-off), one
  `- ` line each with the options and your pick, so a cleanup implements rulings instead
  of deferring them back.
Each note names its files and is concrete enough for someone who never saw this session.
"Nothing to note" under a heading is fine. Never put the notes in the repo or commit them.
