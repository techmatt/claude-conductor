"""Prompt 120: the Workflow agent cap at launch, and the start-access line for a project
whose repos aren't `C:\\Code\\<project>` (staging questions: `test_prompt_67.py`)."""

import json

from conductor import fanout, scaffold, talk

UNCAPPED = """export const meta = { name: 'review', description: 'x', phases: [{ title: 'Review' }] }
const results = await pipeline(DIMENSIONS, d => agent(d.prompt, {label: d.key}),
  review => parallel(review.findings.map(f => () => agent(`Verify: ${f.title}`))))
return results
"""
CAPPED = "const MAX_AGENTS = 10\n" + UNCAPPED.replace("review.findings.map", "review.findings.slice(0, 4).map")


def test_a_fan_out_needs_a_declared_cap_within_ten():
    assert "no `const MAX_AGENTS" in fanout.problem(UNCAPPED)
    assert fanout.problem(CAPPED) is None
    assert "MAX_AGENTS = 16, over the cap of 10" in fanout.problem(CAPPED.replace("= 10", "= 16"))
    straight = "\n".join(f"await agent('step {i}')" for i in range(3))
    assert fanout.problem(straight) is None                                   # three calls, no fan-out
    assert "12 agent() calls" in fanout.problem("\n".join(f"agent('s{i}')" for i in range(12)))
    assert fanout.problem("// for (x of y) agent(x)\nconst a = 1") is None    # comments don't count
    assert fanout.problem("for (const x of xs) console.log(x)") is None       # no agents at all


def test_the_pre_hook_denies_an_uncapped_workflow_and_reads_a_script_path(tmp_path):
    out = json.loads(talk.pre_hook({"tool_name": "Workflow", "tool_input": {"script": UNCAPPED}}, 60, []))
    hs = out["hookSpecificOutput"]
    assert hs["permissionDecision"] == "deny" and "at most 10 agents" in hs["permissionDecisionReason"]
    assert talk.pre_hook({"tool_name": "Workflow", "tool_input": {"script": CAPPED}}, 60, []) is None
    (tmp_path / "wf.js").write_text(UNCAPPED, encoding="utf-8")
    ev = {"tool_name": "Workflow", "cwd": str(tmp_path), "tool_input": {"scriptPath": "wf.js"}}
    assert "deny" in talk.pre_hook(ev, 0, [])
    assert talk.pre_hook({"tool_name": "Workflow", "tool_input": {"name": "saved"}}, 0, []) is None


def test_start_access_names_repos_outside_the_project_folder(tmp_path):
    control = tmp_path / "fractals-control"
    assert scaffold.start_access(control, "fractals", [tmp_path / "fractals"]) is None
    line = scaffold.start_access(control, "fractals", [tmp_path / "fractal-website", tmp_path / "fractal-wallpapers"])
    assert "Matt's saved start preference lists them" in line and "fractal-wallpapers" in line
    _, notes = scaffold.init(control, "fractals", [f"site={tmp_path / 'fractal-website'}"])
    assert any(n.startswith("start access: fractals's repos") for n in notes)
