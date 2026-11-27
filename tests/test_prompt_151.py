"""Prompt 151: a per-repo results folder, `wait_for` ending only on the subagent it names, doc
budgets in the prompt, a run log indexed by step, refused edit forms named up front, the
leftover-hours ask, and a parked play-changing edit's sim red."""

import gzip
import json
import threading

import pytest

from conductor import config, docbudget, parked, resume, runlog, talk, waitfor
from conductor.items import Item

from conftest import git
from test_lean import record
from test_preamble import render
from test_prompt_84 import _server
from test_prompt_123 import LONG


def flat(text: str) -> str:
    return " ".join(text.split())


# ---------------------------------------------------------------- results folder
def test_the_results_folder_is_per_repo_and_a_git_ignored_one_keeps_papers(env):
    assert "(`experiments/results/001_a/`), committed (unless the repo's CLAUDE.md files them elsewhere)" in flat(render(env, LONG))
    env.configure('[repos.code]\npath = "../code"\nresults = "notes/runs"\n')
    env.cfg = env.d.cfg
    assert "(`notes/runs/001_a/`), committed" in flat(render(env, LONG))
    (env.repo / ".gitignore").write_text("notes/\n", encoding="utf-8")       # fractal-wallpapers ignores experiments/
    full = flat(render(env, LONG))
    assert "committed" not in full.split("The box.")[1].split("Conductor's hooks also")[0]
    assert "go in `out/keep/001_a/` (this repo commits no results folder: the report carries what matters)" in full
    toml = env.control / "conductor.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace('"notes/runs"', '"out/keep"'), encoding="utf-8", newline="\n")
    env.cfg = config.load(env.control)
    (env.repo / ".gitignore").unlink()
    assert "this repo commits no results folder" in flat(render(env, LONG))


def test_a_results_folder_outside_the_repo_is_refused(env):
    for bad in ('"../elsewhere"', '"/abs"', "3", '"/"'):
        with pytest.raises(config.ConfigError, match="results must be a folder inside the repo"):
            _load_with(env, bad)


def _load_with(env, value: str):
    toml = env.control / "conductor.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace('[repos]\ncode = "../code"\n',
                                                             f'[repos.code]\npath = "../code"\nresults = {value}\n'),
                    encoding="utf-8", newline="\n")
    return config.load(env.control)


# ---------------------------------------------------------------- wait_for and subagents
def test_wait_for_ends_only_on_the_subagent_it_names(env):
    """fractals 086, 022: every inner workflow agent finishing returned the wait, ~10 extra calls per fan-out."""
    record(env, "001_a", phase="exec")
    server = _server(env)
    for i in range(3):
        talk.note_subagent(env.control, "code/001_a", {"agent_id": f"wf{i}", "agent_type": "general-purpose"})
    text = server.wait_for({"path": "never", "minutes": 0.02, "every": 0.05}, threading.Event())[0]
    assert text.startswith("Nothing yet")             # those finished before the call: not counted

    timer = threading.Timer(0.2, talk.note_subagent, (env.control, "code/001_a", {"agent_id": "inner1", "agent_type": "x"}))
    timer.start()
    text = server.wait_for({"path": "never", "minutes": 0.03, "every": 0.05}, threading.Event())[0]
    timer.join()
    assert text.startswith("Nothing yet") and "Meanwhile 1 subagent(s) finished: x inner1." in text

    timers = [threading.Timer(0.1, talk.note_subagent, (env.control, "code/001_a", {"agent_id": "inner2", "agent_type": "x"})),
              threading.Timer(0.3, talk.note_subagent, (env.control, "code/001_a", {"agent_id": "a7f3c2e9b1", "agent_type": "Explore"}))]
    for t in timers:
        t.start()
    text = server.wait_for({"agent": "a7f3c2", "minutes": 1, "every": 0.05}, threading.Event())[0]
    for t in timers:
        t.join()
    assert text.startswith("Subagent a7f3c2e9b1 (Explore) finished after ")
    assert "subagent a7f3c2 has not finished" in text


def test_agent_names_match_by_id_prefix_or_any():
    assert waitfor.named("any", {"agent_id": "x"}) and waitfor.named("abc", {"agent_id": "abc"})
    assert waitfor.named("abcdef", {"agent_id": "abcdef123"}) and not waitfor.named("abc", {"agent_id": "abcdef"})
    assert not waitfor.named("", {"agent_id": "abc"}) and not waitfor.named("abc", {})
    assert waitfor.target({"agent": " "}, None)[1] == "`agent` must be a subagent's id, or \"any\""
    assert isinstance(waitfor.target({"agent": "any"}, None)[0], waitfor.AgentTarget)


# ---------------------------------------------------------------- doc budgets
def test_an_item_naming_a_trim_or_an_over_budget_doc_sees_the_budget(env):
    (env.repo / "docs").mkdir(exist_ok=True)
    (env.repo / "docs" / "big.md").write_text("x" * 16_000, encoding="utf-8")
    (env.repo / "docs" / "small.md").write_text("y" * 100, encoding="utf-8")
    git(env.repo, "add", "docs")
    git(env.repo, "commit", "-qm", "docs")
    assert docbudget.section(env.cfg, "code", "Add a feature.") == ""
    assert docbudget.section(env.cfg, "code", "Spend the time budget well.") == ""
    for body in ("Trim the docs.", "Tidy docs/big.md.", "Keep README under 12 KB."):
        text = flat(docbudget.section(env.cfg, "code", body))
        assert "held to 15 KB, 1 KB = 1,024 bytes: at most 15,360 bytes (`wc -c <file>`" in text, body
        assert "Over it on main now: `docs/big.md` 16,000." in text
    assert "## Doc budget" in executor_text(env, LONG + " Trim docs/big.md.")
    assert "## Doc budget" not in executor_text(env, LONG + " Trim docs/big.md.", readonly=True)


def executor_text(env, body: str, **meta) -> str:
    from datetime import datetime
    from conductor import executor
    item = Item("code", "001_a", env.control / "queue" / "code" / "001_a.md", num=1, body=body, **meta)
    return executor.build_prompt(env.cfg, item, executor.RunPaths.for_item(env.cfg, item), 1.0, datetime(2026, 10, 25, 17), {})


# ---------------------------------------------------------------- run log index
def _stream(path):
    rows = [
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "mcp__conductor__progress",
                                                       "input": {"note": "starting the sweep"}}]}, "parent_tool_use_id": None},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1"}]},
         "timestamp": "2026-11-26T01:40:00.000Z"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"},
                                                      {"type": "tool_use", "id": "t2", "name": "Agent",
                                                       "input": {"description": "review the diff", "prompt": "..."}}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "s1", "name": "Read",
                                                       "input": {"file_path": "a.py"}}]}, "parent_tool_use_id": "t2"},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t3", "name": "Bash",
                                                       "input": {"command": "uv run pytest\n  -q " + "x" * 300}}]}},
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_a_finished_log_gets_a_step_index_readable_once_gzipped(tmp_path):
    log = tmp_path / "code__080_style.log"
    _stream(log)
    out = runlog.index_log(log)
    assert out == tmp_path / "code__080_style.steps.md"
    text = out.read_text(encoding="utf-8")
    assert "# Steps of code__080_style" in text
    lines = [ln for ln in text.splitlines() if ln[:1].isdigit() or ln.startswith("**")]
    assert lines[0].startswith("**1. ") and "L2 progress: starting the sweep**" in lines[0]
    assert lines[1] == "2. ? L4 Agent: review the diff (+1 inner)"
    assert lines[2].startswith("3. ? L6 Bash: uv run pytest -q xxx") and lines[2].endswith("…") and len(lines) == 3
    assert runlog.index_log(log) is None               # once
    out.unlink()
    with open(log, "rb") as src, gzip.open(runlog.gz_path(log), "wb") as dst:
        dst.write(src.read())
    log.unlink()
    assert runlog.index_log(log) == out                # from the gzip: an old log, read by a resume


def test_a_resume_item_reads_the_step_index(env):
    from conductor import executor
    paths = executor.RunPaths.for_name(env.cfg, "code", "080_style")
    paths.log.parent.mkdir(parents=True, exist_ok=True)
    _stream(paths.log)
    extract = env.control / "x.md"
    assert runlog.steps_path(paths.log) in resume.readable(env.cfg, "code/080_style", extract)


# ---------------------------------------------------------------- preamble lines
def test_the_leftover_rule_asks_before_a_partial_on_reads_out_of_scope(env):
    assert ("Only unmet bar needing reads out of scope: `ask` (`design: true`) before finishing partial."
            in flat(render(env, LONG)))


def test_a_parked_play_change_says_whether_a_sim_red_was_patched_or_reported():
    text = flat(parked.section([{"n": 1, "text": "raise the drill cost to 40"}]))
    assert "may turn a sim or loadout check red: patch the check when the red is the edit's intended effect" in text
    assert "its `parked P<n>:` line says which." in text
