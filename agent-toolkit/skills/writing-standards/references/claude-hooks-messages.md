# エージェント宛てメッセージ

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/claude-hooks-messages.md`である。呼び出し元の取得条件と注目箇所を引き継ぎ、本書と正式な資料を全文読む。節指定がある場合は、指定された見出しを持つ資料に注目する。両方に同名の見出しがあれば両方に注目する。

以下はagent-toolkitが所有する運用条件である。

## 名指し参照の読込方法

本文の各参照における適用条件を守り、選択した資料を読み込み、指定箇所に注目してから、その基準に基づく具体的な判断へ進む。起動時の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/claude-hooks-messages.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks-messages.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `claude-hooks-block-warn.md#「遮断と警告の選択」` | 判断 | `section:agent-toolkit:*/skills/writing-standards/references/claude-hooks-block-warn.md#「遮断と警告の選択」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/writing.md#「読み手別の追加注意点」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/writing.md#「読み手別の追加注意点」` |

## エージェント宛てメッセージの標識

エージェントに直接渡る出力（`reason`、`additionalContext`、終了コード2のstderr）は、全体を`atk-auto`要素で囲む。hookの出力はユーザーの発言と同じ形式で会話へ注入されるため、機械的に識別できる境界と出所を設定する。
`source`には、agent-toolkit自身であれば接頭辞のない生成元名を指定し、他の生成元であれば`<所有者>/<生成元>`を指定する。`kind`には通知種別を設定する。

種別は、受け取ったエージェントが通知の原因を除去できるかどうかに応じて選定する。原因を除去できる事象には`warn`、発話ごとの定型配送には`notice`、遮断には`block`を使用する。
`atk run-script session-review-evidence`は、`info`または`notice`を持つhook通知を問題候補から除外する。そのため、原因や対策を持たない通知に`warn`を付与すると、発話のたびに候補判定が発生する。

```xml
<atk-auto source="pretooluse" kind="warn">
detected ...
</atk-auto>
```

`systemMessage`や`stopReason`などエージェントに届かないフィールド、および追加メッセージを持たない`permissionDecision: "allow"`の呼び出しには付与しない。

## hook以外で生成する本文の要素

自動生成する本文は、hook以外の生成元であっても`atk-auto`で囲み、`source`と`kind`によって生成主体と用途を区別する。エージェント間のメッセージ配送では、委譲先から委譲元への通知に限り`from`で送信元を示す。境界は最初の開始タグと、それに対応する最後の同名終了タグによって確定する。保存済みの会話ログに含まれる古い要素名についても、読み取り側で引き続き受け付ける。

| 要素 | 対象の本文 |
| --- | --- |
| `atk-auto` | hook通知、規範、agent間配送、機械生成の入力 |
| `forwarded-user-input` | 自動生成本文の中に保持したユーザー自身の入力 |

機械が生成した本文は、ユーザー発話の解釈規範を再読させる注記の対象から除外する。
スラッシュコマンドはホストが1行目の先頭でのみ解釈するため、コマンドを含む本文では引数の位置に標識を配置する。

## ヘルパー関数

共有formatterを呼び出し元から使用する。

```python
from agent_toolkit._hooks.notice import formatter


_llm_notice = formatter("myplugin/myhook")
```

## 通知本文の整形

本節では、`_hooks.notice`が整形するblock、warn、noticeの通知本文の構成を定める。遮断か警告かの選択、および反復時の昇格方針は`claude-hooks-block-warn.md`「遮断と警告の選択」に従う。

- block通知は専用の整形関数（`block_formatter`）、warn通知は同モジュールの整形関数を用いて生成し、いずれも解消手段を`fix`引数へ渡す。`fix`が空または空白のみの場合は`ValueError`を送出する。整形関数は本文の末尾に`次の操作: <fix>`の行を追加する。

- blockおよびwarnの本文はこれらの整形関数のみを用いて組み立て、解消手段を本文に直接混ぜずに`fix`引数へ渡す（解消手段の欠落を機械的に検出するため）。文面の基準は、`agent-basics:writing-standards/references/writing.md`「読み手別の追加注意点」のプログラム出力メッセージの項目に従う。

閾値および記録先は`agent-toolkit/agent_toolkit/_hooks/notice.py`で管理する。
