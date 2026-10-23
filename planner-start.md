# planner start — the session start checklist

The planner session start checklist (**start a new <project> session**). Step 5 reads `planner.md` (beside this file: the method, the bridge, the files and the vocab) whole, before any other act.

## Start: five calls

1. **Tool search**, exactly `select:mcp__remote-devices__device_request_folder_access,mcp__remote-devices__device_list_dir,mcp__remote-devices__device_stage_files,mcp__remote-devices__device_commit_files`. It finds nothing: say "This chat isn't linked to the box: link it (the desktop app's Chat tab, not Code) and start again", then stop. No folder request, no other call, no workaround.
2. **Access**, one request for `C:\Code\<project>-control`, each code repo as `C:\Code\<repo>` (`wordwarriors`; `corelight-engine`, `corelight-web`; a new project's: `conductor.toml` `[repos]`) and `C:\Code\claude-conductor`. These paths as written: never a memory read or past-chat search for them.
3. **One shell command** in the workspace writes the signal and prints its stamp, local time:
   `s=$(TZ=America/Los_Angeles date +%Y%m%d-%H%M%S); mkdir -p /mnt/user-data/outputs; printf 'by: planner\nsession: start\n' > /mnt/user-data/outputs/planner-$s.signal; echo $s`
   (`printf` gives LF line endings; the commit takes only paths under `/mnt/user-data/outputs/`). Never read it back.
4. **Commit** it to `signals\planner-<stamp>.signal` in the control folder.
5. **Stage** this file, `session.md` and `planner.md` in one call, and read all three. `session.md`'s header names the signal it was written for; an older one: stage it again, once.

Steps 4 and 5 go out in the same turn: the digest usually already names the new signal, and the re-stage covers one that lags. Steps 1–5 are the same in Matt's saved preference, so this file is first read at step 5. Nothing else at start: no memory reads, past-chat search, folder listings, `claude-ai-new-project.md`, `inbox.md`, `state.md`, design docs or receipt. `session.md`'s *From state.md* carries `state.md`'s *Waiting on Matt*, *Open questions* and the *Pending fixes* and *Maintenance* lines saying "due", as written.

## Then, from the digest

**Open questions first** (blocking ones, then each default and when it is taken), then *Waiting on Matt*, due pending fixes, unreviewed reports, pickers waiting, rejected signals (*Red*: say why each was refused), conductor's reports (a line each) and maintenance recommendations (a line each with its reason: `planner.md`, *Maintenance*). Then wait for Matt: never assume or schedule the next step.
