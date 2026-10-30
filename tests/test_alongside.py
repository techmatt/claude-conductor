"""`alongside` items: planner-judged concurrency in one repo, the warnings conductor sends
both ways, exclusive templates, and tamper pinned on the item that made it."""

import pytest

from conductor import guard, inbox, items, state, talk

from conftest import git


def report(env, name: str) -> str:
    return (env.control / "reports" / f"code__{name}.md").read_text(encoding="utf-8")


def rec(env, name: str, **meta) -> dict:
    """A run record as `launch` writes it: executing in its worktree."""
    return {"item": f"code/{name}", "repo": "code", "name": name, "phase": "exec", "locks": [],
            "readonly": False, "alongside": None, "template": None, "paths": [],
            "worktree": str(env.worktree(name)), **meta}


def decide(env, running: list[dict]) -> tuple[state.Decision, list[str]]:
    """The repo's decision and the early starts, with `running` standing in for live items."""
    view = state.scan(env.cfg, env.d.log)
    view.running = {r["item"]: r for r in running}
    decisions = state.decide(env.cfg, view)
    return decisions["code"], [i.id for i in state.early_starts(env.cfg, view, decisions)]


def test_start_estimate_says_when_and_why(env):
    # aicomplete 026: "queued" alone hid that it waited behind 024 and a cleanup template.
    running = rec(env, "024_rules")
    env.queue("025_clean", "", template="general_cleanup")
    env.queue("026_frames", "x")
    env.queue("027_side", "x", alongside="card art only")
    view = state.scan(env.cfg, env.d.log)
    view.running = {running["item"]: running}
    est = {i.name: state.start_estimate(env.cfg, view, i) for i in view.queued["code"]}
    assert est["025_clean"] == "waits behind 024_rules (general_cleanup runs alone)"
    assert est["026_frames"] == "waits behind 024_rules (repo busy, no `alongside`) and 025_clean (template runs alone)"
    assert est["027_side"] == "waits behind 025_clean (template runs alone)"
    view.queued["code"] = [i for i in view.queued["code"] if i.name == "027_side"]
    assert state.start_estimate(env.cfg, view, view.queued["code"][0]) == "starts now, beside 024_rules (alongside)"
    view.running = {}
    assert state.start_estimate(env.cfg, view, view.queued["code"][0]) == "starts now"


# ---------------------------------------------------------------- the rules
def test_alongside_is_one_line_and_refused_on_a_template(env):
    good = items.parse(env.queue("001_a", "# Recorder fix\nbody", alongside="the recorder only"), "code")
    assert good.alongside == "the recorder only" and not good.errors
    assert good.meta()["alongside"] == "the recorder only" and good.gist() == "the recorder only"
    assert items.parse(env.queue("002_b", "# Voice lines\nbody"), "code").gist() == "Voice lines"
    assert items.parse(env.queue("003_c", "body"), "code").gist() == "003_c"
    bad = items.parse(env.queue("004_d", "x", alongside="''"), "code")
    assert bad.errors == ["`alongside` must be one line on what this item touches"]
    tpl = env.queue("005_t", "", template="general_audit", alongside="docs")
    assert "refused on a template item" in items.parse(tpl, "code").errors[0]


def test_template_with_alongside_is_held_at_enqueue(env):
    env.queue("001_tpl", "", template="bug_hunt", alongside="the menus")
    env.tick_until(lambda: env.events("hold"))
    assert "`alongside` is refused on a template item" in env.events("hold")[0].detail["reason"]
    assert not env.events("start")


def test_alongside_shares_both_ways_and_without_it_the_repo_waits(env):
    running = rec(env, "001_long")
    env.queue("002_plain", "x", stage="false")
    d, early = decide(env, [running])
    assert d.action == "wait" and d.reason == "repo busy: code/001_long running" and early == []
    env.queue("003_side", "x", alongside="the recorder")
    d, early = decide(env, [running])
    assert d.action == "wait" and early == ["code/003_side"]          # starts early, behind the head
    # Symmetric: anything may start beside a running alongside item.
    d, _ = decide(env, [rec(env, "001_long", alongside="voice lines")])
    assert d.action == "run" and d.item.id == "code/002_plain"


def test_alongside_still_waits_for_locks_and_dependencies(env):
    running = rec(env, "001_long", locks=["gpu"])
    env.queue("002_head", "x", stage="false")
    env.queue("003_lock", "x", alongside="a", locks="['gpu']")
    env.queue("004_dep", "x", alongside="b", depends_on="002")
    env.queue("005_free", "x", alongside="c")
    assert decide(env, [running])[1] == ["code/005_free"]


def test_exclusive_templates_run_alone_both_ways(env):
    # A running exclusive template: neither an alongside nor a readonly item starts beside it.
    perf = rec(env, "001_perf", template="test_perf")
    env.queue("002_side", "x", alongside="a", stage="false")
    env.queue("003_ro", "x", readonly="true")
    d, early = decide(env, [perf])
    assert d.action == "wait" and d.reason == "repo busy: code/001_perf running" and early == []
    # An exclusive head waits for every running item, alongside and readonly ones too, and
    # nothing behind it starts early meanwhile: it is never starved.
    for name in ("002_side", "003_ro"):
        (env.control / "queue" / "code" / f"{name}.md").unlink()
    env.queue("002_audit", "", template="general_audit", stage="false")
    env.queue("003_side", "x", alongside="a")
    env.queue("004_ro", "x", readonly="true")
    for other in (rec(env, "001_a", alongside="a"), rec(env, "001_ro", readonly=True)):
        d, early = decide(env, [other])
        assert d.action == "wait" and d.item.id == "code/002_audit" and early == []
    # bug_hunt is no exclusive template: an alongside item may start beside it.
    assert state.can_share(env.cfg, items.parse(env.control / "queue" / "code" / "003_side.md", "code"),
                           rec(env, "001_hunt", template="bug_hunt"))


def test_without_worktrees_alongside_waits(env):
    env.configure('[repos.code]\npath = "../code"\nworktrees = false\n')
    env.queue("002_side", "x", alongside="a", stage="false")
    d, early = decide(env, [rec(env, "001_long", worktree=None)])
    assert d.action == "wait" and early == []


# ---------------------------------------------------------------- end to end
@pytest.mark.slow
def test_alongside_item_starts_beside_a_running_one_and_both_are_warned(env):
    env.queue("001_voice", '# Voice lines\nFAKE until-message 30\nFAKE commit b.txt b', stage="false")
    env.tick_until(lambda: env.events("start"))
    env.queue("002_recorder", "FAKE commit a.txt a", alongside="the recorder's save path only")
    env.tick_until(lambda: env.finished("code/001_voice") and env.finished("code/002_recorder"), timeout=60)
    start = env.d.log.last("start", item="code/002_recorder")
    assert start.detail["alongside"] == "the recorder's save path only"
    assert start.detail["beside"] == ["code/001_voice"]
    assert start.id < env.finished("code/001_voice").id                    # ran beside it
    prompt = (env.control / "reports" / "code__002_recorder.prompt.md").read_text(encoding="utf-8")
    assert "Beside you." in prompt and "- code/001_voice: Voice lines" in prompt
    [tell] = [e for e in env.events("tell") if e.detail.get("beside")]
    assert tell.item == "code/001_voice" and tell.detail["by"] == "conductor"
    assert tell.detail["text"].startswith("code/002_recorder started beside you: the recorder's save path only.")
    assert "code/002_recorder started beside you" in report(env, "001_voice")
    assert all(e.detail["status"] == "done" for e in env.events("finish"))
    assert len(env.events("merge")) == 2 and (env.repo / "a.txt").is_file() and (env.repo / "b.txt").is_file()
    assert [e.detail["commits"] for e in env.events("merge")] == [1, 1]   # the neighbour's isn't counted
    assert "alongside: the recorder's save path only; beside code/001_voice" in inbox.describe(start)


@pytest.mark.slow
def test_a_neighbour_joining_later_is_recorded_and_never_told_twice(env):
    # 003 (alongside) starts beside running 001 and staged 002; its paragraph names both.
    # When 002 resumes beside it, 003's record gains 002, and 003 hears no tell about it.
    go2 = env.root / "go2"
    env.queue("001_c", f"# Long\n{env.hold()}\nFAKE commit c.txt c", stage="false")
    env.tick_until(lambda: env.events("start"))
    env.queue("002_b", "# Bee\nFAKE commit b.txt b")
    env.tick_until(lambda: env.events("staged"))
    env.queue("003_a", f"FAKE wait-for {go2.as_posix()} 60\nFAKE commit a.txt a", alongside="the recorder")
    env.tick_until(lambda: env.d.log.last("start", item="code/003_a"))
    prompt = (env.control / "reports" / "code__003_a.prompt.md").read_text(encoding="utf-8")
    assert "- code/001_c: Long" in prompt and "- code/002_b: Bee" in prompt
    env.release()
    env.tick_until(lambda: env.d.log.last("start", item="code/002_b"))
    a = state.read_run(env.cfg.path("running", "code", "003_a.run.json"))
    assert a["beside"] == ["code/001_c", "code/002_b"] and a["warned"] == ["code/001_c", "code/002_b"]
    assert [(e.item, e.detail["beside"]) for e in env.events("tell") if e.detail.get("beside")] == [
        ("code/001_c", "code/003_a")]
    assert "003_a (alongside, beside code/001_c, code/002_b)" in inbox.render(
        env.cfg, env.d.log, state.scan(env.cfg, env.d.log))
    go2.touch()
    env.tick_until(lambda: env.finished("code/003_a") and env.finished("code/002_b"), timeout=60)


@pytest.mark.slow
def test_tamper_beside_another_item_is_pinned_on_the_one_that_made_it(env):
    state_md = env.control / "state.md"
    env.queue("001_long", env.hold(), stage="false")
    env.tick_until(lambda: env.events("start"))
    env.queue("002_bad", f"FAKE write {state_md.as_posix()} hacked", alongside="docs only")
    env.tick_until(lambda: env.finished("code/002_bad"))
    assert state_md.read_text(encoding="utf-8") == "# state\n"
    [tamper] = env.events("tamper")
    assert tamper.item == "code/002_bad" and tamper.detail["attributed"] == ["code/002_bad"]
    assert tamper.detail["concurrent"] == ["code/001_long", "code/002_bad"]
    assert env.finished("code/002_bad").detail["status"] == "red"
    env.release()
    env.tick_until(lambda: env.finished("code/001_long"))
    assert env.finished("code/001_long").detail["status"] == "done"          # spared
    since = env.d.log.last("start", item="code/001_long").ts
    assert talk.touched_since(env.control, "code/002_bad", since) == ["state.md"]
    assert talk.touched_since(env.control, "code/001_long", since) == []


@pytest.mark.slow
def test_tamper_no_tool_call_named_is_on_every_running_item(env):
    # 001 wrote a control-folder file, but not the one that changed: no evidence against it.
    # The change came from somewhere that never named it (here: the test itself).
    state_md = env.control / "state.md"
    env.queue("001_side", f"FAKE write {(env.control / 'scratch.txt').as_posix()} x\n{env.hold()}", stage="false")
    env.queue("002_other", env.hold(), alongside="docs only")
    env.tick_until(lambda: len(env.events("start")) == 2
                   and talk.touched_since(env.control, "code/001_side", "2000-01-01T00:00:00") == ["scratch.txt"])
    state_md.write_text("hacked\n", encoding="utf-8")
    env.release()
    env.tick_until(lambda: env.finished("code/001_side") and env.finished("code/002_other"))
    [tamper] = env.events("tamper")
    assert tamper.item is None and tamper.detail["attributed"] == [] and len(tamper.detail["concurrent"]) == 2
    assert all(e.detail["status"] == "red" for e in env.events("finish"))


def test_a_touch_names_the_paths_a_writing_tool_names(env):
    c = env.control.resolve()
    item = "code/001_x"
    assert not talk.note_touch(c, item, {"tool_name": "Read", "tool_input": {"file_path": str(c / "state.md")}})
    assert not talk.note_touch(c, item, {"tool_name": "Write", "tool_input": {"file_path": str(env.repo / "a")}})
    # Its own report (and anything under reports/ or .conductor/) is never evidence.
    assert not talk.note_touch(c, item, {"tool_name": "Write", "tool_input": {
        "file_path": str(c / "reports" / "code__001_x.md"), "content": f"see {c / 'state.md'}"}})
    assert talk.note_touch(c, item, {"tool_name": "Write", "tool_input": {"file_path": str(c / "state.md")}}) == ["state.md"]
    drive, rest = c.as_posix().split(":", 1)
    bash = {"tool_name": "Bash", "tool_input": {"command": f"cd /{drive.lower()}{rest} && echo x > /{drive}{rest}/Queue/a.md"}}
    assert talk.note_touch(c, "code/002_y", bash) == ["", "queue/a.md"]
    assert talk.touched_since(c, item, "2000-01-01T00:00:00") == ["state.md"]
    assert talk.touched_since(c, "code/003_z", "2000-01-01T00:00:00") == []
    git(env.control, "status")          # the touch log is under .conductor/, never tracked
    # A touch covers a change of the file it named, or of a file in a folder it named; a
    # bare folder mention covers only a change with no path (a commit, a ref).
    assert guard.named(["state.md"], ["state.md: modified"]) and guard.named(["queue"], ["queue/a.md: deleted"])
    assert not guard.named(["state.md"], ["planner.md: modified"]) and not guard.named(["", "x.md"], ["state.md: modified"])
    assert guard.named([""], ["new commit: HEAD 1234 (was 5678)"]) and not guard.named(["state.md"], ["ref refs/x changed"])
