"""共有読込表の条件別案内を、公開hookから確かめる。"""

import json
import pathlib
import shlex

from agent_toolkit._common import read_triggers
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process


def test_bugfix_start_does_not_request_other_analysis_workflows(tmp_path, monkeypatch):
    """原因分析の開始では定義元を案内し、別工程の4資料を一括案内しない。"""
    root = pathlib.Path(__file__).parent
    rows = read_triggers.parse_tables(root / "share/read-trigger-bootstrap.md", root)
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", root)
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: rows)
    result = _run_hook_in_process(
        "pretooluse",
        {
            "session_id": "bootstrap-bugfix",
            "cwd": str(tmp_path),
            "tool_name": "Skill",
            "tool_input": {"skill": "agent-basics:bugfix"},
        },
    )
    assert result.returncode == 0 and not result.stderr
    assert "bugfix/references/root-cause-analysis.md" in result.stdout
    for path in (
        "wi-standards/references/analysis-records.md",
        "delegation/references/investigation-delegation.md",
        "review-session/references/analysis-operations.md",
        "user-confirmation-and-report/references/analysis-operations.md",
    ):
        assert path not in result.stdout


def test_user_intervention_analysis_requests_both_operation_references(tmp_path, monkeypatch):
    """介入分析の資料を選んだ担当へ、判定と運用の双方の基準を案内する。"""
    root = pathlib.Path(__file__).parent
    rows = read_triggers.parse_tables(root / "share/read-trigger-bootstrap.md", root)
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", root)
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: rows)
    session = "bootstrap-intervention"
    payload = {"session_id": session, "cwd": str(tmp_path)}
    source = root / "skills/review-session/references/analysis-operations.md"
    selected = _run_hook_in_process(
        "pretooluse",
        {**payload, "tool_name": "Bash", "tool_input": {"command": "cat -- " + shlex.quote(str(source))}},
    )
    assert selected.returncode == 0 and not selected.stderr
    result = _run_hook_in_process("pretooluse", {**payload, "tool_name": "Do", "tool_input": {}})
    assert result.returncode == 0 and not result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "review-session/references/analysis-operations.md" in context
    assert "user-confirmation-and-report/references/analysis-operations.md" in context
