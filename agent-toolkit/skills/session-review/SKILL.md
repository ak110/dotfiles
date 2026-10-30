---
name: session-review
description: >
  ユーザーが現在のセッションの振り返りを手動で依頼したときに起動する。
  batch引数ではOSアカウントに記録した非活動セッションをまとめて分析する。
  SessionStart・SessionEndはbatchで自動起動する。通常の作業完了や公開工程から同期起動しない。
---

# セッション振り返り

最初に引数の先頭語を確認し、次の表から1つの処理資料を全文読む。
先頭語がbatchと完全一致する場合だけbatchモードとし、その語を除いた残りを追加の着眼点として保持する。
先頭の空白と語の区切りには空白・タブ・改行を使える。
一致しない場合と引数なしは通常モードとし、引数全体を着眼点として扱う。
着眼点の文頭にbatchという語を置く場合は、別の語から書き始める。
`batch auto`のautoも着眼点であり、自動起動の判別は起動元の情報で行う。
着眼点に対象を限定せず、選んだ資料が指定する対象全体を振り返る。
共通分析は各モードの準備後に読み、通常モードのprepareとbatchの準備済み範囲を重複処理しない。

## 名指し参照の読込方法

選んだ資料とその条件に合う参照先を、判断や操作の前に全文読む。同じ版を読了済みなら再利用する。

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 最初の語がbatchと完全一致する時 | `references/batch.md` | 判断 | `read:agent-toolkit:*/skills/session-review/references/batch.md` | |
| batchに一致しない時、または引数なしの時 | `references/current-session.md` | 判断 | `read:agent-toolkit:*/skills/session-review/references/current-session.md` | |
