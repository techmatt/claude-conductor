Audit notes. While you do this item, keep a running notes file at exactly this path:
    {{notes_path}}
It is the second file you may write outside the working directory; nobody reviews it
per item, and a later cleanup item acts on it. Write it as you go, not at the end, so
it survives a kill, and cover every task you perform, under these headings:
- Poorly structured: what was badly organised in the code or the repo layout.
- Slower than expected: what took longer than it should have, and why.
- Refactor or clean up: what should be refactored, restructured or tidied, with the
  files involved.
- Docs that would have saved time: what the repo's CLAUDE.md or README should say.
Each note names the files it is about and is concrete enough for someone who never saw
this session to act on. "Nothing to note" under a heading is a fine answer. Never put
the notes in the repo, and never commit them.
