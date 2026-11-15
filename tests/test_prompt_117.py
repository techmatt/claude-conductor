"""Prompt 117: planner-notes fixes. A kept `out/keep/<item>/` is not doubled and the preamble
names the kept path; a `partial` Matt told to stop makes no retro; init and session.md name a
control folder with no remote; the preamble's speed-baseline, status, fan-out and seed lines."""

from datetime import datetime

from conductor import executor, git, kept, scaffold
from conductor.items import Item

from test_lean import fin, retro
from test_preamble import SHORT, render


def flat(text: str) -> str:
    return " ".join(text.split())


def test_a_keep_folder_named_for_the_item_is_not_doubled(env):
    cfg = env.d.cfg
    wt = env.root / "wts" / "228_read"
    (wt / "out" / "keep" / "228_read").mkdir(parents=True)
    (wt / "out" / "keep" / "228_read" / "games.jsonl").write_text("{}\n", encoding="utf-8")
    dest = kept.save(cfg, "code", wt)
    assert dest == kept.root(cfg, "code") / "228_read" and (dest / "games.jsonl").exists()
    wt2 = env.root / "wts" / "229_two"                       # anything else stays as written
    (wt2 / "out" / "keep" / "other").mkdir(parents=True)
    (wt2 / "out" / "keep" / "other" / "a.txt").write_text("a\n", encoding="utf-8")
    assert (kept.save(cfg, "code", wt2) / "other" / "a.txt").exists()


def test_the_preamble_names_where_its_keep_goes(env):
    item = Item("code", "009_next", env.control / "queue" / "code" / "009_next.md", num=9, body="Do it. " * 400)
    paths = executor.RunPaths.for_item(env.cfg, item)
    run = {"worktree": str(env.root / "wt" / "009_next"), "branch": "conductor/009_next", "base": "0" * 16}
    text = flat(executor.build_prompt(env.d.cfg, item, paths, 1.0, datetime(2026, 11, 14, 17), run))
    where = (kept.root(env.d.cfg, "code").resolve() / "009_next").as_posix()
    assert f"straight in `out/keep/` (no item subfolder); they are kept at `{where}`" in text
    main = flat(executor.build_prompt(env.d.cfg, item, paths, 1.0, datetime(2026, 11, 14, 17), {}))
    assert (env.d.cfg.repos["code"] / "out" / "keep").resolve().as_posix() in main


def test_a_partial_told_to_stop_makes_no_retro(env):
    fin(env, "010_audit", template="self_audit", resume_of="code/009_x")
    env.d.log.append("tell", item="code/226_base", text="Stopped by Matt for a base change: write up and finish.", by="planner")
    fin(env, "226_base", status="partial")
    assert "**due**" not in retro(env)
    env.d.log.append("tell", item="code/227_b", text="Stop the load test now.", by="conductor")   # conductor's own
    fin(env, "227_b", status="partial")
    assert "**due**: post-audit 227_b (partial" in retro(env)


def test_init_names_the_control_folder_with_no_remote(tmp_path):
    # session.md's line until it has one: test_prompt_91's session test.
    _, notes = scaffold.init(tmp_path / "proj-control", "proj", [f"code={tmp_path / 'code'}"])
    assert notes[-1] == git.CONTROL_NO_REMOTE.format(name="proj-control") + " (session.md says so until it has one)"
    assert "gh repo create proj-control --private --source . --push" in notes[-1]


def test_preamble_lines_for_baselines_status_fanout_and_seeds(env):
    full = flat(render(env, SHORT * 3 + " Tune to the target band with fresh seeds."))
    assert "re-base a speed baseline (`speed.py --update` and the like) only when the last" in full
    assert "or a hard bar missed, even within ~1 SE" in full
    assert "Any fan-out (a Workflow, a diff review) runs at most 10 agents in all" in full
    assert "runs its independent diff review before spending fresh seeds" in full
    assert "`seeds.py --take` or its like" in full
    assert len(render(env, SHORT).split()) <= 430


def test_118_read_only_helpers_return_findings_inline(env):
    full = flat(render(env, "x " * 400, max_hours=2.0))
    assert "a read-only one (Explore, Plan) can't write, so its findings come back inline" in full
    assert len(render(env, SHORT).split()) <= 430
