"""Claude Code agent-toolkit: コーディングエージェント宛てメッセージ整形共通モジュール。

LLMに行動を促すメッセージの出力経路は用途別に使い分ける。
次のユーザー入力ターンまで待ってよい誘導は`hookSpecificOutput.additionalContext`を主経路として使う。
Stop/SubagentStopで当該ターン継続を強制する誘導（振り返りスキル起動等、次のユーザー入力を待たず即時起動が必要な場面）は
`decision: "block"`＋`reason`を採用する。
PostToolUseで`decision: "block"`を返す場合の`reason`はblock理由として直前のツール結果に添えて返す。
`systemMessage`はユーザー向け情報通知専用でLLMに届かない。

LLM宛て出力には自動生成を示すプレフィックスとサフィックスを必ず付ける。
本モジュールの`llm_notice`関数が以下のフォーマットで整形する。

- プレフィックス: `[auto-generated: <plugin>/<hook>]`（警告時は`[warn]`タグを並置）
- サフィックス: `（自動生成のhook通知。行動する前に会話コンテキストとの関連性を評価すること。）`

フィールドの詳細と規約の背景は
`agent-toolkit/skills/agent-standards/references/claude-hooks.md`を参照する。
"""

_MESSAGE_SUFFIX = "（自動生成のhook通知。行動する前に会話コンテキストとの関連性を評価すること。）"


def llm_notice(body: str, hook_id: str, *, tag: str = "") -> str:
    """コーディングエージェント宛てメッセージを標準プレフィックス/サフィックス付きで整形する。

    Args:
        body: メッセージ本文。
        hook_id: hook識別子（例: `agent-toolkit/pretooluse`）。
        tag: `warn`等を渡すとプレフィックスに並置する（`[auto-generated: ...][warn]`）。
    """
    prefix = f"[auto-generated: {hook_id}]"
    if tag:
        prefix = f"{prefix}[{tag}]"
    return f"{prefix} {body} {_MESSAGE_SUFFIX}"
