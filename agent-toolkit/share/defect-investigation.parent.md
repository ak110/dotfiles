# 既存不良の調査担当の起動と受領

```text
起動対象: defect-investigation.subagent.md
```

自分の作業で導入していない既存不良を見つけ、AWIを投入せずに見つけた主体が元の文脈で直す場合に、本書を全文読み、調査担当の起動、入力の受け渡しおよび返却値の検収へ適用する。
元の作業と文脈を分けるのは調査だけとし、修正は見つけた主体が行う。
調査をAWIとして投入する用途（`agent-toolkit:process-wi`の即時対応、公開工程の開始後に次のセッションへ回す不良など）は`${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md`を使う。`add-wi.subagent.md`はAWIの投入までを完了条件とするため、投入せずにその場で直す用途に使わない。
呼び先の作業手順と返却形式は`${CLAUDE_PLUGIN_ROOT}/share/defect-investigation.subagent.md`が定め、本書の記載対象から外す。

## 起動方法

`agents_server`の`start`によるタスク文書起動（`agent-toolkit:delegation`の`references/base-contract.md`「タスク文書起動」）で起動する。
`start_explore`は使わない。その委譲先はSkillツールを持たず、調査に要る`agent-toolkit:bugfix`を起動できないためである。
独立した不良が複数ある場合は、不良ごとに1件起動する（`agent-toolkit:delegation`の`references/routing.md`の独立した文脈による事実収集）。
`cwd`は見つけた主体の作業ディレクトリの絶対パスとする。

## 渡す入力

- `対象の不良`: 観測事象（観測した出力、エラー、差分など）、所在（ファイル、識別子、コマンド）、発見時の状況、見つけた主体の作業との関係。見つけた主体が既に確かめた事実とその根拠も含める
- `引き継ぎ記録先`: 値は`agent-toolkit:delegation`の`references/base-contract.md`「タスク文書起動」が指す`引き継ぎ記録先`の書式に従う

## 受領と検収

返却の形式は`${CLAUDE_PLUGIN_ROOT}/share/defect-investigation.subagent.md`の`## 出力`が定める。呼び出し元は次を確認する。

- `status`が`completed`なら直接的原因、類似見直し、対策、横展開処置および再発防止策の各項目が値を持ち、該当なしの項目はその根拠を持つ
- `status`が`needs_escalation`なら阻害要因が値を持つ。阻害要因が認識の違いで要件または結果を変える未確定事項である場合は、呼び出し元がユーザー確認へ送る

検収した対策、横展開処置および再発防止策に従い、見つけた主体が元の文脈で修正する。調査のやり直しは原則として省く。修正中に調査の前提と矛盾する観測が出た場合は、同じ調査担当へ矛盾する観測を示して補完を指示する。
検収に失敗した場合は、同じ調査担当へ不足する項目を示して補完を指示する。
