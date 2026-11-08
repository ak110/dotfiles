# 工程別のモデル設定と起動手順

本書は、委譲する工程ごとのモデル候補列の解決と、起動・設定確認の手順を定める。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../write-plan/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/write-plan/SKILL.md` |
| 作業を分担するかと総費用を比較する時 | `routing.md#「要否判定」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/routing.md#「要否判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `fallback-launch.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/fallback-launch.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `model-and-effort.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/model-and-effort.md` |

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

- 候補列は`<claude|codex|agy>:<model>[/<effort>]`または`opencode:<provider>/<model>[@<effort>]`の形式をASCIIカンマで区切って記述し、先頭から順に試行する。
  effortを省略した場合は`medium`が適用される。
  OpenCodeのmodel指定に含まれる`/`は維持し、末尾の`@`以降をvariantとして扱う。
  なお、`orchestrate_model`にはOpenCodeを指定できない
- 可用性の問題による障害が発生した場合は、次の候補へ進む。
  OpenCodeのCLIが存在しない場合や、接続・認証に失敗した場合も可用性の障害として処理する。
  Claudeの利用上限に伴う待機手順は、`fallback-launch.md`「代替起動」に従う
- 初期設定として、executeとorchestrateはClaude Opusを最優先、Codex solを予備とする。
  reviewとchoresはCodex sol/mediumを最優先、Claude Sonnetを予備とし、review_fastとchores_fastはCodex lunaを最優先、Claude Haikuを予備とする。
  writeはAntigravity Gemini Flash、Claude Opusの順とする
- 初期設定やプリセットの候補を変える前に、変更後の候補でその工程の役割文書を持つ担当（`review`なら実行レビュー担当）を実際の作業で1回以上起動し、最初の応答が返却形式どおりの完了になることを確かめる。
  候補の適否はモデルの挙動で決まり、候補列の保存と実行時解決の配線を確かめる既存の試験では検出できないためである。
  確かめた候補、工程、作業と結果は、候補を変える理由とともにプロジェクトの設計記録か変更のcommit本文へ残す
- `atk config apply-preset <名前>`を実行すると、writeの設定を維持したまま他の6つのキーを一括で更新できる。
  用意されている5種類のプリセットは次の通りである。
  `codex-balanced`は初期設定、`codex-near-primary`はexecuteの先頭をCodexに変更、`codex-primary`はexecuteとorchestrateの先頭をCodexに変更する。
  `claude-balanced`はchoresとchores_fastの先頭もClaudeに変更し、`claude-primary`は全6工程の先頭をClaudeへ変更する。
  OpenCodeを使用する場合は各キーに直接指定する
- Codexの`astra`、`sol`、`terra`、`luna`という指定は、起動時にApp Serverの`model/list`を参照して同一系列の最新の完全IDへ解決する。
  指定されたeffortが利用できない場合、該当系列が存在しない場合、あるいは一覧取得に失敗した場合は、理由を提示して処理を停止する。
  完全IDは固定指定として扱い、Claudeやagyの候補を受理できるかどうかは各engineの処理に委ねる
- 環境変数`AGENT_TOOLKIT_CONFIG_<キー名の大文字>`に空でない値が設定されている間は、保存された設定値よりも環境変数を優先し、その値を設定ファイルへ保存することはない。
  `set`コマンドは設定ファイルの保存先を更新し、参考一覧に含まれないモデルやeffortであっても警告を表示した上で受理する。
  `show`や`get`コマンドは参考一覧外の値もそのまま表示する
- `model_type`には7種類の工程名または候補列を直接指定できる。
  delegateでは指定が必須であり、exploreやshellで省略した場合はchores_fast、writeで省略した場合はwriteが適用される。
  taskで省略した場合は役割文書のfrontmatterに定義された工程を適用し、明示的に指定した場合はその工程や候補列を使用する。
  候補列を直接指定した場合は保存された設定ファイルを読み込まない。
  起動区分はmode（taskの場合は役割文書のmode）を維持する。
  恒久的な設定変更は`atk config set`を用いて行う
- OpenCodeの各ターンは`opencode run --format json --auto`で実行し、モデルを`--model`、effortを`--variant`、継続セッションIDを`--session`へ渡す。
  共通規範、委譲先規範、ユーザー規範、およびplugin rootをプロンプト本文へ渡し、必要なスキルは同rootから読み込む。
  実行中のターンへの追加メッセージ送信は、現在のターンの終了を待ってから同一ID宛てに送る
- 過去の段位制9キーや用途別engine順5キーが保存されている場合は、初回の`show`、`get`、または設定候補解決のタイミングで6工程の設定へ合成し、廃止された旧キーを一括して削除する。
  古い工程キーが共存している場合も、旧段位制やengine順から合成された値を優先して採用し、write設定および非モデル設定を維持する。
  なお、それ以前の旧旧4キーから段位制への移行処理は廃止済みである。
  廃止されたキーに対応する環境変数が存在する場合は、新しい工程別の環境変数へ移行する案内を表示して処理を停止する

1. 委譲する部分を選ぶため、定型処理はモデルを割り当てる前に`routing.md`「要否判定」で既存CLI・API・標準コマンドによる実行と直接処理を比較する。
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
   終了時に`wait`を実行してmodel_type、engine、model、effortを確認する。WIの指定値がある場合は実際値と比較し、WI名、指定内容、実際値を進捗ログまたは引き継ぎ記録へ記載する。
   なお、保持期限を過ぎた後に`show`を実行できることを検収の前提条件とはしない

4. すべての候補を試しても実行できなかった場合は、未完了または続行できない理由を委譲元へ返却する

作業の分担と総費用の比較は`routing.md`「要否判定」に従い、表にある役割全体の工程指定を維持する。
計画、設計判断、原因分析、観点の選定、アイデア出し、候補の判定、結論の統合は役割担当自身が行う。
内部では判断と確定済みの作業を極力細分化し、同じ背景と実行条件の作業は1件の起動へ集約する。
定型走査・事実収集・コマンド実行は`chores_fast`、中規模の読取・要約・走査と確定済みの書込は`chores`へ渡す。
自律的に切り出せるのは`review`・`review_fast`・`chores`・`chores_fast`・`write`で済み、起動と資料の読込を含めても費用が下がる部分だけとする。
`execute_model`は高価であり、`execute`は役割文書がその工程を定める担当のtask、`orchestrate`はprocess-loop・run-skillなど規範が起動形を定める最上位・統括だけに使う。
`start`は自由本文のdelegate・explore・write・shellにおけるexecuteとorchestrateの工程名指定を起動前に拒否する。
切り出せない判断は、文脈を持つ元の担当が続ける。

自由本文の全mode、候補列の直接指定、taskの上書き、forkにも自律的な切出しの5工程制限を適用する。
候補列と上書きの受理形式、自律選択の認可は別に判定する。
ユーザーがWIの担当モデルへ明示したengine・model・effortの継承と、役割文書が定める工程は維持する。
工程別設定の対象外でもこの境界を保ち、`model-and-effort.md`「modelとreasoning effort」で選ぶ。
独自の名前付きagentについては各文書のfrontmatterの定義に従う。
