# Run logs

Run logs are most of a control folder's size (Word Warriors: 144 MB
of 150 MB after 29 items). Nearly all of it is file copies the stream repeats: each
image an executor reads appears twice as base64 (the tool result and
`tool_use_result.file.base64`), and each edit carries the whole file before it
(`tool_use_result.originalFile`). When an item finishes, conductor rewrites its
`.log`, `.stage.log` and `.merge.log` with those strings emptied and their length kept
as `dropped_by_conductor` (`runlog.compact_log`; a log under 1 MB is left alone).
The result line, what the executor said and every tool call stay, so the app's *Log*
view and `read_result` are unchanged; 18 MB typically becomes under 2 MB. The full
session, images included, stays in Claude Code's own store (`conductor open`). Then
each of those logs of 256 KB or more is gzipped to `<name>.log.gz` and the plain one
removed (`runlog.gzip_log`): compacted logs still ran 8–23 MB per bug hunt (Word
Warriors' control folder ~278 MB), and stream-json shrinks ~10×. Every reader goes
through `runlog.stored_log` / `open_log` (`src/conductor/runlog.py`, re-exported by `executor`) (`read_result`, the app's *Log* and *Stage log*
tabs, a resume item's readable files, a disk cleanup's measurements), so either form
works; by hand, `python -c "import gzip,sys; sys.stdout.write(gzip.open(sys.argv[1],'rt',encoding='utf-8').read())" <file>`.
At daemon start the same pass runs over every finished item's logs, which also covers
logs from before either step existed. A failure is a `warning`; the log is left as it was.

**Step index** (151: fractals 091's post-audit of an `audit: false` run had only the event log
and a 480 KB gzip with no index by step). As an item finishes, before compaction, conductor
writes `reports/<repo>__<NNN_name>.steps.md` (`runlog.index_log`): one line per tool call of
the lead session, numbered, with its local time (when its result came back), `L<n>` (its line
in the stream, which compaction keeps), the tool and its main argument cut at 140 characters;
`progress` notes in bold, a subagent's own calls counted as `+N inner` on the call that ran it.
A resume item (a post-audit) gets it in its readable files, written then for a log from
before 151 (`resume.readable`); the self-audit template says to read it first. Chosen over
audit notes on by default for long items: it costs an executor nothing, covers every item, and
older logs get one on demand.
