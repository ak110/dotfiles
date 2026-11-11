"""助言依頼の対象となる実モデルと起動形の組を検証する。"""

import pytest

from agent_toolkit._agents_server import advice_request


@pytest.mark.parametrize(
    "parent,engine,model,phase,kind,expected",
    [
        (("claude", "claude-haiku-4"), "codex", "gpt-6-astra", "chores", "delegate", True),
        (("claude", "unknown"), "codex", "unknown", "chores", "delegate", True),
        (("codex", "gpt-6-astra"), "codex", "gpt-6-sol", "chores", "delegate", True),
        (("codex", "gpt-6-sol"), "codex", "gpt-6-sol", "chores", "delegate", False),
        (("codex", "gpt-6-luna"), "codex", "gpt-6-sol", "chores", "delegate", False),
        (("codex", "unknown"), "codex", "gpt-6-sol", "chores", "delegate", False),
        (("codex", "gpt-6-astra"), "codex", "unknown", "chores", "delegate", False),
        (None, "codex", "gpt-6-sol", "chores", "delegate", False),
        *(
            (("claude", "claude-fable-5"), "codex", "gpt-6-luna", phase, "delegate", False)
            for phase in ("review", "review_fast")
        ),
        *(
            (("claude", "claude-fable-5"), "codex", "gpt-6-luna", "chores", kind, False)
            for kind in ("explore", "shell", "write")
        ),
    ],
)
def test_advice_request_eligibility(parent, engine, model, phase, kind, expected):
    assert advice_request.advice_request_eligible(parent, engine, model, model_type=phase, launch_kind=kind) is expected


@pytest.mark.parametrize("phase", ["review", "review_fast"])
def test_review_role_is_excluded_with_explicit_candidate(tmp_path, phase):
    root = tmp_path / "agent-toolkit"
    (root / "share").mkdir(parents=True)
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin/plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    role = root / "share/custom.subagent.md"
    role.write_text(
        f"---\nmode: delegate\nmodel_type: {phase}\nwrites_worktree: false\nrequired_inputs: []\n---\n", encoding="utf-8"
    )
    assert not advice_request.advice_request_eligible(
        ("claude", "claude-fable-5"),
        "codex",
        "gpt-6-luna",
        model_type="codex:gpt-6-luna/medium",
        launch_kind="delegate",
        startup_document=role,
    )
