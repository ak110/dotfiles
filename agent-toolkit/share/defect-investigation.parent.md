# 既存不良の調査担当の起動と受領

```text
起動対象: defect-investigation.subagent.md
```

自分の作業で導入していない既存不良を見つけ、AWIを投入せずに見つけた主体が元の文脈で直す場合に、本書を全文読み、調査担当の起動、入力の受け渡しおよび返却値の検収へ適用する。
元の作業と文脈を分けるのは調査だけとし、修正は見つけた主体が行う。
調査をAWIとして投入する用途（`agent-toolkit:process-wi`の即時対応、公開工程の開始後に次のセッションへ回す不良など）は`${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md`を使う。`add-wi.subagent.md`は原稿・証拠の返却、親検収と同じ担当への保存継続を通してAWIの投入まで担う。投入せずにその場で直す用途では、本書が定める調査担当を使う。
委譲先の作業手順と返却形式は`${CLAUDE_PLUGIN_ROOT}/share/defect-investigation.subagent.md`が定め、本書の記載対象から外す。

## 起動方法

`agents_server`の`start`で`defect-investigation.subagent.md`を指定して起動する（`agent-toolkit:delegation`の`references/base-contract.md`「`<役割名>.subagent.md`を指定する起動」）。
`start`では`defect-investigation.subagent.md`を指定する。この指定により、調査に要る`agent-toolkit:bugfix`を起動できる主体を使う。
独立した不良が複数ある場合は、不良ごとに1件起動する（`agent-toolkit:delegation`の`references/routing.md`の独立した文脈による事実収集）。
`cwd`は見つけた主体の作業ディレクトリの絶対パスとする。

## 渡す入力

- `対象の不良`: `agent-toolkit:bugfix`の`references/root-cause-analysis.md`「事象単位の並列調査委譲」の7項目（事象、期待する契約、実際の結果、症状を観測した所在を含む発生条件、ログと資料の絶対パス、対象commit、再現手順）。実際の結果には観測した出力、エラー、差分などを含める。見つけた主体の仮説、原因の候補と考えた実装箇所、修正案は、同節に従い手元に残す
- `引き継ぎ記録先`: 値は`agent-toolkit:delegation`の`references/base-contract.md`「`<役割名>.subagent.md`を指定する起動」が指す`引き継ぎ記録先`の書式に従う

## 受領と検収

返却の形式は`${CLAUDE_PLUGIN_ROOT}/share/defect-investigation.subagent.md`の`## 出力`が定める。委譲元は次を確認する。

- `状態`が`completed`なら直接的原因、類似見直し、対策、横展開処置および再発防止策の各項目が値を持ち、該当なしの項目はその根拠を持つ
- `状態`が`completed`なら、対策、横展開処置および再発防止策を`agent-toolkit:bugfix`の`references/root-cause-analysis.md`「再発防止策の必須性」と「再発防止としてのテスト」に照らす。各処置が類似見直しの母集団を被覆することと、L3かL4の根本原因へ作用することを確かめる。テストなら再現テストではなく再発防止テストであることも確かめる
- `続行できない理由`行を持つ返却は続行不能を示す。その理由が認識の違いで要件または結果を変える未確定事項である場合は、委譲元がユーザー確認へ送る

検収した対策、横展開処置および再発防止策に従い、見つけた主体が元の文脈で修正する。調査のやり直しは原則として省く。修正中に調査の前提と矛盾する観測が出た場合は、同じ調査担当へ矛盾する観測を示して補完を指示する。
検収に失敗した場合は、同じ調査担当へ不足する項目を示して補完を指示する。
