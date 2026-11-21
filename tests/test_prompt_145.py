"""Prompt 145: a staged item's questions stop being listed as open rulings once it has
finished: aicomplete 305's six were quoted again at 8:55pm beside its report."""

from conductor import inbox, review, state

QS = ["Oracle in the ladder? (default: yes)", "Which seed? (default: 7)"]


def launched_on_defaults(env):
    log = env.d.log
    log.append("enqueue", item="code/002_next", template=None)
    log.append("staged", item="code/002_next", questions=QS, plan="p")
    log.append("start", item="code/002_next", stage_defaults=QS)
    return log.append("finish", item="code/002_next", status="done", report_status="done", reasons=[])


def bundle(env) -> str:
    return review.render(env.d.cfg, state.scan(env.d.cfg, env.d.log), "r", log=env.d.log).text


def test_finished_questions_sit_beside_the_report_and_go_once_reviewed(env):
    fin = launched_on_defaults(env)
    text = bundle(env)
    assert "## Staging questions: open rulings" not in text
    assert "- launched " in text and "on the defaults of 2 staging questions (reviewing this report" in text
    assert "> Oracle in the ladder? — **default**: yes" in text
    lines = inbox.staged_questions(env.d.log, state.scan(env.d.cfg, env.d.log))     # the digests: one line
    assert len(lines) == 1 and "its 2 questions settle when its report is reviewed" in lines[0]
    env.d.log.append("reviewed", item="code/002_next", finish=fin.id, status="done", by="planner")
    text = bundle(env)
    assert "Oracle in the ladder?" not in text and "staging question" not in text
    assert inbox.staged_questions(env.d.log, state.scan(env.d.cfg, env.d.log)) == []
    # while it runs they stay quoted as open rulings: test_prompt_67's slow test
