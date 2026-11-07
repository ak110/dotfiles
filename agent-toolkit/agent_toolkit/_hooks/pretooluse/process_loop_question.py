"""process-loopが起動したClaudeのメイン会話で`AskUserQuestion`を拒否し、UWIへの記録へ切り替えさせる。

`atk wi process-loop`はClaudeへ`askUserQuestionTimeout`を渡すが、Claude Code（2.1.319で実読）の自動継続は、
端末フォーカスが`focused`の間は待機の起点を毎秒現在時刻へ更新するため期限に達しない。
フォーカスは端末のfocus-in報告か、未blur状態のキー入力・マウス押下で`focused`になり、focus-outまで続く。
端末がペインのfocus-inを報告する環境では、常駐実行のペインをアクティブにしているだけで期限に達せず、
統括が回答を待って何時間も止まりうる（2026年11月7日にherdrのペインで約3時間52分待った事象は、この条件によると推定している）。
フォーカスはhookから観測できないため、process-loopのメイン会話では質問を発行させず、
自律モードで回答期限が無い場合の手段（事前承認型UWI）へ切り替えさせる。

遮断は`agent-basics:writing-standards`の`references/claude-hooks-block-warn.md`「遮断と警告の選択」が
`AskUserQuestion`の判定を警告にとどめる原則に対し、回答期限が働かない常駐実行の会話について定める例外に当たる。
警告では質問がそのまま表示されて回答待ちに入り、常駐実行の工程を止める実害を防げない。
委譲先と通常の対話セッションは対象外とし、process-loopの環境印と会話IDが一致するメイン会話に限る。
"""

from __future__ import annotations

import os

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy from agent_toolkit._common.process_loop_session import is_process_loop_session
lazy from agent_toolkit._hooks.agent_id import is_main_agent_context
lazy from agent_toolkit._hooks.pretooluse.notices import _block_notice


def check(payload: dict, session_id: str) -> str | None:
    """process-loopのメイン会話の`AskUserQuestion`なら拒否本文を返し、それ以外はNoneを返す。"""
    if payload.get("tool_name") != "AskUserQuestion":
        return None
    if not (is_process_loop_session(session_id, os.environ) and is_main_agent_context(payload)):
        return None
    return _block_notice(
        "`atk wi process-loop`が起動した会話では`AskUserQuestion`の回答期限が働かない。"
        "`Claude Code`は端末フォーカスがある間は自動継続の待機を延長し続けるため、回答が無いまま工程が止まる。",
        fix=(
            "質問を発行せず、未回答のまま期限を過ぎた場合と同じく扱え。"
            "`agent-toolkit:user-confirmation-and-report`の`references/pending-decisions.md`"
            "「未確定判断の保留と暫定判断」に従い、確認事項を事前承認型UWIへ記録し、回答を要する元項目だけを保留して、"
            "回答を要しない工程を続けよ"
        ),
    )
