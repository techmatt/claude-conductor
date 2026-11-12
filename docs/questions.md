# Questions

An executor reaches Matt with the `ask` tool (`mcp__conductor__ask`):

```
ask(question, options?, default?, block?, design?)     # block and design default to false
ask(resume=<id>)                              # wait on a question a message cut short
```

A call writes `questions/<repo>__<NNN_name>__<n>.md` (`n` counts from 1 per item) and
holds the tool call open until the question resolves. That holds the whole session (054 sat
10 minutes with a run queued behind it), so the preamble says to start background work first:

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
  `late` event). It is read once it has been unchanged for 1 second; its receipt is
  `receipts/questions/<id>.answer.md` (`receipts.md`). The tool returns
  `Answer to <id>:` and the text.
- **Default.** With a default and `block: false`, an unanswered question returns the
  default at its `deadline`; the tool result says nobody answered and the default was
  taken. An answer file written after that is logged `late` and does nothing else.
- **A default over the prompt** (61). A default may not contradict a prompt line unless
  the question's first line says `default overrides prompt: <line>` (aicomplete 009's
  unanswered 1am ask reversed the prompt's "skip it"). A flagged question with a default
  is marked: `overrides_prompt` on its `ask` and later events, bold on its inbox line and
  its `ask` and `default` lines, in its toast, and in an answer's receipt. Conductor can't
  judge a contradiction: the mark is the executor's to make, and a skip still binds. So
  since 68 a question or default naming an amendment (`amendment`, `amended`, by number or
  in words), with a default it could take and no flag, is refused once with a reminder to
  check; the same call again goes through (aicomplete 068's default crossed an amendment
  unflagged and the planner corrected it at 12:14am).
- **Block.** `block: true`, or no default at all: the call waits until answered,
  however long. That time does not count toward `max_hours`.
- **Design** (67). `design: true`, which needs a default and refuses `block`, is for a
  question touching a stated design principle: Matt's call, not a planner's turn-around.
  So is a lever that reverses a direction the brief or an amendment states, however clear the
  data (72; aicomplete 075 froze a cheaper card against its brief and amendment). Since 79,
  in both preambles, so are a ruling that puts over ~30% of a pass's items on one side (asked
  before acting on them all: aicomplete 111 cut 76 card prices on a misread ceiling, 112
  reverted them) and a fix that changes rules or play beyond its bug, that fix the default
  (111's "a draw never reshuffles"). Since 110, full preamble only, so is a reading of a prompt
  line, amendment, message or answer that removes or reverses what it names (aicomplete 201
  read "early troops are fine … report the band" with "fewer troops" as leave to remove every
  stage-I Muster, unflagged).
  The call returns at once and the executor carries on with its default, so the item is never
  blocked. The question has no deadline: it stays open, marked `design`, in the app and the
  inbox (never counted as `asking`, needing Matt or a watch flag), and outlives its item. When
  an answer file comes the daemon resolves it (`answer`, not `late`) and a running item gets
  the answer as a message (a `tell` with `answers: <id>`), an answer rather than an amendment;
  a finished item's stands in the log and inbox for the planner. Once its item has finished
  (a `finish` at or after the ask), the Questions tab and the inbox mark it "item finished:
  an answer records a ruling" and sort it below live questions (83, `questions.item_done`),
  and the item's *Unreviewed reports* line, `done` or `partial`, says `commit rests on open
  design default: <id>` so the planner raises it at once (84, `inbox.md`);
  answering it works as before. Before 83, aicomplete 128's two read as blocking with 128 done.
- **Closed.** An item that ends with a question still open (killed, crashed) has it
  closed by the daemon (`question_closed`).
- **A message never answers it** (58; before, a tell became the answer, and aicomplete's
  #619 overrode 016's own defaults that way). Only an answer file answers a question. A
  message hook can't reach an executor waiting in `ask`, so the ask server watches the
  item's mailbox: a `tell:` (planner, app, cli, a watch; `pause:` / `resume:` text too)
  arriving while the call waits hands it back early, saying the question is still open
  and to call `ask` with `resume: "<id>"` once the message is dealt with. The hook after
  the call delivers the message as usual. The `tell` event carries `waiting_on: <id>`,
  and its receipt says the question stays open. While the call is out, the server keeps
  `<id>.away`: the item is working, not blocked (stall checks apply as usual), and
  its `away_seconds` (in the result) don't count as blocked time. A default whose
  deadline passed meanwhile is taken at `resume`. Each mailbox file cuts a wait short
  once, so a helper's `ask` (the hook never delivers to one) can't spin.
- **A message that answers it closes it** (103; aicomplete 167's sat open 54 min after
  one did): the executor calls `progress` with `answered: "<id>"` (the full preamble's
  *Messages* says so). The server resolves it `by_message`, its reason the note; the daemon
  logs `question_closed`, shown as "closed: answered by a message". Only an open question
  of the same item; the executor judges, so a message is still never taken as an answer.
- **The watch answers.** A watch's `answer:` action (`night-shift.md`) writes an answer
  only from one Matt or the planner already gave, naming its source; `watch_answer` logs it.

**A stage has no ask tool**, so its questions go in its plan under a `Questions:` line or a
`## Questions` heading, one bullet each (`questions.plan_questions`; the heading since 75,
after aicomplete 090's four were lost under one). They ride the `staged` event
(`questions`, and `questions_parsed`, whose 0 the inbox line shows as *no questions
parsed from its plan*) and the inbox
and digest list them under *Staged plans' questions* until the item launches; an amendment
answers them (066's "96 games or 96 a cell?" sat unread through a 48-minute lock wait).

The ask server resolves the question by creating `<id>.result.json` exclusively
(`outcome`: `answered` | `default` | `cancelled` | `by_message`, `text`, `at`, `by`); the daemon only
creates one for `closed`. So a question resolves exactly once, and an executor gets its
answer even while the daemon is down. The daemon observes the folder each tick and logs
`ask`, `answer`, `default`, `late` and `question_closed`, each naming the question
in `detail.question`. Open questions lead the inbox under **Questions** whatever the
read mark says, each with the answer file to write. `conductor questions` lists them;
`conductor answer` takes the full id or an unambiguous short form (`012_ask_block__1`,
`012__1`).
