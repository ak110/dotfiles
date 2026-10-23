"""操作を起動の契機とするスキルの取得の契約を、表の全項目と主体の全区分の組で検証する。

Claudeは未取得の本文を配送し、Codexは取得対象を案内して、成功した本文取得の後に同じ操作を許可する。
全主体で通常操作を継続する。表へ項目を加えると、`_POSITIVE_OPERATIONS`へ正例が無い限り`test_positive_operations_cover_every_entry`が失敗する。
"""

from __future__ import annotations

import json
import pathlib
import tempfile
from typing import Any

import pytest

from agent_toolkit._agents_server import claude as claude_backend
from agent_toolkit._agents_server import claude_settings, launch_prompts
from agent_toolkit._agents_server import codex as codex_backend
from agent_toolkit._common import delegated_session
from agent_toolkit._common.session_state import read_state
from agent_toolkit._hooks import plugin_resources, rules_context
from agent_toolkit._hooks.pretooluse import dispatch, operation_skills, read_delivery
from agent_toolkit._testing.pretooluse_support import _additional_context, _plan_file_state_env, _run, _run_posttooluse

_POSITIVE_OPERATIONS: dict[str, tuple[str, dict]] = {
    "delegation": ("Agent", {"prompt": "調査する"}),
    "search": ("Grep", {"pattern": "x"}),
    "bugfix": ("Bash", {"command": "cat > /tmp/awi.md <<'EOF'\n## 原因分析\nEOF"}),
    "managed-temp": ("Bash", {"command": "atk managed-temp create --prefix example"}),
    "writing-standards": ("Write", {"file_path": "/work/notes.md", "content": "# 見出し\n"}),
    "check-execution": ("Bash", {"command": "uv run --frozen pytest a_test.py"}),
}
"""各項目の正例の操作。他の項目へ同時に当たらない入力を選ぶ。"""

_LAUNCH_KIND = delegated_session.LAUNCH_KIND_ENV
_SUBJECTS: dict[str, tuple[dict[str, str], dict[str, str]]] = {
    "main": ({}, {}),
    "claude-delegate": ({"AGENT_TOOLKIT_DELEGATED_SESSION": "1", _LAUNCH_KIND: "delegate"}, {}),
    "subagent": ({}, {"agent_id": "child"}),
    "codex": ({}, {"turn_id": "turn-1"}),
    "codex-delegate": ({"AGENT_TOOLKIT_OWNER_SESSION": "owner", _LAUNCH_KIND: "delegate"}, {"turn_id": "turn-1"}),
    **{
        f"claude-{kind}": ({"AGENT_TOOLKIT_DELEGATED_SESSION": "1", _LAUNCH_KIND: kind}, {})
        for kind in sorted(delegated_session.LIGHTWEIGHT_LAUNCH_KINDS)
    },
    **{
        f"codex-{kind}": ({"AGENT_TOOLKIT_OWNER_SESSION": "owner", _LAUNCH_KIND: kind}, {"turn_id": "turn-1"})
        for kind in sorted(delegated_session.LIGHTWEIGHT_LAUNCH_KINDS)
    },
}
"""主体の区分ごとの環境変数とhook入力の追加項目。軽量区分はagents_serverが委譲先へ渡す起動区分で表す。"""


def _read_payload(path: pathlib.Path, extra: dict[str, str]) -> dict:
    """ホスト本来の読取のPostToolUse入力を返す。

    Claude CodeのReadの`tool_response`はClaude Code 2.1.303の本体の結果（`type`・`file`）から、
    CodexのBashは出力文字列をそのまま渡す形から写した。
    """
    text = path.read_text(encoding="utf-8")
    if "turn_id" in extra:
        return {"tool_name": "Bash", "tool_input": {"command": f"cat {path}"}, "tool_response": text}
    lines = len(text.splitlines())
    file = {"filePath": str(path), "content": text, "numLines": lines, "startLine": 1, "totalLines": lines}
    return {"tool_name": "Read", "tool_input": {"file_path": str(path)}, "tool_response": {"type": "text", "file": file}}


def _acquire(env: dict[str, str], entry: operation_skills.OperationSkill, subject: str, session_id: str, extra: dict) -> None:
    """区分ごとのホスト本来の手段で全必須資料を取得し、PostToolUseへ観測させる。"""
    for relative in entry.documents:
        if relative == operation_skills.SKILL_DOCUMENT and subject in {"main", "claude-delegate"}:
            payload: dict[str, Any] = {"tool_name": "Skill", "tool_input": {"skill": entry.skill_name}}
        else:
            payload = _read_payload(plugin_resources.skill_document_path(entry.short_name, relative), extra)
        event = {"hook_event_name": "PostToolUse", "session_id": session_id, **payload, **extra}
        assert _run_posttooluse(event, env).returncode == 0


def test_positive_operations_cover_every_entry() -> None:
    """表の全項目が正例を持ち、各正例は自身の項目だけに当たる。"""
    assert set(_POSITIVE_OPERATIONS) == {entry.short_name for entry in operation_skills.OPERATION_SKILLS}
    for entry in operation_skills.OPERATION_SKILLS:
        tool, tool_input = _POSITIVE_OPERATIONS[entry.short_name]
        matched = [other.short_name for other in operation_skills.OPERATION_SKILLS if other.matches(tool, tool_input)]
        expected = [entry.short_name, "writing-standards"] if entry.short_name == "bugfix" else [entry.short_name]
        assert matched == expected


def test_lightweight_launch_kinds_match_agents_server() -> None:
    """hookが読む軽量起動の写しは、agents_serverが軽量起動として扱う区分と一致する。"""
    assert delegated_session.LIGHTWEIGHT_LAUNCH_KINDS == launch_prompts.LIGHTWEIGHT_LAUNCH_KINDS


@pytest.mark.parametrize("subject", list(_SUBJECTS))
@pytest.mark.parametrize("entry", operation_skills.OPERATION_SKILLS, ids=lambda entry: entry.short_name)
def test_operation_skill_contract_for_every_entry_and_subject(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, entry: operation_skills.OperationSkill, subject: str
) -> None:
    """全項目と全区分の組で、遮断の有無・案内・本文を含まないこと・取得の観測前に記録しないことを確かめる。"""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    environ, extra = _SUBJECTS[subject]
    env = {**_plan_file_state_env(tmp_path), **environ}
    session_id = f"{entry.short_name}-{subject}"
    tool, tool_input = _POSITIVE_OPERATIONS[entry.short_name]
    payload = {"tool_name": tool, "tool_input": tool_input, "session_id": session_id, **extra}
    paths = [plugin_resources.skill_document_path(entry.short_name, relative) for relative in entry.documents]

    result = _run(payload, env)
    output = result.stderr + _additional_context(result)
    if environ.get(_LAUNCH_KIND) in delegated_session.LIGHTWEIGHT_LAUNCH_KINDS:
        assert result.returncode == 0
        assert entry.skill_name not in output
        assert rules_context.OPERATION_SKILL_READY_KEY not in read_state(session_id)
        return

    assert result.returncode == 0
    assert 'kind="block"' not in output
    observed = [output]
    for _attempt in range(12):
        if subject.startswith("codex"):
            records = read_state(session_id)[rules_context.OPERATION_SKILL_READY_KEY]
            targets = read_delivery.selected_targets(records, extra.get("agent_id", "main"))
            assert targets
            # 案内だけでは取得済みにならない。
            assert _run(payload, env).returncode == 0
            for target in targets:
                event = {
                    "hook_event_name": "PostToolUse",
                    "session_id": session_id,
                    **_read_payload(target.path, extra),
                    **extra,
                }
                assert _run_posttooluse(event, env).returncode == 0
        result = _run(payload, env)
        observed.append(result.stderr + _additional_context(result))
        assert result.returncode == 0
        if not result.stderr and not _additional_context(result):
            break
    else:
        pytest.fail("宣言資料の配送・全文取得後にも同じ通知が続いた")
    assert result.returncode == 0
    assert not result.stderr
    assert _run(payload, env).returncode == 0
    joined = "\n".join(observed)
    assert str(paths[0]) in joined or entry.short_name in joined


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


def test_missing_skill_document_keeps_operation_blocked_without_record(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """案内する資料を取得できない間は診断付きで遮断し記録しない。復旧後は取得の案内で遮断する。"""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    skill_md = plugin_resources.skill_document_path("search", "SKILL.md")
    original = pathlib.Path.read_text

    def fail_skill(path: pathlib.Path, *args: Any, **kwargs: Any) -> str:
        if path == skill_md:
            raise OSError("資料を取得できない")
        return original(path, *args, **kwargs)

    payload = json.dumps({"tool_name": "Grep", "tool_input": {"pattern": "x"}, "session_id": "failure"})
    with monkeypatch.context() as patch:
        patch.setattr(pathlib.Path, "read_text", fail_skill)
        for _ in range(2):
            assert dispatch.main(payload) == 0
            captured = capsys.readouterr()
            if _ == 0:
                assert "資料を取得できない" in captured.out + captured.err
            assert rules_context.OPERATION_SKILL_READY_KEY not in read_state("failure")
    assert dispatch.main(payload) == 0
    recovered = capsys.readouterr()
    assert "操作に関係する資料を配送する" in recovered.out + recovered.err
