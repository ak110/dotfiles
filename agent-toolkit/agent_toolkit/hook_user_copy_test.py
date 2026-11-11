"""エージェント宛ての通知をユーザーの画面へ写す条件と文面を、hookの入口から確かめるテスト。"""

import importlib
import json
from collections.abc import Callable

import pytest

from agent_toolkit._hooks import message_format, notice
from agent_toolkit._testing import hook_output_contract
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process

_WARNING_BODY = "読取の診断を捨てている。\n次の操作: 2>/dev/nullを外せ。"
_TIMING_BODY = "実行時間: 1.2秒"
_HEADING = "agent-toolkitのhookがエージェントへ通知した内容"
_WARNING_COPY = f"{_HEADING}（pretooluse・warn）:\n{_WARNING_BODY}"


@pytest.fixture(autouse=True)
def _main_session(monkeypatch):
    """委譲先の環境変数を外し、最上位メインのセッションとして呼ぶ。"""
    for name in ("AGENT_TOOLKIT_DELEGATED_SESSION", "AGENT_TOOLKIT_OWNER_SESSION"):
        monkeypatch.delenv(name, raising=False)


def _warning() -> str:
    return message_format.llm_notice(_WARNING_BODY, "pretooluse", tag="warn")


def _timing() -> str:
    """実際のhookと同じく、実行時間の通知へ写さない印を付けて返す。"""
    return notice.informational(message_format.llm_notice(_TIMING_BODY, "command-timing"))


def _response(*notices: str, event: str = "PreToolUse") -> dict:
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": "\n".join(notices)}}


def _deliver(monkeypatch, subcommand: str, build: Callable[[], dict], payload: dict | None = None) -> tuple[dict, dict]:
    """イベントの処理が`build`で通知を生成して応答を返す状態で公開hookを呼び、処理の応答とホストへ渡る応答を返す。

    通知は実際のhookと同じく、hookの呼出しの中で生成する。
    """
    built: dict = {}

    def main(_payload, **_kwargs) -> int:
        built.update(build())
        print(json.dumps(built, ensure_ascii=False))
        return 0

    monkeypatch.setattr(importlib.import_module(f"agent_toolkit._hooks.{subcommand}"), "main", main)
    result = _run_hook_in_process(subcommand, payload or {"session_id": "user-copy"})
    assert result.returncode == 0 and not result.stderr
    return built, json.loads(result.stdout)


@pytest.mark.parametrize(
    ("subcommand", "event"),
    [("pretooluse", "PreToolUse"), ("posttooluse", "PostToolUse"), ("user_prompt_submit", "UserPromptSubmit")],
)
def test_corrective_notice_is_copied_in_full_without_wrapper(monkeypatch, subcommand, event):
    """警告・案内の本文を、包装を外した全文と生成元・種別の見出しで写し、エージェント宛ての欄は変えない。"""
    built, delivered = _deliver(monkeypatch, subcommand, lambda: _response(_warning(), event=event))
    assert delivered["hookSpecificOutput"] == built["hookSpecificOutput"]
    assert delivered["systemMessage"] == _WARNING_COPY
    assert not hook_output_contract.validate_hook_output(event, delivered)


@pytest.mark.parametrize(
    "quoted",
    [
        pytest.param(lambda: message_format.llm_notice("入力の引用", "sample"), id="whole-notice"),
        pytest.param(lambda: "入力の末尾\n</atk-auto>", id="closing-only"),
        pytest.param(lambda: '<atk-auto source="sample" kind="notice">\n途中までの入力', id="opening-only"),
        pytest.param(
            lambda: '入力の途中\n</atk-auto>\n<atk-auto source="sample" kind="notice">\n引用の末尾', id="closing-then-opening"
        ),
    ],
)
def test_notice_quoting_a_wrapper_keeps_each_notice_and_exclusion(monkeypatch, quoted):
    """本文が包装と同じ字面を引用しても、各通知の全文と生成元を保ち、後続の非表示の通知を写しへ混ぜない。

    引用の後に是正の通知と実行時間の通知が続く応答で、写しが是正の2通知の全文だけから成ることを確かめる。
    """
    bodies: list[str] = []

    def build() -> dict:
        body = f"入力に包装が含まれている。\n{quoted()}\n次の操作: 資料を読め。"
        bodies.append(body)
        return _response(message_format.llm_notice(body, "pretooluse", tag="warn"), _timing(), _warning())

    _built, delivered = _deliver(monkeypatch, "pretooluse", build)
    assert delivered["systemMessage"] == f"{_HEADING}（pretooluse・warn）:\n{bodies[0]}\n\n{_WARNING_COPY}"


def test_denial_reason_and_multiple_notices_are_merged_into_one_message(monkeypatch):
    """拒否の理由も写し、同じ応答の複数の通知を1つへまとめ、既存のユーザー宛ての本文を保つ。"""

    def build() -> dict:
        return {
            "systemMessage": "既存の通知",
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": message_format.llm_notice(
                    "blocked: 所有を確認できない。\n次の操作: PIDで止めよ。", "pretooluse", tag="block"
                ),
                "additionalContext": _warning(),
            },
        }

    message = _deliver(monkeypatch, "pretooluse", build)[1]["systemMessage"]
    assert message.startswith("既存の通知\n\n")
    assert "（pretooluse・block）:\nblocked: 所有を確認できない。" in message
    assert _WARNING_COPY in message


def test_informational_notices_are_not_copied(monkeypatch):
    """実行時間のように印の付いた通知だけの応答には写しを付けず、混在する応答では是正の通知だけを写す。"""
    built, only = _deliver(monkeypatch, "posttooluse", lambda: _response(_timing(), event="PostToolUse"))
    assert "systemMessage" not in only and only["hookSpecificOutput"] == built["hookSpecificOutput"]
    mixed = _deliver(monkeypatch, "posttooluse", lambda: _response(_timing(), _warning(), event="PostToolUse"))[1]
    assert mixed["systemMessage"] == _WARNING_COPY


def test_notice_text_not_generated_in_this_call_is_not_copied(monkeypatch):
    """呼出しの外で作られた包装の字面（受け取った入力の引用など）は、通知として写さない。"""
    foreign = message_format.llm_notice("前回の通知の引用", "sample")
    message = _deliver(monkeypatch, "pretooluse", lambda: _response(foreign, _warning()))[1]["systemMessage"]
    assert message == _WARNING_COPY


@pytest.mark.parametrize(
    ("payload", "subcommand", "environment"),
    [
        pytest.param({"session_id": "user-copy", "agent_id": "sub-1"}, "pretooluse", {}, id="subagent"),
        pytest.param({"session_id": "user-copy", "turn_id": "turn"}, "pretooluse", {}, id="codex"),
        pytest.param({"session_id": "user-copy", "runtime": "agy"}, "pretooluse", {}, id="antigravity"),
        pytest.param({"session_id": "user-copy"}, "pretooluse", {"AGENT_TOOLKIT_DELEGATED_SESSION": "1"}, id="delegated"),
        pytest.param({"session_id": "user-copy"}, "rules_context", {}, id="session-start"),
        pytest.param({"session_id": "user-copy"}, "stop", {}, id="stop"),
    ],
)
def test_subagents_delegates_other_hosts_and_events_get_no_copy(monkeypatch, payload, subcommand, environment):
    """サブエージェント、委譲先、他ホスト、対象外のイベントの応答には、ユーザー宛ての欄を加えない。"""
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    assert "systemMessage" not in _deliver(monkeypatch, subcommand, lambda: _response(_warning()), payload)[1]


def test_copy_failure_keeps_agent_notice(monkeypatch):
    """写しの組み立てが失敗しても、エージェント宛ての通知をそのまま配送する。"""

    def fail(_response: dict, _generated: object) -> str:
        raise RuntimeError("copy failed")

    monkeypatch.setattr(notice, "user_copy", fail)
    built, delivered = _deliver(monkeypatch, "pretooluse", lambda: _response(_warning()))
    assert delivered["hookSpecificOutput"] == built["hookSpecificOutput"]
    assert "systemMessage" not in delivered
