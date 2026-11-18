"""131: a `partial` whose every unmet bar waits on Matt's call queues no workflow retro."""

from __future__ import annotations

from conductor import executor, inbox, session

from test_lean import fin, retro
from test_preamble import SHORT, render

MATT = "matt: Popular Uprising, Retained Earnings, Particle Accelerator, Positron Storage Ring need his play calls"
ON = ["choice: Popular Uprising, choice: Retained Earnings"]     # what 132 checks it names


def test_the_header_reads_a_partial_line(tmp_path):
    report = tmp_path / "r.md"
    report.write_text("status: partial\nblocks: none\ncarry: none\nslow: none\nfriction: none\nwarnings: none\n"
                      f"partial: {MATT}\n\n# body\npartial: not this\n", encoding="utf-8")
    header, problem = executor.read_header(report)
    assert problem is None and header["partial"] == MATT


def test_the_full_preamble_asks_for_it(env):
    assert "partial: <each unmet bar and why>" in executor.PREAMBLE and "`matt: `" in executor.PREAMBLE
    assert "partial:" not in render(env, SHORT)     # the light one is at its word cap: the old rule


def test_a_partial_only_waiting_on_matt_makes_no_retro(env):
    fin(env, "010_audit", template="self_audit", resume_of="code/009_x")
    fin(env, "264_face", status="partial", partial=MATT, matt_on=ON)               # like aicomplete 264
    assert "**due**" not in retro(env)
    fin(env, "265_b", status="partial", partial="Matt: one call; cards:fit still fails on a face I did not get to",
        matt_on=["questions/x__1", None])
    assert "**due**: post-audit 265_b (partial" in retro(env)


def test_a_bare_or_missing_partial_line_still_makes_one(env):
    fin(env, "010_audit", template="self_audit", resume_of="code/009_x")
    fin(env, "011_a", status="partial")                                  # an older report: no line
    assert "**due**: post-audit 011_a (partial" in retro(env)


def test_a_stall_still_makes_one(env):
    fin(env, "010_audit", template="self_audit", resume_of="code/009_x")
    env.d.log.append("watch_flag", item="code/011_a", key="quiet:code/011_a", flag="quiet", text="quiet 40m")
    fin(env, "011_a", status="partial", partial=MATT, matt_on=ON)
    assert "**due**: post-audit 011_a (partial" in retro(env)


def test_the_skip_is_said_in_the_inbox(env):
    env.d.cfg.auto_retros = True                                         # the tests' toml turns it off
    fin(env, "264_face", status="partial", partial=MATT, matt_on=ON)
    env.d.note_retro_skip("code/264_face")
    ev = env.d.log.last("retro_skip", item="code/264_face")
    assert ev and "no workflow retro for code/264_face: its partial only waits on Matt" in inbox.describe(ev)
    fin(env, "265_b", status="partial", partial="the import half is not done")
    env.d.note_retro_skip("code/265_b")
    assert env.d.log.last("retro_skip", item="code/265_b") is None
    assert session.waits_on_matt(env.d.log, env.d.log.last("finish", item="code/264_face"))
