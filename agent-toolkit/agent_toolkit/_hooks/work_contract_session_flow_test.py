"""公開hookから既存の作業契約の本文を通知する流れを確認する。"""

from __future__ import annotations

import json
import pathlib

import pytest

from agent_toolkit._common import session_state
from agent_toolkit._hooks import document_reads, pretooluse, rules_context, user_prompt_submit, work_contract
from agent_toolkit._testing.document_delivery import run_hook


@pytest.mark.parametrize("codex", [False, True])
def test_each_user_prompt_delivers_the_contract_body(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, codex: bool
) -> None:
    """実入力のたびに、同じ契約本文と回答後の復帰基準を両ホストの公開UserPromptSubmitから届ける。"""
    path = tmp_path / "work-contract.md"
    path.write_text("目的: A全体の完了\n要件: Bを保持\n", encoding="utf-8")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.setattr(work_contract, "record_path", lambda _session: path)
    monkeypatch.setattr(user_prompt_submit.termination_evidence, "observe_user", lambda *_args: None)
    extra = {"turn_id": "turn"} if codex else {}
    session = "contract-body-" + ("codex" if codex else "claude")
    for prompt in ("Xはどこから来たの？", "状況は？"):
        result = run_hook(
            "user_prompt_submit", {"session_id": session, "hook_event_name": "UserPromptSubmit", "prompt": prompt, **extra}
        )
        assert result.returncode == 0
        assert "目的: A全体の完了" in result.stdout
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "work-contract-updates.md" in context
        assert "`agent-toolkit/share/work-contract.md`の条件別資料" in context
    machine = run_hook(
        "user_prompt_submit",
        {
            "session_id": session,
            "hook_event_name": "UserPromptSubmit",
            "prompt": '<atk-auto source="agents-server" kind="delivery">追送</atk-auto>',
            **extra,
        },
    )
    assert "目的: A全体の完了" not in machine.stdout


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
    assert "work-contract-updates.md" in context
    assert "`agent-toolkit/share/work-contract.md`の条件別資料" in context


@pytest.mark.parametrize("initial_prompt", [None, "調査を進めて", '<atk-auto source="process-loop">/goal 作業</atk-auto>'])
@pytest.mark.cpu_limit(
    3.75,
    reason=(
        "不在から作成後、追加入力と圧縮までの公開Pre7回/User/Startで作成案内の解除を検証する（Linux限定再確認"
        "のCPU合計最大1.697秒）。"
    ),
)
def test_missing_contract_public_hook_flow(tmp_path, monkeypatch, capsys, initial_prompt):
    """初回、自動goal、追加入力、圧縮後の最初の操作だけで作成条件を届ける。"""
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
@pytest.mark.cpu_limit(
    5.75,
    reason=(
        "契約2更新と圧縮の公開Pre10回/Post/Startで、取得後と更新後の案内と本文非配送を検証する（Linux限定再"
        "確認のCPU合計最大2.695秒）。"
    ),
)
def test_delegated_contract_update_and_compact_guide_current_input(tmp_path, monkeypatch, codex):
    """起動時の読了後に更新した契約を1回案内し、実読取と圧縮をまたいで現行版へ戻る。"""
    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    path = tmp_path / "whole.md"
    path.write_text("# 目的\n継続する\n", encoding="utf-8")
    sid = "whole-update-" + str(codex)
    metadata = json.dumps({"path": str(path), "heading": "", "selectors": ["input:作業契約"]})
    records = {
        "startup-input-target:" + metadata: ["main"],
        document_reads.read_trigger_key(path, document_reads.content_fingerprint(path)): ["main"],
    }
    session_state.update_state(sid, lambda state: {**state, rules_context.OPERATION_SKILL_READY_KEY: records})
    extra = {"turn_id": "turn"} if codex else {}
    operation = {"session_id": sid, "tool_name": "Judge", "tool_input": {}, **extra}
    assert "全体作業契約の現行版を全文取得したことを確認できない" not in run_hook("pretooluse", operation).stdout
    path.write_text("# 目的\n継続する\n## 即時対応で確定した暫定規則\n契約専用の新規則\n", encoding="utf-8")
    for tool, tool_input in (
        ("Skill", {"skill": "agent-toolkit:delegation"}),
        ("Read", {"file_path": str(path)}),
        ("Bash", {"command": "cat -- " + str(path)}),
    ):
        preparation = run_hook("pretooluse", {**operation, "tool_name": tool, "tool_input": tool_input})
        assert preparation.returncode == 0
        assert "全体作業契約が取得後に更新された" not in preparation.stdout
    updated = run_hook("pretooluse", operation)
    assert updated.returncode == 0
    assert "全体作業契約が取得後に更新された" in updated.stdout
    assert str(path) in updated.stdout and "契約専用の新規則" not in updated.stdout
    assert 'permissionDecision": "deny' not in updated.stdout
    assert "全体作業契約の現行版を全文取得したことを確認できない" not in run_hook("pretooluse", operation).stdout
    read = run_hook(
        "posttooluse",
        {
            "session_id": sid,
            "tool_name": "Read",
            "tool_input": {"file_path": str(path)},
            "tool_response": path.read_text(encoding="utf-8"),
            **extra,
        },
    )
    assert read.returncode == 0
    assert "全体作業契約の現行版を全文取得したことを確認できない" not in run_hook("pretooluse", operation).stdout
    compact = run_hook(
        "rules_context",
        {
            "session_id": sid,
            "hook_event_name": "SessionStart",
            "source": "compact",
            **extra,
        },
    )
    assert compact.returncode == 0
    assert "全体作業契約の現行版を全文取得したことを確認できない" not in compact.stdout
    assert "契約専用の新規則" not in compact.stdout
    path.write_text("# 目的\n## 即時対応で確定した暫定規則\n次の訂正を適用する\n", encoding="utf-8")
    reupdated = run_hook("pretooluse", operation)
    assert reupdated.returncode == 0
    assert "全体作業契約の現行版を全文取得したことを確認できない" in reupdated.stdout
    assert "次の訂正を適用する" not in reupdated.stdout
    assert "全体作業契約の現行版を全文取得したことを確認できない" not in run_hook("pretooluse", operation).stdout


@pytest.mark.parametrize("mode", ["oversized", "unreadable", "missing-delegate"])
def test_contract_diagnostic_reaches_public_response(tmp_path, monkeypatch, capsys, mode):
    path = tmp_path / "work-contract.md"
    monkeypatch.setattr(work_contract, "record_path", lambda *_args: path)
    if mode == "missing-delegate":
        monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
        metadata = json.dumps({"path": str(path), "selectors": ["input:作業契約"]})
        session_state.update_state(
            "missing-delegate",
            lambda state: {**state, rules_context.OPERATION_SKILL_READY_KEY: {"startup-input-target:" + metadata: ["main"]}},
        )
        assert pretooluse.main(json.dumps({"session_id": "missing-delegate", "tool_name": "Judge", "tool_input": {}})) == 0
    else:
        monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
        monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
        path.write_bytes(b"\xff" if mode == "unreadable" else ("要件" * 3000).encode())
        assert user_prompt_submit.main(json.dumps({"session_id": "diagnostic-" + mode, "prompt": "続けて"})) == 0
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert str(path) in context
    assert {"oversized": "通知の上限", "unreadable": "UnicodeDecodeError", "missing-delegate": "委譲元"}[mode] in context
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
