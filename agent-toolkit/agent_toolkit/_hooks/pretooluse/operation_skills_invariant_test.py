"""操作の直前に起動を求めるスキルの表の全項目について、正例と委譲先の主体の区分ごとの案内を検証する。

表へ項目を加えると、`_POSITIVE_OPERATIONS`へ正例が無い限り`test_positive_operations_cover_every_entry`が失敗する。
各ホストの最上位の主体への案内、同じ主体への再案内の抑止、軽量の委譲先の除外は`operation_skills_test.py`が確かめる。
軽量の委譲先の判定が読む起動区分を、agents_serverの各backendが委譲先の環境へ渡すこともここで確かめる。
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
from typing import Any

import pytest

from agent_toolkit._agents_server import claude as claude_backend
from agent_toolkit._agents_server import claude_settings
from agent_toolkit._agents_server import codex as codex_backend
from agent_toolkit._common import delegated_session
from agent_toolkit._hooks.pretooluse import operation_skills
from agent_toolkit._testing.pretooluse_support import (
    _additional_context,
    _plan_file_state_env,
    _run,
)

_POSITIVE_OPERATIONS: dict[str, tuple[str, dict]] = {
    "search": ("Bash", {"command": "rg -n needle ."}),
    "bugfix": ("Bash", {"command": "cat > /tmp/awi.md <<'EOF'\n## 原因分析\nEOF"}),
    "writing-standards": ("Bash", {"command": "cat > /work/notes.md <<'EOF'\n# 見出し\nEOF"}),
    "check-execution": ("Bash", {"command": "uv run --frozen pytest a_test.py"}),
}
"""各項目の正例の操作。Codexの主体にも同じ入力を送るため両ホストにあるBashを使い、他の項目へ同時に当たらない入力を選ぶ。"""

_LAUNCH_KIND = delegated_session.LAUNCH_KIND_ENV
_SUBJECTS: dict[str, tuple[dict[str, str], dict[str, str]]] = {
    "claude-delegate": ({"AGENT_TOOLKIT_DELEGATED_SESSION": "1", _LAUNCH_KIND: "delegate"}, {}),
    "subagent": ({}, {"agent_id": "child"}),
    "codex-delegate": ({"AGENT_TOOLKIT_OWNER_SESSION": "owner", _LAUNCH_KIND: "delegate"}, {"turn_id": "turn-1"}),
}
"""スキルを使う委譲先の主体の区分ごとの環境変数とhook入力の追加項目。区分はagents_serverが委譲先へ渡す起動区分で表す。"""

_NOTICE = re.compile(r"スキル`([^`]+)`をまだ起動していなければ")
"""起動を求める案内から、案内したスキルの名前を取り出す。"""


def _permission_decision(result: subprocess.CompletedProcess[str]) -> object:
    """公開PreToolUse hookの応答が示す権限の判定を返す。応答が無ければ`None`を返す。"""
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout).get("hookSpecificOutput", {}).get("permissionDecision")


def test_positive_operations_cover_every_entry() -> None:
    """表の全項目が正例を持ち、各正例は自身の項目だけに当たる（原因分析の記述は本文の作成・更新にも当たる）。"""
    assert set(_POSITIVE_OPERATIONS) == {entry.short_name for entry in operation_skills.OPERATION_SKILLS}
    for entry in operation_skills.OPERATION_SKILLS:
        tool, tool_input = _POSITIVE_OPERATIONS[entry.short_name]
        matched = [other.short_name for other in operation_skills.OPERATION_SKILLS if other.matches(tool, tool_input, "")]
        expected = [entry.short_name, "writing-standards"] if entry.short_name == "bugfix" else [entry.short_name]
        assert matched == expected


@pytest.mark.parametrize("subject", list(_SUBJECTS))
@pytest.mark.parametrize("entry", operation_skills.OPERATION_SKILLS, ids=lambda entry: entry.short_name)
def test_every_entry_notifies_delegated_subjects(
    tmp_path: pathlib.Path, entry: operation_skills.OperationSkill, subject: str
) -> None:
    """スキルを使う委譲先とサブエージェントにも、表の全項目の操作で遮断せずにそのスキルの起動を案内する。"""
    environ, extra = _SUBJECTS[subject]
    tool, tool_input = _POSITIVE_OPERATIONS[entry.short_name]
    payload = {"tool_name": tool, "tool_input": tool_input, "session_id": f"{entry.short_name}-{subject}", **extra}
    result = _run(payload, {**_plan_file_state_env(tmp_path), **environ})
    assert result.returncode == 0
    assert not result.stderr
    assert _permission_decision(result) != "deny"
    assert entry.skill_name in _NOTICE.findall(_additional_context(result))


@pytest.mark.parametrize("launch_kind", ["delegate", "explore", "shell", "write"])
def test_backends_pass_launch_kind_to_delegate_environment(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, launch_kind: claude_backend.LaunchKind
) -> None:
    """Claude backendは委譲先の環境へ起動区分を設定し、継承した値を上書きする。"""
    monkeypatch.setenv(_LAUNCH_KIND, "explore" if launch_kind == "delegate" else "delegate")
    options = claude_backend._build_options(str(tmp_path), None, None, launch_kind=launch_kind)  # pylint: disable=protected-access
    claude_settings.cleanup_settings(options)
    assert options.env[_LAUNCH_KIND] == launch_kind
    assert delegated_session.is_lightweight_delegated(options.env) is (launch_kind != "delegate")


@pytest.mark.asyncio
@pytest.mark.parametrize("launch_kind", ["delegate", "explore", None])
async def test_codex_app_server_environment_carries_launch_kind(
    monkeypatch: pytest.MonkeyPatch, launch_kind: codex_backend.LaunchKind | None
) -> None:
    """Codex backendはsessionのApp Serverの子環境へ起動区分を設定し、照会用の接続では継承した値を除く。

    所有接続の取得（開始・再開）は起動区分を渡し、モデル一覧などの照会用の接続は起動区分を持たない。
    """
    captured: list[dict[str, str]] = []

    async def fake_exec(*_args: Any, env: dict[str, str], **_kwargs: Any) -> Any:
        captured.append(env)
        raise OSError("起動しない")

    monkeypatch.setenv(_LAUNCH_KIND, "shell")
    monkeypatch.setattr(codex_backend.asyncio, "create_subprocess_exec", fake_exec)
    async with codex_backend.AppServerManager() as manager:
        with pytest.raises(codex_backend.AppServerError):
            if launch_kind is None:
                await manager._ensure_client()  # pylint: disable=protected-access
            else:
                await manager._acquire_owned_client("session", launch_kind)  # pylint: disable=protected-access
        assert captured[0].get(_LAUNCH_KIND) == launch_kind
