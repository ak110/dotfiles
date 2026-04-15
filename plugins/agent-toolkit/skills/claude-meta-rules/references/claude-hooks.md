# Claude Code Hook出力フィールドガイドライン

## フィールドの使い分け

hookやプラグインのJSON出力で使えるフィールドは、表示先が異なる。
LLMに行動を促す必要がある場合は`reason`または`additionalContext`を使い、`systemMessage`は使わない。

**共通フィールド（全hookイベント）:**

| フィールド | 表示先 | 用途 |
| --- | --- | --- |
| `systemMessage` | ユーザーのみ | 情報通知。LLMに届かない |
| `stopReason` | ユーザーのみ | `continue: false`時の終了メッセージ |

**Stop / PostToolUse:**

| フィールド | 表示先 | 用途 |
| --- | --- | --- |
| `reason` | LLM（block時） | block理由をLLMに提示する |
| `hookSpecificOutput.additionalContext` | LLM | 補足情報をLLMに提示する |

**PreToolUse:**

| フィールド | allow/ask時 | deny時 |
| --- | --- | --- |
| `permissionDecisionReason` | ユーザーのみ | LLM |
| `hookSpecificOutput.additionalContext` | LLM | LLM |

PreToolUseの`permissionDecision: "allow"`でLLMに情報を渡せる唯一のフィールドは`hookSpecificOutput.additionalContext`。

## メッセージの記述言語

hookやプラグインが出力するメッセージは英語で記述する（プロジェクト方針が無い場合）。
ユーザーの作業コンテキスト（日本語の思考の流れ）にシステム出力が注入される際、英語であればシステムメッセージと一目で区別できるため。
