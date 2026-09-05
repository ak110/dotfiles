r"""agent-toolkit pluginの自律終了Stopフック。

環境変数`AGENT_TOOLKIT_PROCESS_LOOP_SESSION=1`または移行互換名
`DOTFILES_AUTONOMOUS_EXIT_REQUIRED=1`が設定されたセッションを対象とする。
本フックは環境変数印を検出したセッションに限り、`agent-toolkit:exit-session`スキルの
呼び出し漏れを検知して当該ターンの継続をblockし再促する。

`agent-toolkit:exit-session`呼び出しの記録はpluginのPostToolUse
（`agent-toolkit/scripts/posttooluse.py`）が担い、`autonomous_exit_invoked`フラグへ
反映する。本フックは同フラグをセッション状態ファイル経由で読み取るのみで、記録は行わない。

判定順序は以下のとおり。

1. 新旧いずれのセッション識別子も`"1"`でない: 常駐ループ外のセッションのため無条件approve
2. 委譲先セッションの印が`"1"`: 常駐ループの最上位ではないため無条件approve
3. `stop_hook_active`が真: 連続ブロック上限回避のため無条件approve
4. `is_pending_async_work`が真: サブエージェント継続時の誤発火防止のためapprove
5. `autonomous_exit_invoked`が真: 呼び出し済みのためapprove
6. 上記いずれでもない: blockして順序制約の再促文を返す

LLM宛て出力は`_hook_notice`のblock専用整形関数経由で整形し、
`decision: "block"`＋`reason`フィールドへ載せて返す。

各判定分岐の最終判定ラベルと根拠は`_stop_gate.append_stop_log`で
常時ログへ記録する。

委譲先での実行可否: 委譲先は最上位セッションの終了工程を実行できないため、環境変数による除外が必要である。
"""

import json
import os

from _hook_notice import block_formatter as _block_notice_formatter
from _session_state import read_state
from _stop_gate import append_stop_log, is_pending_async_work
from _stop_gate import parse_stop_session as _parse_stop_session

# このスクリプトのhook識別子。
_HOOK_ID = "agent-toolkit/autonomous_exit"

# 常駐ループから起動されたセッションであることを示す環境変数名。
_ENV_REQUIRED = "AGENT_TOOLKIT_PROCESS_LOOP_SESSION"

# 更新中に旧process-loopと併存するため受理する移行互換名。
_LEGACY_ENV_REQUIRED = "DOTFILES_AUTONOMOUS_EXIT_REQUIRED"

# agents_serverから起動された委譲先セッションであることを示す環境変数名。
_ENV_DELEGATED_SESSION = "AGENT_TOOLKIT_DELEGATED_SESSION"

# PostToolUse（`posttooluse.py`）が`agent-toolkit:exit-session`呼び出し検出時に
# セッション状態へ記録するフラグ名。
_STATE_KEY = "autonomous_exit_invoked"

# 発火判定が観測する環境変数印だけを根拠として適用範囲を断定する再促文。
# 起動元のCLIや起動時のスキル名は判定していないため、本文では例示として扱わない。
_REASON_BODY = """\
このセッションには常駐ループの終了保証が適用される。
`agent-toolkit:process-wi`の全工程を完了し、`agent-toolkit:completion-report`で完了報告した後に、`agent-toolkit:exit-session`を起動する。
未完了の工程がある場合は、その工程へ戻ってから終了を再検討する。"""


_block_notice = _block_notice_formatter(_HOOK_ID)


def _approve() -> None:
    """空のapprove応答を返す。"""
    print(json.dumps({}, ensure_ascii=False))


def evaluate(payload_text: str) -> tuple[str, str]:
    """自律終了の判定結果と、遮断する場合の理由を返す。"""
    resolved = _parse_stop_session(payload_text, lambda: None)
    if resolved is None:
        return "approve", ""
    session_id, payload = resolved

    # 常駐ループ外のセッションでは本hookの誘導対象外とする。
    if os.environ.get(_ENV_REQUIRED) != "1" and os.environ.get(_LEGACY_ENV_REQUIRED) != "1":
        append_stop_log(session_id, "approve_no_env", {})
        return "approve", ""

    if os.environ.get(_ENV_DELEGATED_SESSION) == "1":
        append_stop_log(session_id, "approve_delegated_session", {})
        return "approve", ""

    # Stop hookが直前のターンで既にブロック済みの再呼び出し。
    # 同一判定を繰り返すと連続ブロック上限に達して強制終了するため、即座にapproveする。
    if payload.get("stop_hook_active") is True:
        append_stop_log(session_id, "approve_stop_hook_active", {"stop_hook_active": True})
        return "approve", ""

    raw_transcript = payload.get("transcript_path", "")
    transcript_path = raw_transcript if isinstance(raw_transcript, str) else ""
    if transcript_path and is_pending_async_work(
        transcript_path,
        session_id,
        background_tasks=payload.get("background_tasks"),
    ):
        append_stop_log(session_id, "approve_pending_async", {})
        return "approve", ""

    state = read_state(session_id)
    if state.get(_STATE_KEY) is True:
        append_stop_log(session_id, "approve_exit_invoked", {})
        return "approve", ""

    append_stop_log(session_id, "block_autonomous_exit", {})
    reason = _block_notice(
        _REASON_BODY,
        fix="列挙した前提工程をすべて完了してから、/agent-toolkit:exit-sessionを起動する。",
    )
    return "block", reason


def main(payload_text: str) -> int:
    """`agent-toolkit:exit-session`呼び忘れを検知し再促するエントリポイント。"""
    decision, body = evaluate(payload_text)
    if decision == "block":
        print(json.dumps({"decision": "block", "reason": body}, ensure_ascii=False))
    else:
        _approve()
    return 0
