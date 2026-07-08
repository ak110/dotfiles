#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
r"""Claude Code Stopフック: dotfiles個人環境専用の`exit-session`呼び忘れ防止。

`atk fb process-loop`CLIが常駐ループの1反復ごとに起動するclaudeサブプロセスは、
環境変数`DOTFILES_AUTONOMOUS_EXIT_REQUIRED=1`を設定した状態で起動される。
本hookは同環境変数が設定されたセッションに限り、`agent-toolkit:exit-session`スキルの
呼び出し漏れを検知して当該ターンの継続をblockし再促する。

`agent-toolkit:exit-session`呼び出しの記録は個人フックPostToolUse
（`scripts/claude_hook_posttooluse.py`）が担い、`autonomous_exit_invoked`フラグへ
反映する。本hookは同フラグをセッション状態ファイル経由で読み取るのみで、記録は行わない。

判定順序は以下のとおり。

1. `DOTFILES_AUTONOMOUS_EXIT_REQUIRED != "1"`: 常駐ループ外のセッションのため無条件approve
2. `stop_hook_active`が真: 連続ブロック上限回避のため無条件approve
3. `is_pending_async_work`が真: サブエージェント継続時の誤発火防止のためapprove
4. `autonomous_exit_invoked`が真: 呼び出し済みのためapprove
5. 上記いずれでもない: blockして順序制約の再促文を返す

LLM宛て出力は`agent-toolkit/scripts/_message_format.llm_notice`経由で整形し、
`decision: "block"`＋`reason`フィールドへ載せて返す。
参照経路は`Path(__file__).resolve().parent.parent / "agent-toolkit" / "scripts"`を
`sys.path`に追加して解決する（`scripts/claude_hook_stop.py`と同形式）。

各判定分岐の最終判定ラベルと根拠は`agent-toolkit/scripts/_stop_gate.append_stop_log`で
常時ログへ記録する。
"""

import json
import os
import pathlib
import sys
import traceback

# agent-toolkit の共通ゲートモジュールを import する。
# plugin が無効化されていても dotfiles リポジトリ上にファイルが存在し続けるため import は成立する。
sys.path.insert(
    0,
    str(pathlib.Path(__file__).resolve().parent.parent / "agent-toolkit" / "scripts"),
)
from _message_format import llm_notice as _llm_notice_base  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _session_state import read_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _stop_gate import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    append_stop_log,
    is_pending_async_work,
)

# このスクリプトの hook 識別子。
_HOOK_ID = "dotfiles/claude_hook_autonomous_exit"

# 常駐ループから起動されたセッションであることを示す環境変数名。
_ENV_REQUIRED = "DOTFILES_AUTONOMOUS_EXIT_REQUIRED"

# PostToolUse（`claude_hook_posttooluse.py`）が`agent-toolkit:exit-session`呼び出し検出時に
# セッション状態へ記録するフラグ名。
_STATE_KEY = "autonomous_exit_invoked"

# 順序制約の再促文。process-feedbacksの多段処理途中でexit-session呼び出しが
# 忘却されることを防ぐため、工程順序を明示する。
_REASON_BODY = """\
This session was launched in autonomous execution mode by the atk fb process-loop CLI.
After processing completes, you must call /agent-toolkit:exit-session to end the session.
Before calling exit-session, fully complete the following steps.
1. process-feedbacks skill steps 1-3 (feedback adoption decision, commit, push, cleanup)
2. process-feedbacks skill step 4, "振り返り工程"
   (both the agent-toolkit:session-review skill and the session-review-dotfiles skill)
3. Submission of improvement proposals via the session-review-dotfiles skill
Call exit-session only after all of the above steps are complete.
Calling exit-session before submitting improvement proposals is strictly forbidden, \
because it discards the reflection results.
If any step remains incomplete, resume that step before reconsidering this message."""


def _llm_notice(body: str) -> str:
    """コーディングエージェント宛てメッセージを標準プレフィックス / サフィックス付きで整形する。"""
    return _llm_notice_base(body, _HOOK_ID)


def _approve() -> None:
    print(json.dumps({}, ensure_ascii=False))


def _emit_block(reason: str) -> None:
    """Stop hookで当該ターン継続を強制する誘導を返す。

    `stop_hook_active`保護で1回のみ発火する前提。反復呼び出しに備え、
    本メッセージは反復再促の役割も担う。
    """
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))


def main() -> int:
    """`exit-session`呼び忘れを検知し再促するエントリポイント。"""
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        _approve()
        return 0

    session_id = payload.get("session_id", "")
    if not isinstance(session_id, str) or not session_id:
        _approve()
        return 0

    # 常駐ループ外のセッションでは本hookの誘導対象外とする。
    if os.environ.get(_ENV_REQUIRED) != "1":
        append_stop_log(session_id, "approve_no_env", {})
        _approve()
        return 0

    # Stop hookが直前のターンで既にブロック済みの再呼び出し。
    # 同一判定を繰り返すと連続ブロック上限に達して強制終了するため、即座にapproveする。
    if payload.get("stop_hook_active") is True:
        append_stop_log(session_id, "approve_stop_hook_active", {"stop_hook_active": True})
        _approve()
        return 0

    raw_transcript = payload.get("transcript_path", "")
    transcript_path = raw_transcript if isinstance(raw_transcript, str) else ""
    if transcript_path and is_pending_async_work(transcript_path, session_id):
        append_stop_log(session_id, "approve_pending_async", {})
        _approve()
        return 0

    state = read_state(session_id)
    if state.get(_STATE_KEY) is True:
        append_stop_log(session_id, "approve_exit_invoked", {})
        _approve()
        return 0

    append_stop_log(session_id, "block_autonomous_exit", {})
    _emit_block(_llm_notice(_REASON_BODY))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        _approve()
        sys.exit(0)
