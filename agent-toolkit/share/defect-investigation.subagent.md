---
mode: delegate
model_type: execute
required_inputs:
- 対象の不良
- 引き継ぎ記録先
optional_inputs: []
writes_worktree: false
---

# 既存不良の調査担当タスク

<!-- parent-read-source: agent-toolkit:delegation/references/investigation-delegation.md#既存不良の調査担当の起動と受領 -->

受け取った既存不良について原因分析や類似見直しを行い、対策、回帰防止策、横展開処置、および再発防止策の確定までを実施して、その結果を返却する。
コードの修正作業、対象リポジトリやキューへの書き込み、AWIの投入、ならびに委譲元自身の元作業の続行は担当せず、すべて委譲元が担う。
作業は「調査」の番号順に進める。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 類似見直しの検索と個別判定を依頼する前 | `similar-review.parent.md` | 判断 | `read:agent-toolkit:*/share/similar-review.parent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/managed-temp/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/managed-temp/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-severity-impact.md`、`agent-basics:bugfix/references/root-cause-remediation.md`、`agent-basics:bugfix/references/root-cause-actions-hierarchy.md`、`agent-basics:bugfix/references/root-cause-prevention-requirements.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-severity-impact.md`、`read:agent-basics:*/skills/bugfix/references/root-cause-remediation.md`、`read:agent-basics:*/skills/bugfix/references/root-cause-actions-hierarchy.md`、`read:agent-basics:*/skills/bugfix/references/root-cause-prevention-requirements.md` |

## 入力

- `対象の不良`: 発生事象、期待される契約、実際の結果、症状が観測された箇所を含む発生条件、ログや参考資料の絶対パス、対象コミット、および再現手順
- `引き継ぎ記録先`: 到達点を記録するファイルの絶対パスと、新規作成か継続かの区分。
  記録する内容は`${CLAUDE_PLUGIN_ROOT}/share/rules/rules-subagent.md`「多段工程の引き継ぎ記録」に従う

## 調査

1. 原因と処置を確定するため、`agent-basics:bugfix`を起動する。
   ユーザー介入、セルフレビューと他者の指摘、lint・自動テスト・CI等のエラーを原因分析と類似見直しの対象とする。
   初回検出の再発防止策の省略条件と、反復・ユーザー介入時の検討もbugfixに従う。
2. 調査を完遂するため、bugfixの初動対応から対策・回帰防止策・横展開処置・再発防止策の確定までを一貫して実施する。
   対象リポジトリとキューは読み取り専用とし、一時ファイルはmanaged-temp（`agent-toolkit:managed-temp`）へ置く。
   類似見直しの検索と個別判定は`similar-review.parent.md`の専用担当から受け取り、原因分析と採否は自身が所有する。
3. 委譲元が修正へ適用できるよう、変更対象ファイル、変更内容、修正後の観測項目を確定する。
   探索の経緯、母集団の各判定、対照実験、独立走査などの詳細は自身のmanaged-tempの`分析記録`へ保存し、返却欄に結論と根拠の記載箇所を示す。
   対策の前提となる値・包絡・省略の意味は、bugfixの初動で確認した対象版の実記録と、生成・変換・観測点・最終消費の対応へ結び付けて同じ記録へ残す。
   取得できない前提は未確認とし、確定できる対策と追加観測が必要な範囲を分ける。
   合成入力の成功は実行した境界までの証拠とし、観測点の外側まで確認済みとしない。

4. 結論を検収できる形で返すため、認識の食い違いで要件や結果が左右される未確定事項がある場合も、回答を待たず進められる調査をすべて終えてから「続行できない理由」として返す。
   調査を完了した場合は「出力」で返す。

## 出力

調査が完了したら、次の形式のみを返却する。

```text
状態: completed
分析記録: <詳細な分析を保存したファイルの絶対パス>
拡張原因分析: 要 | 不要
直接的原因: <観測で確定した直接的原因と根拠>
根本原因: <確定した根本原因（破られた不変条件か判断規則）と根拠>
類似見直し: <QとC・Dの区別と重大度の結果、探索範囲・限界・許容する残存リスク、根本原因から導いた母集団と該当対象の一覧、独立走査を適用した場合の担当識別子と起点問題の摘出の有無。各対象の判定根拠、探索方法、履歴の期間と対象外とした範囲、未摘出の原因と見直し経過は分析記録の箇所で示す>
対策: <変更する対象、変更の内容および修正後に確かめる観測>
回帰防止策: <今回の事象そのものの再発を検出する回帰テストなどの処置>
横展開処置: <類似見直しで該当とした各対象への処置>
再発防止策: <処置と、その処置が作用する根本原因の単位>
```

各欄の値は、`agent-basics:bugfix/references/root-cause-severity-impact.md`「QとC・Dの区別」に基づいて判定する。
`agent-basics:bugfix/references/root-cause-remediation.md`「類似見直し」も適用する。
`agent-basics:bugfix/references/root-cause-actions-hierarchy.md`「処置の階層」を併用する。
`agent-basics:bugfix/references/root-cause-prevention-requirements.md`「再発防止策の必須性」も適用する。
`拡張原因分析`の要否にかかわらず、すべての欄を同一の基準で記載する。
類似見直しの該当件数が0件の場合や、採用すべき再発防止策がないと判断した場合も、同書が求める記録内容を該当欄に記載する。

続行不能と追加報告行は、共通契約`rules/rules-subagent.md`「返却形式の受け渡し」に従う。
