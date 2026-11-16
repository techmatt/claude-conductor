# Hooks: preamble rules enforced

The executor's PreToolUse hook (`talk.pre_hook`, matcher `talk.PRE_TOOLS`: the shell, file,
Agent/Task and Workflow tools) denies a call that breaks a rule a hook can check cheaply and
reliably, with a one-line `permissionDecisionReason` starting `conductor: refused:` that says
what to do instead. The checks live in `rules.py` (the agent cap in `fanout.py`); the hook also
caps shell timeouts (`progress.md`, *Short calls*). It runs as
`<python> -m conductor.talk pre --cap <s> --control <folder> [--readonly] [--background=<glob> …]`,
one Python start per matched call. A hook error lets the call through.

Enforcement beats text (Anthropic's Claude Code team: short guidance plus enforcement work
better than long rule lists). A rule below marked **hooked** needs no preamble line beyond the
gist; the preamble rewrite after 122 drops the rest. Checked live (122): a real `claude -p` in
the sandbox with these settings had its `sed -i` and `%TEMP%` Write refused and its heredoc
commit message allowed.

## The ledger

| Rule (preamble) | State | How, or why not |
|---|---|---|
| Edit files with Write/Edit, never a shell heredoc, `sed` or `-c` string (full, light) | **hooked** | `sed -i`/`perl -i`; a heredoc redirected or `tee`d to a file; a heredoc script or `-c`/`-e` string with a file write (`open(…,'w')`, `write_text`, `writeFileSync` …); PowerShell `-replace` or a here-string into `Set-Content`/`Out-File`. A heredoc feeding stdin (`$(cat <<'EOF' …)`, `git commit -F - <<EOF`) and read-only `sed` pass. |
| Scratch and output never in `%TEMP%` (all three) | **hooked** | A file tool's path, or a shell command naming `%TEMP%`, `$TEMP`/`$TMP`/`$TMPDIR` (upper case: a shell's own `$tmp` is fine), `$env:TEMP`, `AppData\Local\Temp`, Git Bash `/tmp`, a bare `mktemp`, a temp-file API. Claude Code's own task output (`Temp\claude\…\tasks\`) may be read. |
| Never `isolation: worktree` (full) | **hooked** | An Agent/Task call's `isolation`, or the literal in a Workflow script. |
| Helpers never `git checkout`/`restore` (full) | **hooked** | A call with `agent_id` running `git checkout`, `restore`, `switch`, `stash` (not `list`/`show`), `clean -f` or `reset --hard`. The lead may. |
| Any fan-out ≤ 10 agents (full, light) | **hooked** (120) | `fanout.py`. |
| No elevation (full, light) | **hooked** (new) | `sudo`, `gsudo`, `runas`, `-Verb RunAs`. |
| Long jobs via `start`, never `&`/`nohup` (full, light) | **hooked** (new) | Bash: `nohup`, `disown`, `setsid`, a trailing `&`. |
| Wait with `wait_for`, never `sleep` or polls (full, light) | **hooked** (new) | A `sleep`/`Start-Sleep`/`timeout /t` of 5 s or more, or a `while`/`until` loop with a sleep. |
| Every background call sets a `timeout` (full, light) | **hooked** (new) | `run_in_background` with no `timeout`. |
| `~/.claude`: config only, never memory or a `CLAUDE.md` (full, light) | **hooked** (new) | A file tool writing `~/.claude/**/CLAUDE.md` or a `memory/` path there. |
| The report is the only file in the control folder (all three) | **hooked** (new), file tools | A file tool's path in the control folder outside `reports/` (`talk.touch_paths`). Shell writes stay with the tamper guard: a command naming the folder may only read it. |
| Readonly: commit nothing (readonly items, watches) | **hooked** (new) | `git commit` when the item is `readonly` (`--readonly`). |
| Scripts in a file, not a heredoc or `-c` string (full, light) | text | Only the writing ones are hooked: a read-only `python -c "print(…)"` or `node -e` one-liner is routine, and denying them all would be false positives. |
| Never touch the main checkout; never check out, commit to, rebase or push main (worktree items) | text | Git already refuses checking out a branch another worktree holds. A path check would need each launch's shared-link list: shared folders are the main checkout's own, linked in, so their main-checkout paths are legitimate. Worth hooking with that list passed in. |
| `paths:` only (items declaring it) | text | The merge's branch check is exact and catches shell-made changes (builds, formatters) a pre-hook can't see; a Write/Edit-only check would add little. |
| Heavy compute at below-normal priority, threads, 2 heavy subagents, full suite never beside a sim | text | A command doesn't say whether it is heavy; a subagent call doesn't say whether it builds. |
| Servers on `CONDUCTOR_PORT`; `npm ci` first | text | A listen port isn't visible in most command lines. |
| Asking, latitude, premises, progress, outline, messages, report rules, helper briefs | text | Judgement, not a call's shape. |
