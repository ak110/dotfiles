---
name: careful-review
description: >
  対象範囲を指定するユーザー手動レビューで起動する。
  plan-impl-executorは起動元ではなく、共通の実装差分レビューreferenceを直接利用する。
---

# careful-review

対象差分全体を単一のレビュー系へ委譲し、採用指摘を実装・修正系へ反映する。
レビュー担当と修正担当を分離し、レビュー前後の実体比較で成果物を保護する。

## 入力

- 対象範囲と不変参照点
- 計画ファイルの絶対パス（省略可）
- 変更の意図と意図的に許容した挙動変化
- 継続するレビュー系と実装・修正系の経路、`threadId`、Claude代替履歴

計画ファイルを省略する場合は、ファイルパス列挙またはgit差分範囲による対象範囲の直接指定を必須とする。
計画ファイルと対象範囲の両方が無い場合は`needs_escalation`で返す。
計画ファイルがあり対象範囲が無い場合は、計画着手前コミットから`HEAD`までの差分を対象とする。
不変参照点は計画に記載されたSHAを優先する。

`plan-impl-executor`から起動された場合は受領した両系統を継続する。
単独起動時は新しいレビュー系と実装・修正系を開始する。

## 起動

1. `agent-toolkit:codex-exec`を起動する
2. `${CLAUDE_PLUGIN_ROOT}/skills/codex-exec/references/plan-codex-implementation-review.md`と
   `${CLAUDE_PLUGIN_ROOT}/skills/codex-exec/references/plan-codex-implementation-review-task.md`をReadする
3. 対象ファイルの内容を退避し、内容ハッシュと`git diff`を記録する
4. 対象差分全体を1つのレビュー系へ委譲する
5. レビュー応答の受領後に内容ハッシュと差分を比較する

観点・カテゴリ・ファイルによる複数起動は行わない。
レビュー担当には対象を読み取り専用とし、指摘だけを返すよう指示する。

## 指摘の統合と修正依頼

指摘を通番・重大度／観点・区分・箇所・内容・対応方針の6列表へ統合する。
重複指摘は根拠と影響範囲を保持して1件へまとめる。
不対応と判断した指摘には、対象成果物の最上位責任主体が理由を注記する。

指摘の採否は、対象成果物に責任を持つ`careful-review`の呼び出し元が実測確認のうえで確定する。
事実確認済みの致命的・重大指摘は採用し、軽微指摘はメインが品質への寄与を基準に採否を判断する。
対象範囲の変更や方針の再構成を要する指摘は、設計判断が必要な指摘として扱う。
設計判断が必要な場合、codex経路では判断材料をプロンプトへ含め、
Claude代替では`model: opus`を指定し、実装・修正系には採否案と技術的根拠を返させる。
呼び出し元は採否案と対象の実体を照合して採否を確定する。
不対応注記は呼び出し元が付し、実装・修正系は指摘内容と見解の記載に留める。
呼び出し元が採否を確定できない場合は、観測事実と選択肢を`needs_escalation`で返す。

レビュー中の変更を検知した場合は、
`plan-codex-implementation-review.md`「変更検知と復元」節に従って
呼び出し元の明示的な実行確認を得た後に限り、実装・修正系へ復旧を委譲する。
復元後の内容ハッシュと差分がレビュー前の状態へ戻ったことを確認する。

採用指摘は原文、重大度、対象箇所、期待結果、再検証条件を実装・修正系へ全文で渡す。
修正後は同じレビュー系へ前回指摘と反映結果を渡して再レビューする。
codex経路では各系統の`threadId`を継続する。
Claude代替では前回応答全文を渡して毎回新規起動する。

初回1回と再レビュー4回の合計5ラウンドを上限とする。
致命的・重大の指摘が解消し、採用した軽微指摘の反映を確認した時点で完了とする。
5ラウンド目にも致命的・重大指摘が残る場合は完了とせず、指摘全文、重大度、対象箇所、
区分、根拠、必要な修正を`findings`へ記載して`needs_escalation`で返す。

## 出力

```text
status: completed | needs_escalation
summary: <結果>
implementation_route: codex | claude | unavailable | not_started
review_route: codex | claude | unavailable | not_started
implementation_thread_id: <threadIdまたは「なし」>
review_thread_id: <threadIdまたは「なし」>
review_rounds: <回数>
findings:
- <統合した指摘と対応結果>
verification:
- <レビュー前後比較と修正後検証>
```
