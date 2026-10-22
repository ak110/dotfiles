# 診断の品質基準と基準版

品質の定義元は`agent-basics:check-execution`の`references/diagnostics.md`である。呼出元が指定した条件と読込範囲を、同名の正式な品質資料へ引き継ぐ。節指定がある場合は移設先の実見出しへ接続し、指定がない場合は従来の全文読込契約を保つ。取得済みの同版本文は再取得せず使う。

基準版は通常の変更では実装着手直前HEAD、レーンでは初回実装前HEAD（担当切替と履歴統合後も同じ基準からの累積差分）、CI修正では修正前の版、統合では統合前HEADとする。是正を要する既存不良の扱いは`agent-toolkit/rules/01-agent.md`「完遂と先送り」に従い、委譲先は対象と方向を委譲元へ返す。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:check-execution/references/diagnostics.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/diagnostics.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:check-execution/references/diagnostics.md#「読む出力」` | 判断 | `section:agent-basics:*/skills/check-execution/references/diagnostics.md#「読む出力」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:check-execution/references/diagnostics.md#「基準版と変更行への帰属」` | 判断 | `section:agent-basics:*/skills/check-execution/references/diagnostics.md#「基準版と変更行への帰属」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:check-execution/references/diagnostics.md#「合否」` | 判断 | `section:agent-basics:*/skills/check-execution/references/diagnostics.md#「合否」` |

## 読む出力

品質の定義は`agent-basics:check-execution`の`references/diagnostics.md`「読む出力」を適用する。

## 基準版と変更行への帰属

品質の定義は`agent-basics:check-execution`の`references/diagnostics.md`「基準版と変更行への帰属」を適用する。

## 合否

品質の定義は`agent-basics:check-execution`の`references/diagnostics.md`「合否」を適用する。
