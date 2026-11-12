Audit notes. While you do this item, keep a running notes file at exactly this path:
    {{notes_path}}
Nobody reviews it per item; a later cleanup acts on what is left. Write it as you go, so
it survives a kill, and cover every task you perform, under these headings:
- Poorly structured: what was badly organised in the code or the repo layout.
- Slower than expected: what took longer than it should have, and why, in tokens and
  context as well as wall-clock time: what consumed the most, broken down by activity
  where it's clear (locating code, understanding behaviour, editing, running and
  verifying, navigating between files).
- Re-derived or re-read: files re-read, searches repeated, context rebuilt (the same
  file, pattern or fact established more than once), and what would have cached it (a
  CLAUDE.md or README line, an index, a script, a fixture). Name the files.
- Refactor or clean up: what should be refactored, restructured or tidied, with the
  files involved.
- Docs that would have saved time: what the repo's CLAUDE.md or README should say.
- Needs a decision: a note only the planner can settle (a ruling, scope, a trade-off), one
  `- ` line each with the options and your pick. The planner rules on these when it reads
  your report, so a cleanup implements rulings instead of deferring them back.
Each note names the files it is about and is concrete enough for someone who never saw
this session to act on. "Nothing to note" under a heading is a fine answer. Never put
the notes in the repo, and never commit them.
