"""An item's outline: remaining sections with finish times, posted through `progress`,
requested with `outline:`, shown in the inbox's Now line, `conductor status` and the app."""

import io
import json

import pytest

from conductor import ask_server, cli, talk
from conductor.app import model
from conductor.app.model import Project
from conductor.inbox import describe
from conductor.util import clock

OUTLINE = [{"title": "Read the code", "eta": "10:40am"}, {"title": "Re-cut images", "eta": "11:05am"},
           {"title": "Report", "eta": "11:20am"}]


def rows(*done: int) -> list[dict]:
    return [dict(r, done=i in done) for i, r in enumerate(OUTLINE)]


def now_line(env) -> str:
    env.d.write_inbox(force=True)
    now = (env.control / "inbox.md").read_text(encoding="utf-8").split("## Now")[1]
    return next(ln for ln in now.splitlines() if ln.startswith("- `code`"))


@pytest.mark.slow
def test_outline_posted_ticked_and_requested(env, capsys):
    go = env.root / "go"
    env.queue("001_out", f"FAKE progress {json.dumps({'outline': rows()})}\n"
                         f"FAKE progress {json.dumps({'note': 'read it', 'outline': rows(0)})}\n"
                         f"FAKE reply-with 30 {json.dumps({'outline': rows(0, 1)})}\n"
                         f"FAKE wait-for {go.as_posix()} 30")
    env.tick_until(lambda: len(env.events("progress")) == 2)
    first, ticked = env.events("progress")
    assert "note" not in first.detail and first.detail["outline"] == rows()
    assert (ticked.detail["note"], ticked.detail["outline"]) == ("read it", rows(0))
    # The Now line and `conductor status`: the note, then the outline's one-line form.
    line = now_line(env)
    assert "running 001_out — read it, at " in line
    assert line.endswith("; outline 1/3 · next: Re-cut images by 11:05am · finish ~11:20am "
                         f"(posted {clock(ticked.ts)})")
    assert cli.main(["status", str(env.control)]) == 0
    out = capsys.readouterr().out
    assert "progress " in out and "outline 1/3 · next: Re-cut images by 11:05am" in out
    # Requested: a tell of the fixed text; its reply is the new outline.
    assert cli.main(["outline", "001", "--control", str(env.control)]) == 0
    env.tick_until(lambda: len(env.events("progress")) == 3)
    [req] = env.events("tell")
    assert (req.detail["text"], req.detail["outline_request"], req.detail["by"]) == (talk.OUTLINE_REQUEST, True, "cli")
    assert describe(req) == f"outline request #{req.id} for code/001_out (by cli)"
    reply = env.events("progress")[2]
    assert reply.detail["reply_to"] == req.id and reply.detail["outline"] == rows(0, 1)
    # An outline-only line: no note on the Now line, the outline alone.
    assert now_line(env).endswith("running 001_out — outline 2/3 · next: Report by 11:20am · finish ~11:20am "
                                  f"(posted {clock(reply.ts)})")
    go.touch()
    env.tick_until(lambda: env.finished("code/001_out"))
    assert f"CONTEXT: MESSAGE #{req.id} from Matt via cli" in (
        env.control / "reports" / "code__001_out.md").read_text(encoding="utf-8")
    # The Conversation: outlines as compact turns, the request with its reply under it,
    # and the latest outline is the one to pin.
    project = Project(env.control)
    project.poll_events()
    events = project.trail("code/001_out").events
    turns = model.conversation(events)
    assert [t.title.split(" #")[0] for t in turns] == ["Started", "Outline", "Progress", "Outline request",
                                                       "Finished done"]
    assert turns[1].text == "outline 0/3 · next: Read the code by 10:40am · finish ~11:20am"
    assert turns[2].text == "read it\noutline 1/3 · next: Re-cut images by 11:05am · finish ~11:20am"
    asked = turns[3]
    assert asked.who == "matt" and asked.text == "" and asked.under[-1][0] == "reply"
    assert asked.under[-1][2] == "outline 2/3 · next: Report by 11:20am · finish ~11:20am"
    assert model.current_outline(events).id == reply.id
    assert model.activity_text(req) == f"outline request #{req.id} to 001_out (by cli)"
    project.close()


def test_outline_refused_for_an_item_not_running(env):
    env.queue("001_q", "FAKE report done", stage="false")
    env.signal("o.signal", "outline: 009")
    env.tick_until(lambda: not (env.control / "signals" / "o.signal").exists())
    assert (env.control / "signals" / "o.signal.bad").is_file()
    assert env.events("invalid")[-1].detail["error"].startswith("outline: no running item")


def test_progress_tool_takes_an_outline(env):
    server = ask_server.Server(env.control, "code/009_o", 10, None, out=io.StringIO())
    ok = server.progress({"outline": [{"title": "  Build   it ", "eta": "2pm"}, {"title": "Report", "eta": "3pm", "done": False}]})
    assert ok == ("Logged; carry on.", False)
    [line] = talk.read_progress(env.control, "code/009_o")
    assert "note" not in line and line["outline"] == [{"title": "Build it", "eta": "2pm", "done": False},
                                                      {"title": "Report", "eta": "3pm", "done": False}]
    for args, why in [({}, "`note` must be"),
                      ({"outline": []}, "non-empty list"),
                      ({"outline": [{"eta": "2pm"}]}, "row 1 needs a `title`"),
                      ({"outline": [{"title": "a", "eta": "2pm", "done": "yes"}]}, "`done` must be"),
                      ({"outline": [{"title": str(n), "eta": "2pm"} for n in range(13)]}, "at most 8")]:
        text, err = server.progress(args)
        assert err and why in text, args
    assert len(talk.read_progress(env.control, "code/009_o")) == 1
