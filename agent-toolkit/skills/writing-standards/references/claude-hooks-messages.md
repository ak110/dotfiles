# コーディングエージェント宛てメッセージ

本書はhookとhook以外の生成元がコーディングエージェントへ直接渡す本文の記述言語と`atk-auto`の標識を定める。

## メッセージの記述言語

コーディングエージェントに直接渡る出力（`reason` / `additionalContext` / exit 2のstderr）は
日本語で記述する。会話コンテキストへ日本語以外の言語の文が挿入されると、
モデルが以後の発話言語をその言語へ引きずられるためである。
自動生成であることは次節の標識だけが担う。

hookメッセージ中で原本ファイル（`01-agent.md`・`CLAUDE.md`等）の章名・節名・キーワードを参照する場合は、
コーディングエージェントが参照先を特定できるよう原本表記をそのまま引用する。
訳した参照名（例:「日本語」節を`Japanese section`と訳すなど）は、原本の章名を変更したときに参照が追従しなくなる。

## コーディングエージェント宛てメッセージの標識

コーディングエージェントに直接渡る出力（`reason` / `additionalContext` / exit 2のstderr）は、
`atk-auto`要素で全体を囲む。`source`へagent-toolkit自身は接頭辞の無い生成元名、他の生成元は`<所有者>/<生成元>`を置き、`kind`へ通知種別を置く。
hookの出力はユーザー発言と同じ形で会話コンテキストに注入されるため、機械判定できる境界と出所を設ける。

種別は受領した主体が通知の原因を除去できるかで選ぶ。除去できる事象には`warn`、発話ごとの定型の配送には`notice`、遮断には`block`を使う。
`atk run-script session-review-evidence`は`info`または`notice`を持つhook通知を問題候補から除く。原因も対策も持たない通知へ`warn`を指定すると、候補の判定工程が発話のたびに生じる。

```xml
<atk-auto source="pretooluse" kind="warn">
detected ...
</atk-auto>
```

`systemMessage` / `stopReason` などコーディングエージェントに届かないフィールドや、
`permissionDecision: "allow"`で追加メッセージを持たない呼び出しは、付与の対象に含めない。

### hook以外で生成する本文の要素

自動生成する本文はhook以外の生成元も`atk-auto`で囲む。`source`と`kind`で生成主体と用途を区別する。agent間の配送では、委譲先から委譲元への通知だけ`from`で送信元を示す。最初の開始タグと最後の同名終了タグで境界を確定する。保存済みの会話にある旧要素名は読み取り側が引き続き受け付ける。

| 要素 | 対象の本文 |
| --- | --- |
| `atk-auto` | hook通知、規範、agent間配送、機械生成の入力 |
| `forwarded-user-input` | 自動生成本文の中に保持したユーザー自身の入力 |

機械が生成した本文は、ユーザー発話の解釈規範を再読させる注記の対象から外れる。
スラッシュコマンドはホストが1行目の先頭でだけ解釈するため、コマンドを伴う本文では引数の位置へ標識を置く。

### ヘルパー関数

共有formatterを発出箇所から呼び出し、実装をhook間で共有する。

```python
from agent_toolkit._hooks.notice import formatter


_llm_notice = formatter("myplugin/myhook")
```
