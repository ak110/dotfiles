# 実行レビュー担当の起動とレビュー修正

```text
起動対象: exec-review.subagent.md
```

メインエージェントは、実装完了後のHEADに対する実行レビュー、指摘の修正、コミット履歴の統合を管理する。
本書は起動・入力生成・修正・管理表保存を扱う。
担当手順は`${CLAUDE_PLUGIN_ROOT}/share/exec-review.subagent.md`に定める（所在参照）。
分類・判定基準は`agent-basics:review-standards`と`exec-review-criteria.md`に従う。

**1.** レビュー担当が判定できる証拠と残工程を揃える。

実装担当またはメインは、計画の検証コマンドと受入シナリオの結果を`検証結果`として返し、進捗ログ（計画なしは引き継ぎ記録）へ記録する。
メイン自身の実装も完了前に同様に記録する。
形式は`verification-evidence-records.md`「完成条件証拠の検証記録」、手動観測と全量保存は「手動観測の保存」に従う。
レビュー担当が記録を読むため、プロンプトへ再掲しない。
任意入力の`受理済みの完成条件証拠`には、同じ計画の進捗ログに保存した受理済みの実行レビュー返却ファイルのうち、後続の担当が読めるものの絶対パスを渡す（無ければ`なし`）。
参照できる条件と判定基準は、`exec-review-recording-evidence.md`「受理済み判定への参照」に従う。
最終検証と回数を限った手動観測の時機は`../skills/commit/references/commit-verification.md`「検証」に従う。
保存済みの成功記録と失敗対象だけの再実行結果の合成は`agent-basics:check-execution/references/verification-scope-reruns.md`「実行条件と再実行」に従う。
委譲元が管理する残工程があれば、後続の担当と再開先を対応付け、検証済み・未実施・条件変更の各判断を整理してレビュー担当へ渡す。
根拠の十分さと要件・外部仕様の達成は、実行レビュー担当が判定する。
完成条件証拠（返却の条件別判定行）を書くのもレビュー担当である。

画面表示に変更がある場合は並列画面レビュー（`workflow-phases.md`「用語」参照）を行う。
ユーザビリティレビュー担当の起動と検収は`usability-review.parent.md`に従う。

文章成果物のレビュー（読者適合性レビュー）は、`reader-fit-review.parent.md`の直接確認条件・起動単位に従う。
通常リスクを含む一括起動の識別子をファイル別申告へ対応付け、初回・再レビュー・引き継ぎ再レビューとも、管理外ファイル・回答下書きの不足や失敗を含め、対象ファイルと想定読者ごとに検収する。

## 入力

<!-- input-contract:exec-review:start -->
<!-- 生成元: share/exec-review.subagent.mdのrequired_inputs・optional_inputs。手動編集しない。 -->
入力の定義はこの起動契約とagents_serverの`start`の検査が所有する。
起動準備はこの契約の入力一覧で完結する。

- 必須入力名: `レビュー基準`、`引き継ぎ記録先`
- 任意入力名: `計画`、`CI記録`、`WI記録`、`review_contract`、`評価用WI原本`、`修正系列の開始時のHEAD`、`原因commitOID`、`開始時のHEAD`
- 任意入力名: `レーン識別子`、`レビュー種別`、`round`、`対象ファイル限定`、`Git管理外の変更`、`前回確認版`、`担当モデル`、`rewrite_map`、`受理済みの完成条件証拠`

<!-- input-contract:exec-review:end -->

レビュー評価で過去の要求を固定する場合だけ、任意入力`評価用WI原本`へ、WI名から保存本文の絶対パスへのJSON辞書の絶対パスを渡す。
計画・対応記録・WIの保存版を評価事例で確定し、条件表の生成・起動前検査・レビュー・完成条件証拠の受領で同じ原本を使う。
一覧の不足・読取不能は対象と修正する入力を示して失敗させ、最新キューで補わない。
通常のWI処理ではこの入力を渡さず、現行キューの契約を維持する。

入力値の確定と起動前の検収は`exec-review-parent-launch.md`に従う。
`subagent_md_path`には`exec-review`を渡す。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 形式判別前 | `../skills/wi-standards/references/awi-body.md` | | |
| 基準の参照時 | `agent-basics:review-standards/SKILL.md` | | |
| 検証結果を記録する前 | `verification-evidence-records.md` | | |
| 手動観測を保存する前 | `verification-evidence-observation.md` | | |
| 最終の検証と有限の手動観測の時機を判断する前 | `../skills/commit/references/commit-verification.md` | | |
| 保存済み成功と失敗限定の結果を合成する前 | `agent-basics:check-execution/references/verification-scope-reruns.md` | | |
| 基準の参照時 | `exec-review-recording-evidence.md` | | |
| 基準の参照時 | `agent-toolkit:/share/workflow-phases.md` | | |
| 基準の参照時 | `usability-review.parent.md` | | |
| 基準の参照時 | `reader-fit-review.parent.md` | | |
| 基準の参照時 | `../skills/delegation/references/base-contract-handoff.md` | | |
| 基準の参照時 | `exec.parent.md`、`exec-parent-integration.md` | | |
| 基準の参照時 | `../skills/wi-standards/references/origin-and-approval.md` | | |
| 返却の`達成`行の根拠を受領確認する前 | `exec-review-recording-grounds.md` | 判断 | `delegation-result`、`read:agent-toolkit:*/share/exec-review-recording-grounds.md` |
| 基準の参照時 | `../skills/wi-standards/references/wi-standards-origin.md` | | |
| 基準の参照時 | `../skills/report-completion/references/report-formats-completion.md` | | |
| 基準の参照時 | `agent-basics:bugfix/references/root-cause-severity-impact.md`、`agent-basics:bugfix/references/root-cause-remediation.md`、`agent-basics:bugfix/references/root-cause-prevention-verification.md` | | |
| 基準の参照時 | `../skills/commit/references/history-rewrite-wi-records.md` | | |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 完成条件証拠の検収時に、計画や委譲プロンプトから複数のWI名を取得する場合 | `agent-toolkit:wi-standards`の`references/wi-show-bulk.md` | 判断 | `delegation-result`、`read:agent-toolkit:*/skills/wi-standards/references/wi-show-bulk.md` |
| `review_contract`の要否を判断し、生成・再生成する前 | `exec-review-contract-generation.md` | | |

<!-- return-contract:exec-review:start -->
<!-- 生成元: share/exec-review.subagent.md「出力」。手動編集しない。 -->
## 実行レビューの返却契約

以下の本文返却に加え、同じ担当が正式原本を保存して所在だけを返す方式を選べる。
正式原本には必須7行と必要な全条件判定行を記載する。
保存後に全文を読み戻し、HEAD・round・管理表・読者の全対象・条件番号と根拠が判定結果と一致することを確かめる。
不足は返却前に同じ原本へ補う。
検収済み原本を使う返却通知は次の2行だけとし、同じ原本をパスで引き渡す。

```text
状態: completed
正式原本: <この担当が保存・読み戻しした正式原本の絶対パス>
```

委譲元は終了した担当と原本を対応付け、その本文へ共通の受領条件と従来の検収を適用する。
短い本文返却と正式原本返却のどちらでも、以下の必須内容と判定基準を保つ。

- 読者別探索は`{"files":{"<相対パス>":{"sessions":["<ID>"]}},"external":{"<絶対パス>":{"sessions":["<ID>"]}}}`の1行JSONで申告する。
  IDにはUUID、16進数、`agent-`付き16進数、Codexの`task_name`（`/root/<名前>`）を受け付け、起動結果を使う。
  全読者のIDを各ファイルへ対応付け、集約起動のIDも全対象へ記す。
  Git管理外の対象がなければ`external`は`{}`とする
- 各パスの直接確認は`{"direct":[{"読者像":"...","判定":"...","根拠":"...","証拠":"/結果.md"}]}`とする。
  別読者を起動したら`sessions`も併記する。
  省略した`direct`・`sessions`は空配列として扱う。
  適用条件と証拠の保存は`reader-fit-review.parent.md`「起動」に従う。
  `external`は直接確認の対象外とする
- Git管理下における申告対象は、削除されたものを除く変更対象の`.md`および`.md.tmpl`ファイルのうち、今回のレビュー判定対象commitによって変更された範囲とし、ファイル名が変更された場合は新しいパスを記載する。
  `review_contract`で判定対象外とされたcommitのみによる変更は除外し、どの計画にも属していないcommitによる変更は対象として残す。
  2回目以降のレビューで前回と今回のファイル別差分が完全に一致しているファイルに限り、値を`{"omission":"再点検対象なし"}`と指定できる
- Git管理外における申告対象は、回答下書きの絶対パス、および`Git管理外の変更`として受け取った元ファイルのうち`.md`や`.md.tmpl`の絶対パスとする。
  roundが2以上で、前回の複製本文と現行本文のバイト列が完全に一致している場合に限り、`external`の値を`{"omission":"再点検対象なし","previous":"<前回本文の複製の絶対パス>"}`と指定できる
- 初回レビューでは理由をつけて省略することはせず、Git管理下および管理外の対象ファイルがともに存在しない場合に限り、`文章成果物なし`と申告する

延期対象となる項目や、本セッションの実行内で完了しない項目については、既存の引き継ぎ記録の段落に条件1件ごとに以下の情報を記述し、判定行の根拠からその記録を参照する（複数の条件をまとめた要約表現は記録として認められない）。

- `AWI: <WIファイル名|計画由来>`、`判定対象: <完成条件か要求単位の原文を逐語>`
- `終了区分: 延期adopt`または`終了区分: 終了しない`（`exec.parent.md`、`exec-parent-integration.md`の`AWI終了区分`のうち、統合時に`adopt`しない値）、および実際の結論を記載した`後続工程:`、`検収時機:`
- 公開工程で判定する条件は`判定工程: 公開工程`、並行して実施中の画面レビューで判定する条件は`判定工程: ユーザビリティレビュー`かつ`進行状態: 並行中`

返却の状態行、続行不能の区分と理由、標識行および一行返却は`rules/rules-subagent.md`「返却形式の受け渡し」に従う。
完成条件証拠の提出が要求されている場合は末尾に`exec-review-recording-evidence.md`「条件別判定行の形式」に従った判定行を全条件の数だけ記載し、証拠要求がない場合は判定行を配置しない。
一般的な要約などの地の文は加えずに、役割固有の規定行だけを返す。

```text
状態: completed
レビューHEAD: <レビューした対象HEADの完全OID>
未解決の指摘数: <未解決指摘数>
読者別探索: <ファイル別JSON申告、または文章成果物なし>
レビュー開始時点: <今回の開始時点の完全OID>
前回確認版の開始時点: <前回の開始時点の完全OID|なし>
前回確認版HEAD: <前回のレビューHEADの完全OID|なし>
<WI> 完成条件<N>: 達成|未達|根拠不足 — 根拠: <実行結果またはパス:行>
<WI> 原文要求<N>「<要求単位の逐語>」: 達成|未達|根拠不足 — 根拠: <実行結果またはパス:行>
```

`達成`行の`根拠:`には、`exec-review-recording-grounds.md`「根拠の書き方」で規定された確認場所と観測内容の組み合わせを記述し、この組み合わせを提示できない条件は`根拠不足`とする。

現在のroundにおいて未回答の指摘が1件以上残っている返却は「指摘ありのcompleted」であり、回答の記入は後ほどレビュー対象者が行う。
指摘が0件であればレビュー収束の候補となり、1件以上であれば修正作業へ引き渡す正常な完了状態となるため、いずれの場合も`状態`は`completed`となる。
複数のレビュー基準を受け取った場合、`未解決の指摘数`は全基準の合算値を記載する。
<!-- return-contract:exec-review:end -->

## レビュー工程の運用資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| レビュー工程の判定・記録を始める前 | `review-table-operations.md` |
| レビュー工程の判定・記録を始める前 | `exec-review-criteria.md` |
| レビュー工程の判定・記録を始める前 | `exec-review-responses.md` |
| レビュー工程の判定・記録を始める前 | `exec-review-recording.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 起動方法別のレビュー基準・レビュー指摘管理表のパス・入力・起動に関する判断・作業の前 | `exec-review-parent-launch.md` | | |
| 完成条件証拠の受領確認に関する判断・作業の前 | `exec-review-parent-acceptance.md` | 判断 | `delegation-result`、`read:agent-toolkit:*/share/exec-review-parent-acceptance.md` |
| 返却に含まれる既存不良の受領に関する判断・作業の前 | `exec-review-parent-existing-defects.md` | 判断 | `delegation-result`、`read:agent-toolkit:*/share/exec-review-parent-existing-defects.md` |
| レビュー修正・レビュー指摘管理表の保存に関する判断・作業の前 | `exec-review-parent-correction.md` | | |

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| コミットの作成・履歴統合に着手する前 | `../skills/commit/SKILL.md` |
