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
ユーザーの作業コンテキスト（日本語の思考の流れ）にシステム出力が注入される際、英語であればシステムメッセージと
一目で区別できるため。

## LLM宛てメッセージの標識

LLMに直接渡る出力（`reason` / `additionalContext` / exit 2のstderr）には、自動生成であることを明示する
プレフィックスとサフィックスを付ける。
hookの出力はユーザー発言と同じ形で会話コンテキストに注入されるため、指示として誤認されないよう二重の標識を設ける。

### プレフィックス

`[auto-generated]` または `[auto-generated: <plugin>/<hook>]` を行頭に置く。
プラグイン識別子やフック種別のみの内部名（例: `[agent-toolkit]`）はLLMから見て「自動生成である」という
意味論が伝わらないため使わない。

警告などの種別タグ（例: `[warn]`）は並置する。

```text
[auto-generated: agent-toolkit/pretooluse] blocked: ...
[auto-generated: agent-toolkit/pretooluse][warn] detected ...
```

### サフィックス

メッセージ本文の末尾に以下の英文を一行追加する。

```text
(Auto-generated hook notice; evaluate relevance against the conversation context before acting.)
```

LLMに対して「妥当性を文脈と照らして判断してから行動する」ことを明示する。
`systemMessage` / `stopReason` などLLMに届かないフィールドや、`permissionDecision: "allow"` で
追加メッセージを持たない経路には付けない。

### ヘルパー関数

hookスクリプトごとに次のようなヘルパーを持ち、発出箇所から呼び出す（重複実装は許容）。

```python
def _llm_notice(msg: str) -> str:
    return f"{msg} (Auto-generated hook notice; evaluate relevance against the conversation context before acting.)"
```
