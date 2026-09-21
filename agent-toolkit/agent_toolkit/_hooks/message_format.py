"""Claude Code agent-toolkit: コーディングエージェント宛てメッセージ整形共通モジュール。

LLMに行動を促すメッセージの出力経路は用途別に使い分ける。
次のユーザー入力ターンまで待ってよい誘導は`hookSpecificOutput.additionalContext`を主経路として使う。
Stop/SubagentStopで当該ターン継続を強制する誘導（振り返りスキル起動等、次のユーザー入力を待たず即時起動が必要な場面）は
`decision: "block"`＋`reason`を採用する。
PostToolUseで`decision: "block"`を返す場合の`reason`はblock理由として直前のツール結果に添えて返す。
`systemMessage`はユーザー向け情報通知専用でLLMに届かない。

LLM宛て出力は生成主体、種別及び配送単位を属性に持つXML要素で囲む。
本文に開始タグが現れても、nonceと最後の終了タグから配送境界を確定できる。

フィールドの詳細と規約の背景は
`agent-toolkit/skills/writing-standards/references/claude-hooks.md`を参照する。
"""

import secrets
from collections.abc import Mapping
from xml.sax.saxutils import quoteattr

NOTICE_ELEMENT = "agent-toolkit-hook-message"


def xml_message(element: str, body: str, attributes: Mapping[str, str]) -> str:
    """自動生成メッセージへnonce付きXML配送境界を付与する。"""
    nonce = secrets.token_hex(8)
    while nonce in body:
        nonce = secrets.token_hex(8)
    values = {**attributes, "nonce": nonce}
    serialized = "".join(f" {name}={quoteattr(value)}" for name, value in values.items())
    return f"<{element}{serialized}>\n{body}\n</{element}>"


def llm_notice(body: str, hook_id: str, *, tag: str = "") -> str:
    """コーディングエージェント宛てメッセージをXML境界付きで整形する。

    Args:
        body: メッセージ本文。
        hook_id: hook識別子（例: `agent-toolkit/pretooluse`）。
        tag: `warn`等のメッセージ種別。空値は`notice`として出力する。
    """
    kind = tag or "notice"
    return xml_message(
        NOTICE_ELEMENT,
        body,
        {
            "source": hook_id,
            "kind": kind,
        },
    )
