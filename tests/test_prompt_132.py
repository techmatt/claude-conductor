"""132: conductor checks a `matt:` bar names a question the item raised or a choice its
report lists for Matt; a bare label still queues the post-audit."""

from __future__ import annotations

from conductor import executor, inbox, session

from test_lean import fin, retro

ITEM = "code/264_face"
QID = "code__264_face__2"
REPORT = """status: partial
blocks: none
carry: none
partial: {partial}

# 264: card face pass

## For Matt
- **Popular Uprising 5.4:** I asked (question 2) and took my default: keep the icons.
- **Retained Earnings 6.3:**
  - "at 4, gain 12 credits" (play change), or
  - "at 4, +3 credit income" (grammar change).
- Particle Accelerator 6.3: drop the megaproject half.

## Done-when
- **Positron Storage Ring:** not in the Matt list
"""
MATT = ("matt: Popular Uprising needs his play call, question 2; matt: Retained Earnings: "
        "play or grammar change; matt: Particle Accelerator: drop its megaproject half?")


def finish(env, name: str, partial: str, asked: bool = True):
    """A finish as the daemon writes it: `matt_on` from the report on disk and the item's asks."""
    item = f"code/{name}"
    if asked:
        env.d.log.append("ask", item=item, question=f"code__{name}__2", text="icons?")
    report = env.control / "reports" / f"{name}.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(REPORT.format(partial=partial), encoding="utf-8")
    on = session.finish_matt_on(env.d.log, item, partial, report)
    return fin(env, name, status="partial", partial=partial, matt_on=on)


def test_choices_under_a_matt_heading():
    assert session.matt_choices(REPORT) == ["Popular Uprising", "Retained Earnings", "Particle Accelerator"]


def test_each_part_names_what_it_waits_on():
    parts = [p.strip() for p in MATT.split(";")]
    on = session.matt_on(parts, REPORT, [QID])
    assert on[0] == f"questions/{QID}, choice: Popular Uprising"
    assert on[1:] == ["choice: Retained Earnings", "choice: Particle Accelerator"]
    assert session.matt_on([f"matt: see questions/{QID}"], "", [QID]) == [f"questions/{QID}"]
    assert session.matt_on(["matt: his call"], REPORT, [QID]) == [None]                 # a bare label
    assert session.matt_on(["Popular Uprising is not done"], REPORT, [QID]) == [None]   # a plain part


def test_264s_case_still_queues_no_retro(env):
    fin(env, "010_audit", template="self_audit", resume_of="code/009_x")
    finish(env, "264_face", MATT)
    assert "**due**" not in retro(env)
    env.d.cfg.auto_retros = True
    env.d.note_retro_skip(ITEM)
    line = inbox.describe(env.d.log.last("retro_skip", item=ITEM))
    assert f"(on questions/{QID}, choice: Popular Uprising)" in line and "(on choice: Retained Earnings)" in line


def test_a_bare_matt_label_queues_one(env):
    fin(env, "010_audit", template="self_audit", resume_of="code/009_x")
    finish(env, "265_b", "matt: his call on the faces", asked=False)
    assert "**due**: post-audit 265_b (partial" in retro(env)


def test_one_unbacked_part_among_backed_ones_queues_one(env):
    fin(env, "010_audit", template="self_audit", resume_of="code/009_x")
    finish(env, "266_c", MATT + "; matt: Positron Storage Ring needs repricing")      # listed, but not for Matt
    assert "**due**: post-audit 266_c (partial" in retro(env)


def test_a_finish_from_before_132_has_no_matt_on(env):
    fin(env, "010_audit", template="self_audit", resume_of="code/009_x")
    fin(env, "267_d", status="partial", partial="matt: Popular Uprising, question 2")
    assert "**due**: post-audit 267_d (partial" in retro(env)


def test_the_preamble_says_what_to_name():
    assert "`questions/<id>`" in executor.PREAMBLE and "`## For\nMatt` heading" in executor.PREAMBLE
