"""実物のSKILL.md全文を操作前に配送する契約を検証する。"""

from __future__ import annotations

import json
import pathlib
import tempfile
from typing import Any

import pytest

from agent_toolkit._agents_server import claude as claude_backend
from agent_toolkit._agents_server import claude_settings
from agent_toolkit._common.session_state import read_state
from agent_toolkit._hooks import rules_context
from agent_toolkit._hooks.pretooluse import dispatch
from agent_toolkit._testing.pretooluse_support import _additional_context, _plan_file_state_env, _run

_SEARCH_SKILL_MD = pathlib.Path(__file__).resolve().parents[3] / "skills" / "search" / "SKILL.md"
_WARN_OPENING = '<atk-auto source="pretooluse" kind="block">'


def _search_warning(payload: dict, env: dict[str, str]) -> str:
    result = _run(payload, env)
    assert result.returncode in {0, 2}
    return result.stderr if result.returncode == 2 else _additional_context(result)


_LIGHTWEIGHT_SEARCH_PAYLOADS = [
    ("explore", {"tool_name": "Grep", "tool_input": {"pattern": "x"}}),
    ("explore", {"tool_name": "Glob", "tool_input": {"pattern": "**/*.py"}}),
    ("shell", {"tool_name": "Bash", "tool_input": {"command": "git grep -n -F x"}}),
    ("write", {"tool_name": "Grep", "tool_input": {"pattern": "x"}}),
    ("write", {"tool_name": "Glob", "tool_input": {"pattern": "*.md"}}),
]


@pytest.mark.parametrize(
    ("launch_kind", "payload"),
    _LIGHTWEIGHT_SEARCH_PAYLOADS,
    ids=[f"{kind}-{payload['tool_name']}" for kind, payload in _LIGHTWEIGHT_SEARCH_PAYLOADS],
)
def test_claude_warning_offers_skill_md_read_for_sessions_without_skill(
    tmp_path: pathlib.Path, launch_kind: claude_backend.LaunchKind, payload: dict
) -> None:
    """Claudeの警告は`Skill`の起動と、`Skill`を使えない主体が実在する`SKILL.md`を`Read`で読む操作を併記する。

    `agents_server`の軽量起動の委譲先は`Skill`を使えずスキルの一覧も届かないため、`Skill`だけを示す警告では
    起動の失敗を報告するだけで検索の基準へ到達できない。案内する`Read`が各modeの許可ツールにあることも確かめる。
    """
    allowed_tools = claude_backend._LAUNCH_ALLOWED_TOOLS[launch_kind]  # pylint: disable=protected-access
    assert "Read" in allowed_tools
    options = claude_backend._build_options(str(tmp_path), None, None, launch_kind=launch_kind)  # pylint: disable=protected-access
    assert "Skill" in options.disallowed_tools
    claude_settings.cleanup_settings(options)
    assert payload["tool_name"] in allowed_tools
    env = {**_plan_file_state_env(tmp_path), "AGENT_TOOLKIT_DELEGATED_SESSION": "1"}

    warning = _search_warning({**payload, "session_id": f"light-{launch_kind}"}, env)

    assert warning.startswith(_WARN_OPENING)
    assert _SEARCH_SKILL_MD.read_text(encoding="utf-8") in warning
    assert "同じ操作を再発行" in warning
    assert _run({**payload, "session_id": f"light-{launch_kind}"}, env).returncode == 0


@pytest.mark.parametrize(
    "skill,tool,inputs",
    [
        ("delegation", "Agent", {"prompt": "調査する"}),
        ("search", "Grep", {"pattern": "x"}),
        ("bugfix", "Write", {"file_path": "/work/analysis.md", "content": "## 原因分析\n"}),
        ("managed-temp", "Bash", {"command": "atk managed-temp create --prefix example"}),
        ("writing-standards", "Write", {"file_path": "/work/AGENTS.md", "content": "# 規範\n"}),
    ],
)
def test_first_operation_delivers_entire_skill_before_retry(
    tmp_path: pathlib.Path, skill: str, tool: str, inputs: dict
) -> None:
    """5スキルの全文が実応答へ届き、同じ主体の再発行を通す。"""
    env = _plan_file_state_env(tmp_path)
    payload = {"tool_name": tool, "tool_input": inputs, "session_id": "full-delivery"}
    result = _run(payload, env)
    body = (_SEARCH_SKILL_MD.parent.parent / skill / "SKILL.md").read_text(encoding="utf-8")
    assert result.returncode == 2
    assert body in result.stderr
    assert "同じ操作を再発行" in result.stderr
    assert _run(payload, env).returncode == 0


def test_delivery_failure_keeps_operation_blocked_without_ready_state(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """本文の取得失敗では記録せず、復旧した同じ操作で全文を配送する。"""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    original = pathlib.Path.read_text

    def fail_skill(path: pathlib.Path, *args: Any, **kwargs: Any) -> str:
        if path == _SEARCH_SKILL_MD:
            raise OSError("資料を取得できない")
        return original(path, *args, **kwargs)

    payload = json.dumps({"tool_name": "Grep", "tool_input": {"pattern": "x"}, "session_id": "failure"})
    with monkeypatch.context() as patch:
        patch.setattr(pathlib.Path, "read_text", fail_skill)
        for _ in range(2):
            assert dispatch.main(payload) == 2
            assert "資料を取得できない" in capsys.readouterr().err
            assert rules_context.OPERATION_SKILL_READY_KEY not in read_state("failure")
    assert dispatch.main(payload) == 2
    assert original(_SEARCH_SKILL_MD, encoding="utf-8") in capsys.readouterr().err
    assert dispatch.main(payload) == 0
