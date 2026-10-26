# 原因分析の品質基準

品質基準の定義元は`agent-basics:bugfix`の`references/root-cause-analysis.md`である。呼び出し元が指定した条件と読み込み範囲は、同名の正式な品質基準資料へ引き継ぐ。節の指定がある場合は移設先の該当見出しを参照し、指定がない場合は資料全体の読み込みとして扱う。すでに取得済みの同一バージョンの本文がある場合は、再取得せずにそれを再利用する。

ユーザー介入、規範の未適用、役割分担、および記録の運用手順については、`analysis-operations.md`を全文読み込んで適用する。

## 名指し参照の読込方法

本文の各参照で定めた適用条件に従い、選んだ資料の指定範囲を読み込んでから、その基準に基づく具体的な判断へ進む。起動時の行に記載された資料は、起動時のプロンプトとともに提供される。

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

品質基準の詳細は、`agent-basics:bugfix`の`references/root-cause-analysis.md`「原因分析の品質確認」を参照して適用する。

## QとC・Dの区別

品質基準の詳細は、`agent-basics:bugfix`の`references/root-cause-analysis.md`「QとC・Dの区別」を参照して適用する。

## ユーザー介入と確認の判定

運用の詳細は、`analysis-operations.md`「ユーザー介入と確認の判定」を参照して適用する。

## 規範の未適用と目的逸脱

運用の詳細は、`analysis-operations.md`「規範の未適用と目的逸脱」を参照して適用する。

## 前提の再検討

品質基準の詳細は、`agent-basics:bugfix`の`references/root-cause-analysis.md`「前提の再検討」を参照して適用する。

## 反復した指摘の原因判定

品質基準の詳細は、`agent-basics:bugfix`の`references/root-cause-analysis.md`「反復した指摘の原因判定」を参照して適用する。

## 情報探索の問題

品質基準の詳細は、`agent-basics:bugfix`の`references/root-cause-analysis.md`「情報探索の問題」を参照して適用する。

## 反復する加工の問題

品質基準の詳細は、`agent-basics:bugfix`の`references/root-cause-analysis.md`「反復する加工の問題」を参照して適用する。

## 類似見直し

品質基準の詳細は、`agent-basics:bugfix`の`references/root-cause-analysis.md`「類似見直し」を参照して適用する。

## 処置の階層

品質基準の詳細は、`agent-basics:bugfix`の`references/root-cause-analysis.md`「処置の階層」を参照して適用する。

## 再発防止策

品質基準の詳細は、`agent-basics:bugfix`の`references/root-cause-analysis.md`「再発防止策」を参照して適用する。

## 再発防止策の必須性

品質基準の詳細は、`agent-basics:bugfix`の`references/root-cause-analysis.md`「再発防止策の必須性」を参照して適用する。

## 類似見直しと再発防止の完了

品質基準の詳細は、`agent-basics:bugfix`の`references/root-cause-analysis.md`「類似見直しと再発防止の完了」を参照して適用する。

## 再発防止としてのテスト

品質基準の詳細は、`agent-basics:bugfix`の`references/root-cause-analysis.md`「再発防止としてのテスト」を参照して適用する。
