# Questions

An executor reaches Matt with the `ask` tool (`mcp__conductor__ask`):

```
ask(question, options?, default?, block?)     # block defaults to false
```

A call writes `questions/<repo>__<NNN_name>__<n>.md` (`n` counts from 1 per item) and
holds the tool call open until the question resolves:

```
---
item: sandbox/012_ask_block
asked: '2026-10-15T14:31:31-07:00'
block: false
default: notes/default-taken.md
deadline: '2026-10-15T14:41:31-07:00'   # asked + ask_default_minutes; null when blocking
session_id: a6874e17-…
---
What should the notes file be called?

Options:
- notes/default-taken.md
- notes/answered.md
```

- **Answer.** The planner (or `conductor answer <id> <text>`) writes
  `questions/<id>.answer.md`: the answer as plain text (front-matter, if any, is
  dropped; a `by:` field in it — the app writes `by: app` — goes on the `answer` or
  `late` event). It is read once it has been unchanged for 1 second. The tool returns
  `Answer to <id>:` and the text.
- **Default.** With a default and `block: false`, an unanswered question returns the
  default at its `deadline`; the tool result says nobody answered and the default was
  taken. An answer file written after that is logged `late` and does nothing else.
- **Block.** `block: true`, or no default at all: the call waits until answered,
  however long. That time does not count toward `max_hours`.
- **Closed.** An item that ends with a question still open (killed, crashed) has it
  closed by the daemon (`question_closed`).

The ask server resolves the question by creating `<id>.result.json` exclusively
(`outcome`: `answered` | `default` | `cancelled`, `text`, `at`, `by`); the daemon only
creates one for `closed`. So a question resolves exactly once, and an executor gets its
answer even while the daemon is down. The daemon observes the folder each tick and logs
`ask`, `answer`, `default`, `late` and `question_closed`, each naming the question
in `detail.question`. Open questions lead the inbox under **Questions** whatever the
read mark says, each with the answer file to write. `conductor questions` lists them;
`conductor answer` takes the full id or an unambiguous short form (`012_ask_block__1`,
`012__1`).
