# コーディングエージェント宛てメッセージの標識の書式

`agent-toolkit/skills/agent-standards/references/claude-hooks.md`
「コーディングエージェント宛てメッセージの標識」節から分離した書式詳細。

## プレフィックス

`[auto-generated]` または `[auto-generated: <plugin>/<hook>]` を行頭に置く。
プラグイン識別子やフック種別のみの内部名（例: `[agent-toolkit]`）はコーディングエージェントの観点では
「自動生成である」という意味論が伝わらないため使わない。

警告などの種別タグ（例: `[warn]`）は、警告であることを区別したい場合に並置する（任意）。

```text
[auto-generated: agent-toolkit/pretooluse] blocked: ...
[auto-generated: agent-toolkit/pretooluse][warn] detected ...
```

## サフィックス

メッセージ本文の末尾に以下の英文を一行追加する。

```text
(Auto-generated hook notice; evaluate relevance against the conversation context before acting.)
```

コーディングエージェントに対して「妥当性を文脈と照らして判断してから行動する」ことを明示する。
`systemMessage` / `stopReason` などコーディングエージェントに届かないフィールドや、
`permissionDecision: "allow"`で追加メッセージを持たない経路には付けない。

## ヘルパー関数

hookスクリプトごとに次のようなヘルパーを持ち、発出箇所から呼び出す（重複実装は許容）。

```python
_MESSAGE_PREFIX = "[auto-generated: myplugin/myhook]"
_MESSAGE_SUFFIX = "(Auto-generated hook notice; evaluate relevance against the conversation context before acting.)"

def _llm_notice(body: str) -> str:
    return f"{_MESSAGE_PREFIX} {body} {_MESSAGE_SUFFIX}"
```
