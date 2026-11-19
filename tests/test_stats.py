"""`conductor stats` and `read(what="stats")` (docs/stats.md): a scoreboard read from the event log."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from mcp_client import call_planner  # noqa: E402

from conductor import cli, stats  # noqa: E402

TODAY = datetime.fromisoformat("2026-11-19T12:00:00-08:00")      # a Thursday: the week began Nov 16


def at(day: int, hour: float) -> str:
    return f"2026-11-{day:02d}T{int(hour):02d}:{int(hour % 1 * 60):02d}:00-08:00"


def run(log, item, day, start, end, model=None, **finish):
    log.append("start", item=item, happened=at(day, start), **({"model": model} if model else {}))
    log.append("finish", item=item, happened=at(day, end), **({"model": model} if model else {}), **finish)


def fill(log) -> None:
    run(log, "code/000_old", 10, 9, 10, cost_usd=1.0, status="done", friction="none")
    run(log, "code/001_a", 17, 8, 10, "claude-fable-5-2", cost_usd=2.0, status="done", friction="a tool fought back",
        slow="none")
    log.append("ask", item="code/001_a", happened=at(17, 9), question="code__001_a__1")
    log.append("default", item="code/001_a", happened=at(17, 9.5), question="code__001_a__1")
    run(log, "code/002_b", 17, 11, 12, "claude-opus-5-5", cost_usd=4.0, status="partial", friction="none")
    run(log, "code/003_c", 17, 12, 15, "claude-opus-5-5", cost_usd=6.0, status="done", friction=None,
        slow="the build took 20 min")
    run(log, "code/W1_watch", 17, 15, 15.5, "claude-opus-5-5", cost_usd=1.0, status="done", template="watch")
    run(log, "code/004_bug_hunt", 17, 16, 18, "claude-opus-5-5", cost_usd=3.0, status="done", template="bug_hunt")
    # Corrections: a slug naming a finished number, a resume, a finished dependency, a land.
    log.append("enqueue", item="code/005_fix_002", happened=at(18, 8))
    log.append("enqueue", item="code/006_more", happened=at(18, 8), resume_of="code/001_a")
    log.append("enqueue", item="code/007_x", happened=at(18, 8), depends_on=["003_c"])
    log.append("enqueue", item="code/008_plain", happened=at(18, 8))
    log.append("enqueue", item="code/009_post_audit_003", happened=at(18, 8), template="self_audit",
               resume_of="code/003_c")
    log.append("land", item="code/002_b", happened=at(18, 8), state="started", branch="conductor/002_b")
    # A planner session: what it wrote, conductor's own items left out.
    log.append("checkpoint", happened=at(18, 9), label="start", session="session.md", number=7, by="planner")
    log.append("request", happened=at(18, 9.1), request="20261118-0905-mcp01001", targets=["state.md"])
    log.append("request_failed", happened=at(18, 9.2), request="20261118-0910-x", error="no")
    log.append("cancel", happened=at(18, 9.3), paths=["signals/planner-20261118-091800-000001.signal"], by="planner")
    log.append("reviewed", happened=at(18, 9.3), paths=["signals/planner-20261118-091800-000001.signal"], by="planner")
    log.append("cancel", happened=at(18, 9.4), paths=["signals/planner-20261118-092400.signal"], by="planner")
    log.append("enqueue", item="code/010_y", happened=at(18, 9.5))
    log.append("watch", item="code/W2_watch", happened=at(18, 9.6), by="conductor")
    log.append("enqueue", item="code/W2_watch", happened=at(18, 9.6), template="watch")
    log.append("amended", item="code/010_y", happened=at(18, 9.7), by="planner", text="more")


def test_the_scoreboard_counts_items_spend_corrections_and_planner_writes(env):
    fill(env.d.log)
    text = stats.report(env.d.log, "test", 2, today=TODAY)
    lines = text.splitlines()
    assert lines[0] == "test: stats, 2 weeks from Monday Nov 9"
    rows = [ln for ln in lines if ln.startswith("  Nov 16  ") and ("fable" in ln or "opus" in ln)]
    fable = next(ln for ln in rows if "fable-5-2" in ln).split()
    assert fable[3:7] == ["1", "1/0/0", "100%", "0%"] and fable[7:9] == ["2.0h", "2.00"] and fable[9:] == ["1", "(1)"]
    opus = next(ln for ln in rows if "opus-5-5" in ln).split()      # watch and bug hunt are not features
    assert opus[3:7] == ["2", "1/1/0", "0%", "50%"] and opus[7:9] == ["2.0h", "5.00"]
    assert any(ln.startswith("  Nov 9   unrecorded   1") for ln in lines)
    spend = next(ln for ln in lines if ln.startswith("  Nov 16  $16: "))
    assert spend == "  Nov 16  $16: feature 75% ($12, 6.0h), bug_hunt 19% ($3, 2.0h), watch 6% ($1, 0.5h)"
    fixes = next(ln for ln in lines if ln.startswith("  Nov 16  ") and "queued" in ln)
    assert fixes == "  Nov 16  4 of 5 queued (depends 1, land 1, name 1, resume 1: 005 006 007 002)"
    session = next(ln for ln in lines if ln.startswith("  #7 ")).split()
    # requests 2 (1 refused), signals 2 (one file's two events counted once), queued 1, rewrites 0,
    # amends 1, answers 0, mcp 2 (the request and the countered signal file)
    assert session[1:] == ["2", "1", "2", "1", "0", "1", "0", "2"]
    assert len(text.encode("utf-8")) < 2500


def test_the_cli_and_the_planners_read_return_the_same_table(env, capsys):
    fill(env.d.log)
    assert cli.main(["stats", str(env.control), "--weeks", "3"]) == 0
    printed = capsys.readouterr().out.rstrip("\n")
    assert printed.startswith("test: stats, 3 weeks from Monday ")
    text, is_error = call_planner("read", {"project": "test", "what": "stats", "weeks": 3}, [env.control])
    assert not is_error and text == printed
    by_name, _ = call_planner("read", {"project": "test", "what": "stats", "name": "3"}, [env.control])
    assert by_name == printed
