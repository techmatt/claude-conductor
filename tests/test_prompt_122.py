"""Prompt 122: preamble rules enforced by the executor's PreToolUse hook (`rules.py`), the
watch's round cap on tells, and cleanup notes deduped against handled ones."""

import json
from pathlib import Path

import pytest

from conductor import audits, rules, talk


def bash(command, **extra):
    return {"tool_name": "Bash", "tool_input": {"command": command, **extra}}


def denied(event, **kw):
    return rules.check(event, **kw)


COMMIT = "git commit -m \"$(cat <<'EOF'\nFix the thing\n\nNever %TEMP%, never sleep 30.\nEOF\n)\""


@pytest.mark.parametrize("command", [
    "sed -i 's/a/b/' src/x.py",
    "sed -E -i.bak 's/a/b/' x",
    "perl -pi -e 's/a/b/' x.py",
    "python -c \"open('x.py','w').write('hi')\"",
    "python3 -c 'from pathlib import Path; Path(\"x\").write_text(\"y\")'",
    "node -e \"require('fs').writeFileSync('a.js', s)\"",
    "cat > src/x.py <<'EOF'\nprint('x')\nEOF",
    "cat <<EOF >> notes.md\nline\nEOF",
    "tee docs/a.md <<'EOF' >/dev/null\nx\nEOF",
    "python - <<'PY'\nfrom pathlib import Path\nPath('a').write_text('b')\nPY",
])
def test_shell_edits_are_denied(command):
    assert "Edit files with Write or Edit" in denied(bash(command))


@pytest.mark.parametrize("command", [
    COMMIT,
    "git commit -F - <<'EOF'\nmsg > here\nEOF",
    "cat <<'EOF' | git commit -F -\nmsg\nEOF",
    "sed -n '1,20p' src/x.py",
    "grep -i foo x | sed 's/a/b/'",
    "python -c \"import json; print(json.load(open('a.json')))\"",
    "python - <<'PY'\nprint(open('a').read())\nPY",
    "uv run pytest -q 2>&1 | tail -5",
    "npm test > out/001_x/test.log 2>&1",
    "ls $TEMP_DIR_NOT_IT; tmp=out/x; echo $tmp",
    "cat C:/Users/m/AppData/Local/Temp/claude/C--x/abc/tasks/b1.output",
    "mktemp -p out/001_x",
    "git log --oneline -5 && git status",
    "sleep 2",
])
def test_legitimate_calls_go_through(command):
    assert denied(bash(command)) is None


@pytest.mark.parametrize("command", [
    "echo x > %TEMP%\\run.log", "python bench.py --out $TEMP/b.json", "ls /tmp/x",
    "cp a C:\\Users\\m\\AppData\\Local\\Temp\\a", "d=$(mktemp -d)",
])
def test_temp_in_the_shell_is_denied(command):
    assert "%TEMP%" in denied(bash(command))


def test_file_tools_temp_home_claude_and_control(tmp_path):
    temp = {"tool_name": "Write", "tool_input": {"file_path": "C:\\Users\\m\\AppData\\Local\\Temp\\x.json"}}
    assert "never %TEMP%" in denied(temp)
    mem = {"tool_name": "Write", "tool_input": {"file_path": str(Path.home() / ".claude/projects/x/memory/a.md")}}
    assert "config only" in denied(mem)
    assert denied({"tool_name": "Edit", "tool_input": {"file_path": str(Path.home() / ".claude/settings.json")}}) is None
    ctl = tmp_path / "proj-control"
    inside = {"tool_name": "Edit", "tool_input": {"file_path": str(ctl / "state.md")}}
    assert "control folder" in denied(inside, control=ctl)
    assert denied({"tool_name": "Write", "tool_input": {"file_path": str(ctl / "reports/a_report.md")}}, control=ctl) is None
    assert denied({"tool_name": "Write", "tool_input": {"file_path": "out/001_x/a.json"}}, control=ctl) is None


def test_isolation_helpers_readonly_elevation_jobs_and_waits():
    assert "isolation: worktree" in denied({"tool_name": "Agent", "tool_input": {"prompt": "x", "isolation": "worktree"}})
    assert denied({"tool_name": "Agent", "tool_input": {"prompt": "x"}}) is None
    wf = {"tool_name": "Workflow", "tool_input": {"script": "await agent('x', {isolation: 'worktree'})"}}
    assert "isolation" in denied(wf)
    helper = {"agent_id": "a1", "agent_type": "general-purpose"}
    for cmd in ("git checkout -- src/x.py", "git restore .", "git -C ../x stash", "git reset --hard HEAD"):
        assert "helper never" in denied(bash(cmd) | helper), cmd
        assert denied(bash(cmd)) is None                       # the lead may
    assert denied(bash("git stash list") | helper) is None and denied(bash("git diff") | helper) is None
    assert "readonly" in denied(bash(COMMIT), readonly=True) and denied(bash(COMMIT)) is None
    assert "elevation" in denied(bash("sudo choco install x"))
    assert "elevation" in denied({"tool_name": "PowerShell", "tool_input": {"command": "Start-Process x -Verb RunAs"}})
    assert "dies with its shell" in denied(bash("python farm.py &"))
    assert "dies with its shell" in denied(bash("nohup python farm.py > out/x/f.log 2>&1"))
    assert denied(bash("a && b")) is None
    assert "wait_for" in denied(bash("sleep 60; cat x.log"))
    assert "wait_for" in denied(bash("until grep -q EXIT x.log; do sleep 1; done"))
    assert "wait_for" in denied({"tool_name": "PowerShell", "tool_input": {"command": "Start-Sleep -Seconds 30"}})
    assert "needs a `timeout`" in denied(bash("npm test", run_in_background=True))
    assert denied(bash("npm test", run_in_background=True, timeout=900000)) is None


def test_powershell_rewrites_through_set_content_are_denied():
    ps = {"tool_name": "PowerShell", "tool_input": {
        "command": "(Get-Content a.py) -replace 'x','y' | Set-Content a.py"}}
    assert "Edit files" in denied(ps)
    ok = {"tool_name": "PowerShell", "tool_input": {"command": "Get-Process | Out-File out/001_x/p.txt"}}
    assert denied(ok) is None


@pytest.mark.slow
def test_a_queued_item_has_its_sed_and_temp_denied_and_its_commit_message_allowed(env):
    """The done-when check, end to end: the settings conductor writes for a launch run
    the real hook process on the item's calls."""
    calls = [("Bash", {"command": "sed -i 's/a/b/' README.md"}),
             ("Write", {"file_path": "C:\\Users\\m\\AppData\\Local\\Temp\\bench.json", "content": "{}"}),
             ("Bash", {"command": COMMIT}),
             ("Bash", {"command": "git checkout -- README.md"})]
    body = "\n".join(f"FAKE pre {t} {json.dumps(i)}" for t, i in calls)
    body += "\nFAKE helper-pre Bash " + json.dumps({"command": "git checkout -- README.md"})
    env.queue("001_hooks", body)
    env.tick_until(lambda: env.finished("code/001_hooks"))
    text = (env.control / "reports" / "code__001_hooks.md").read_text(encoding="utf-8")
    got = [ln.split(": ", 2)[2] for ln in text.splitlines() if ln.startswith("ASK RESULT: PRE ")]
    assert got[0].startswith("deny") and "sed -i" in got[0]
    assert got[1].startswith("deny") and "%TEMP%" in got[1]
    assert not got[2].startswith("deny") and not got[3].startswith("deny")   # commit message; lead's checkout
    assert got[4].startswith("deny") and "helper never" in got[4]


@pytest.mark.slow
def test_a_third_watch_tell_to_one_item_goes_to_matt_as_a_notify(env, monkeypatch):
    from conductor import daemon_watch
    monkeypatch.setattr(daemon_watch, "WATCH_BODY", "{reason}\nFAKE note ## Actions"
                        + "".join(f"\\n- tell: code/001_long post where you are ({n})" for n in (1, 2, 3)) + "\n")
    env.queue("001_long", env.hold())
    env.signal("cli-1.signal", "by: cli\nwatch: now")
    env.tick_until(lambda: env.finished("code/W1_watch"))
    assert [e.detail["text"] for e in env.events("tell")] == ["post where you are (1)", "post where you are (2)"]
    [held] = [e for e in env.events("notify") if e.detail.get("held_tell")]
    assert "already had 2 watch tells" in held.detail["text"] and held.detail["text"].endswith("(3)")


def write_note(env_like, repo, name, text):
    p = audits.note_path(env_like, repo, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_cleanup_notes_drop_handled_bullets_and_merge_repeats(tmp_path):
    from types import SimpleNamespace
    cfg = SimpleNamespace(report_path=lambda repo, name: tmp_path / "reports" / f"{repo}__{name}.md")
    old = write_note(cfg, "code", "001_old", "## Refactor\n- `docs/run.md` still names the removed --fast flag in its usage block\n")
    write_note(cfg, "code", "005_a", "## Poorly structured\n- daemon.py mixes signal parsing with the tick loop\n"
               "- docs/run.md still names the removed --fast flag in the usage block\n  (seen twice)\n")
    write_note(cfg, "code", "006_b", "## Poorly structured\n- the tick loop in daemon.py mixes signal parsing in\n"
               "- tests/fake_claude.py has no index of its FAKE verbs\n")
    text = audits.render_notes(cfg, ["code/005_a", "code/006_b"], {}, [old])
    assert "--fast" not in text and "(seen twice)" not in text
    assert "1 bullet left out: an earlier cleanup already handled" in text
    assert text.count("signal parsing") == 1 and "raised again by 006_b" in text
    assert "no index of its FAKE verbs" in text


def test_the_hook_prints_the_denial_and_its_settings_match_every_tool(tmp_path):
    out = json.loads(talk.pre_hook(bash("sed -i s/a/b/ x"), 60, [], tmp_path))["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny" and "sed -i" in out["permissionDecisionReason"]
    settings = json.loads(talk.hook_settings("py", tmp_path, "code/001_x", 60, [], readonly=True))
    [group] = settings["hooks"]["PreToolUse"]
    assert {"Bash", "Write", "Edit", "Agent", "Workflow"} <= set(group["matcher"].split("|"))
    args = group["hooks"][0]["args"]
    assert args[args.index("--control") + 1] == str(tmp_path) and "--readonly" in args
