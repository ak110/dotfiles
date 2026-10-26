# 診断の品質基準と基準版

診断メッセージの評価や合否判定に関する品質基準の定義元は、`agent-basics:check-execution`の`references/diagnostics.md`である。呼び出し元の取得条件と注目箇所を引き継ぎ、本書と正式な資料を全文読む。節指定がある場合は、指定された見出しを持つ資料に注目する。両方に同名の見出しがあれば両方に注目する。すでに同一バージョンの本文を取得している場合は、再取得せずにその内容を再利用する。

基準版（比較のベースとなるバージョン）は、通常の変更では実装着手直前のHEADコミット、レーン運用では初回実装前のHEADコミット（担当者の交代や履歴の統合後も同一基準からの累積差分として評価する）、CI修正では修正前のバージョン、ブランチ統合では統合前のHEADコミットを採用する。是正が必要となる既存の不具合を発見した際の対応は、`agent-toolkit/rules/01-agent.md`「完遂と先送り」の定めに従い、委譲先エージェントは修正対象と修正の方向性を委譲元へ報告して引き渡す。

## 名指し参照の読込方法

本文内の各参照における適用条件を確認し、選択した資料を読み込み、指定箇所に注目してから、その基準を用いた具体的な判断に進む。起動時の行に指定された資料は、起動時の本文とともに提供される。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:check-execution/references/diagnostics.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/diagnostics.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:check-execution/references/diagnostics.md#「読む出力」` | 判断 | `section:agent-basics:*/skills/check-execution/references/diagnostics.md#「読む出力」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:check-execution/references/diagnostics.md#「基準版と変更行への帰属」` | 判断 | `section:agent-basics:*/skills/check-execution/references/diagnostics.md#「基準版と変更行への帰属」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:check-execution/references/diagnostics.md#「合否の判定」` | 判断 | `section:agent-basics:*/skills/check-execution/references/diagnostics.md#「合否の判定」` |

## 読む出力

品質基準の定義は、`agent-basics:check-execution`の`references/diagnostics.md`「読む出力」を適用する。

## 基準版と変更行への帰属

品質基準の定義は、`agent-basics:check-execution`の`references/diagnostics.md`「基準版と変更行への帰属」を適用する。

## 合否

品質基準の定義は、`agent-basics:check-execution`の`references/diagnostics.md`「合否の判定」を適用する。
