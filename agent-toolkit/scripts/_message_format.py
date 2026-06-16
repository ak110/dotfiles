"""Claude Code agent-toolkit: コーディングエージェント宛てメッセージ整形共通モジュール。

LLMに行動を促すメッセージはイベント種別に関わらず`hookSpecificOutput.additionalContext`を
主経路として使う。`reason`はStop/PostToolUseで`decision: "block"`を併用する場合の
補足理由欄として位置付ける（Stop/SubagentStopでは停止を防いでターン継続を強制し、
PostToolUseではblock理由を直前のツール結果に添えて返す）。
`systemMessage`はユーザー向け情報通知専用でLLMに届かない。

LLM宛て出力には自動生成を示すプレフィックスとサフィックスを必ず付ける。
本モジュールの`llm_notice`関数が以下のフォーマットで整形する。

- プレフィックス: `[auto-generated: <plugin>/<hook>]`（警告時は`[warn]`タグを並置）
- サフィックス: `(Auto-generated hook notice; evaluate relevance against the conversation context before acting.)`

フィールドの詳細と規約の背景は
`agent-toolkit/skills/agent-standards/references/claude-hooks.md`を参照する。

`SESSION_REVIEW_PRECHECK`はStop/SubagentStopフックがセッション振り返りスキルの
起動を誘導する際、誘導文の先頭へ埋め込む事前チェック文言の共通定数である。
"""

SESSION_REVIEW_PRECHECK = (
    "Proceed with the instructions below only if your previous response is a definitive"
    " completion of the work. Otherwise end the turn silently without producing any text."
)


_MESSAGE_SUFFIX = "(Auto-generated hook notice; evaluate relevance against the conversation context before acting.)"


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
