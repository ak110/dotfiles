"""共有読込表の条件別案内を、公開hookから確かめる。"""

import json
import pathlib
import shlex

from agent_toolkit._common import read_triggers
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process


def test_bugfix_start_does_not_request_other_analysis_workflows(tmp_path, monkeypatch):
    """Skill取得時は依存案内を保留し、別工程の4資料を一括案内しない。"""
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
    assert "bugfix/references/root-cause-analysis.md" not in result.stdout
    for path in (
        "wi-standards/references/analysis-records.md",
        "delegation/references/investigation-delegation.md",
        "review-session/references/analysis-operations-root-cause.md",
        "user-confirmation-and-report/references/analysis-operations-intervention.md",
    ):
        assert path not in result.stdout


def test_user_intervention_analysis_requests_both_operation_references(tmp_path, monkeypatch):
    """介入分析の読取時は候補を保留し、無関係な次操作へ候補を拡張しない。"""
    root = pathlib.Path(__file__).parent
    rows = read_triggers.parse_tables(root / "share/read-trigger-bootstrap.md", root)
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", root)
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: rows)
    session = "bootstrap-intervention"
    payload = {"session_id": session, "cwd": str(tmp_path)}
    source = root / "skills/review-session/references/analysis-operations-root-cause.md"
    selected = _run_hook_in_process(
        "pretooluse",
        {**payload, "tool_name": "Bash", "tool_input": {"command": "cat -- " + shlex.quote(str(source))}},
    )
    assert selected.returncode == 0 and not selected.stderr
    context = json.loads(selected.stdout)["hookSpecificOutput"]["additionalContext"] if selected.stdout else ""
    assert "条件の成立は未確定" not in context
    assert "review-session/references/analysis-operations-root-cause.md" not in context
    assert "user-confirmation-and-report/references/analysis-operations-intervention.md" not in context
    result = _run_hook_in_process("pretooluse", {**payload, "tool_name": "Do", "tool_input": {}})
    assert result.returncode == 0 and not result.stderr and not result.stdout
