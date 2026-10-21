import pytest

from conductor import audits, executor, inbox, items, resume, review, state
from conductor.app import actions


def send(env, text: str, name: str = "s.signal") -> None:
    env.signal(name, text)
    env.tick_until(lambda: not (env.control / "signals" / name).exists())


def prompt(env, name: str) -> str:
    return (env.control / "reports" / f"code__{name}.prompt.md").read_text(encoding="utf-8")


def report(env, name: str) -> str:
    return (env.control / "reports" / f"code__{name}.md").read_text(encoding="utf-8")


def parsed(tmp_path, front: str, body: str = "1. Why?") -> items.Item:
    p = tmp_path / "005_ask.md"
    p.write_text(f"---\nrepo: code\n{front}\n---\n{body}\n", encoding="utf-8")
    return items.parse(p, "code")


def finish_original(env, body: str = "REMEMBER chose the blue door\nFAKE commit a.txt a") -> str:
    env.queue("001_orig", body)
    env.tick_until(lambda: env.finished("code/001_orig"))
    return env.finished("code/001_orig").detail["session_id"]


def test_resume_front_matter(tmp_path):
    ok = parsed(tmp_path, "resume: code/001_orig")
    assert not ok.errors and ok.readonly and ok.resume_of == "code/001_orig"
    assert ok.meta()["resume_of"] == "code/001_orig" and "resume" not in ok.meta()
    assert ok.gist() == "questions for code/001_orig"
    audit = parsed(tmp_path, "resume: code/001_orig\ntemplate: self_audit", body="")
    assert not audit.errors and audit.template == "self_audit"
    for front, why in [("resume: 001_orig", "<repo>/<NNN_name>"), ("resume: code/orig", "<repo>/<NNN_name>"),
                       ("resume: other/001_x", "another repo"), ("resume: [code/001_x]", "<repo>/<NNN_name>"),
                       ("resume: code/001_orig\nreadonly: false", "always readonly"),
                       ("resume: code/001_orig\ntemplate: bug_hunt", "refused on a resume item"),
                       ("template: self_audit", "needs `resume:")]:
        assert any(why in e for e in parsed(tmp_path, front).errors), front
    assert "the prompt body is empty" in parsed(tmp_path, "resume: code/001_orig", body="").errors


def test_fork_command_line(env):
    argv = executor.command(env.cfg, "new-sid", None, fork_of="old-sid")
    i = argv.index("--resume")
    assert argv[i:i + 5] == ["--resume", "old-sid", "--fork-session", "--session-id", "new-sid"]
    plain = executor.command(env.cfg, "new-sid", None)
    assert "--fork-session" not in plain and plain[plain.index("--session-id") + 1] == "new-sid"


def test_resume_of_what_has_not_finished_is_invalid(env):
    env.queue("001_orig", env.hold())
    env.queue("002_ask", "1. Why?", resume="code/001_orig")
    env.queue("003_ghost", "1. Why?", resume="code/009_never")
    env.tick_until(lambda: len(env.events("invalid")) >= 2 and env.events("start"))
    errors = {ev.item: ev.detail["error"] for ev in env.events("invalid")}
    assert "only a finished item can be resumed" in errors["code/002_ask"]       # queued, then running
    assert "no finished item code/009_never" in errors["code/003_ghost"]
    view = state.scan(env.cfg, env.d.log)
    queued = {i.id: i for i in view.queued["code"]}
    assert "only a finished item" in queued["code/002_ask"].errors[0]
    env.release()
    env.tick_until(lambda: env.finished("code/001_orig"))


def test_a_launch_that_failed_has_no_session(env):
    fin = type("Fin", (), {"detail": {"status": "red", "session_id": "x", "reasons": ["launch failed: worktree: no"]}})
    view = state.View(finishes={"code/001_orig": fin})
    item = items.Item(repo="code", name="002_ask", path=None, resume_of="code/001_orig")
    assert "has no session to resume" in resume.problem(view, item)


@pytest.mark.slow
def test_ask_forks_the_original_session_and_answers_from_it(env, claude_config):
    sid = finish_original(env)
    original = next(claude_config.glob(f"projects/*/{sid}.jsonl"))
    before = original.read_text(encoding="utf-8")
    env.queue("002_later", "FAKE commit b.txt b")
    env.tick_until(lambda: env.finished("code/002_later"))
    env.queue("003_ask", "1. Which door did you choose?", resume="code/001_orig")
    env.tick_until(lambda: env.finished("code/003_ask"))
    fin = env.finished("code/003_ask")
    assert fin.detail["status"] == "done" and fin.detail["resume_of"] == "code/001_orig"
    assert fin.detail["session_id"] != sid and "merged" not in fin.detail
    start = env.d.log.last("start", item="code/003_ask")
    assert start.detail["resume_of"] == "code/001_orig" and start.detail["resume_session"] == sid
    text = report(env, "003_ask")
    assert f"FORKED FROM {sid}" in text and "FORK MEMORY: REMEMBER chose the blue door" in text
    assert original.read_text(encoding="utf-8") == before            # the original is untouched
    p = prompt(env, "003_ask")
    assert "This session is your own run of code/001_orig, resumed as a fork" in p
    assert "This item is readonly" in p and "fake: b.txt" in p and "fake: a.txt" not in p
    orig = executor.RunPaths.for_name(env.cfg, "code", "001_orig")
    extract = executor.RunPaths.for_name(env.cfg, "code", "003_ask").original
    for path in (orig.prompt, orig.report, orig.log, extract):
        assert f"    {path.resolve()}" in p
    assert str(orig.audit.resolve()) not in p                         # it kept no notes
    events = extract.read_text(encoding="utf-8")
    assert "`finish`" in events and "`start`" in events and "code/002_later" not in events
    assert not env.worktree("003_ask").exists() and not env.events("stage")


@pytest.mark.slow
def test_post_audit_writes_notes_for_the_next_cleanup(env):
    finish_original(env)
    env.queue("002_post", "FAKE audit What misled you: the README", resume="code/001_orig", template="self_audit")
    env.tick_until(lambda: env.finished("code/002_post"))
    p = prompt(env, "002_post")
    assert "Self-audit of your own run of code/001_orig in `code`" in p and "{{" not in p
    assert "What misled you" in p and "**About conductor**" in p
    fin = env.finished("code/002_post")
    assert fin.detail["audit"] is True and fin.detail["template"] == "self_audit"
    view = state.scan(env.cfg, env.d.log)
    notes = view.unused_notes(env.cfg, "code")
    assert [n.item for n in notes] == ["code/002_post"]
    text = audits.render_notes(env.cfg, ["code/002_post"], view.finishes)
    assert "a self-audit of code/001_orig" in text and "What misled you: the README" in text


@pytest.mark.slow
def test_a_missing_transcript_fails_the_launch(env, claude_config):
    sid = finish_original(env, "REMEMBER x")
    next(claude_config.glob(f"projects/*/{sid}.jsonl")).unlink()
    env.queue("002_ask", "1. Why?", resume="code/001_orig")
    env.tick_until(lambda: env.finished("code/002_ask"))
    fin = env.finished("code/002_ask")
    assert fin.detail["status"] == "red"
    assert any("has no transcript" in r for r in fin.detail["reasons"])


@pytest.mark.slow
def test_a_red_hold_still_lets_its_item_be_asked(env):
    env.queue("001_orig", "REMEMBER broke it\nFAKE report red")
    env.queue("002_next", "FAKE commit n.txt n")
    env.tick_until(lambda: env.finished("code/001_orig"))
    env.queue("003_ask", "1. Why red?", resume="code/001_orig")
    env.tick_until(lambda: env.finished("code/003_ask"))
    assert "FORK MEMORY: REMEMBER broke it" in report(env, "003_ask")
    assert not env.finished("code/002_next")                         # the hold stands for the rest
    assert "nothing has landed on it since your run." in prompt(env, "003_ask")


@pytest.mark.slow
def test_a_resume_items_invalid_reason_is_logged_again_when_it_changes(env):
    env.queue("001_orig", env.hold())
    env.queue("002_ask", "1. Why?", resume="code/001_orig")
    env.tick_until(lambda: env.events("start") and any(e.item == "code/002_ask" for e in env.events("invalid")))
    env.release()
    env.tick_until(lambda: env.finished("code/002_ask"))
    reasons = [e.detail["error"] for e in env.events("invalid") if e.item == "code/002_ask"]
    assert any("is running" in r for r in reasons)          # logged anew once 001 moved from queued to running


def test_reorder_refuses_to_strand_a_resume(env):
    env.queue("001_head", env.hold())
    env.queue("002_orig", "x")
    env.queue("003_other", "y")
    env.queue("004_ask", "1. Why?", resume="code/002_orig")
    env.tick_until(lambda: env.events("start"))
    send(env, "by: test\nreorder: code 003_other 002_orig")
    assert any("has resume 'code/002_orig'" in e.detail["error"] for e in env.events("invalid"))
    assert (env.control / "queue" / "code" / "002_orig.md").is_file()
    env.release()


@pytest.mark.slow
def test_a_reviewed_report_edited_later_is_unreviewed_again(env):
    env.queue("001_a", "FAKE report done")
    env.queue("002_ask", "1. Anything?", resume="code/001_a")
    env.tick_until(lambda: env.finished("code/002_ask"))
    view = state.scan(env.cfg, env.d.log)
    assert [e.item for e in view.unreviewed()] == ["code/001_a", "code/002_ask"]      # a resume arrives unreviewed
    assert "· resume of code/001_a" in inbox.unreviewed_lines(view, view.unreviewed())[1]
    send(env, "by: test\nreviewed: code/001_a")
    rev = env.d.log.last("reviewed", item="code/001_a")
    assert rev.detail["hashes"]["report"] and rev.detail["hashes"]["audit"] is None
    env.tick_until(lambda: True)
    assert not env.events("report_changed")                 # nothing moved: quiet
    actions.open_session(env.cfg, "code/001_a", "sid", spawn=lambda a, c: None, by="cli")
    env.tick_until(lambda: env.events("session_opened"))
    report = env.control / "reports" / "code__001_a.md"
    report.write_text(report.read_text(encoding="utf-8") + "\nA correction.\n", encoding="utf-8")
    env.tick_until(lambda: env.events("report_changed"))
    chg = env.events("report_changed")[-1]
    assert chg.detail["files"] == ["report"] and chg.detail["opened_by"] == "cli" and chg.detail["reviewed"] == rev.id
    view = state.scan(env.cfg, env.d.log)
    assert "code/001_a" in [e.item for e in view.unreviewed()] and not view.reviewed("code/001_a")
    line = next(ln for ln in inbox.unreviewed_lines(view, view.unreviewed()) if "code/001_a " in ln)
    assert "report changed since review" in line and "opened by cli" in line
    assert "report changed since review" in review.render(env.cfg, view, "r", "code/001_a").text
    (env.control / "reports" / "code__001_a.audit.md").write_text("late notes\n", encoding="utf-8")
    env.tick_until(lambda: len(env.events("report_changed")) == 2)
    assert env.events("report_changed")[-1].detail["files"] == ["report", "audit"]    # all since the review
    send(env, "by: test\nreviewed: code/001_a")           # reviewed again: quiet until the next change
    env.tick_until(lambda: True)
    view = state.scan(env.cfg, env.d.log)
    assert view.reviewed("code/001_a") and len(env.events("report_changed")) == 2
