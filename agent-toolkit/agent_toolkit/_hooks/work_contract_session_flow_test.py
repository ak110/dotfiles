"""公開hookから既存の作業契約の本文を通知する流れを確認する。"""

from __future__ import annotations

import json
import pathlib

import pytest

from agent_toolkit._agents_server import launch_requests
from agent_toolkit._common import session_registry
from agent_toolkit._hooks import pretooluse, rules_context, user_prompt_submit, work_contract
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process as run_hook


def _publish_launch(session_id: str, whole: pathlib.Path, handoff: pathlib.Path | None = None) -> None:
    """`agents_server`が委譲先を起動した時と同じ起動入力（全体作業契約の全文と引き継ぎ記録先）を登録簿へ置く。"""
    prompt = launch_requests.whole_contract_reference(whole_contract_path=str(whole))
    if handoff is not None:
        prompt = launch_requests.handoff_notice(handoff, whole.parent) + "\n\n" + prompt
    session_registry.publish(
        session_id,
        state=session_registry.PublicationState.RESULT_COMPLETED,
        engine="claude",
        cwd=str(whole.parent),
        launch_info=session_registry.LaunchInfo(prompt=prompt),
    )


@pytest.mark.parametrize("codex", [False, True])
def test_each_user_prompt_delivers_contract_metadata(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, codex: bool
) -> None:
    path = tmp_path / "work-contract.md"
    path.write_text("目的: A全体の完了\n要件: Bを保持\n", encoding="utf-8")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.setattr(work_contract, "record_path", lambda _session: path)
    monkeypatch.setattr(user_prompt_submit.termination_evidence, "observe_user", lambda *_args: None)
    extra = {"turn_id": "turn"} if codex else {}
    session = "contract-body-" + ("codex" if codex else "claude")
    for count, prompt in enumerate(("Xの出所を教えて。", "状況は？"), start=1):
        result = run_hook(
            "user_prompt_submit", {"session_id": session, "hook_event_name": "UserPromptSubmit", "prompt": prompt, **extra}
        )
        assert result.returncode == 0
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        assert str(path) in context
        assert "作業契約の最終更新:" in context
        assert f"その後の実ユーザー発話: {count}件" in context
        assert "目的: A全体の完了" not in context
    machine = run_hook(
        "user_prompt_submit",
        {
            "session_id": session,
            "hook_event_name": "UserPromptSubmit",
            "prompt": '<atk-auto source="agents-server" kind="delivery">追送</atk-auto>',
            **extra,
        },
    )
    if machine.stdout.strip():
        machine_context = json.loads(machine.stdout).get("hookSpecificOutput", {}).get("additionalContext", "")
        assert str(path) not in machine_context


def test_compaction_delivers_the_contract_body(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "work-contract.md"
    path.write_text("目的: A全体\n", encoding="utf-8")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.setattr(work_contract, "record_path", lambda _session: path)
    assert rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": "s"})) == 0
    output = capsys.readouterr().out
    assert "目的: A全体" in output
    context = json.loads(output)["hookSpecificOutput"]["additionalContext"]
    assert "「更新する時点」" in context
    assert "`agent-toolkit/skills/atk-mode/references/work-contract.md`「追加入力の統合」" in context


@pytest.mark.parametrize("initial_prompt", [None, "調査を進めて", '<atk-auto source="process-loop">/goal 作業</atk-auto>'])
@pytest.mark.cpu_limit(
    3.75,
    reason=(
        "不在から作成後、追加入力と圧縮までの公開Pre7回/User/Startで作成案内の解除を検証する（Linux限定再確認"
        "のCPU合計最大1.697秒）。"
    ),
)
def test_missing_contract_public_hook_flow(tmp_path, monkeypatch, capsys, initial_prompt):
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    path = tmp_path / "work-contract.md"
    monkeypatch.setattr(work_contract, "record_path", lambda *_args: path)
    session = "missing-public-flow"

    def operation():
        assert (
            pretooluse.main(json.dumps({"session_id": session, "tool_name": "Skill", "tool_input": {"skill": "unknown"}})) == 0
        )
        return capsys.readouterr().out

    if initial_prompt is not None:
        assert user_prompt_submit.main(json.dumps({"session_id": session, "prompt": initial_prompt})) == 0
        capsys.readouterr()
    output = operation()
    assert str(path) not in output
    assert "「作成と所有」" not in output
    assert "事実回答では作成しない" not in output
    assert "作業契約が未作成" not in operation()
    assert user_prompt_submit.main(json.dumps({"session_id": session, "prompt": "続きを進めて"})) == 0
    capsys.readouterr()
    assert str(path) not in operation()
    assert "作業契約が未作成" not in operation()
    assert rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": session})) == 0
    capsys.readouterr()
    assert str(path) not in operation()
    assert "作業契約が未作成" not in operation()
    path.write_text("目的: 継続\n", encoding="utf-8")
    assert user_prompt_submit.main(json.dumps({"session_id": session, "prompt": "続けて"})) == 0
    capsys.readouterr()
    assert "作業契約が未作成" not in operation()


@pytest.mark.parametrize("env", ["AGENT_TOOLKIT_OWNER_SESSION", "AGENT_TOOLKIT_DELEGATED_SESSION"])
def test_delegated_public_hook_has_no_missing_notice(tmp_path, monkeypatch, capsys, env):
    monkeypatch.setenv(env, "1")
    monkeypatch.setattr(work_contract, "record_path", lambda *_args: tmp_path / "work-contract.md")
    assert pretooluse.main(json.dumps({"session_id": "delegated", "tool_name": "Skill", "tool_input": {}})) == 0
    assert "作業契約が未作成" not in capsys.readouterr().out


@pytest.mark.parametrize("codex", [False, True])
def test_delegated_contract_update_and_compact_guide_current_input(tmp_path, monkeypatch, codex):
    """起動入力に入れた版から更新された全体作業契約を、本文を配らずに版ごとに1回案内し、圧縮後は所在を示す。"""
    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    path = tmp_path / "whole.md"
    path.write_text("# 目的\n継続する\n", encoding="utf-8")
    handoff = tmp_path / "handoff.md"
    sid = "whole-update-" + str(codex)
    _publish_launch(sid, path, handoff)
    extra = {"turn_id": "turn"} if codex else {}
    operation = {"session_id": sid, "tool_name": "Judge", "tool_input": {}, **extra}
    assert "全体作業契約が起動後に更新された" not in run_hook("pretooluse", operation).stdout
    path.write_text("# 目的\n継続する\n## 即時対応で確定した暫定規則\n契約専用の新規則\n", encoding="utf-8")
    updated = run_hook("pretooluse", operation)
    assert updated.returncode == 0
    assert "全体作業契約が起動後に更新された" in updated.stdout
    assert str(path) in updated.stdout and "契約専用の新規則" not in updated.stdout
    assert 'permissionDecision": "deny' not in updated.stdout
    assert "全体作業契約が起動後に更新された" not in run_hook("pretooluse", operation).stdout
    compact = run_hook("rules_context", {"session_id": sid, "hook_event_name": "SessionStart", "source": "compact", **extra})
    assert compact.returncode == 0
    assert f"全体作業契約`{path}`" in compact.stdout and f"自身の引き継ぎ記録`{handoff}`" in compact.stdout
    assert "契約専用の新規則" not in compact.stdout
    path.write_text("# 目的\n## 即時対応で確定した暫定規則\n次の訂正を適用する\n", encoding="utf-8")
    reupdated = run_hook("pretooluse", operation)
    assert reupdated.returncode == 0
    assert "全体作業契約が起動後に更新された" in reupdated.stdout
    assert "次の訂正を適用する" not in reupdated.stdout
    assert "全体作業契約が起動後に更新された" not in run_hook("pretooluse", operation).stdout


def test_delegate_outside_registry_gets_no_contract_notice(monkeypatch):
    """登録簿に無い委譲先（組み込みの委譲機能の子など）へは、全体作業契約の案内と圧縮後の所在を出さない。"""
    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    operation = {"session_id": "unregistered-delegate", "tool_name": "Judge", "tool_input": {}}
    assert "全体作業契約" not in run_hook("pretooluse", operation).stdout
    compact = run_hook(
        "rules_context", {"session_id": "unregistered-delegate", "hook_event_name": "SessionStart", "source": "compact"}
    )
    assert str(rules_context.SUBAGENT_PLAYBOOK_PATH) in compact.stdout
    assert "全体作業契約`" not in compact.stdout and "引き継ぎ記録`" not in compact.stdout


@pytest.mark.parametrize("mode", ["unreadable", "missing-delegate"])
def test_contract_diagnostic_reaches_public_response(tmp_path, monkeypatch, capsys, mode):
    path = tmp_path / "work-contract.md"
    monkeypatch.setattr(work_contract, "record_path", lambda *_args: path)
    if mode == "missing-delegate":
        monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
        path.write_text("目的: 全体\n", encoding="utf-8")
        _publish_launch("missing-delegate", path)
        path.unlink()
        assert pretooluse.main(json.dumps({"session_id": "missing-delegate", "tool_name": "Judge", "tool_input": {}})) == 0
    else:
        monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
        monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
        path.write_bytes(b"\xff")
        monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *_args: tmp_path)
        assert (
            rules_context.main(
                json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": "unreadable-contract"})
            )
            == 0
        )
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert str(path) in context
    assert {"unreadable": "UnicodeDecodeError", "missing-delegate": "委譲元"}[mode] in context
    if mode == "missing-delegate":
        assert "原本の所在と読取可能性を確認せよ" in context
        assert "全文読み" not in context


def test_compaction_oversized_contract_delivers_location(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    path = tmp_path / "work-contract.md"
    path.write_text("要件" * 6000, encoding="utf-8")
    monkeypatch.setattr(work_contract, "record_path", lambda *_args: path)
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *_args: tmp_path)
    assert (
        rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": "large-contract"}))
        == 0
    )
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert str(path) in context and "現在の目的・要件を確かめよ" in context
    assert "完了した要求が残っていれば、現在の目的・要件だけに書き直せ" in context
    assert "要件" * 10 not in context
