"""欠陥指摘のターンで`agent-basics:bugfix`を起動せずに報告する操作を、PreToolUseとStopが同じ判定で遮断することを確かめる。"""

import json
import pathlib

import pytest

from agent_toolkit._hooks import bugfix_gate, posttooluse, stop
from agent_toolkit._hooks.pretooluse import dispatch, operation_skills
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process, codex_execution_payload

pytestmark = pytest.mark.usefixtures("without_skill_notices")

_SEND_TO_USER = "mcp__plugin_agent-toolkit_agent-toolkit__send_to_user"


def _human(text: str) -> dict:
    return {"type": "user", "origin": {"kind": "human"}, "message": {"content": text}}


def _tool_use(name: str, tool_input: dict) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": name, "input": tool_input}]}}


_BUGFIX = _tool_use("Skill", {"skill": "agent-basics:bugfix"})


@pytest.fixture(autouse=True)
def _main_session(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AGENT_TOOLKIT_DELEGATED_SESSION", "AGENT_TOOLKIT_OWNER_SESSION", "AGENT_TOOLKIT_HANDOFF_PATH"):
        monkeypatch.delenv(name, raising=False)


def _payload(entries: list[dict], tmp_path: pathlib.Path, **extra: object) -> dict:
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries), encoding="utf-8")
    return {"session_id": "bugfix-gate", "transcript_path": str(path), "cwd": str(tmp_path), **extra}


def _pretooluse_denied(payload: dict) -> bool:
    result = _run_hook_in_process("pretooluse", {**payload, "tool_name": _SEND_TO_USER, "tool_input": {"message": "原因は…"}})
    assert result.returncode == 0 and not result.stderr
    output = json.loads(result.stdout)["hookSpecificOutput"] if result.stdout.strip() else {}
    if output.get("permissionDecision") == "deny":
        assert "agent-basics:bugfix" in output["permissionDecisionReason"]
        return True
    return False


@pytest.mark.parametrize(
    "prompt,expected",
    [
        ("ここの処理がバグっている", True),
        ("この出力は間違ってるよ", True),
        ("the output is broken", True),
        ("進捗を教えて", False),
        ("貼り付けたコード:\n```\n# バグ\n```\n進捗は？", False),
        ("`バグ`という語の意味を教えて", False),
        ("> おかしい\n続けて", False),
    ],
)
def test_defect_report_judgement(prompt: str, expected: bool) -> None:
    """欠陥指摘の語を本文から判定し、貼り付けた素材（コードブロック・インラインコード・引用）の中の語は数えない。"""
    assert bugfix_gate.is_defect_report(prompt) is expected


def test_send_to_user_is_denied_until_bugfix_is_invoked(tmp_path: pathlib.Path) -> None:
    """欠陥指摘の後にbugfixを起動せず`send_to_user`を呼ぶと遮断され、起動後は通る。"""
    assert _pretooluse_denied(_payload([_human("ここがバグっている")], tmp_path))
    assert not _pretooluse_denied(_payload([_human("ここがバグっている"), _BUGFIX], tmp_path))


def test_send_to_user_without_defect_report_or_by_subagent_is_allowed(tmp_path: pathlib.Path) -> None:
    """欠陥指摘ではない発話、前のターンの起動、サブエージェントの呼出しは遮断しない。"""
    assert not _pretooluse_denied(_payload([_human("進捗を教えて")], tmp_path))
    assert _pretooluse_denied(_payload([_BUGFIX, _human("まだバグっている")], tmp_path))
    assert not _pretooluse_denied(_payload([_human("ここがバグっている")], tmp_path, agent_id="sub-1"))


def _stop(entries: list[dict], tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, **extra: object) -> dict:
    monkeypatch.setattr(stop, "CHECK_MODULE_NAMES", ("bugfix_gate",))
    return stop.evaluate(json.dumps(_payload(entries, tmp_path, **extra)))


def test_stop_blocks_direct_answer_without_bugfix_and_respects_recursion_guard(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """応答本文だけで終えるStopも同じ判定で遮断し、bugfix起動後と`stop_hook_active`の再呼出しでは通す。"""
    entries = [_human("ここがバグっている")]
    blocked = _stop(entries, tmp_path, monkeypatch)
    assert blocked["decision"] == "block" and "agent-basics:bugfix" in str(blocked["reason"])
    assert "起動し" in str(blocked["reason"]) and "診断してから報告せよ" in str(blocked["reason"])
    assert "起動すれば同じ操作を通せる" not in str(blocked["reason"])
    assert "decision" not in _stop([*entries, _BUGFIX], tmp_path, monkeypatch)
    assert "decision" not in _stop(entries, tmp_path, monkeypatch, stop_hook_active=True)
    assert "decision" not in _stop([_human("進捗を教えて")], tmp_path, monkeypatch)


@pytest.mark.parametrize(
    "correction",
    [
        "手戻り多すぎない？",
        "ちゃんと分析して直すべきでしょ。",
        "処理する手順になってなかったっけ？",
        "自分でできるはずでしょ。ユーザーの手を煩わせないで！",
        "再発防止しないとまた再発するでしょ！！",
    ],
)
def test_each_queued_correction_needs_its_own_bugfix(
    correction: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同じホストターンの先行する起動は、新しい割り込みの遮断を解除しない。"""
    entries = [
        _human("ここがバグっている"),
        _BUGFIX,
        {"type": "attachment", "attachment": {"type": "queued_command", "origin": {"kind": "human"}, "prompt": correction}},
    ]
    assert _pretooluse_denied(_payload(entries, tmp_path))
    assert _stop(entries, tmp_path, monkeypatch)["decision"] == "block"
    assert not _pretooluse_denied(_payload([*entries, _BUGFIX], tmp_path))
    assert "decision" not in _stop([*entries, _BUGFIX], tmp_path, monkeypatch)


@pytest.mark.parametrize("previous_skill", [False, True])
@pytest.mark.parametrize("args,defect", [("進捗を教えて", False), ("まだバグがある", True)])
def test_command_arguments_and_bugfix_invocation_belong_to_current_turn(
    previous_skill: bool, args: str, defect: bool, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """包装の欠陥語と旧ターンの指摘・起動を除き、今回のコマンド引数だけを両入口で判定する。"""
    entries = [_human("以前のバグを直して")]
    if previous_skill:
        entries.append(_BUGFIX)
    entries.append(
        {
            "type": "user",
            "promptId": "command-turn",
            "message": {
                "content": "<command-name>/goal</command-name>\n<command-message>bugfix</command-message>\n"
                f"<command-args>{args}</command-args>"
            },
        }
    )
    assert _pretooluse_denied(_payload(entries, tmp_path)) is defect
    assert (_stop(entries, tmp_path, monkeypatch).get("decision") == "block") is defect
    assert not _pretooluse_denied(_payload([*entries, _BUGFIX], tmp_path))
    assert "decision" not in _stop([*entries, _BUGFIX], tmp_path, monkeypatch)


@pytest.mark.parametrize("actor", ["main", "lane"])
def test_verification_failure_requires_same_actor_bugfix_before_changes(tmp_path, monkeypatch, actor):
    """初回失敗・他主体の起動・同主体の起動・新しい失敗を公開hook列で区別する。"""
    base = {"session_id": "verification-flow", "cwd": str(tmp_path), "agent_id": actor}
    failure = {
        **base,
        "hook_event_name": "PostToolUse",
        "tool_use_id": "failed-1",
        "tool_name": "Bash",
        "tool_input": {"command": "uv run --frozen pytest sample_test.py"},
        "tool_response": {"exit_code": 1},
    }
    edit: dict = {
        **base,
        "tool_name": "Edit",
        "tool_input": {"file_path": str(tmp_path / "sample.py"), "old_string": "before", "new_string": "after"},
    }
    (tmp_path / "sample.py").write_text("before", encoding="utf-8")

    def pre(*, public=False):
        if not public:
            return "verification-bugfix" in bugfix_gate.verification_notice(
                edit, edit["tool_name"], edit["tool_input"], base["session_id"]
            )
        result = _run_hook_in_process("pretooluse", edit)
        assert result.returncode == 0
        body = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
        assert body.get("permissionDecision") != "deny"
        return "verification-bugfix" in body.get("additionalContext", "")

    monkeypatch.setattr(dispatch, "_decide", lambda *_args, **_kwargs: dispatch.Decision())
    # Skill本文の取得記録は専用の受領試験へ分担し、この遷移では全宣言の解析を繰り返さない。
    monkeypatch.setattr(operation_skills, "notices", lambda *_args, **_kwargs: [])
    assert not pre()
    posttooluse.main(json.dumps(failure))
    assert pre(public=True)
    skill = {
        **base,
        "hook_event_name": "PostToolUse",
        "tool_name": "Skill",
        "tool_input": {"skill": "agent-basics:bugfix"},
        "tool_response": "起動成功",
    }
    posttooluse.main(json.dumps({**skill, "agent_id": "other"}))
    assert bugfix_gate.read_state("verification-flow")[bugfix_gate.FAILURE_STATE_KEY][actor]["pending"]
    posttooluse.main(json.dumps(skill))
    assert not pre()
    posttooluse.main(json.dumps(failure))
    assert not pre()  # 同じ失敗の再配送で解除済みの印を戻さない。
    posttooluse.main(json.dumps({**failure, "tool_use_id": "failed-2"}))
    assert pre()


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize("code,pending", [(0, False), (1, True), (2, False), (3, False), (4, False), (6, True)])
def test_ci_failure_is_distinguished_from_waiting_and_missing_runs(tmp_path, code, pending, host):
    """CI失敗1/6だけを保持し、待機の終了を不具合の失敗にしない。"""
    payload = {
        "session_id": "ci-failure",
        "tool_use_id": "ci-1",
        "hook_event_name": "PostToolUseFailure" if code else "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "atk run-script wait-ci -- --baseline baseline.json"},
        "tool_response": {"exit_code": code},
    }
    if host == "codex":
        payload.update(turn_id="ci-turn", tool_response="CIの終了状態を確認した")
        payload = codex_execution_payload(payload, tmp_path, exit_code=code)
    posttooluse.main(json.dumps(payload))
    current = bugfix_gate.read_state("ci-failure").get(bugfix_gate.FAILURE_STATE_KEY, {}).get("main", {})
    assert bool(current.get("pending")) is pending
    notice = bugfix_gate.verification_notice(payload, "Bash", {"command": "git commit -m fix"}, "ci-failure")
    assert bool(notice) is pending


@pytest.mark.parametrize("field", ["tool_use_id", "turn_id", "session_id"])
def test_ci_failure_uses_only_the_same_native_execution(tmp_path, field):
    """別の実行・ターン・主体の失敗や本文中の終了コードを、現在のCI失敗に流用しない。"""
    payload = codex_execution_payload(
        {
            "session_id": "ci-failure",
            "turn_id": "ci-turn",
            "tool_use_id": "ci-1",
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Bash",
            "tool_input": {"command": "atk run-script wait-ci -- --baseline baseline.json"},
            "tool_response": "exit_code: 1",
        },
        tmp_path,
        exit_code=1,
    )
    payload[field] = "different"
    posttooluse.main(json.dumps(payload))
    assert not bugfix_gate.read_state(payload["session_id"]).get(bugfix_gate.FAILURE_STATE_KEY)
