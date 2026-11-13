# WI投入担当の起動と受領：起動方法

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 要件と委譲元の判断を分類する前（操作対象外: ツールと結び付かない判断） | `../skills/wi-standards/references/drafting-workflow-requirements.md` | | |
| 原稿の意味を検収する前 | `agent-toolkit:wi-standards`の`references/drafting-completion.md`の採否・成立性の共通基準（同スキルの読込表の調査前・原稿確定前の範囲）と`references/draft-semantic-review.md` | 判断 | `delegation-result`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-completion.md`、`read:agent-toolkit:*/skills/wi-standards/references/draft-semantic-review.md` |
| 委譲元が投入先の対象の外で動き、起動時の`cwd`を決める時 | `../skills/process-wi/references/lanes.md` | | |

## 起動方法

1. 独立確認を保つ投入経路を選ぶ。
   通常は付随型で原稿返却・親の意味検収・保存開始の二段階とする。
   次の場面は要求ごとに`投入経路: 専念型`を指定し、共通手順と投入前レビュー（`${CLAUDE_PLUGIN_ROOT}/share/wi-draft-review.parent.md`）を経て保存まで担当者が完結させる。

   - `agent-toolkit:add-awi-by-user`において、文脈を分離する合理性があると判断して委譲する要求。
     原則としては要求の件数にかかわらず、メインエージェント自身が調査・起草・保存する。
   - 実行レビューの差分外で発見された既存不良を投入する場合（`exec-review-parent-existing-defects.md`「返却に含まれる既存不良の受領」）。
     受領したエージェントが起動、終了状態の回収、および保存結果の報告を担当する。
   - `atk wi submit`の永続受付から自己完結した要求を受け取り、独立した実行が調査・意味確認・保存・報告まで所有する場合。
     要求の由来とsourceは受付値を保持する。

   専念型の場合、委譲元は原稿全文の意味を再検収せず、保存開始の中継も行わない。
   専念型と付随型のどちらでも、役割定義における`execute`工程を使用する。
   専念型における投入先の初動調査は、担当者が主体となって進める。
   委譲元は要求と投入先を確定するために必要な範囲だけを読み、確認済みの証拠を参照として渡す。
   同一の要求に対する共通の背景情報を親子間で二重に再構成することは避ける。

2. 元作業から調査・保存を分離し、1要求1AWIと担当の割当を区別する。
   同じ担当が似た対象から順に知見を再利用し、要求ごとに原稿と根拠を分ける。

   - 担当の分割は、起動、規範・背景の重複読込、指示作成、検収、結論の再構築を含む総費用で比較し、有利な場合に似た対象をまとめて渡す。
     件数や独立性だけでは分けず、書込所有権と競合は保持する。
   - 1つの要求を投入先ごとに展開したすべての組（上流投入における同一の`元項目`の組）、`agent-toolkit:wi-standards`の`references/cross-repository-submission.md`で整合性が求められる組、および依存関係で結ばれている要求は、同一の担当者へ渡す。
   - 担当者は他の担当者の成果物を把握できないため、担当者をまたぐ事実情報（割当先、1つの判断を展開した本文一式、依存先の正式名称など）は委譲元が保持して各担当者へ渡し、`add-wi-parent-acceptance.md`「受領と検収」の段階で原稿全体と突き合わせる。
   - 起動後の新しい要求も同じ総費用比較で割り当てる。
     同じ担当へ追加送信する場合は、要求ごとの識別子、原稿、根拠の対応を保つ。
   - 既存要求の要件更新や、`add-wi-parent-acceptance.md`「受領と検収」に基づく修正および再開は同じ担当へ送信する。
     追加送信する前には、追加された発話を要求単位ごとに分解し、`確定した要件`としての帰属条件を単位ごとに判定する。
     一部の内容が要件更新に該当するからといって、無関係な要求単位までまとめて含めない。

3. 確定した入力で`agents_server`の`start`を呼び出す。

| 引数 | 値 |
| --- | --- |
| `cwd` | 投入先の対象リポジトリの作業ツリーの絶対パス。委譲元がその作業ツリーで動いている場合は委譲元の作業ディレクトリ、委譲元が対象の外で動く場合（常駐起動の統括など）は対象の主作業ツリー（`../skills/process-wi/references/lanes.md`「用語」。常駐起動では集合JSONの`worktree`）とする。上流投入では元項目を保持している対象リポジトリの絶対パス |
| `subagent_md_path` | `add-wi` |
| `extra_params` | `投入する要求`には要求ファイルの絶対パス、`引き継ぎ記録先`には記録先のパスを渡す |

```json
{"投入する要求":"/absolute/managed-temp/requirements.md","引き継ぎ記録先":"/absolute/managed-temp/handoff.md（新規）"}
```

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| WI投入担当の起動と受領：受領と検収の基準を適用する前 | `add-wi-parent-acceptance.md` |
