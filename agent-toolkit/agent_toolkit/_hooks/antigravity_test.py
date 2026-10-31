"""Antigravityの公開入力と応答形式、共通判定への接続を確かめる。"""

import io
import json
import pathlib
from typing import Any

import pytest

from agent_toolkit import hook
from agent_toolkit._common import session_state
from agent_toolkit._hooks import antigravity, host, plugin_resources
from agent_toolkit._hooks.antigravity import _Response


@pytest.fixture(autouse=True)
def _isolate_activity(monkeypatch: pytest.MonkeyPatch) -> None:
    """検証プロセス自身の祖先を会話の実行主体として記録しない。"""
    monkeypatch.setattr(antigravity.session_activity, "record_activity", lambda *args, **kwargs: None)


def _payload(tmp_path: pathlib.Path, **kwargs: Any) -> dict[str, Any]:
    return {
        "conversationId": "agy-test-conversation",
        "workspacePaths": [str(tmp_path)],
        "transcriptPath": str(tmp_path / "transcript.jsonl"),
        "modelName": "gemini-4-argon-medium",
        **kwargs,
    }


def _run(payload: dict[str, Any], event: str, capsys: pytest.CaptureFixture[str]) -> dict:
    assert antigravity.main(json.dumps(payload), event=event) == 0
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("plugin", ["agent-toolkit", "agent-basics"])
@pytest.mark.parametrize("relative", ["SKILL.md", "references/search.md", "references/antigravity-full-instructions.md"])
@pytest.mark.parametrize("partial", [False, True])
def test_read_keeps_document_owner_for_shared_observation(tmp_path, monkeypatch, plugin, relative, partial):
    """全文を返した`SKILL.md`の読取だけを、資料の所有者付きでSkill観測へ渡す。部分取得と参照資料は渡さない。"""
    root = tmp_path / plugin
    manifest = root / ".claude-plugin/plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"name": plugin}), encoding="utf-8")
    (root / "plugin.json").write_text(json.dumps({"name": plugin}), encoding="utf-8")
    path = root / "skills/search" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    header = "---\nname: search\n---\n" if relative == "SKILL.md" else "# 検索の本文\n"
    path.write_text(header + "末尾の本文\n", encoding="utf-8")
    observed = []

    def invoke(_command, payload):
        observed.append(payload)
        return _Response(0, {}, "")

    monkeypatch.setattr(antigravity, "_invoke", invoke)
    payload = {
        "session_id": "owner-read",
        "tool_input": {"file_path": str(path)},
        "tool_response": header if partial else path.read_text(encoding="utf-8"),
    }
    assert not antigravity._record_read(payload)  # noqa: SLF001  # pylint: disable=protected-access
    if not partial and relative != "references/search.md":
        assert observed[0]["tool_input"] == {"skill": f"{plugin}:search"}
    else:
        assert not observed


@pytest.mark.parametrize(
    ("tool", "args", "name", "field", "expected"),
    [
        ("run_command", {"CommandLine": '"echo ok"'}, "Bash", "command", "echo ok"),
        ("read_file", {"Path": '"/example/a.py"'}, "Read", "file_path", "/example/a.py"),
        ("write_file", {"Content": '"本文"'}, "Write", "content", "本文"),
        ("edit_file", {"Replacement": '"新しい本文"'}, "Edit", "new_string", "新しい本文"),
        (
            "ask_question",
            {"questions": '[{"question":"質問"}]'},
            "AskUserQuestion",
            "questions",
            [{"question": "質問", "options": []}],
        ),
        ("invoke_subagent", {"Prompt": '"調査せよ"'}, "Agent", "prompt", "調査せよ"),
        ("manage_subagents", {"Action": '"stop"', "SubagentId": '"child-1"'}, "TaskStop", "agent_id", "child-1"),
        ("manage_task", {"Action": "cancel", "TaskId": "task-1"}, "TaskStop", "task_id", "task-1"),
    ],
)
def test_normalize_protojson(tmp_path: pathlib.Path, tool: str, args: dict, name: str, field: str, expected: object) -> None:
    """JSON文字列と展開済み引数を、共通判定の読み手へそろえる。"""
    raw = _payload(tmp_path, toolCall={"name": tool, "args": args, "result": '{"stdout":"本文"}'})
    normalized = antigravity.normalize_payload(raw, "PreToolUse")
    assert normalized["tool_name"] == name
    assert normalized["tool_input"][field] == expected
    assert normalized["session_id"] == raw["conversationId"]
    assert normalized["tool_response"] == {"stdout": "本文"}
    assert host.is_antigravity_payload(normalized)
    assert not host.is_codex_payload({**normalized, "prompt": "本文"})


def test_real_pretooluse_blocks_pattern_kill(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """既存の危険コマンド判定がagyでもdenyを返す。"""
    raw = _payload(tmp_path, toolCall={"name": "run_command", "args": {"CommandLine": '"pkill -f worker"'}})
    result = _run(raw, "PreToolUse", capsys)
    assert result["decision"] == "deny"
    assert "pkill" in result["reason"]


@pytest.mark.parametrize("response", [_Response(2, {}, ""), _Response(0, {"decision": "block", "reason": ""}, "")])
def test_pretooluse_denial_without_reason_reports_next_action(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, response: _Response
) -> None:
    monkeypatch.setattr(antigravity, "_invoke", lambda *_: response)
    result = _run(_payload(tmp_path), "PreToolUse", capsys)
    assert result["decision"] == "deny"
    assert "理由を返さず" in result["reason"]
    assert "委譲元" in result["reason"] and "ユーザー" in result["reason"] and "報告せよ" in result["reason"]


@pytest.mark.parametrize(
    ("response", "expected"),
    [(_Response(2, {"reason": "理由"}, "診断"), "診断"), (_Response(0, {"decision": "deny", "reason": "理由"}, ""), "理由")],
)
def test_pretooluse_denial_preserves_inner_reason(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    response: _Response,
    expected: str,
) -> None:
    monkeypatch.setattr(antigravity, "_invoke", lambda *_: response)
    assert _run(_payload(tmp_path), "PreToolUse", capsys) == {"decision": "deny", "reason": expected}


def test_session_start_defers_context(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """SessionStartのstdoutを空objectに保ち、次のInvocationで共通文脈を配送する。"""
    raw = _payload(tmp_path)
    assert _run(raw, "SessionStart", capsys) == {}
    state = session_state.read_state(raw["conversationId"])
    assert state["agy_cwd"] == str(tmp_path)
    assert state[antigravity.PENDING_CONTEXTS]
    delivered = _run(raw, "PreInvocation", capsys)
    assert any("agent-toolkit" in step["ephemeralMessage"] for step in delivered["injectSteps"])
    assert _run(raw, "PostInvocation", capsys) == {}


def test_preinvocation_reads_user_once(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """同じユーザー発話を複数モデル呼出しのたびに再処理しない。"""
    raw = _payload(tmp_path)
    pathlib.Path(raw["transcriptPath"]).write_text(
        json.dumps(
            {
                "type": "USER_INPUT",
                "source": "USER_EXPLICIT",
                "step_index": 9,
                "content": "<USER_REQUEST>\nユーザーの要求\n</USER_REQUEST>\n<metadata>付随情報</metadata>",
            }
        ),
        encoding="utf-8",
    )
    received: list[dict] = []

    def invoke(module_name: str, payload: dict) -> _Response:
        received.append(payload)
        assert module_name == "user_prompt_submit"
        return _Response(0, {"hookSpecificOutput": {"additionalContext": "入力の通知"}}, "")

    monkeypatch.setattr(antigravity, "_invoke", invoke)
    assert _run(raw, "PreInvocation", capsys) == {"injectSteps": [{"ephemeralMessage": "入力の通知"}]}
    assert received[0]["prompt"] == "ユーザーの要求"
    assert received[0]["source"] == "user"
    assert _run(raw, "PreInvocation", capsys) == {}
    assert len(received) == 1


@pytest.mark.parametrize("event", ["PreToolUse", "PostToolUse"])
def test_tool_notices_arrive_at_postinvocation(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, event: str
) -> None:
    """出力欄を持たないツールイベントの警告を、後続Invocationへ一度だけ届ける。"""

    def invoke(*_args: Any, **_kwargs: Any) -> _Response:
        return _Response(0, {"hookSpecificOutput": {"additionalContext": "警告本文"}}, "")

    monkeypatch.setattr(antigravity, "_invoke", invoke)
    raw = _payload(tmp_path, toolCall={"name": "run_command", "args": {"CommandLine": "echo ok"}})
    assert _run(raw, event, capsys) == ({"decision": "allow"} if event == "PreToolUse" else {})
    assert _run(raw, "PostInvocation", capsys) == {"injectSteps": [{"ephemeralMessage": "警告本文"}]}
    assert _run(raw, "PostInvocation", capsys) == {}


@pytest.mark.parametrize("body", [{}, {"hookSpecificOutput": {"permissionDecision": "allow"}}])
def test_pretooluse_pass_has_required_native_decision(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, body: dict
) -> None:
    """通常通過にもAGYの必須decisionを返し、理由のない拒否を生まない。"""
    monkeypatch.setattr(antigravity, "_invoke", lambda *_args: _Response(0, body, ""))
    raw = _payload(tmp_path, toolCall={"name": "run_command", "args": {"CommandLine": "echo ok"}})
    assert _run(raw, "PreToolUse", capsys) == {"decision": "allow"}


def test_real_stop_continues_for_response_language(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """共通Stop判定のblockを、agyのcontinueへ変換する。"""
    raw = _payload(tmp_path, terminationReason="NO_TOOL_CALL")
    pathlib.Path(raw["transcriptPath"]).write_text(
        json.dumps(
            {
                "type": "PLANNER_RESPONSE",
                "step_index": 1,
                "content": "I have completed all the requested changes and verified the implementation successfully.",
            }
        ),
        encoding="utf-8",
    )
    result = _run(raw, "Stop", capsys)
    assert result["decision"] == "continue"
    assert "日本語" in result["reason"]
    assert session_state.read_state(raw["conversationId"])[antigravity.STOP_CONTINUING] is True
    assert _run({**raw, "terminationReason": "USER_CANCELED"}, "Stop", capsys) == {"decision": "allow"}


@pytest.mark.parametrize("reason", ["NO_TOOL_CALL", "USER_CANCELED", "ERROR", "MAX_TOKEN_BUDGET_EXCEEDED"])
def test_stop_permission_has_required_native_decision(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    """正常停止と中断も必須decisionを明示して停止を許可する。"""
    monkeypatch.setattr(antigravity, "_invoke", lambda *_args: _Response(0, {}, ""))
    assert _run(_payload(tmp_path, terminationReason=reason), "Stop", capsys) == {"decision": "allow"}


@pytest.mark.parametrize("encoded", [True, False])
def test_native_subagent_receives_rules(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], encoded: bool) -> None:
    """invoke_subagentのPromptへ委譲先の規範を追加し、引数の符号化を保つ。"""
    prompt = json.dumps("調査せよ") if encoded else "調査せよ"
    raw = _payload(tmp_path, toolCall={"name": "invoke_subagent", "args": {"Prompt": prompt}})
    skill = plugin_resources.skill_document_path("delegation", "SKILL.md")
    initial = _run(raw, "PreToolUse", capsys)
    assert initial["decision"] == "allow"
    delivered = json.dumps(_run(raw, "PostInvocation", capsys), ensure_ascii=False)
    assert f"cat -- {skill}" in delivered
    read = _payload(
        tmp_path,
        toolCall={
            "name": "read_file",
            "args": {"Path": str(skill)},
            "result": skill.read_text(encoding="utf-8"),
        },
    )
    assert _run(read, "PostToolUse", capsys) == {}
    result = _run(raw, "PreToolUse", capsys)
    assert result["decision"] == "allow"
    assert "overwrite" in result, result
    rewritten = result["overwrite"]["Prompt"]
    rewritten = json.loads(rewritten) if encoded else rewritten
    assert rewritten.startswith("調査せよ")
    assert "rules-subagent" in rewritten
    assert 'kind="rules-subagent"' in rewritten
    for _ in range(12):
        if not _run(raw, "PostInvocation", capsys):
            break
        assert _run(raw, "PreToolUse", capsys)["decision"] == "allow"
    else:
        pytest.fail("関連資料の配送が完了しなかった")
    assert _run(raw, "PreToolUse", capsys)["decision"] == "allow"
    assert _run(raw, "PostInvocation", capsys) == {}


def test_hook_entry_accepts_host_and_event(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """配布のatk-hook antigravity Eventと同じ引数で応答を得る。"""
    raw = _payload(tmp_path, toolCall={"name": "run_command", "args": {"CommandLine": "pkill -f worker"}})
    stdin = io.TextIOWrapper(io.BytesIO(json.dumps(raw).encode()), encoding="utf-8")
    monkeypatch.setattr(hook.sys, "stdin", stdin)
    assert hook.main(["antigravity", "PreToolUse"]) == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "deny"


def test_invalid_session_id_never_selects_state_path(tmp_path: pathlib.Path) -> None:
    """ホスト入力の識別子から状態領域外へ書き込まない。"""
    with pytest.raises(ValueError, match="conversationId"):
        antigravity.main(json.dumps(_payload(tmp_path, conversationId="../escape")), event="SessionStart")


def test_native_children_are_owned_and_reconciled(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """親の起動応答と欠落のないactive一覧で、背景の子の所有を更新する。"""
    raw = _payload(
        tmp_path,
        toolCall={
            "name": "invoke_subagent",
            "args": {"Prompt": "調査せよ"},
            "result": 'Created the following subagent:\n{\n"conversationId": "child-1"\n}',
        },
    )
    assert _run(raw, "PostToolUse", capsys) == {}
    state = session_state.read_state(raw["conversationId"])
    assert state[antigravity.NATIVE_SUBAGENTS] == ["child-1"]
    assert "child-1" in state["background_task_ids"]
    raw["toolCall"] = {"name": "manage_subagents", "args": {"Action": "list"}, "result": "You have 0 active subagent(s):\n[]"}
    assert _run(raw, "PostToolUse", capsys) == {}
    state = session_state.read_state(raw["conversationId"])
    assert not state[antigravity.NATIVE_SUBAGENTS]
    # 起動の所有履歴は既存のTTLまで保持し、稼働中の子の集合を別に更新する。
    assert "child-1" in state["background_task_ids"]


def test_shared_decoder_preserves_string_content(tmp_path: pathlib.Path) -> None:
    """二度デコードすると型が変わる本文も、共通ツール正規化で文字列として保つ。"""
    raw = _payload(tmp_path, toolCall={"name": "write_file", "args": {"Content": '"1"'}})
    normalized = antigravity.normalize_payload(raw, "PreToolUse")
    assert normalized["tool_input"]["content"] == "1"


def test_question_answer_keeps_multiline_feedback(tmp_path: pathlib.Path) -> None:
    """質問への自由記述も、ユーザー発話と同じ通知判定へ全文で渡す。"""
    raw = _payload(
        tmp_path,
        toolCall={
            "name": "ask_question",
            "args": {"questions": '[{"question":"方針は？"}]'},
            "result": "Created At: 時刻\nA1: 一行目\n二行目！！",
        },
    )
    normalized = antigravity.normalize_payload(raw, "PostToolUse")
    assert normalized["tool_response"]["answers"] == {"方針は？": "一行目\n二行目！！"}


def test_native_manual_plan_skill_is_observed(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """ツール名`Skill`を提供しないagyでも、ユーザーが起動した計画スキルを記録する。"""
    raw = _payload(tmp_path)
    pathlib.Path(raw["transcriptPath"]).write_text(
        json.dumps(
            {
                "type": "USER_INPUT",
                "source": "USER_EXPLICIT",
                "step_index": 0,
                "content": "<USER_REQUEST>\n/agent-toolkit:plan-mode 計画して\n</USER_REQUEST>",
            }
        ),
        encoding="utf-8",
    )
    _run(raw, "PreInvocation", capsys)
    assert session_state.read_state(raw["conversationId"])["plan_mode_skill_invoked"] is True


def test_native_async_offsets_keep_original_bytes(tmp_path: pathlib.Path) -> None:
    """hook解析写しの呼出IDを、元JSONLの開始バイト位置へ対応付ける。"""
    from agent_toolkit._hooks import background_tasks  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    path = tmp_path / "transcript_full.jsonl"
    first = json.dumps(
        {"type": "USER_INPUT", "source": "USER_EXPLICIT", "step_index": 0, "content": "依頼"}, ensure_ascii=False
    )
    second = json.dumps(
        {
            "type": "PLANNER_RESPONSE",
            "source": "MODEL",
            "step_index": 1,
            "tool_calls": [{"name": "run_command", "args": {"CommandLine": '"echo ok"'}}],
        },
        ensure_ascii=False,
    )
    path.write_text(first + "\n" + second + "\n", encoding="utf-8")
    assert background_tasks.async_launch_offsets(str(path))["agy-1-0"] == len((first + "\n").encode())
