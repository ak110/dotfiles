"""subagent_stop_advisorのテスト。

scope-escalation検出テストの入力フレーズは
`agent-toolkit/skills/agent-standards/references/_scope_escalation_test_inputs.txt`
から動的に読み込む（`agent-toolkit:agent-standards`「コンテキスト汚染の回避」節。
検出語そのものをテストコード本文へ転記しない）。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import _fork_runner
import pytest
from _scope_escalation import _ASYNC_WAIT_SELF_LAUNCHED_RE
from _scope_escalation_test_helpers import load_scope_escalation_inputs
from _stop_gate_test import _user_async_launched_entry, _user_task_notification_entry, _write_transcript
from subagent_stop_advisor import _SELF_LAUNCHED_SUBAGENT_WAIT_RE

_SCRIPT = Path(__file__).parent / "subagent_stop_advisor.py"

_SCOPE_ESCALATION_INPUTS = load_scope_escalation_inputs()


def _pick_scope_escalation_text(category: str) -> str:
    """指定カテゴリの最小マッチ入力を1件返す。フィクスチャ不在時は空文字列。

    フィクスチャ内の最後の該当行を返す。新規追記した最小マッチ入力を
    優先的にE2Eテストへ供給するため（末尾追記が既定の追記位置のため）。
    """
    picked = ""
    for text, cat in _SCOPE_ESCALATION_INPUTS:
        if cat == category:
            picked = text
    return picked


def _run(payload: dict) -> subprocess.CompletedProcess[str]:
    return _fork_runner.run_script(_SCRIPT, input=json.dumps(payload))


def test_no_message_passes() -> None:
    result = _run({})
    assert result.stdout == ""
    assert result.returncode == 0


def test_normal_message_passes() -> None:
    result = _run({"last_assistant_message": "工程4完了。次工程へ移行する。"})
    assert result.stdout == ""


def _make_transcript(tmp_path: Path, tool_uses: list[dict]) -> str:
    """指定したtool_useブロック列を含むassistant messageを1件出力したtranscriptパスを返す。"""
    path = tmp_path / "transcript.jsonl"
    entry = {
        "type": "assistant",
        "message": {"content": tool_uses},
    }
    path.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    return str(path)


def test_process_omission_blocks() -> None:
    text = _pick_scope_escalation_text("process-omission")
    if not text:
        pytest.skip("scope-escalation fixture for process-omission not available")
    result = _run({"last_assistant_message": text})
    body = json.loads(result.stdout)
    assert body["decision"] == "block"


def test_single_session_blocks() -> None:
    text = _pick_scope_escalation_text("single-session")
    if not text:
        pytest.skip("scope-escalation fixture for single-session not available")
    result = _run({"last_assistant_message": text})
    body = json.loads(result.stdout)
    assert body["decision"] == "block"


def test_blocks_async_wait_new_phrases() -> None:
    """`async-wait`カテゴリの新規追記フレーズもblockする。"""
    text = _pick_scope_escalation_text("async-wait")
    if not text:
        pytest.skip("scope-escalation fixture for async-wait not available")
    result = _run({"last_assistant_message": text})
    body = json.loads(result.stdout)
    assert body["decision"] == "block"
    assert "async-wait" in body["reason"]


def test_blocks_overhead_tradeoff_phrases() -> None:
    """`overhead-tradeoff`カテゴリのフレーズもblockする。"""
    text = _pick_scope_escalation_text("overhead-tradeoff")
    if not text:
        pytest.skip("scope-escalation fixture for overhead-tradeoff not available")
    result = _run({"last_assistant_message": text})
    body = json.loads(result.stdout)
    assert body["decision"] == "block"
    assert "overhead-tradeoff" in body["reason"]


def test_approves_async_wait_when_background_tracked(tmp_path: Path) -> None:
    """async-wait表明でも未消化の追跡中background起動が実在すれば通過する。

    ピックされる文言は自身配下起動のレビュアー系サブエージェント名を含まない前提とする
    （含む場合は`_is_self_launched_subagent_wait`によりbypassが無効化されblockへ倒れるため）。
    """
    text = _pick_scope_escalation_text("async-wait")
    if not text:
        pytest.skip("scope-escalation fixture for async-wait not available")
    assert not any(name in text for name in ("plan-reviewer", "plan-codex-reviewer", "plan-impl-reviewer")), (
        "ピック文言が自身起動サブエージェント名を含むと本テストの前提が崩れる"
    )
    transcript = str(_write_transcript(tmp_path, [_user_async_launched_entry("toolu_bg1")]))
    result = _run({"last_assistant_message": text, "transcript_path": transcript})
    assert result.stdout == ""


def test_blocks_async_wait_without_tracked_background() -> None:
    """async-wait表明かつ追跡中background起動が無い場合は現行どおりブロックする。"""
    text = _pick_scope_escalation_text("async-wait")
    if not text:
        pytest.skip("scope-escalation fixture for async-wait not available")
    result = _run({"last_assistant_message": text})
    body = json.loads(result.stdout)
    assert body["decision"] == "block"


def test_blocks_async_wait_when_tracked_background_completed(tmp_path: Path) -> None:
    """起動記録があっても完了通知で全消化済みなら現行どおりブロックする。"""
    text = _pick_scope_escalation_text("async-wait")
    if not text:
        pytest.skip("scope-escalation fixture for async-wait not available")
    entries = [_user_async_launched_entry("toolu_bg2"), _user_task_notification_entry("toolu_bg2")]
    transcript = str(_write_transcript(tmp_path, entries))
    result = _run({"last_assistant_message": text, "transcript_path": transcript})
    body = json.loads(result.stdout)
    assert body["decision"] == "block"


def _pick_scope_escalation_text_containing(category: str, substring: str) -> str:
    """指定カテゴリかつ指定部分文字列を含む最初のフィクスチャ入力を返す。"""
    for text, cat in _SCOPE_ESCALATION_INPUTS:
        if cat == category and substring in text:
            return text
    return ""


def test_blocks_self_launched_subagent_wait_even_with_tracked_background(tmp_path: Path) -> None:
    """自身配下起動のレビュアー系サブエージェントへの待機表明はbackground追跡有無に関わらずblockする。"""
    text = _pick_scope_escalation_text_containing("async-wait", "plan-impl-reviewer")
    if not text:
        pytest.skip("scope-escalation fixture for self-launched subagent wait not available")
    transcript = str(_write_transcript(tmp_path, [_user_async_launched_entry("toolu_bg3")]))
    result = _run({"last_assistant_message": text, "transcript_path": transcript})
    body = json.loads(result.stdout)
    assert body["decision"] == "block"


def test_self_launched_subagent_wait_alias_is_shared_constant() -> None:
    """SSOT不一致の再発防止として`is`identityで共有定数のalias関係を検証する。"""
    assert _SELF_LAUNCHED_SUBAGENT_WAIT_RE is _ASYNC_WAIT_SELF_LAUNCHED_RE


def test_stop_hook_active_bypasses_check() -> None:
    """`stop_hook_active`真は判定処理をせず無条件approveを返す。

    通常なら縮退表明としてblockされる本文であっても、再呼び出し時は
    連続ブロック上限による強制終了を避けるため無条件approveを返す。
    """
    text = _pick_scope_escalation_text("single-session")
    if not text:
        pytest.skip("scope-escalation fixture for single-session not available")
    result = _run({"last_assistant_message": text, "stop_hook_active": True})
    body = json.loads(result.stdout)
    assert body.get("decision") == "approve"


def test_empty_message_blocks_as_empty_result() -> None:
    """空文字列の完了報告は`is_empty_completion_report`でblockする。"""
    result = _run({"last_assistant_message": ""})
    body = json.loads(result.stdout)
    assert body["decision"] == "block"


def test_whitespace_only_message_blocks_as_empty_result() -> None:
    """trim後空の完了報告は`is_empty_completion_report`でblockする。"""
    result = _run({"last_assistant_message": "   \n  \t  "})
    body = json.loads(result.stdout)
    assert body["decision"] == "block"
    assert "empty" in body["reason"]


def test_skill_invocation_only_blocks_as_empty_result() -> None:
    """`Skill`呼び出し単独の完了報告はblockする。"""
    result = _run({"last_assistant_message": "Skill(skill='foo')"})
    body = json.loads(result.stdout)
    assert body["decision"] == "block"
    assert "Skill" in body["reason"]


def test_named_subagent_without_main_send_blocks(tmp_path: Path) -> None:
    """named subagentが閾値以上のtool_useを実行しSendMessage(to='main')が無い場合blockする。"""
    tool_uses = [
        {"type": "tool_use", "name": "Read", "input": {}},
        {"type": "tool_use", "name": "Edit", "input": {}},
        {"type": "tool_use", "name": "Bash", "input": {}},
    ]
    transcript = _make_transcript(tmp_path, tool_uses)
    result = _run(
        {
            "last_assistant_message": "実装が完了した。差分は3ファイル。",
            "agent_name": "plan-impl-1",
            "transcript_path": transcript,
        }
    )
    body = json.loads(result.stdout)
    assert body["decision"] == "block"
    assert "SendMessage" in body["reason"]


def test_named_subagent_with_main_send_passes(tmp_path: Path) -> None:
    """SendMessage(to='main')送付済みnamed subagentは通過する。"""
    tool_uses = [
        {"type": "tool_use", "name": "Read", "input": {}},
        {"type": "tool_use", "name": "Edit", "input": {}},
        {"type": "tool_use", "name": "SendMessage", "input": {"to": "main", "message": "done"}},
    ]
    transcript = _make_transcript(tmp_path, tool_uses)
    result = _run(
        {
            "last_assistant_message": "完了報告を送付した。",
            "agent_name": "plan-impl-1",
            "transcript_path": transcript,
        }
    )
    assert result.stdout == ""
    assert result.returncode == 0


def test_short_lived_named_subagent_passes(tmp_path: Path) -> None:
    """tool_use数が閾値未満のnamed subagentは検査対象外。"""
    tool_uses = [
        {"type": "tool_use", "name": "Read", "input": {}},
    ]
    transcript = _make_transcript(tmp_path, tool_uses)
    result = _run(
        {
            "last_assistant_message": "対象ファイルを1件確認した。",
            "agent_name": "plan-impl-1",
            "transcript_path": transcript,
        }
    )
    assert result.stdout == ""
    assert result.returncode == 0


def test_unnamed_subagent_missing_send_passes(tmp_path: Path) -> None:
    """`agent_name`未指定（匿名subagent）はSendMessage検査対象外。"""
    tool_uses = [
        {"type": "tool_use", "name": "Read", "input": {}},
        {"type": "tool_use", "name": "Edit", "input": {}},
        {"type": "tool_use", "name": "Bash", "input": {}},
    ]
    transcript = _make_transcript(tmp_path, tool_uses)
    result = _run(
        {
            "last_assistant_message": "作業が完了した。",
            "agent_name": "",
            "transcript_path": transcript,
        }
    )
    assert result.stdout == ""
    assert result.returncode == 0


def test_named_subagent_send_to_other_target_blocks(tmp_path: Path) -> None:
    """SendMessage送付先が`main`以外の場合はメイン報告未送とみなしblockする。"""
    tool_uses = [
        {"type": "tool_use", "name": "Read", "input": {}},
        {"type": "tool_use", "name": "Edit", "input": {}},
        {"type": "tool_use", "name": "SendMessage", "input": {"to": "plan-impl-2", "message": "hi"}},
    ]
    transcript = _make_transcript(tmp_path, tool_uses)
    result = _run(
        {
            "last_assistant_message": "実装が完了した。",
            "agent_name": "plan-impl-1",
            "transcript_path": transcript,
        }
    )
    body = json.loads(result.stdout)
    assert body["decision"] == "block"


def test_skill_invocation_with_body_passes() -> None:
    """`Skill`呼び出し後に完了本文が続く正常報告はblockされない。"""
    text = "Skill(skill='foo')\n\n点検実施済。指摘なし。次工程へ移行する。"
    result = _run({"last_assistant_message": text})
    assert result.stdout == ""
    assert result.returncode == 0


def test_non_string_message_passes() -> None:
    """非文字列型の`last_assistant_message`は判定を通過する。"""
    result = _run({"last_assistant_message": None})
    assert result.stdout == ""
    assert result.returncode == 0


def test_named_subagent_without_main_send_logs_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`_inspect_named_subagent_send`検査のブロックケースで`append_stop_log`が呼ばれる。"""
    log_dir = tmp_path / "logdir"
    log_dir.mkdir()
    monkeypatch.setenv("TMPDIR", str(log_dir))
    tool_uses = [
        {"type": "tool_use", "name": "Read", "input": {}},
        {"type": "tool_use", "name": "Edit", "input": {}},
        {"type": "tool_use", "name": "Bash", "input": {}},
    ]
    transcript = _make_transcript(tmp_path, tool_uses)
    session_id = "test-session-block"
    _run(
        {
            "last_assistant_message": "実装が完了した。差分は3ファイル。",
            "agent_name": "plan-impl-1",
            "transcript_path": transcript,
            "session_id": session_id,
        }
    )
    log_path = log_dir / f"claude-agent-toolkit-stop-{session_id}.log"
    content = log_path.read_text(encoding="utf-8")
    assert "decision=block_named_subagent_missing_send" in content
    assert "agent_name=plan-impl-1" in content
    assert "tool_use_count=3" in content
    assert "has_main_send=False" in content


def test_named_subagent_with_main_send_logs_allow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`_inspect_named_subagent_send`検査の許可ケースで`append_stop_log`が呼ばれる。"""
    log_dir = tmp_path / "logdir"
    log_dir.mkdir()
    monkeypatch.setenv("TMPDIR", str(log_dir))
    tool_uses = [
        {"type": "tool_use", "name": "Read", "input": {}},
        {"type": "tool_use", "name": "Edit", "input": {}},
        {"type": "tool_use", "name": "SendMessage", "input": {"to": "main", "message": "done"}},
    ]
    transcript = _make_transcript(tmp_path, tool_uses)
    session_id = "test-session-allow"
    _run(
        {
            "last_assistant_message": "完了報告を送付した。",
            "agent_name": "plan-impl-1",
            "transcript_path": transcript,
            "session_id": session_id,
        }
    )
    log_path = log_dir / f"claude-agent-toolkit-stop-{session_id}.log"
    content = log_path.read_text(encoding="utf-8")
    assert "decision=allow_named_subagent_send" in content
    assert "has_main_send=True" in content


def test_named_subagent_fail_open_logs_allow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`_inspect_named_subagent_send`のfail-open（tool_use数閾値未満）ケースでも`append_stop_log`が呼ばれる。"""
    log_dir = tmp_path / "logdir"
    log_dir.mkdir()
    monkeypatch.setenv("TMPDIR", str(log_dir))
    tool_uses = [
        {"type": "tool_use", "name": "Read", "input": {}},
    ]
    transcript = _make_transcript(tmp_path, tool_uses)
    session_id = "test-session-fail-open"
    _run(
        {
            "last_assistant_message": "対象ファイルを1件確認した。",
            "agent_name": "plan-impl-1",
            "transcript_path": transcript,
            "session_id": session_id,
        }
    )
    log_path = log_dir / f"claude-agent-toolkit-stop-{session_id}.log"
    content = log_path.read_text(encoding="utf-8")
    assert "decision=allow_named_subagent_send" in content
    assert "tool_use_count=1" in content
    assert "has_main_send=False" in content


def _run_with_state_dir(payload: dict, state_dir: Path) -> subprocess.CompletedProcess[str]:
    """`session_state.py`の状態ファイル配置先を`state_dir`へ切り替えて`subagent_stop_advisor.py`を実行する。"""
    env = os.environ.copy()
    env["TMPDIR"] = str(state_dir)
    env["TEMP"] = str(state_dir)
    env["TMP"] = str(state_dir)
    return _fork_runner.run_script(_SCRIPT, input=json.dumps(payload), env=env)


def _write_flag_state(state_dir: Path, session_id: str, sub_session_id: str, subagent_type: str = "plan-impl-executor") -> None:
    """`plan_impl_executor_active_subagent_sessions`フラグを事前に書き込む。"""
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / f"claude-agent-toolkit-{session_id}.json"
    state_path.write_text(
        json.dumps(
            {
                "plan_impl_executor_active_subagent_sessions": {
                    sub_session_id: {"subagent_type": subagent_type, "started_at": 0.0},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _complete_report(**overrides: str) -> str:
    """`plan-impl-executor`「出力」節の主要欄を全て含む雛形報告を返す。"""
    fields = {
        "status": "completed",
        "summary": "全変更を反映",
        "changed": "- [x] item — /path",
        "verification": "- `pytest` — pass",
        "commit_sha": "abc123",
        "review_handoff": "実施完了（採用指摘0件反映）",
        "pending_confirmations": "なし",
        "plan_gaps": "なし",
    }
    fields.update(overrides)
    return "\n".join(f"{k}: {v}" if not v.startswith("-") else f"{k}:\n{v}" for k, v in fields.items())


class TestPlanImplExecutorReportFormat:
    """`plan-impl-executor`完了報告本文の主要欄ラベル存在検査。"""

    def test_flag_not_registered_passes_without_check(self, tmp_path: Path) -> None:
        """フラグ未登録時は書式検査を発火せず通過する。"""
        result = _run_with_state_dir(
            {"session_id": "sid-format-no-flag", "last_assistant_message": "実装完了"},
            tmp_path,
        )
        assert result.stdout == ""
        assert result.returncode == 0

    def test_complete_report_passes(self, tmp_path: Path) -> None:
        """主要欄が全て含まれる報告は通過する。"""
        sid = "sid-format-complete"
        _write_flag_state(tmp_path, sid, "sub-a")
        result = _run_with_state_dir(
            {"session_id": sid, "last_assistant_message": _complete_report()},
            tmp_path,
        )
        assert result.stdout == ""
        assert result.returncode == 0

    def test_missing_label_blocks(self, tmp_path: Path) -> None:
        """主要欄が欠落する報告はblockし理由文に欠落ラベルを列挙する。"""
        sid = "sid-format-missing"
        _write_flag_state(tmp_path, sid, "sub-b")
        report = _complete_report()
        # `plan_gaps:`行を除去する
        report = "\n".join(line for line in report.splitlines() if not line.startswith("plan_gaps"))
        result = _run_with_state_dir(
            {"session_id": sid, "last_assistant_message": report},
            tmp_path,
        )
        body = json.loads(result.stdout)
        assert body["decision"] == "block"
        assert "plan_gaps" in body["reason"]

    def test_needs_escalation_requires_blockers(self, tmp_path: Path) -> None:
        """`status: needs_escalation`検出時は`blockers`欄も必須。"""
        sid = "sid-format-needs-escalation"
        _write_flag_state(tmp_path, sid, "sub-c")
        report = _complete_report(status="needs_escalation")
        result = _run_with_state_dir(
            {"session_id": sid, "last_assistant_message": report},
            tmp_path,
        )
        body = json.loads(result.stdout)
        assert body["decision"] == "block"
        assert "blockers" in body["reason"]

    def test_needs_escalation_with_blockers_passes(self, tmp_path: Path) -> None:
        """`status: needs_escalation`かつ`blockers`欄あり報告は通過する。"""
        sid = "sid-format-escalation-ok"
        _write_flag_state(tmp_path, sid, "sub-d")
        report = _complete_report(status="needs_escalation") + "\nblockers:\n- 未解決事項"
        result = _run_with_state_dir(
            {"session_id": sid, "last_assistant_message": report},
            tmp_path,
        )
        assert result.stdout == ""
        assert result.returncode == 0

    def test_flag_entry_removed_after_check(self, tmp_path: Path) -> None:
        """SubagentStop発火時に該当エントリを状態辞書から削除する（E2Eサイクル）。"""
        sid = "sid-format-cleanup"
        _write_flag_state(tmp_path, sid, "sub-e")
        _run_with_state_dir(
            {"session_id": sid, "last_assistant_message": _complete_report()},
            tmp_path,
        )
        state_path = tmp_path / f"claude-agent-toolkit-{sid}.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state.get("plan_impl_executor_active_subagent_sessions") == {}

    def test_background_parallel_declaration_with_unchecked_item_blocks(self, tmp_path: Path) -> None:
        """FB[3]: background並列起動宣言と`changed`欄未消化項目が共起する完了報告をblockする。"""
        sid = "sid-format-bg-violation"
        _write_flag_state(tmp_path, sid, "sub-f")
        report = _complete_report(changed="- [ ] item — /path（run_in_background=trueで並列起動）")
        result = _run_with_state_dir(
            {"session_id": sid, "last_assistant_message": report},
            tmp_path,
        )
        body = json.loads(result.stdout)
        assert body["decision"] == "block"

    def test_background_parallel_declaration_with_all_checked_passes(self, tmp_path: Path) -> None:
        """全項目チェック済みならbackground並列起動宣言があっても通過する。"""
        sid = "sid-format-bg-ok"
        _write_flag_state(tmp_path, sid, "sub-g")
        report = _complete_report(changed="- [x] item — /path（run_in_background=trueで並列起動）")
        result = _run_with_state_dir(
            {"session_id": sid, "last_assistant_message": report},
            tmp_path,
        )
        assert result.stdout == ""
        assert result.returncode == 0

    def test_unchecked_item_outside_changed_section_does_not_block(self, tmp_path: Path) -> None:
        """FB[3]是正: `changed`欄以外の未チェック項目はbackground並列起動宣言と共起しても誤ってblockしない。"""
        sid = "sid-format-bg-outside-changed"
        _write_flag_state(tmp_path, sid, "sub-h")
        report = _complete_report(
            changed="- [x] item — /path（run_in_background=trueで並列起動）",
            blockers="- [ ] 未解決の論点",
        )
        result = _run_with_state_dir(
            {"session_id": sid, "last_assistant_message": report},
            tmp_path,
        )
        assert result.stdout == ""
        assert result.returncode == 0


def _write_explore_named_background_state(state_dir: Path, session_id: str, agent_name: str) -> None:
    """`explore_named_background_active_names`リストへ`agent_name`を事前登録する。"""
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / f"claude-agent-toolkit-{session_id}.json"
    state_path.write_text(
        json.dumps({"explore_named_background_active_names": [agent_name]}, ensure_ascii=False),
        encoding="utf-8",
    )


class TestExploreNamedBackgroundSend:
    """Explore named background起動の完了報告能動送付検査（閾値バイパス）。"""

    def test_registered_without_main_send_blocks_below_threshold(self, tmp_path: Path) -> None:
        """登録済みExploreがtool_use数1件でもSendMessage(to='main')が無ければblockする。"""
        sid = "sid-explore-missing"
        _write_explore_named_background_state(tmp_path, sid, "explore-1")
        transcript = _make_transcript(tmp_path, [{"type": "tool_use", "name": "Grep", "input": {}}])
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "agent_name": "explore-1",
                "last_assistant_message": "調査結果をまとめた。",
                "transcript_path": transcript,
            },
            tmp_path,
        )
        body = json.loads(result.stdout)
        assert body["decision"] == "block"
        assert "SendMessage" in body["reason"]

    def test_registered_with_main_send_passes(self, tmp_path: Path) -> None:
        """登録済みExploreがSendMessage(to='main')送付済みなら通過する。"""
        sid = "sid-explore-sent"
        _write_explore_named_background_state(tmp_path, sid, "explore-2")
        transcript = _make_transcript(
            tmp_path,
            [{"type": "tool_use", "name": "SendMessage", "input": {"to": "main", "message": "done"}}],
        )
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "agent_name": "explore-2",
                "last_assistant_message": "調査結果を送付した。",
                "transcript_path": transcript,
            },
            tmp_path,
        )
        assert result.stdout == ""
        assert result.returncode == 0

    def test_unregistered_agent_not_gated(self, tmp_path: Path) -> None:
        """未登録の`agent_name`は新規ゲート対象外（一般named subagent検査のみ適用）。"""
        sid = "sid-explore-unregistered"
        transcript = _make_transcript(tmp_path, [{"type": "tool_use", "name": "Grep", "input": {}}])
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "agent_name": "explore-3",
                "last_assistant_message": "調査結果をまとめた。",
                "transcript_path": transcript,
            },
            tmp_path,
        )
        assert result.stdout == ""
        assert result.returncode == 0

    def test_entry_removed_after_check(self, tmp_path: Path) -> None:
        """SubagentStop発火時に該当名を状態リストから消費（削除）する。"""
        sid = "sid-explore-cleanup"
        _write_explore_named_background_state(tmp_path, sid, "explore-4")
        transcript = _make_transcript(
            tmp_path,
            [{"type": "tool_use", "name": "SendMessage", "input": {"to": "main", "message": "done"}}],
        )
        _run_with_state_dir(
            {
                "session_id": sid,
                "agent_name": "explore-4",
                "last_assistant_message": "完了。",
                "transcript_path": transcript,
            },
            tmp_path,
        )
        state_path = tmp_path / f"claude-agent-toolkit-{sid}.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state.get("explore_named_background_active_names") == []

    def test_empty_agent_name_not_gated(self, tmp_path: Path) -> None:
        """`agent_name`が空文字列の場合は新規ゲート対象外として通過する。"""
        sid = "sid-explore-empty-agent-name"
        _write_explore_named_background_state(tmp_path, sid, "explore-5")
        transcript = _make_transcript(tmp_path, [{"type": "tool_use", "name": "Grep", "input": {}}])
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "agent_name": "",
                "last_assistant_message": "調査結果をまとめた。",
                "transcript_path": transcript,
            },
            tmp_path,
        )
        assert result.stdout == ""
        assert result.returncode == 0

    def test_empty_session_id_not_gated(self, tmp_path: Path) -> None:
        """`session_id`が空文字列の場合は新規ゲート対象外として通過する。"""
        transcript = _make_transcript(tmp_path, [{"type": "tool_use", "name": "Grep", "input": {}}])
        result = _run_with_state_dir(
            {
                "session_id": "",
                "agent_name": "explore-6",
                "last_assistant_message": "調査結果をまとめた。",
                "transcript_path": transcript,
            },
            tmp_path,
        )
        assert result.stdout == ""
        assert result.returncode == 0

    def test_concurrent_same_name_each_consumed_independently(self, tmp_path: Path) -> None:
        """同名の並行起動（2件登録）はSubagentStop発火ごとに1件ずつ消費される。"""
        sid = "sid-explore-concurrent"
        state_dir = tmp_path
        state_dir.mkdir(parents=True, exist_ok=True)
        state_path = state_dir / f"claude-agent-toolkit-{sid}.json"
        state_path.write_text(
            json.dumps({"explore_named_background_active_names": ["explore-7", "explore-7"]}, ensure_ascii=False),
            encoding="utf-8",
        )
        transcript_missing = _make_transcript(tmp_path, [{"type": "tool_use", "name": "Grep", "input": {}}])
        result_first = _run_with_state_dir(
            {
                "session_id": sid,
                "agent_name": "explore-7",
                "last_assistant_message": "1件目の調査結果。",
                "transcript_path": transcript_missing,
            },
            tmp_path,
        )
        body_first = json.loads(result_first.stdout)
        assert body_first["decision"] == "block"
        state_after_first = json.loads(state_path.read_text(encoding="utf-8"))
        assert state_after_first.get("explore_named_background_active_names") == ["explore-7"]

        transcript_sent = _make_transcript(
            tmp_path,
            [{"type": "tool_use", "name": "SendMessage", "input": {"to": "main", "message": "done"}}],
        )
        result_second = _run_with_state_dir(
            {
                "session_id": sid,
                "agent_name": "explore-7",
                "last_assistant_message": "2件目の調査結果を送付した。",
                "transcript_path": transcript_sent,
            },
            tmp_path,
        )
        assert result_second.stdout == ""
        state_after_second = json.loads(state_path.read_text(encoding="utf-8"))
        assert state_after_second.get("explore_named_background_active_names") == []

    def test_second_stop_after_consumption_not_regated(self, tmp_path: Path) -> None:
        """1件消費済みの名前へ対する2回目のSubagentStop発火は新規ゲート対象外として通過する。"""
        sid = "sid-explore-double-fire"
        _write_explore_named_background_state(tmp_path, sid, "explore-8")
        transcript_sent = _make_transcript(
            tmp_path,
            [{"type": "tool_use", "name": "SendMessage", "input": {"to": "main", "message": "done"}}],
        )
        _run_with_state_dir(
            {
                "session_id": sid,
                "agent_name": "explore-8",
                "last_assistant_message": "完了。",
                "transcript_path": transcript_sent,
            },
            tmp_path,
        )
        result_second = _run_with_state_dir(
            {
                "session_id": sid,
                "agent_name": "explore-8",
                "last_assistant_message": "再度の完了報告。",
                "transcript_path": transcript_sent,
            },
            tmp_path,
        )
        assert result_second.stdout == ""
        assert result_second.returncode == 0

    def test_unreadable_transcript_not_consumed_allows_retry(self, tmp_path: Path) -> None:
        """transcript読み取り不能時はfail-openで通過し、状態のエントリは消費せず残す。"""
        sid = "sid-explore-unreadable"
        _write_explore_named_background_state(tmp_path, sid, "explore-9")
        missing_transcript_path = str(tmp_path / "does-not-exist.jsonl")
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "agent_name": "explore-9",
                "last_assistant_message": "調査結果をまとめた。",
                "transcript_path": missing_transcript_path,
            },
            tmp_path,
        )
        assert result.stdout == ""
        assert result.returncode == 0
        state_path = tmp_path / f"claude-agent-toolkit-{sid}.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state.get("explore_named_background_active_names") == ["explore-9"]
