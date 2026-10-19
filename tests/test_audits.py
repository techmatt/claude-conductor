import json
import subprocess
import sys

import pytest

from conductor import audits, items

ROOT_CLI = [sys.executable, "-m", "conductor"]


def prompt(env, name: str) -> str:
    return (env.control / "reports" / f"code__{name}.prompt.md").read_text(encoding="utf-8")


def notes_file(env, name: str):
    return env.control / "reports" / f"code__{name}.audit.md"


@pytest.mark.slow
def test_audit_item_keeps_notes_beside_its_report(env):
    env.queue("001_a", "FAKE audit slow: the build script\nFAKE commit a.txt a", audit="true")
    env.queue("002_plain", "FAKE commit b.txt b")
    env.tick_until(lambda: env.finished("code/002_plain"))
    text = prompt(env, "001_a")
    assert str(notes_file(env, "001_a").resolve()) in text and "Docs that would have saved time" in text
    assert "audit notes file named above are the only things" in text
    assert "audit notes" not in prompt(env, "002_plain").lower()
    assert notes_file(env, "001_a").read_text(encoding="utf-8") == "slow: the build script\n"
    fin = env.finished("code/001_a")
    assert fin.detail["audit"] is True and "reports/code__001_a.audit.md" in fin.paths
    assert "audit" not in env.finished("code/002_plain").detail
    inbox = (env.control / "inbox.md").read_text(encoding="utf-8")
    assert "1 unused audit note(s) (001_a)" in inbox
    out = subprocess.run([*ROOT_CLI, "audits", str(env.control)], capture_output=True, text=True,
                         encoding="utf-8").stdout
    assert "code: 1 unused audit note(s)" in out and "code/001_a" in out and "unused" in out


@pytest.mark.slow
def test_cleanup_gets_unused_notes_and_records_them(env):
    env.queue("001_a", "FAKE audit note one", audit="true")
    env.queue("002_b", "FAKE audit note two", audit="true")
    env.queue("003_clean", "Leave the CLI alone.", template="cleanup", ultracode="true")
    env.tick_until(lambda: env.finished("code/003_clean"))
    text = prompt(env, "003_clean")
    assert not env.d.log.last("hold", item="code/003_clean")       # it waited; it never staged
    assert not env.d.log.last("stage", item="code/003_clean")
    assert audits.template_text("cleanup").splitlines()[0].replace("{{repo}}", "code") in text
    assert text.index("Notes from code/001_a") < text.index("note one") < text.index("Notes from code/002_b")
    assert text.rstrip().endswith("Leave the CLI alone.") and "The planner adds" in text
    start = env.d.log.last("start", item="code/003_clean").detail
    assert start["template"] == "cleanup" and start["notes"] == ["code/001_a", "code/002_b"]
    assert start["template_version"] == audits.template_version("cleanup")
    [used] = env.events("audit_used")
    assert used.item == "code/003_clean" and used.detail["notes"] == ["code/001_a", "code/002_b"]
    assert env.finished("code/003_clean").detail["template"] == "cleanup"
    # Nothing left: the next cleanup holds until there are notes, and a cancel releases it.
    env.queue("004_again", "", template="cleanup")
    env.tick_until(lambda: env.events("hold"))
    assert "no unused audit notes" in env.events("hold")[-1].detail["reason"]
    env.signal("c.txt", "cancel: code/004")
    env.queue("005_c", "FAKE audit note three", audit="true")
    env.queue("006_clean", "", template="cleanup")
    env.tick_until(lambda: env.finished("code/006_clean"))
    assert env.d.log.last("start", item="code/006_clean").detail["notes"] == ["code/005_c"]
    assert "note one" not in prompt(env, "006_clean")


@pytest.mark.slow
def test_a_red_cleanup_uses_no_notes(env):
    env.queue("001_a", "FAKE audit note one", audit="true")
    env.queue("002_clean", "FAKE report red", template="cleanup", after_red="continue")
    env.queue("003_clean", "", template="cleanup")
    env.tick_until(lambda: env.finished("code/003_clean"))
    assert env.finished("code/002_clean").detail["status"] == "red"
    [used] = env.events("audit_used")
    assert used.item == "code/003_clean" and used.detail["notes"] == ["code/001_a"]


def test_general_audit_writes_and_uses_its_own_notes(env):
    env.queue("001_ga", "FAKE audit messy layout", template="general_audit")
    env.tick_until(lambda: env.finished("code/001_ga"))
    text = prompt(env, "001_ga")
    assert "General audit of `code`" in text and "running notes file at exactly" in text
    assert text.rstrip().endswith("The planner adds, for this item:\n\nFAKE audit messy layout")
    [used] = env.events("audit_used")
    assert used.detail["notes"] == ["code/001_ga"]
    assert "unused audit note" not in (env.control / "inbox.md").read_text(encoding="utf-8")


def test_disk_audit_is_handed_conductors_measurements(env):
    (env.control / "reports" / "code__000_old.log").write_bytes(b"x" * 5000)
    env.queue("001_disk", "Leave the fixtures alone.", template="disk_audit")
    env.tick_until(lambda: env.finished("code/001_disk"))
    text = prompt(env, "001_disk")
    assert "Disk space audit of `code`" in text and "{{" not in text
    assert "| Control folder | Size |" in text and "`code__000_old.log` 4.9 KB" in text
    assert f"Worktrees of `code`: `{env.worktree('x').parent}`" in text
    assert "`001_disk`: live (this item's own working directory)" in text
    assert "Main checkout:" in text and text.rstrip().endswith("Leave the fixtures alone.")
    assert not env.events("stage") and not env.events("audit_used")
    assert env.finished("code/001_disk").detail["template"] == "disk_audit"


def test_claude_md_cleanup_template(env):
    env.queue("001_md", "", template="claude_md_cleanup")
    env.tick_until(lambda: env.finished("code/001_md"))
    text = prompt(env, "001_md")
    assert "CLAUDE.md cleanup of `code`" in text and "running notes file" not in text
    assert "The planner adds" not in text and not env.events("audit_used")


def test_compact_log_drops_image_bytes_and_file_copies(tmp_path):
    from conductor import executor
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "A" * 600_000}}
    lines = [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "looking"}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": [image]}]},
         "tool_use_result": {"type": "image", "file": {"base64": "A" * 600_000, "type": "image/png"}}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "edited"}]},
         "tool_use_result": {"filePath": "x.py", "originalFile": "y" * 5000}},
        {"type": "result", "subtype": "success", "result": "done", "total_cost_usd": 1.5},
    ]
    log = tmp_path / "a.log"
    log.write_text("".join(json.dumps(x) + "\n" for x in lines) + "not json\n", encoding="utf-8")
    before, after = executor.compact_log(log)
    assert before > 1_200_000 and after < 2000
    out = log.read_text(encoding="utf-8").splitlines()
    assert json.loads(out[0]) == lines[0] and out[-1] == "not json"
    assert json.loads(out[1])["tool_use_result"]["file"] == {"base64": "", "type": "image/png",
                                                            executor.DROPPED: 600_000}
    assert json.loads(out[2])["tool_use_result"]["originalFile"] == ""
    assert executor.read_result(log) == lines[3]
    assert executor.compact_log(log) is None                    # under 1 MB now: left alone


def test_daemon_start_compacts_finished_items_logs(env):
    big = {"type": "user", "tool_use_result": {"file": {"base64": "A" * (2 << 20)}}}
    (env.control / "done" / "code").mkdir(parents=True, exist_ok=True)
    (env.control / "done" / "code" / "001_old.md").write_text("---\nrepo: code\n---\nx\n", encoding="utf-8")
    for name in ("001_old", "002_queued"):
        (env.control / "reports" / f"code__{name}.log").write_text(json.dumps(big) + "\n", encoding="utf-8")
    env.d.log.close()
    env.d = env.new_daemon()
    assert (env.control / "reports" / "code__001_old.log").stat().st_size < 1000
    assert (env.control / "reports" / "code__002_queued.log").stat().st_size > 2 << 20


def test_test_perf_template_and_bad_template(env, tmp_path):
    env.queue("001_tp", "", template="test_perf")
    env.tick_until(lambda: env.finished("code/001_tp"))
    text = prompt(env, "001_tp")
    assert "Test performance in `code`" in text and "running notes file" not in text
    assert not env.events("audit_used")
    bad = tmp_path / "002_x.md"
    bad.write_text("---\nrepo: code\ntemplate: nope\n---\n", encoding="utf-8")
    assert any("`template` must be one of" in e for e in items.parse(bad, "code").errors)
    assert "the prompt body is empty" in items.parse(bad, "code").errors
