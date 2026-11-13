# 工程別のモデル設定と起動手順

本書は、委譲する工程ごとのモデル候補列の解決と、起動・設定確認の手順を定める。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 作業を分担するかと総費用を比較する時 | `routing-decisions.md` |

## 工程別モデル設定

各工程の`model_type`は、対応する`<工程>_model`の設定に定義された候補列を直接参照する。
下表における「メイン」は、`agent-toolkit:process-wi`のメインエージェント、または`agent-toolkit:write-plan`を起動したエージェントを指す。

| 対応工程・役割 | model_type | 起動直前に解決する主体 | 起動手段 |
| --- | --- | --- | --- |
| WI選定・レーン分け・計画・設計判断を伴う実装と修正・原因分析・観点選定・候補判定・結論の統合。`exec`・`add-wi`・`copilot-review-audit`・`defect-investigation`・`lane-integration`・`session-termination` | `execute` | 各担当を委譲するメインか工程担当 | `agents_server` |
| 内容と完成判定が確定した単発コーディング・修正・追随、中規模走査。`refine-prompt` | `chores` | 分担を所有する担当 | `agents_server` |
| 定型走査・軽微な作業・コマンド実行。自由本文の`explore`・`shell`の省略時 | `chores_fast` | 委譲する担当 | `agents_server` |
| 実行レビュー・ユーザビリティレビュー・投入前レビュー・再発防止セルフチェック。`exec-review`・`usability-review`・`wi-draft-review`・`recurrence-self-check` | `review` | 各レビューを委譲する担当 | `agents_server` |
| 小規模レビュー。`bulk-replace-review`・`external-write-review`・`reader-fit-review` | `review_fast` | 各レビューを委譲する担当 | `agents_server` |
| 最上位オーケストレーター、process-loopの新規セッション、run-skill。`repository-process` | `orchestrate` | process-loop・run-skill・all-reposの統括担当 | `atk`か`agents_server` |
| 文章起草、`write`の省略時 | `write` | 委譲する担当 | `agents_server` |
| `atk commit`の省略時 | `chores` | `atk` | `atk` |

設定値は`execute_model`、`review_model`、`review_fast_model`、`chores_model`、`chores_fast_model`、`orchestrate_model`、`write_model`の7つのキーで管理され、それぞれ個別に変更できる。

- `model_type`には7種類の工程名または候補列を直接指定できる。
  delegateでは指定が必須であり、exploreやshellで省略した場合はchores_fast、writeで省略した場合はwriteが適用される。
  taskで省略した場合は役割文書のfrontmatterに定義された工程を適用し、明示的に指定した場合はその工程や候補列を使用する。
  候補列を直接指定した場合は保存された設定ファイルを読み込まない。
  起動区分はmode（taskの場合は役割文書のmode）を維持する。
  恒久的な設定変更は`atk config set`を用いて行う

1. 委譲する部分を選ぶため、定型処理はモデルを割り当てる前に`routing-decisions.md`「要否判定」で既存CLI・API・標準コマンドによる実行と直接処理を比較する。
   その比較でモデルへ委譲する部分を選んだ後に、本節の工程別設定を適用する。
   WIの`担当モデル`において担当者、モデル、effortが明記されている工程では、委譲元がその指定値を`model_type`へ渡す。
   1回の起動に対して異なる指定が混在する場合は、WIと指定値を明示して委譲単位を分割する。
   各工程は自身の担当名（`担当モデル.実装担当`など）と本段落の参照先を保持する。

   起動手段に応じて手順2の分岐を選び、手順3へ進む

2. 設定どおりに起動するため、`agents_server`では`start`を使い、役割文書を指定する場合は`subagent_md_path`と`extra_params`、自由本文の委譲では`mode: delegate`と`prompt`と`model_type`を渡す。
   役割のmode、model_type、入力項目、書き込み権限はfrontmatterで管理し、`usage`フィールドは使用しない。
   共通モジュール`agent_toolkit/_common/task_documents.py`で読み込みを行い、サーバー側で適切な候補を選択する

   `Agent`ツールを使用する場合は、起動直前に`atk config get <工程>_model`を1回実行して設定を取得し、先頭の候補をengine、model、effortへ分解する。
   Claude以外のengineについて実行手段を確保できない場合は、未完了として返すか続行できない理由を報告する。
   分解した値は表に記載された工程を実行する委譲先のみに渡し、定義側でモデルが固定されている委譲先に対してmodelを上書きすることは避ける

3. 起動レスポンスとして返されたsession_idとstatusを受け取り、候補の切り替えが発生した場合は除外理由と採用候補も確認する。
   終了時に`wait`を実行してmodel_type、engine、model、effortを確認する。
   WIの指定値がある場合は実際値と比較し、WI名、指定内容、実際値を進捗ログまたは引き継ぎ記録へ記載する。
   なお、保持期限を過ぎた後に`show`を実行できることを検収の前提条件とはしない

4. すべての候補を試しても実行できなかった場合は、未完了または続行できない理由を委譲元へ返却する

作業の分担と総費用の比較は`routing-decisions.md`「要否判定」に従い、表にある役割全体の工程指定を維持する。
計画、設計判断、原因分析、観点の選定、アイデア出し、候補の判定、結論の統合は役割担当自身が行う。
内部では判断と確定済みの作業を極力細分化し、同じ背景と実行条件の作業は1件の起動へ集約する。
定型走査・事実収集・コマンド実行は`chores_fast`、中規模の読取・要約・走査と確定済みの書込は`chores`へ渡す。
内部群の先行準備・実装・独立文脈レビューにも同じ工程指定と明示された担当モデルを適用する。
群数と同時数の比較は`routing-units.md`「委譲単位の確定」に従い、判断・設計・原因分析・採否は役割担当自身が所有する。
自律的に切り出せるのは`review`・`review_fast`・`chores`・`chores_fast`・`write`で済み、起動と資料の読込を含めても費用が下がる部分だけとする。
`execute_model`は高価であり、`execute`は役割文書がその工程を定める担当のtask、`orchestrate`はprocess-loop・run-skillなど規範が起動形を定める最上位・統括だけに使う。
`start`は自由本文のdelegate・explore・write・shellにおけるexecuteとorchestrateの工程名指定を起動前に拒否する。
切り出せない判断は、文脈を持つ元の担当が続ける。

自由本文の全mode、候補列の直接指定、taskの上書き、forkにも自律的な切出しの5工程制限を適用する。
候補列と上書きの受理形式、自律選択の認可は別に判定する。
taskの役割文書に定義した工程との差と自由本文の候補列直接指定は、起動前に検出して応答のwarningsへ示す。
現在設定を使うtaskはmodel_typeを省略する。
正当なユーザー指定・試行・同じ要求への個別引上げは受理し、以後の初回起動には工程設定を使う。
ユーザーがWIの担当モデルへ明示したengine・model・effortの継承と、役割文書が定める工程は維持する。
工程別設定の対象外でもこの境界を保ち、`model-and-effort.md`「modelとreasoning effort」で選ぶ。
独自の名前付きagentについては各文書のfrontmatterの定義に従う。

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 候補列と設定の確認・変更を扱う前 | `process-model-settings-candidates.md` |
| OpenCodeのターン起動を扱う前 | `process-model-settings-opencode.md` |
| 旧モデル設定の移行を扱う前 | `process-model-settings-migration.md` |
