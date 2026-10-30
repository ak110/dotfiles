---
name: process-wi
description: >
  対象リポジトリのAWIを計画、実装、レビュー、公開まで処理するときに起動する。
  single-lane引数で主作業ツリーの単一処理、all-repos引数で登録全体の処理を行う。
  常駐process-loopの統括担当もall-reposで起動する。
---

# AWIの処理

引数の最初の語を確認し、共通工程を始める前に次の表から1つの処理資料を全文読む。
先頭語が完全一致する場合だけモードとして消費し、残りを追加指示として保持する。
先頭の空白と語の区切りには空白・タブ・改行を使える。
一致しない場合と引数なしは通常のレーン処理とし、引数全体を追加指示として扱う。
選んだ資料の工程に従い、他のモードの処理資料はそのモードへ切り替える時だけ読む。

## 名指し参照の読込方法

選んだ資料とその条件に合う参照先を、判断や操作の前に全文読む。同じ版を読了済みなら再利用する。

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 最初の語がsingle-laneと完全一致する時 | `references/single-lane.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/single-lane.md` | |
| 最初の語がall-reposと完全一致する時 | `references/all-repos.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/all-repos.md` | |
| 上記の語に一致しない時、または引数なしの時 | `references/lanes.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/lanes.md` | |
