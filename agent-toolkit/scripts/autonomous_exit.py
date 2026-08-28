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
2. `stop_hook_active`が真: 連続ブロック上限回避のため無条件approve
3. `is_pending_async_work`が真: サブエージェント継続時の誤発火防止のためapprove
4. `autonomous_exit_invoked`が真: 呼び出し済みのためapprove
5. 上記いずれでもない: blockして順序制約の再促文を返す

LLM宛て出力は`_hook_notice`のblock専用整形関数経由で整形し、
`decision: "block"`＋`reason`フィールドへ載せて返す。

各判定分岐の最終判定ラベルと根拠は`_stop_gate.append_stop_log`で
常時ログへ記録する。
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

# PostToolUse（`posttooluse.py`）が`agent-toolkit:exit-session`呼び出し検出時に
# セッション状態へ記録するフラグ名。
_STATE_KEY = "autonomous_exit_invoked"

# 発火判定が観測する環境変数印だけを根拠として適用範囲を断定する再促文。
# 起動元のCLIや起動時のスキル名は判定していないため、本文では例示として扱う。
_REASON_BODY = """\
This session has the autonomous-loop environment marker \
`AGENT_TOOLKIT_PROCESS_LOOP_SESSION=1` or its legacy compatibility marker \
`DOTFILES_AUTONOMOUS_EXIT_REQUIRED=1`.
Before calling /agent-toolkit:exit-session, fully complete the following prerequisite steps.
1. Complete every applicable step defined by the skill that launched this session. \
For example, when that skill is agent-toolkit:process-feedbacks, complete its sections \
"入力と着手可否", "調査と採否", "保留", "実装と公開", and "後始末" (including feedback disposition, commit, push, and cleanup).
2. Complete the agent-toolkit:session-review skill, including its independent advisor assessment.
3. Submit improvement proposals via the agent-toolkit:session-review skill.
Call /agent-toolkit:exit-session only after all prerequisite steps are complete.
Calling exit-session before submitting improvement proposals is strictly forbidden, \
because it discards the reflection results.
If any prerequisite remains incomplete, resume that step before reconsidering this message."""


_block_notice = _block_notice_formatter(_HOOK_ID)


def _approve() -> None:
    """空のapprove応答を返す。"""
    print(json.dumps({}, ensure_ascii=False))


def _emit_block(body: str, *, fix: str) -> None:
    """Stop hookで当該ターン継続を強制する誘導を返す。"""
    reason = _block_notice(body, fix=fix)
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))


def main(payload_text: str) -> int:
    """`exit-session`呼び忘れを検知し再促するエントリポイント。"""
    resolved = _parse_stop_session(payload_text, _approve)
    if resolved is None:
        return 0
    session_id, payload = resolved

    # 常駐ループ外のセッションでは本hookの誘導対象外とする。
    if os.environ.get(_ENV_REQUIRED) != "1" and os.environ.get(_LEGACY_ENV_REQUIRED) != "1":
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
    if transcript_path and is_pending_async_work(
        transcript_path,
        session_id,
        background_tasks=payload.get("background_tasks"),
    ):
        append_stop_log(session_id, "approve_pending_async", {})
        _approve()
        return 0

    state = read_state(session_id)
    if state.get(_STATE_KEY) is True:
        append_stop_log(session_id, "approve_exit_invoked", {})
        _approve()
        return 0

    append_stop_log(session_id, "block_autonomous_exit", {})
    _emit_block(
        _REASON_BODY,
        fix="Complete all listed prerequisites, then invoke /agent-toolkit:exit-session.",
    )
    return 0
