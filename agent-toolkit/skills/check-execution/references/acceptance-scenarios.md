# 受入シナリオの品質基準と試行の記録

品質の定義元は`agent-basics:check-execution`の`references/acceptance-scenarios.md`である。呼出元が指定した条件と読込範囲を、同名の正式な品質資料へ引き継ぐ。節指定がある場合は移設先の実見出しへ接続し、指定がない場合は従来の全文読込契約を保つ。取得済みの同版本文は再取得せず使う。

受入シナリオ検証の工程定義は`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`「用語」に従う。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:check-execution/references/acceptance-scenarios.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/acceptance-scenarios.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/workflow-phases.md#「用語」` | 判断 | `section:agent-toolkit:*/share/workflow-phases.md#「用語」` |
| 対話の継続と成果物の達成を確かめる試行を選ぶ前 | `../../refine-prompt/references/multi-turn-trials.md` | 判断 | `read:agent-toolkit:*/skills/refine-prompt/references/multi-turn-trials.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/agents-server-explore.md#「文書の判定試行」` | 判断 | `section:agent-toolkit:*/share/agents-server-explore.md#「文書の判定試行」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../session-review/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/session-review/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:check-execution/references/acceptance-scenarios.md#「検証資源の分離」` | 判断 | `section:agent-basics:*/skills/check-execution/references/acceptance-scenarios.md#「検証資源の分離」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:check-execution/references/acceptance-scenarios.md#「常駐サービスの公開前の観測」` | 判断 | `section:agent-basics:*/skills/check-execution/references/acceptance-scenarios.md#「常駐サービスの公開前の観測」` |

## エージェント向け文書の試行

対話の継続と成果物の達成には、読込表の基準に従って、同じ固定課題による手動の多ターン試行を選べる。
読み取り専用の文書の判定試行は以下に従う。

- 実装担当は読み取り専用の委譲へ固定入力と対象文書を渡し、委譲先はスキルを実際に起動せず文書の判定を入力へ適用して結果を返す。読取範囲と返却根拠には`${CLAUDE_PLUGIN_ROOT}/share/agents-server-explore.md`「文書の判定試行」を適用し、別の読み取り専用の起動手段を使う場合もその契約を委譲入力へ含める
- 抜粋だけの判定や未読を文書不足とする返却は、必要な読取を指示し直してから合否を判定する
- 実装担当は入力、対象文書の版（commitと相対パス、または本文を複製した絶対パス）、返却、期待結果との比較と合否をmanaged-tempの記録へまとめ、その絶対パスを検証結果へ記す。別ファイルへ保存した入力・返却・原稿も同じ記録から絶対パスで辿れるようにし、実行レビュー担当が取得できる状態にする
- 試行では文書を読むだけなので、スキルが定める起動してよいエージェントの制限（`agent-toolkit:session-review`を最上位セッションのメインに限る規定など）は実際の起動時に適用する。実際にスキルを起動する場合はその制限と操作の認可に従う

## 検証資源の分離

品質の定義は`agent-basics:check-execution`の`references/acceptance-scenarios.md`「検証資源の分離」を適用する。

## 常駐サービスの公開前の観測

品質の定義は`agent-basics:check-execution`の`references/acceptance-scenarios.md`「常駐サービスの公開前の観測」を適用する。
