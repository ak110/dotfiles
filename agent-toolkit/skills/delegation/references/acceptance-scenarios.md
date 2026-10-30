# 受入シナリオの品質基準と試行の記録

受入シナリオ検証に関する品質基準の定義元は、`agent-basics:check-execution/references/acceptance-scenarios.md`である。呼び出し元の取得条件と注目箇所を引き継ぎ、本書と正式な資料を全文読む。節指定がある場合は、指定された見出しを持つ資料に注目する。両方に同名の見出しがあれば両方に注目する。すでに同一バージョンの本文を取得している場合は、再取得せずにその内容を再利用する。

受入シナリオ検証における工程の定義は、`agent-toolkit:/share/workflow-phases.md`の「用語」に従う。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:check-execution/references/acceptance-scenarios.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/acceptance-scenarios.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:/share/workflow-phases.md#「用語」` | 判断 | `section:agent-toolkit:*/share/workflow-phases.md#「用語」` |
| 対話の継続と成果物の達成を確かめる試行を選ぶ前 | `agent-toolkit:refine-prompt/references/multi-turn-trials.md` | 判断 | `read:agent-toolkit:*/skills/refine-prompt/references/multi-turn-trials.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:/share/agents-server-explore.md#「文書の判定試行」` | 判断 | `section:agent-toolkit:*/share/agents-server-explore.md#「文書の判定試行」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:session-review/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/session-review/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:check-execution/references/acceptance-scenarios.md#「検証資源の分離」` | 判断 | `section:agent-basics:*/skills/check-execution/references/acceptance-scenarios.md#「検証資源の分離」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:check-execution/references/acceptance-scenarios.md#「常駐サービスの公開前の観測」` | 判断 | `section:agent-basics:*/skills/check-execution/references/acceptance-scenarios.md#「常駐サービスの公開前の観測」` |

## エージェント向け文書の試行

対話の継続性や成果物の達成度を確認する手段として、読込表の基準に従い、同一の固定課題を用いた手動のマルチターン対話試行を選択できる。
読み取り専用で行う文書の判定試行については、以下の手順に従う。

- 実装担当エージェントは、読み取り専用の委譲タスクとして固定入力と対象文書を渡し、委譲先はスキルを実際に起動せずに文書の判定ルールを入力データへ適用して結果を返す。読み取り範囲と回答根拠の提示には、`agent-toolkit:/share/agents-server-explore.md`「文書の判定試行」を適用し、他の読み取り専用起動手段を用いる場合もその契約内容を委譲時の入力に含める。
- 一部の抜粋のみに基づく判定や、未読部分がある状態で「文書が不足している」とする回答が返された場合は、必要な読み込みを指示し直した上で合否を判定する。
- 実装担当エージェントは、入力内容、対象文書のバージョン（コミットハッシュと相対パス、または本文を複製した絶対パス）、委譲先からの回答、期待結果との比較内容、および合否判定をmanaged-temp内の記録ファイルにまとめ、その絶対パスを検証結果に記載する。別のファイルに保存した入力データ、回答内容、および原稿テキストも同じ記録から絶対パスで参照できるように構成し、実行レビュー担当エージェントが取得・確認できる状態を整える。
- 文書の判定試行は文書の内容を読み取るだけの処理であるため、スキル仕様が定める「起動可能なエージェントの制限」（例えば`agent-toolkit:session-review`を最上位セッションのメインエージェントに限定する規定など）は、後で実際にスキルを起動する段階で適用する。実際にスキルを起動する処理を行う際は、その制限規定と操作認可に厳格に従う。

## 検証資源の分離

品質基準の定義は、`agent-basics:check-execution/references/acceptance-scenarios.md`「検証資源の分離」を適用する。

## 常駐サービスの公開前の観測

品質基準の定義は、`agent-basics:check-execution/references/acceptance-scenarios.md`「常駐サービスの公開前の観測」を適用する。
