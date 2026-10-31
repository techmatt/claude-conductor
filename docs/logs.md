# Run logs

Run logs are most of a control folder's size (Word Warriors: 144 MB
of 150 MB after 29 items). Nearly all of it is file copies the stream repeats: each
image an executor reads appears twice as base64 (the tool result and
`tool_use_result.file.base64`), and each edit carries the whole file before it
(`tool_use_result.originalFile`). When an item finishes, conductor rewrites its
`.log`, `.stage.log` and `.merge.log` with those strings emptied and their length kept
as `dropped_by_conductor` (`executor.compact_log`; a log under 1 MB is left alone).
The result line, what the executor said and every tool call stay, so the app's *Log*
view and `read_result` are unchanged; 18 MB typically becomes under 2 MB. The full
session, images included, stays in Claude Code's own store (`conductor open`). Then
each of those logs of 256 KB or more is gzipped to `<name>.log.gz` and the plain one
removed (`executor.gzip_log`): compacted logs still ran 8–23 MB per bug hunt (Word
Warriors' control folder ~278 MB), and stream-json shrinks ~10×. Every reader goes
through `executor.stored_log` / `open_log` (`read_result`, the app's *Log* and *Stage log*
tabs, a resume item's readable files, a disk cleanup's measurements), so either form
works; by hand, `python -c "import gzip,sys; sys.stdout.write(gzip.open(sys.argv[1],'rt',encoding='utf-8').read())" <file>`.
At daemon start the same pass runs over every finished item's logs, which also covers
logs from before either step existed. A failure is a `warning`; the log is left as it was.
