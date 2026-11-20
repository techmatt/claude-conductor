"""Prompt 142: what the first real planner sessions on the MCP tools found (aicomplete 37,
fractals 7): a second amendment's receipt, staging questions answered after launch, staged
items gone stale under a moving main, calls that outlive the bridge, two chats on one
project, and the small ones."""

import time

import pytest

from conductor import inbox, planner_mcp, planner_read, session, state
from conductor.app import actions
from conductor.daemon_receipts import rel_for
from conductor.executor import RunPaths
from conductor.util import read_text, split_front_matter
from conftest import git


def receipt_of(env, path) -> dict:
    meta, _ = split_front_matter(read_text(env.control / rel_for(env.cfg.rel(path))))
    return meta


def test_a_second_amendment_to_a_running_item_gets_its_own_name_and_receipt(env):
    """aicomplete 292 at 2:39pm: the daemon deletes an amend file once taken, so the second was
    `.amend.md` again and its receipt path held the first's receipt."""
    env.queue("001_hold", env.hold())
    env.tick_until(lambda: env.events("start"))
    first = actions.amend(env.control, "code/001_hold", "One.", by="planner")
    env.tick_until(lambda: (env.control / rel_for(env.cfg.rel(first))).is_file())
    assert not first.exists()                   # taken: the name is free on disk again
    second = actions.amend(env.control, "code/001_hold", "Two.", by="planner")
    assert second.name == "001_hold.amend.2.md"
    env.tick_until(lambda: len(env.events("amended")) == 2)
    env.tick_until(lambda: (env.control / rel_for(env.cfg.rel(second))).is_file())
    a, b = env.events("amended")
    assert receipt_of(env, first)["events"][0] == a.id
    assert receipt_of(env, second)["events"][0] == b.id and b.detail["text"] == "Two."
    env.release()
    env.tick_until(lambda: env.finished("code/001_hold"))


@pytest.mark.slow
def test_questions_answered_after_launch_close_and_a_moved_main_is_said(env):
    """292: launched on its defaults with 291 merged since it staged; two amendments answered
    its questions while it ran, and the digests went on listing all six as open."""
    env.queue("001_long", env.hold())
    env.tick_until(lambda: env.events("start"))
    go2 = (env.root / "go2").as_posix()
    env.queue("002_next", "FAKE stage-question Does docs/rulings.md exist? (default: no, use guidelines)\n"
              f"FAKE wait-for {go2} 180\nFAKE report done\n", depends_on="[001_long]")
    env.tick_until(lambda: env.events("staged"))
    (env.repo / "docs").mkdir(exist_ok=True)
    (env.repo / "docs" / "rulings.md").write_text("# Rulings\n", encoding="utf-8", newline="\n")
    git(env.repo, "add", "docs/rulings.md")
    git(env.repo, "commit", "-qm", "rulings doc")
    env.release()
    env.tick_until(lambda: env.d.log.last("start", item="code/002_next"))
    prompt = RunPaths.for_name(env.d.cfg, "code", "002_next").prompt.read_text(encoding="utf-8")
    assert "Main moved since you staged (" in prompt and "re-check each question's premise" in prompt
    inbox_md = lambda: (env.control / "inbox.md").read_text(encoding="utf-8")     # noqa: E731
    env.tick_until(lambda: "on its defaults, running" in inbox_md())
    assert "landed between staging and launch: a premise" in inbox_md()
    actions.amend(env.control, "code/002_next", "docs/rulings.md exists: use it.", by="planner")
    env.tick_until(lambda: "answered by amendment #" in inbox_md())
    text = inbox_md()
    assert "> Does docs/rulings.md exist?" not in text and "its 1 question answered by amendment #" in text
    (env.root / "go2").touch()
    env.tick_until(lambda: env.finished("code/002_next"))
    env.tick_until(lambda: "finished, report unreviewed: its 1 question answered" in inbox_md())
    assert "> Does docs/rulings.md exist?" not in inbox.staged_questions(env.d.log, state.scan(env.cfg, env.d.log))


def test_status_and_receipts_count_the_planner_writes_since_the_session_start(env):
    log = env.d.log
    assert planner_read.session_writes(log) is None
    log.append("checkpoint", label="start", by="planner", session="session.md", number=37)
    log.append("request", request="x", message="state: split", targets=["state.md"], via="mcp")
    log.append("enqueue", item="code/004_more", template=None)
    log.append("enqueue", item="code/W1_watch", template="watch")          # conductor's own, not the planner's
    am = log.append("amended", item="code/004_more", by="planner", text="t")
    head, lines = planner_read.session_writes(log)
    assert head.startswith("planner writes since session #37 began ") and ": 3 (" in head
    assert [ln.split(" ", 1)[1] for ln in lines] == [
        "request: state: split (mcp)", "queued code/004_more (bridge)", f"amended code/004_more #{am.id} (bridge)"]
    assert "planner writes since session #37" in planner_read.project_status(env.cfg)


def test_a_call_answers_inside_the_bridges_80_seconds(env):
    """aicomplete and fractals, 2:17-2:25pm: calls timed out at the bridge with nothing said."""
    from conductor import mcp_launcher
    assert mcp_launcher.CALL_SECONDS < 80 and planner_mcp.CALL_BUDGET < mcp_launcher.CALL_SECONDS
    out = []
    srv = planner_mcp.PlannerServer([env.control], out=type("W", (), {"write": out.append, "flush": lambda s: None})())
    msg = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "_arrived": time.monotonic() - 60,
           "params": {"name": "signal", "arguments": {"project": "test", "lines": ["pause: code"]}}}
    srv.handle(msg)
    assert "not written: this call waited" in out[0] and not list((env.control / "signals").glob("*.signal"))


def test_recent_server_writes_list_receipts_and_untaken_marks(env):
    from conductor import daemon_receipts
    assert "none." in planner_read.recent_writes(env.cfg)
    daemon_receipts.mark(env.control, "queue/code/003_x.md")
    r = env.control / "receipts" / "signals" / "planner-1.md"
    r.parent.mkdir(parents=True, exist_ok=True)
    r.write_text("---\nwrite: signals/planner-1.signal\noutcome: accepted\nresult: paused code\nvia: mcp\n---\n",
                 encoding="utf-8")
    text = planner_read.recent_writes(env.cfg)
    assert "`queue/code/003_x.md`: written, no receipt yet" in text
    assert "`signals/planner-1.signal`: accepted: paused code" in text
    assert planner_read.read(env.cfg, "receipt")[0] == text


def test_a_section_names_with_or_without_its_hashes():
    text = "# s\n\n## Paged\n\n- a\n"
    assert planner_mcp.find_section(text, "## Paged", "state.md", 1)[1] == "Paged"
    assert planner_mcp.find_section(text, "*Paged*", "state.md", 1)[1] == "Paged"


def test_a_parked_receipt_names_the_item_that_carries_it_never_a_template(env):
    env.queue("001_hold", env.hold())
    env.tick_until(lambda: env.events("start"))
    env.queue("002_post_audit", "FAKE report done\n", template="post_audit")
    env.queue("003_work", "FAKE report done\n")
    p = env.control / "parked" / "code.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("- README: say hello\n", encoding="utf-8")
    env.tick_until(lambda: (env.control / "receipts" / "parked" / "code.md").is_file())
    result = split_front_matter(read_text(env.control / "receipts" / "parked" / "code.md"))[0]["result"]
    assert result == "1 parked edit(s) waiting for code: code/003_work carries them at its launch"
    env.release()


def test_the_digest_says_whether_the_rulings_doc_exists_while_state_holds_rulings(env):
    (env.control / "state.md").write_text("# s\n\n## Standing rulings\n\n- x\n", encoding="utf-8")
    view = state.scan(env.cfg, env.d.log)
    assert "- `docs/rulings.md`: missing (state.md still has *Standing rulings*)" in \
        session.maintenance(env.cfg, env.d.log, view, "code")
    (env.repo / "docs").mkdir(exist_ok=True)
    (env.repo / "docs" / "rulings.md").write_text("# r\n", encoding="utf-8")
    assert "- `docs/rulings.md`: exists (state.md still has *Standing rulings*)" in \
        session.maintenance(env.cfg, env.d.log, view, "code")
    (env.control / "state.md").write_text("# s\n\n## Now\n", encoding="utf-8")
    assert not [ln for ln in session.maintenance(env.cfg, env.d.log, view, "code") if "rulings" in ln]
