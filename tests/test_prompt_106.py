"""106: `start` launches a job that outlives its shell; a `state.md` past budget names the
lines to fold (request receipt and digest); a misnamed inbox prompt is named, not skipped."""

import io
import time
from pathlib import Path

import psutil

from conductor import ask_server, session
from conductor.util import read_text

from test_prompt_inbox import root, run, tool  # noqa: F401  (fixture and helpers)
from test_receipts import receipt

PARENT = "- **Card rules**: settled as of r62, holds"
SUBS = ["  - 2026-11-11: r63 talk moved the cap to 4", "  - Nov 11 (3pm): Matt prefers 3",
        "    a continuation of that sub-bullet", "  - #8373 asked again"]


def state_text(pad_kb: int = 0) -> str:
    return ("# State\n\n## Open\n\n" + "\n".join([PARENT, *SUBS]) + "\n- a plain line\n  - one sub only\n\n"
            "## Planner habits\n\n- keep it short\n" + ("x" * 99 + "\n") * (pad_kb * 10))


def test_fold_candidates_names_lines_with_sub_bullets_biggest_first():
    [line] = session.fold_candidates(state_text())
    assert line.startswith('*Open*: "**Card rules**: settled as of r62, holds" (3 sub-bullets, 3 dated, ')
    assert session.state_size(Path("."), ["docs/plan.md"]) == {}


def test_a_request_leaving_state_past_budget_warns_with_the_lines_to_fold(env):
    d = env.control / "requests" / "20261111-1000-state"
    d.mkdir(parents=True)
    (d / "request.md").write_text("---\ntargets: [state.md]\nmessage: \"state: design talk\"\n---\n", encoding="utf-8")
    (d / "state.md").write_text(state_text(pad_kb=26), encoding="utf-8")
    env.d.tick()
    meta, _ = receipt(env, "requests/20261111-1000-state")
    assert meta["outcome"] == "accepted" and "past its 25 KB budget: fold these lines" in meta["warning"]
    assert "**Card rules**" in meta["warning"]
    req = env.events("request")[-1].detail
    assert req["state_kb"] > 25 and len(req["fold"]) == 1


def test_start_runs_a_job_detached_that_outlives_the_server(tmp_path):
    server = ask_server.Server(tmp_path, "code/106_x", 10, None, out=io.StringIO(), workdir=tmp_path)
    assert server.start({"command": ""})[1]
    text, err = server.start({"command": "echo \"it's here\" 'and \"there\"' && sleep 1 && echo done > marker.txt", "log": "out/job.log"})
    assert not err and "pid " in text, text
    pid = int(text.split("pid ")[1].split(",")[0])
    p = psutil.Process(pid)
    assert p.ppid() not in {pp.pid for pp in psutil.Process().children(recursive=True)} | {psutil.Process().pid}
    end = time.time() + 30
    while not (tmp_path / "marker.txt").exists() and time.time() < end:
        time.sleep(0.2)
    assert (tmp_path / "marker.txt").exists()
    assert "it's here and \"there\"" in read_text(tmp_path / "out" / "job.log")


def test_a_misnamed_inbox_prompt_is_named_not_skipped(root, capsys):  # noqa: F811
    (root / "prompts" / "inbox" / "106_background_jobs.md").write_text("go\n", encoding="utf-8")
    code, out = run(root, "next", capsys=capsys)
    assert code == 1 and out.startswith("Nothing waiting in prompts/inbox/.\n")
    assert "Skipped, misnamed: inbox/106_background_jobs.md (rename it to " in out and "-<project>-background-jobs.md" in out
    _, out = run(root, "list", capsys=capsys)
    assert "Skipped, misnamed: inbox/106_background_jobs.md" in out
