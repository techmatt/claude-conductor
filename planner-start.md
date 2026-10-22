# planner start — the session start checklist

The only file a planner session start reads (**start a new <project> session**). Before any other act — a signal, request, prompt, report, or any phrase of Matt's but this one — read `planner.md` whole (beside this file): the method, the bridge, the files and the vocab.

## Start: five calls

1. **Tool search**, exactly `select:mcp__remote-devices__device_request_folder_access,mcp__remote-devices__device_list_dir,mcp__remote-devices__device_stage_files,mcp__remote-devices__device_commit_files`. It finds nothing: say "This chat isn't linked to the box: link it (the desktop app's Chat tab, not Code) and start again", then stop. No folder request, no other call, no workaround.
2. **Access**, one request for `C:\Code\<project>-control`, each code repo as `C:\Code\<repo>` (`wordwarriors`; `corelight-engine`, `corelight-web`; a new project's: `conductor.toml` `[repos]`) and `C:\Code\claude-conductor`. These paths as written: never a memory read or past-chat search for them.
3. **One shell command** in the workspace writes the signal and prints its stamp, local time:
   `s=$(TZ=America/Los_Angeles date +%Y%m%d-%H%M%S); printf 'by: planner\nsession: start\n' > planner-$s.signal; echo $s`
   (`printf` gives LF line endings). Never read it back.
4. **Commit** it to `signals\planner-<stamp>.signal` in the control folder.
5. **Stage** this file and `session.md` in one call, and read both. `session.md`'s header names the signal it was written for; an older one: stage it again, once.

Steps 1–4 are the same in Matt's saved preference, so they run before this file is read. Nothing else at start: no memory reads, past-chat search, folder listings, `claude-ai-new-project.md`, `inbox.md`, `state.md`, design docs or receipt. `session.md`'s *From state.md* carries `state.md`'s *Waiting on Matt*, *Open questions* and the *Pending fixes* and *Maintenance* lines saying "due", as written.

## Then, from the digest

**Open questions first** (blocking ones, then each default and when it is taken), then *Waiting on Matt*, due pending fixes, unreviewed reports, pickers waiting, rejected signals (*Red*: say why each was refused), conductor's reports (a line each) and maintenance recommendations (a line each with its reason: `planner.md`, *Maintenance*). Then wait for Matt: never assume or schedule the next step.
