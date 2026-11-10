# レビュー担当側心得：再レビューの場合

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 変更した状態の散在表現と遷移の分散を走査する前 | `../../writing-standards/references/implementation-time-data.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/implementation-time-data.md` |
| 変更した状態の不変条件と保証箇所を比較する前 | `../../writing-standards/references/design-time-state.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-time-state.md` |
| 機能の正しさを確認するテスト層を選ぶ時 | `../../writing-standards/references/testing-selection.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/testing-selection.md` |
| OS分岐の試験や保存済み成功を適用判定する前 | `../../writing-standards/references/testing-selection.md`、`../../writing-standards/references/testing-platforms.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/testing-selection.md`、`read:agent-basics:*/skills/writing-standards/references/testing-platforms.md` |

## 再レビューの場合

1. 前回からの変更を正しく扱うため、前ラウンドの記録で修正方針、対応結果、違反規定、発生条件、根本原因、要求した検収手段を把握し、`reviewer-initial.md`「初回レビュー」の手順1〜3で要求・期待結果・許容範囲を確定する。
2. 成立しない前提で再指摘しないため、前提の不成立、適用条件の不一致、検収手段の到達不能を主張された指摘は他の候補より先に自身で裏付け直す。
   前提が成立しなければ取り下げ、同じ根本原因を別の観点から再提出しない。
   成立すれば裏付けを示して再指摘する。
   元要求の解釈が対立し、原文・実際の回答・現物でも解消しなければ採否を保留し、共通規定に従いユーザーへ確認できるメインエージェントへ戻す。
   回答後は手順1へ戻る。
3. 未解決と別作業を区別するため、範囲外として別の作業（課題、Issue、計画など）へ割り当てた応答は、割当先の実在と同じ趣旨を扱うことを確認する。
   両方を確認できれば対応先確保を根拠に未解決から除外する。
   範囲外とする判断への異論は、要求原文と割当先を根拠に新しい要件として区別して返す。
   実在や内容を確認できなければ、割当先と不足根拠を示す通常の指摘とする。
4. 必要な範囲を漏れなく確認するため、以下で範囲を定め、`reviewer-initial.md`「初回レビュー」の手順4〜7へ再入する。
   追加発話や反論の割り込みは手順1・2で要求と前提を確認してから、この手順へ戻る。
   - 対象は修正で新たに生じた欠陥、前回未走査の直接影響範囲の欠陥、違反規定と発生条件が同一の未解消欠陥に限る。
     修正箇所と変更した識別子・契約の直接影響範囲を扱い、無関係な既存コードや記述は対象から除く。
     方式の不変条件を直接影響する全工程・状態・入力規模へ適用し、方式全体の成立を判定する
   - 過去に証拠不足で取得待ちと回答された範囲は、直接影響範囲外でも裏付けの取得を試みる。
     欠陥を確認できれば指摘し、認められなければ追加を見送り、取得できなければ証拠不足として再記録する。
     過去の記録行は残す
   - 規定と発生条件が同一の未解消指摘は、根本原因と設計の再検討を求める
   - 初回に判定できた軽微な新論点は全体走査をやり直さず、初回の確認不足として記録にとどめる。
     重大な見落としや修正による新たな欠陥は、根拠を添えて指摘する

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| レビュー担当側心得：初回レビューの基準を適用する前 | `reviewer-initial.md` | 判断 | `read:agent-basics:*/skills/review-standards/references/reviewer-initial.md` |
