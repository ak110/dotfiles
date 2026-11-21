# 実行レビュー担当の起動とレビュー修正

```text
起動対象: exec-review.subagent.md
```

実装を終えた変更差分の実行レビューを起動し、返却を受領して、修正と収束を管理する起動側の文書である。
起動するのは実装を担当したレーン担当（single-laneではメイン）自身であり、`write-plan`ではメイン、CI修正の系列では`agent-toolkit:atk-mode/playbooks/process-wi-ci-fix.md`が定める担当である。
起動から収束までは「条件と行動」の該当資料を全文読み、示された節を適用する。
担当の手順と返却形式は`agent-toolkit:atk-mode/roles/exec-review.subagent.md`が定める。
レビューの基準は`agent-basics:review-standards`にある。

## 入力

<!-- input-contract:exec-review:start -->
<!-- 生成元: skills/atk-mode/roles/exec-review.subagent.mdのrequired_inputs・optional_inputs。手動編集しない。 -->
入力の定義はこの起動契約とagents_serverの`start`の検査が所有する。
役割固有の入力（`extra_params`）はこの契約の入力一覧で完結する。
通常起動（`mode: delegate`）の共通の準備: 起動の前に`agent-toolkit:atk-mode/references/work-contract.md`に従う全体作業契約を用意し、`extra_params`とは別の公開入力`whole_contract_path`へ絶対パスを渡す。

- 必須入力名: `レビュー基準`、`引き継ぎ記録先`
- 任意入力名: `計画`、`CI記録`、`WI記録`、`review_contract`、`評価用WI原本`、`修正系列の開始時のHEAD`、`原因commitOID`、`開始時のHEAD`
- 任意入力名: `レーン識別子`、`レビュー種別`、`round`、`対象ファイル限定`、`Git管理外の変更`、`前回確認版`、`担当モデル`、`rewrite_map`、`受理済みの完成条件証拠`

<!-- input-contract:exec-review:end -->

レビュー評価で過去の要求を固定する場合だけ、任意入力`評価用WI原本`へ、WI名から保存本文の絶対パスへのJSON辞書の絶対パスを渡す。
計画・対応記録・WIの保存版を評価事例で確定し、条件表の生成・起動前検査・レビュー・完成条件証拠の受領で同じ原本を使う。
一覧の不足・読取不能は対象と修正する入力を示して失敗させ、最新キューで補わない。
通常のWI処理ではこの入力を渡さず、現行キューの契約を維持する。

各入力の値の決め方は`agent-toolkit:atk-mode/playbooks/exec-review-loop.md`「起動」にあり、`subagent_md_path`には`exec-review`を渡す。

## 条件と行動

| 時点または条件 | 読む資料 |
| --- | --- |
| 起動の入力を決める前 | `agent-toolkit:atk-mode/playbooks/exec-review-loop.md`「起動」 |
| 返却を受領する前 | `agent-toolkit:atk-mode/playbooks/exec-review-loop.md`「完成条件証拠の受領確認」 |
| 差分外の既存不良を受領する前 | `agent-toolkit:atk-mode/playbooks/exec-review-loop.md`「返却に含まれる既存不良の受領」 |
| 修正の指示を行う前 | `agent-toolkit:atk-mode/playbooks/exec-review-loop.md`「レビュー修正」 |
| 再レビューに進む前 | `agent-toolkit:atk-mode/playbooks/exec-review-loop.md`「ラウンドの遷移」 |
| 収束を判定する前 | `agent-toolkit:atk-mode/playbooks/exec-review-loop.md`「収束と統合への引渡し」 |
| 返却の`達成`行の根拠を受領確認する前 | `agent-toolkit:atk-mode/references/review-record.md` |
| 完成条件証拠の検収時に、計画や委譲プロンプトから複数のWI名を取得する場合 | `agent-toolkit:atk-mode/references/states-and-dependencies.md` |
| 差分外の不良を受領して対処時機を決める前 | `agent-toolkit:atk-mode/playbooks/delegation-receiving.md` |
| process-wi中に差分外の既存不良や正式改善AWIを受領した時 | `agent-toolkit:atk-mode/playbooks/process-wi-immediate-fix.md` |

- 単一計画の初回でない起動は、`agent-toolkit:atk-mode/playbooks/exec-review-loop.md`「起動」の手順3が示す資料を読む。
- レビューの後にHEADが変わった時は`agent-toolkit:atk-mode/references/exec-review-head-update.md`を読む。
- `review_contract`の生成は`agent-toolkit:atk-mode/references/review-contract.md`、判定行の形式は`agent-toolkit:atk-mode/references/review-record.md`にある。
- 指摘管理表のパス・保存・操作は`agent-toolkit:atk-mode/references/review-record-table.md`にある。
- 画面差分がある時は`agent-toolkit:atk-mode/roles/usability-review.parent.md`、文章成果物の読者別探索は`agent-toolkit:atk-mode/roles/reader-fit-review.parent.md`に従う。
- 指摘を自分で直す時は`agent-toolkit:atk-mode/playbooks/review-fix.md`を使う。
- 収束の後の統合は`agent-toolkit:atk-mode/playbooks/lane-receipt.md`と`agent-toolkit:atk-mode/roles/lane-integration.subagent.md`にある。

<!-- return-contract:exec-review:start -->
<!-- 生成元: skills/atk-mode/roles/exec-review.subagent.md「出力」。手動編集しない。 -->
## 実行レビューの返却契約

以下の本文返却に加え、同じ担当が正式原本を保存して所在だけを返す方式を選べる。
正式原本には必須7行と必要な全条件判定行を記載する。
保存後に全文を読み戻し、HEAD・round・管理表・読者の全対象・条件番号と根拠が判定結果と一致することを確かめ、不足は返却前に同じ原本へ補う。
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
  適用条件と証拠の保存は`agent-toolkit:atk-mode/roles/reader-fit-review.parent.md`「起動」に従う。
  `external`は直接確認の対象外とする
- Git管理下の申告対象は、削除を除く変更対象の`.md`と`.md.tmpl`のうち、今回のレビュー判定対象commitが変えた範囲とし、改名されたファイルは新しいパスで書く。
  `review_contract`で判定対象外としたcommitだけによる変更は除き、どの計画にも属さないcommitによる変更は残す。
  2回目以降のレビューで前回と今回のファイル別差分が完全に一致するファイルに限り、値を`{"omission":"再点検対象なし"}`にできる
- Git管理外の申告対象は、回答下書きの絶対パスと、`Git管理外の変更`の元ファイルのうち`.md`と`.md.tmpl`の絶対パスとする。
  roundが2以上で、前回の複製本文と現行本文のバイト列が完全に一致する場合に限り、`external`の値を`{"omission":"再点検対象なし","previous":"<前回本文の複製の絶対パス>"}`にできる
- 初回レビューでは理由を付けて省略せず、Git管理下と管理外の対象ファイルがともに無い場合に限り`文章成果物なし`と申告する

延期する項目と、このセッションの実行内で終わらない項目は、既存の引き継ぎ記録の段落へ条件1件ごとに次を書き、判定行の根拠からその記録を指す（複数の条件をまとめた要約は記録として認めない）。

- `AWI: <WIファイル名|計画由来>`、`判定対象: <完成条件か要求単位の原文を逐語>`
- `終了区分: 延期adopt`または`終了区分: 終了しない`（`agent-toolkit:atk-mode/playbooks/process-wi-lanes.md`「採否の確定と終了区分」の区分のうち、統合時に`adopt`しない値）、および実際の結論を書いた`後続工程:`、`検収時機:`
- 公開工程で判定する条件は`判定工程: 公開工程`、並行して実施中の画面レビューで判定する条件は`判定工程: ユーザビリティレビュー`かつ`進行状態: 並行中`

返却の状態行、続行不能の区分と理由、標識行と一行返却は`agent-toolkit:atk-mode/playbooks/subagent.md`「返却形式の受け渡し」に従う。
完成条件証拠の提出を求められた場合は、末尾に`agent-toolkit:atk-mode/references/review-record.md`「条件別判定行の形式」の判定行を全条件の数だけ書き、求められていなければ判定行を置かない。
一般的な要約などの地の文は加えず、役割固有の規定行だけを返す。

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

`達成`行の`根拠:`には`agent-toolkit:atk-mode/references/review-record.md`「根拠の書き方」の所在と観測内容の組を書き、この組を示せない条件は`根拠不足`とする。

現在のroundで未回答の指摘が1件以上残る返却は「指摘ありのcompleted」であり、回答は後でレビュー対象者が書く。
指摘が0件なら収束の候補、1件以上なら修正へ渡す正常な完了であり、どちらも`状態`は`completed`とする。
複数のレビュー基準を受け取った場合、`未解決の指摘数`は全基準の合算値を書く。
<!-- return-contract:exec-review:end -->
