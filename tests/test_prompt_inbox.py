"""`tools/prompts.py`: conductor's own prompts are numbered when run, oldest first, with a
guard against a second run while one is open (CLAUDE.md, *Run next prompt*)."""

import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("prompts_tool", Path(__file__).resolve().parents[1] / "tools" / "prompts.py")
tool = importlib.util.module_from_spec(spec)
sys.modules["prompts_tool"] = tool            # its dataclass looks its module up while it loads
spec.loader.exec_module(tool)


@pytest.fixture
def root(tmp_path):
    (tmp_path / "prompts" / "inbox").mkdir(parents=True)
    (tmp_path / "prompts" / "92_workbench_init.md").write_text("old\n", encoding="utf-8")
    (tmp_path / "prompts" / "ledger.md").write_text(
        "4:10pm start 92_workbench_init\n4:11pm done 92_workbench_init → reports/x.md\n", encoding="utf-8")
    return tmp_path


def write(root, stamp, project, slug, body="go\n"):
    p = root / "prompts" / "inbox" / f"{stamp}-{project}-{slug}.md"
    p.write_text(body, encoding="utf-8")
    return p


def run(root, *argv, capsys):
    code = tool.main(list(argv), root=root)
    return code, capsys.readouterr().out


def test_two_stamped_prompts_run_in_write_order_with_consecutive_numbers(root, capsys):
    write(root, "20261109-170500", "aicomplete", "late-one", "second\n")     # written later, sorts first by name
    write(root, "20261109-163000", "fractals", "early-one", "first\n")
    code, out = run(root, "list", capsys=capsys)
    assert out.index("early_one") < out.index("late_one") and "fractals" in out
    code, out = run(root, "next", capsys=capsys)
    assert code == 0 and "Running 93_early_one" in out and "1 more waiting" in out
    assert (root / "prompts" / "93_early_one.md").read_text(encoding="utf-8") == "first\n"
    code, out = run(root, "next", capsys=capsys)                # the first is still open
    assert code == 2 and "93_early_one started at" in out and "run anyway" in out
    run(root, "done", "93_early_one", "reports/93_early_one_report.md", capsys=capsys)
    code, out = run(root, "next", capsys=capsys)
    assert code == 0 and "Running 94_late_one" in out and "0 more waiting"in out
    assert not list((root / "prompts" / "inbox").iterdir())
    lines = (root / "prompts" / "ledger.md").read_text(encoding="utf-8").splitlines()
    assert [ln.split(" ", 1)[1] for ln in lines[2:]] == [
        "start 93_early_one", "done 93_early_one → reports/93_early_one_report.md", "start 94_late_one"]


def test_a_slug_runs_out_of_order_and_run_anyway_is_recorded(root, capsys):
    write(root, "20261109-090000", "corelight", "first-written")
    write(root, "20261109-100000", "workbench", "wanted_now")
    code, out = run(root, "run", "wanted_now", capsys=capsys)
    assert code == 0 and "Running 93_wanted_now" in out and "1 more waiting" in out
    code, out = run(root, "run", "first-written", capsys=capsys)
    assert code == 2                                           # 93 is open
    code, out = run(root, "run", "20261109-090000-corelight-first-written.md", "--anyway", capsys=capsys)
    assert code == 0 and "Running 94_first_written" in out
    text = (root / "prompts" / "ledger.md").read_text(encoding="utf-8")
    assert "stale 93_wanted_now (run anyway)" in text and text.rstrip().endswith("start 94_first_written")
    run(root, "done", "94_first_written", "stopped", capsys=capsys)
    assert run(root, "run", "nope", capsys=capsys)[0] == 1


def test_numbers_skip_past_every_used_one_and_migration(root, capsys):
    (root / "prompts" / "97_written_numbered.md").write_text("x\n", encoding="utf-8")   # old style, never run
    old = time.time() - 3600
    os.utime(root / "prompts" / "97_written_numbered.md", (old, old))
    code, out = run(root, "migrate", capsys=capsys)
    assert "97_written_numbered.md -> inbox/" in out and "92_" not in out
    [w] = tool.inbox(root)
    assert w.project == "conductor" and w.slug == "written_numbered"
    assert tool.next_number(root) == 93                        # the migrated number is free again
    with open(root / "prompts" / "ledger.md", "a", encoding="utf-8") as f:
        f.write("1:00pm done 95b_x → reports/95b_x_report.md\n")     # a number only the ledger holds
    assert tool.next_number(root) == 96
    assert run(root, "migrate", capsys=capsys)[1].startswith("Nothing to migrate")
