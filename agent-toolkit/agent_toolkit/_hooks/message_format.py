"""Claude Code agent-toolkit: コーディングエージェント宛てメッセージ整形共通モジュール。

LLMに行動を促すメッセージの出力経路は用途別に使い分ける。
次のユーザー入力ターンまで待ってよい誘導は`hookSpecificOutput.additionalContext`を主経路として使う。
Stop/SubagentStopで当該ターン継続を強制する誘導（振り返りスキル起動等、次のユーザー入力を待たず即時起動が必要な場面）は
`decision: "block"`＋`reason`を採用する。
PostToolUseで`decision: "block"`を返す場合の`reason`はblock理由として直前のツール結果に添えて返す。
`systemMessage`はユーザー向け情報通知専用でLLMに届かない。

LLM宛て出力は生成主体、種別及び配送単位を属性に持つXML要素で囲む。
包装の実装は`agent_toolkit._common.message_format`が持ち、本モジュールはhookの経路向けに再公開する。
hook以外の経路も同じ実装を経由するため、包装の実装を層の順序で最も前にある`_common`へ置く。

フィールドの詳細と規約の背景は
`agent-toolkit/skills/writing-standards/references/claude-hooks.md`を参照する。
"""

from agent_toolkit._common.message_format import AUTO_INSERTED_ELEMENT, auto_message, xml_message

NOTICE_ELEMENT = AUTO_INSERTED_ELEMENT

__all__ = ["NOTICE_ELEMENT", "llm_notice", "xml_message"]


def llm_notice(body: str, hook_id: str, *, tag: str = "") -> str:
    """コーディングエージェント宛てメッセージをXML境界付きで整形する。

    Args:
        body: メッセージ本文。
        hook_id: hook識別子（例: `agent-toolkit/pretooluse`）。
        tag: `warn`等のメッセージ種別。空値は`notice`として出力する。
    """
    kind = tag or "notice"
    return auto_message(body, source=hook_id, kind=kind)
