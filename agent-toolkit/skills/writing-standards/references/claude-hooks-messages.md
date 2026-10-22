# エージェント宛てメッセージ

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/claude-hooks-messages.md`である。呼出元が指定した条件と読込範囲を、同名の正式な品質資料へ引き継ぐ。節指定がある場合は移設先の実見出しへ接続し、指定がない場合は従来の全文読込契約を保つ。

以下はagent-toolkitが所有する運用条件である。呼出元の条件と読込範囲に従い、ここに残る運用節を補う。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/claude-hooks-messages.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks-messages.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `claude-hooks-block-warn.md#「遮断と警告の選択」` | 判断 | `section:agent-toolkit:*/skills/writing-standards/references/claude-hooks-block-warn.md#「遮断と警告の選択」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/writing.md#「読み手別の追加注意点」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/writing.md#「読み手別の追加注意点」` |

## エージェント宛てメッセージの標識

エージェントに直接渡る出力（`reason` / `additionalContext` / exit 2のstderr）は、`atk-auto`要素で全体を囲む。hookの出力はユーザー発言と同じ形で会話へ注入されるため、機械判定できる境界と出所を設ける。
`source`にはagent-toolkit自身なら接頭辞の無い生成元名、他の生成元は`<所有者>/<生成元>`を置き、`kind`へ通知種別を置く。

種別は受領したエージェントが通知の原因を除去できるかで選ぶ。除去できる事象には`warn`、発話ごとの定型の配送には`notice`、遮断には`block`を使う。
`atk run-script session-review-evidence`は`info`または`notice`を持つhook通知を問題候補から除くため、原因も対策も持たない通知へ`warn`を付けると発話のたびに候補判定が生じる。

```xml
<atk-auto source="pretooluse" kind="warn">
detected ...
</atk-auto>
```

`systemMessage` / `stopReason`などエージェントに届かないフィールドと、`permissionDecision: "allow"`で追加メッセージを持たない呼び出しは付与の対象外とする。

## hook以外で生成する本文の要素

自動生成する本文はhook以外の生成元も`atk-auto`で囲み、`source`と`kind`で生成主体と用途を区別する。agent間の配送では、委譲先から委譲元への通知だけ`from`で送信元を示す。境界は最初の開始タグと最後の同名終了タグで確定する。保存済みの会話にある旧要素名は読み取り側が引き続き受け付ける。

| 要素 | 対象の本文 |
| --- | --- |
| `atk-auto` | hook通知、規範、agent間配送、機械生成の入力 |
| `forwarded-user-input` | 自動生成本文の中に保持したユーザー自身の入力 |

機械が生成した本文は、ユーザー発話の解釈規範を再読させる注記の対象から外れる。
スラッシュコマンドはホストが1行目の先頭でだけ解釈するため、コマンドを伴う本文では引数の位置へ標識を置く。

## ヘルパー関数

共有formatterを発出箇所から呼び出す。

```python
from agent_toolkit._hooks.notice import formatter


_llm_notice = formatter("myplugin/myhook")
```

## 通知本文の整形

本節は`_hooks.notice`が整形するblock・warn・noticeの通知本文の構成を定める。遮断か警告かの選択と反復時の昇格は`claude-hooks-block-warn.md`「遮断と警告の選択」が定める。

- block通知はblock専用の整形関数（`block_formatter`）、warn通知は同モジュールの整形関数で生成し、どちらも解消手段を`fix`へ渡す。`fix`が空か空白だけなら`ValueError`となり、整形関数は本文の後へ`次の操作: <fix>`の行を置く

- block・warnの本文はこれらの整形関数だけで組み立て、解消手段は本文へ混ぜず`fix`へ渡す（解消手段の欠落を機械的に検出するため）。文面の基準は`agent-basics:writing-standards/references/writing.md`「読み手別の追加注意点」のプログラムが出力するメッセージの項目に従う

閾値と記録先は`agent-toolkit/agent_toolkit/_hooks/notice.py`が持つ。
