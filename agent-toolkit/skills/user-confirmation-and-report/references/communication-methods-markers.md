# 確認・報告の手段と認識合わせ：エージェント宛てメッセージの標識

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| `予定`の改善点を確定して完了報告へ記載する前 | `agent-toolkit:report-completion/SKILL.md` |
| 進捗のセルフチェック通知を受けて点検する時 | `../../delegation/references/waiting-and-monitoring-progress.md` |

## エージェント宛てメッセージの標識

エージェントに直接渡る出力（`reason`、`additionalContext`、終了コード2のstderr）は、全体を`atk-auto`要素で囲む。
hookの出力はユーザーの発言と同じ形式で会話へ注入されるため、機械的に識別できる境界と出所を設定する。
`source`には、agent-toolkit自身であれば接頭辞のない生成元名を指定し、他の生成元であれば`<所有者>/<生成元>`を指定する。
`kind`には通知種別を設定する。

種別は、受け取ったエージェントが通知の原因を除去できるかどうかに応じて選定する。
原因を除去できる事象には`warn`、発話ごとの定型配送には`notice`、遮断には`block`を使用する。
`atk run-script session-review-evidence`は、`info`または`notice`を持つhook通知を問題候補から除外する。
そのため、原因や対策を持たない通知に`warn`を付与すると、発話のたびに候補判定が発生する。

```xml
<atk-auto source="pretooluse" kind="warn">
detected ...
</atk-auto>
```

エージェント宛ての通知を`systemMessage`へ写す場合は、同じ`atk-auto`の開始・終了タグを保つ。
それ以外の`systemMessage`や`stopReason`などユーザー専用の本文、および追加メッセージを持たない`permissionDecision: "allow"`の呼び出しには付与しない。

## hook以外で生成する本文の要素

自動生成する本文は、hook以外の生成元であっても`atk-auto`で囲み、`source`と`kind`によって生成主体と用途を区別する。
エージェント間のメッセージ配送では、委譲先から委譲元への通知に限り`from`で送信元を示す。
境界は最初の開始タグと、それに対応する最後の同名終了タグによって確定する。
保存済みの会話ログに含まれる古い要素名も、読み取り側で引き続き受け付ける。

| 要素 | 対象の本文 |
| --- | --- |
| `atk-auto` | hook通知、規範、agent間配送、機械生成の入力 |
| `forwarded-user-input` | 自動生成本文の中に保持したユーザー自身の入力 |

機械が生成した本文は、ユーザー発話の解釈規範を再読させる注記の対象から除外する。
スラッシュコマンドはホストが1行目の先頭でのみ解釈するため、コマンドを含む本文では引数の位置に標識を配置する。
