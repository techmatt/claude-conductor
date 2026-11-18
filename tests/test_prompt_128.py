"""128: planner notes fixes. A rewrite's receipt names what changed (or that nothing did);
`wait_for` returns when a subagent finishes; the preambles' wait, ETA and file-write lines;
`reviewed:` takes a comma list."""

import io
import json
import os
import sys
import threading
import time

import pytest

from conductor import executor, talk
from conductor.daemon_queue import changes
from conductor.daemon_receipts import rel_for
from conductor.util import read_text, split_front_matter
from test_lean import record
from test_needs_pick import picker, save
from test_prompt_84 import _server
from test_prompt_123 import LONG, SHORT, flat, render
from test_signals import bad, send


def receipt(env, name: str) -> dict:
    return split_front_matter(read_text(env.control / rel_for(f"queue/code/{name}.md")))[0]


# ---------------------------------------------------------------- rewrites
def test_changes_names_fields_and_body_lines():
    old = "---\nrepo: code\nmax_hours: 2\nlocks: [a]\n---\nDo it.\nThen this.\n"
    new = "---\nrepo: code\nmax_hours: 3\nneeds_pick: art_alts_183\n---\nDo it.\nThen that.\nAnd more.\n"
    assert changes(old, new) == ["max_hours: 2 → 3", "locks removed (was [\"a\"])",
                                 "needs_pick: art_alts_183 added", "body +2 −1 line(s)"]
    assert changes(old, old.replace("\n", "\r\n")) == ["whitespace only"]


@pytest.mark.slow
def test_a_rewrite_names_what_changed_carries_choices_and_an_identical_one_says_so(env):
    """Word Warriors 184: a rewrite adding `needs_pick` never reached the file, and the
    receipt said only "it runs the new text"; earlier rewrites left no receipt at all."""
    save(env, picker(env))                                  # art_alts saved before the rewrite
    env.queue("002_long", env.hold() + "\nFAKE report done", stage="false")
    env.tick_until(lambda: env.events("start"))
    path = env.queue("003_fonts", "FAKE report done", stage="false")
    env.tick_until(lambda: any(e.item == "code/003_fonts" for e in env.events("enqueue")))

    env.queue("003_fonts", "FAKE report done\n- promote every pick", stage="false", needs_pick="art_alts")
    env.tick_until(lambda: env.events("edited"))
    [ev] = env.events("edited")
    assert ev.detail["changes"] == ["needs_pick: art_alts added", "body +1 −0 line(s)"]
    assert ev.detail["choices"] == "art_alts"
    r = receipt(env, "003_fonts")
    assert r["outcome"] == "accepted"
    assert r["result"].startswith("rewritten: it runs the new text (needs_pick: art_alts added; body +1 −0 line(s)); "
                                  "it carries picker art_alts's saved choices")

    # The same bytes again (a stale copy sent): a receipt saying nothing changed.
    data = path.read_bytes()
    path.write_bytes(data)
    later = time.time() + 5
    os.utime(path, (later, later))
    env.tick_until(lambda: len(env.events("edited")) == 2)
    assert env.events("edited")[-1].detail["identical"] is True
    r = receipt(env, "003_fonts")
    assert r["result"] == "rewritten with identical bytes: nothing changed, and it runs the text it had"
    assert "was the edited copy sent?" in r["warning"]
    for _ in range(3):
        env.d.tick()
    assert len(env.events("edited")) == 2                   # once per write, not every tick

    env.release()
    env.tick_until(lambda: env.finished("code/003_fonts"))
    prompt = read_text(executor.RunPaths.for_name(env.d.cfg, "code", "003_fonts").prompt)
    assert "- promote every pick" in prompt and "Matt's choices from picker `art_alts`" in prompt


# ---------------------------------------------------------------- wait_for and subagents
def test_wait_for_returns_when_a_subagent_finishes(env):
    """helium-hustle 027, 030: a `wait_for` on a path nothing wrote held the item 12 and 10
    minutes after its reviewer had finished."""
    record(env, "001_a", phase="exec")
    talk.note_subagent(env.control, "code/001_a", {"agent_type": "Explore"})     # before the call: not counted
    server = _server(env)
    args = {"path": "never", "minutes": 0.03, "every": 0.05}
    assert server.wait_for(args, threading.Event())[0].startswith("Nothing yet")

    timer = threading.Timer(0.3, talk.note_subagent,
                            (env.control, "code/001_a", {"agent_id": "a1", "agent_type": "general-purpose"}))
    timer.start()
    text = server.wait_for(args | {"minutes": 1}, threading.Event())[0]
    timer.join()
    assert text.startswith("A subagent finished (general-purpose) after ")
    assert "never does not exist yet" in text


def test_the_subagent_stop_hook_is_wired_and_records(env, monkeypatch):
    settings = json.loads(talk.hook_settings("python", env.control, "code/001_a"))
    [entry] = settings["hooks"]["SubagentStop"]
    assert entry["hooks"][0]["args"][:3] == ["-m", "conductor.talk", "subagent"]
    event = {"hook_event_name": "SubagentStop", "agent_id": "x9", "agent_type": "Plan"}
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(event).encode())))
    assert talk.main(["subagent", "--control", str(env.control), "--item", "code/001_a"]) == 0
    [rec] = talk.subagents_done(env.control, "code/001_a", 0)
    assert (rec["agent_id"], rec["agent_type"]) == ("x9", "Plan")


# ---------------------------------------------------------------- preambles
def test_the_preambles_say_how_to_wait_estimate_and_write(env):
    full, light = flat(render(env, LONG)), flat(render(env, SHORT))
    assert "a message or a subagent finishing returns it early" in full
    assert ("For a background subagent, `wait_for` with `minutes` near its expected length, or check its output; "
            "never a path nothing will write.") in full
    assert "`minutes` near a subagent's length, never a path nothing writes" in light
    assert ("once you have a plan, an estimate by rate (`5:40pm (by rate)`); `(to the cap)` only when you expect "
            "to use the whole time limit") in full
    assert "Every file write goes through Write or Edit, and a script that writes files runs from a file" in full
    assert "Write files with Write/Edit only: hooks refuse heredoc, `-c`/`-e` and `sed -i` writes" in light


# ---------------------------------------------------------------- reviewed: a list
@pytest.mark.slow
def test_reviewed_takes_a_comma_list(env):
    env.queue("001_a", "FAKE report done")
    env.queue("002_b", "FAKE report partial")
    env.queue("003_c", "FAKE report done")
    env.tick_until(lambda: env.finished("code/003_c"))
    send(env, "by: planner\nreviewed: 001, code/009_x", "one_bad")     # one unknown: none marked
    assert bad(env, "one_bad") and not env.events("reviewed")
    send(env, "by: planner\nreviewed: 001")
    send(env, "by: planner\nreviewed: 001, code/002_b,003_c", "list")
    assert not bad(env, "list")
    assert [e.item for e in env.events("reviewed")] == ["code/001_a", "code/002_b", "code/003_c"]
    assert "already reviewed" in read_text(env.control / "receipts" / "signals" / "list.md")   # 001: a no-op (129)
