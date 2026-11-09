# エージェント宛てメッセージ

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/claude-hooks-messages.md`である。
呼び出し元の読込条件を引き継ぎ、本書の運用条件と条件に該当する正式な資料の全文を読む。
両方に同じ主題があれば、両方の全文を確認する。
範囲の選択と取得済み本文の再利用は`agent-basics:writing-standards/references/read-method.md`に従う。

以下はagent-toolkitが所有する運用条件である。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/claude-hooks-messages.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks-messages.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `./claude-hooks-block-warn.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/claude-hooks-block-warn.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/program-messages.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/program-messages.md` |

## ヘルパー関数

共有formatterを呼び出し元から使用する。

```python
from agent_toolkit._hooks.notice import formatter

_llm_notice = formatter("myplugin/myhook")
```

## 通知本文の整形

本節では、`_hooks.notice`が整形するblock、warn、noticeの通知本文の構成を定める。
遮断か警告かの選択、および反復時の昇格方針は`claude-hooks-block-warn.md`「遮断と警告の選択」に従う。

対象にはTypeScript製hookの`automated_message.ts`の`automatedMessage`が包む本文と、`agent_toolkit/_common/next_action.py`の次の操作を含む。
これらにも`agent-basics:writing-standards/references/claude-hooks-messages-format.md`「通知本文の整形」を適用する。

- block通知は専用の整形関数（`block_formatter`）、warn通知は同モジュールの整形関数を用いて生成し、いずれも解消手段を`fix`引数へ渡す。
  `fix`が空または空白のみの場合は`ValueError`を送出する。
  整形関数は本文の末尾に`次の操作: <fix>`の行を追加する。

- blockおよびwarnの本文はこれらの整形関数のみを用いて組み立て、解消手段を本文に直接混ぜずに`fix`引数へ渡す（解消手段の欠落を機械的に検出するため）。
  文面の基準は、`agent-basics:writing-standards/references/program-messages.md`「出力の基準」に従う。

閾値および記録先は`agent-toolkit/agent_toolkit/_hooks/notice.py`で管理する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| エージェント宛てメッセージ：通知本文の整形の基準を適用する前 | `agent-basics:writing-standards/references/claude-hooks-messages-format.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks-messages-format.md` |
