"""Prompt 110: planner-notes fixes. A finished item's `out/keep/` outlives its worktree
(bounded) and the next item's prompt names it; a message carries the re-read-on-a-new-base
line; the preamble sends a reading that removes what an answer names to `design: true`."""

import os
from datetime import datetime

from conductor import executor, kept, talk
from conductor.items import Item


def render(env, name: str = "009_next") -> str:
    item = Item("code", name, env.control / "queue" / "code" / f"{name}.md", num=9, body="Do the work. " * 400)
    paths = executor.RunPaths.for_item(env.cfg, item)
    run = {"worktree": str(env.root / "wt"), "branch": f"conductor/{name}", "base": "0123456789abcdef"}
    return executor.build_prompt(env.d.cfg, item, paths, 1.0, datetime(2026, 11, 12, 17), run)


def test_out_keep_outlives_the_worktree_and_reaches_the_next_prompt(env):
    assert "Kept reads." not in render(env)
    env.queue("001_farm", "FAKE write out/keep/games.jsonl {\"g\": 1}\nFAKE write out/scratch.txt gone\nFAKE report done")
    env.tick_until(lambda: env.finished("code/001_farm"))
    where = kept.root(env.d.cfg, "code") / "001_farm"
    assert env.finished("code/001_farm").detail["status"] == "done"
    assert (where / "games.jsonl").read_text(encoding="utf-8").strip() == '{"g": 1}'
    assert not (where / "scratch.txt").exists()
    text = render(env)
    assert "Kept reads." in text and "001_farm, kept" in text and "games.jsonl" in text
    assert "out/keep/" in text                                  # the preamble names the folder


def test_kept_reads_are_bounded_by_count_and_size(env, monkeypatch):
    cfg = env.d.cfg
    for i in range(5):
        wt = env.root / "wts" / f"00{i}_x"
        (wt / "out" / "keep").mkdir(parents=True)
        (wt / "out" / "keep" / "a.bin").write_bytes(b"x" * 100)
        assert kept.save(cfg, "code", wt) is not None
        os.utime(kept.root(cfg, "code") / wt.name, (1000 + i, 1000 + i))
        kept.prune(cfg, "code")
    assert [p.name for p in kept.listed(cfg, "code")] == ["004_x", "003_x", "002_x"]
    monkeypatch.setattr(kept, "KEEP_BYTES", 150)
    assert kept.prune(cfg, "code") == ["003_x", "002_x"]       # the newest always stays
    assert kept.save(cfg, "code", env.root / "wts" / "none") is None


def test_a_message_carries_the_new_base_line_and_the_preamble_the_design_rule(env):
    text = talk.context_text([{"id": 3, "text": "Annihilate is out.", "sent": "2026-11-12T10:00:00-08:00"}])
    assert "changes your base" in text and "one re-read on the new base before it ships" in text
    assert "removes or reverses what it names" in render(env)
