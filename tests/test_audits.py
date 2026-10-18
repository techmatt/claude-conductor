import subprocess
import sys

from conductor import audits, items

ROOT_CLI = [sys.executable, "-m", "conductor"]


def prompt(env, name: str) -> str:
    return (env.control / "reports" / f"code__{name}.prompt.md").read_text(encoding="utf-8")


def notes_file(env, name: str):
    return env.control / "reports" / f"code__{name}.audit.md"


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
