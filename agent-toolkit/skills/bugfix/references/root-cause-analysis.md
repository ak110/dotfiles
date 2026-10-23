# 原因分析の品質基準

品質の定義元は`agent-basics:bugfix`の`references/root-cause-analysis.md`である。呼出元が指定した条件と読込範囲を、同名の正式な品質資料へ引き継ぐ。節指定がある場合は移設先の実見出しへ接続し、指定がない場合は従来の全文読込契約を保つ。取得済みの同版本文は再取得せず使う。

ユーザー介入・規範の未適用・分担・記録の運用は`analysis-operations.md`を全文読んで適用する。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-analysis.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `analysis-operations.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/references/analysis-operations.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md#「原因分析の品質確認」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「原因分析の品質確認」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md#「QとC・Dの区別」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「QとC・Dの区別」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `analysis-operations.md#「ユーザー介入と確認の判定」` | 判断 | `section:agent-toolkit:*/skills/bugfix/references/analysis-operations.md#「ユーザー介入と確認の判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `analysis-operations.md#「規範の未適用と目的逸脱」` | 判断 | `section:agent-toolkit:*/skills/bugfix/references/analysis-operations.md#「規範の未適用と目的逸脱」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md#「前提の再検討」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「前提の再検討」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md#「反復した指摘の原因判定」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「反復した指摘の原因判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md#「情報探索の問題」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「情報探索の問題」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md#「反復する加工の問題」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「反復する加工の問題」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md#「類似見直し」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「類似見直し」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md#「処置の階層」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「処置の階層」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md#「再発防止策」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「再発防止策」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md#「再発防止策の必須性」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「再発防止策の必須性」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md#「類似見直しと再発防止の完了」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「類似見直しと再発防止の完了」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md#「再発防止としてのテスト」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「再発防止としてのテスト」` |

## 原因分析の品質確認

品質の定義は`agent-basics:bugfix`の`references/root-cause-analysis.md`「原因分析の品質確認」を適用する。

## QとC・Dの区別

品質の定義は`agent-basics:bugfix`の`references/root-cause-analysis.md`「QとC・Dの区別」を適用する。

## ユーザー介入と確認の判定

運用の定義は`analysis-operations.md`「ユーザー介入と確認の判定」を適用する。

## 規範の未適用と目的逸脱

運用の定義は`analysis-operations.md`「規範の未適用と目的逸脱」を適用する。

## 前提の再検討

品質の定義は`agent-basics:bugfix`の`references/root-cause-analysis.md`「前提の再検討」を適用する。

## 反復した指摘の原因判定

品質の定義は`agent-basics:bugfix`の`references/root-cause-analysis.md`「反復した指摘の原因判定」を適用する。

## 情報探索の問題

品質の定義は`agent-basics:bugfix`の`references/root-cause-analysis.md`「情報探索の問題」を適用する。

## 反復する加工の問題

品質の定義は`agent-basics:bugfix`の`references/root-cause-analysis.md`「反復する加工の問題」を適用する。

## 類似見直し

品質の定義は`agent-basics:bugfix`の`references/root-cause-analysis.md`「類似見直し」を適用する。

## 処置の階層

品質の定義は`agent-basics:bugfix`の`references/root-cause-analysis.md`「処置の階層」を適用する。

## 再発防止策

品質の定義は`agent-basics:bugfix`の`references/root-cause-analysis.md`「再発防止策」を適用する。

## 再発防止策の必須性

品質の定義は`agent-basics:bugfix`の`references/root-cause-analysis.md`「再発防止策の必須性」を適用する。

## 類似見直しと再発防止の完了

品質の定義は`agent-basics:bugfix`の`references/root-cause-analysis.md`「類似見直しと再発防止の完了」を適用する。

## 再発防止としてのテスト

品質の定義は`agent-basics:bugfix`の`references/root-cause-analysis.md`「再発防止としてのテスト」を適用する。
