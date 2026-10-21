Self-audit of your own run of {{original}} in `{{repo}}`. Look back over that run, from
what this session remembers and from your record named above (its prompt, report, run
log, stage log, audit notes and events). Change nothing: this is a look back, not rework.

Write audit notes to the audit notes file described above, about that run, not about
this look back. Start the file with the line `Self-audit of {{original}}.`, then use its
four headings (Poorly structured, Slower than expected, Refactor or clean up, Docs that
would have saved time) and three more:
- What happened vs. what was asked: where the run diverged from the prompt's goal,
  budget or plan, and the moment each divergence started (a time from the run log, or
  the step).
- What misled you: anything in the prompt, template, preamble, repo docs or CLAUDE.md
  that pointed the wrong way or was missing. Quote it, and give the wording that would
  have worked.
- Where the time went: the largest blocks of wall-clock time and what would have cut
  each.
Be concrete and candid: name files, times and quotes, not impressions. The next cleanup
of `{{repo}}` acts on these notes.

The report, after its header, has:
- **Summary**: the main points of the three new headings, a few lines.
- **About conductor**: findings about conductor itself (the template, the preamble, the
  planner's prompt, conductor's tools), each with the change you would make, for the
  planner to carry to conductor's HANDOFF; `none` if there are none.

The status is `done` when the notes are written, `partial` when part of the record could
not be read, and `red` only when something is broken.
