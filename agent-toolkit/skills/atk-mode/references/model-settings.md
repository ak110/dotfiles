# モデル設定とモデル・effortの選び方

委譲する工程ごとのモデル候補列（`atk config`の工程別モデル設定）と、モデルとreasoning effortの選び方を定める。
モデルとeffortは、要求した品質の成果を受け入れるまでの総コストで比べる（原則2、`agent-basics:writing-standards/references/quality-principles-qcd.md`）。
工程の設定と、ユーザーやWIが明示した値は、エージェントの判断より優先して保つ。

## モデル選定と判断の偏り

独立して並行できる作業と、読む量が多く結論だけが必要な調査を`agents_server`で委譲し、少数の呼び出しで済む確認は自分で行う。
認可、全体の調整、検収、成果の所有、終了の観測は委譲元が担う。
起動候補を事前に特定せず、`agent-toolkit:atk-mode/references/delegation-prompt.md`に従って依頼する。
高速化では保つ処理を、レビューではエンドユーザーへの影響と根拠を指定する。

実行時はモデルを問わず、原則2と`agent-toolkit:atk-mode`の「プロダクトへの責任」「ユーザーの方針」に従う。
処理の削除や指摘数ではなく結果を確かめ、画面では見た目と使いやすさも確かめる。
承認と受領の判断は`agent-toolkit:atk-mode/playbooks/delegation-receiving.md`「受領後の扱い」に従う。
モデル固有の助言は対象が確かめられる場合だけ適用する。

## 工程別モデル設定

`model_type`は工程名か候補列で、工程名は対応する`<工程>_model`の設定の候補列を参照する。
表の「メイン」は、`process-wi`のメインか、`agent-toolkit:atk-mode/playbooks/write-plan.md`で計画を作ったエージェントを指す。

| `model_type` | 対応する工程・役割 |
| --- | --- |
| `execute` | WI選定、レーン分け、計画、設計判断を伴う実装と修正、原因分析、観点選定、候補判定、結論の統合。`exec`・`add-wi`・`copilot-review-audit`・`defect-investigation`・`lane-integration`・`repository-process`・`session-termination` |
| `chores` | 内容と完成判定が確定した単発のコーディング・修正・追随、中規模の走査。`refine-prompt`、`atk commit`の省略時 |
| `chores_fast` | 定型走査、軽微な作業、コマンド実行。自由本文の`explore`・`shell`の省略時 |
| `review` | 実行レビュー、ユーザビリティレビュー、投入前レビュー。`exec-review`・`usability-review`・`wi-draft-review` |
| `review_fast` | 小規模レビュー。`bulk-replace-review`・`external-write-review`・`reader-fit-review` |
| `orchestrate` | 最上位の統括、process-loopの新規セッション、run-skill |
| `write` | 文章の起草。`write`の省略時 |

設定は`execute_model`、`review_model`、`review_fast_model`、`chores_model`、`chores_fast_model`、`orchestrate_model`、`write_model`の7つのキーで、個別に変えられる。
`model_type`を省いた時は、`explore`・`shell`は`chores_fast`、`write`は`write`、`task`は役割文書のfrontmatterの工程を使う。
`delegate`では`model_type`を必ず指定する。
候補列を直接指定した起動は保存済みの設定を読まず、起動区分は`mode`（`task`では役割文書の`mode`）のまま変わらない。

1. 定型処理は、モデルへ割り当てる前に、既存のCLI・API・標準コマンドで実行する案と比べる（`agent-toolkit:atk-mode/playbooks/delegation.md`「要否判定」）。
   モデルへ委譲すると決めた部分にだけ、この表の工程を当てる。
2. WIでユーザー自身が記入・指示した`担当モデル`だけを使う。
   モデルを`model_type`へ渡す時は`model_override=user_instruction`を指定し、`model_override_evidence`にWIのファイル名とユーザー由来の根拠を書く。
   1回の起動に異なる指定が含まれる場合は、WIと指定値を示して委譲単位を分ける。
3. 工程は役割文書が定めたものを使い、`task`で現在の設定を使う時は`model_type`を省く。
   エージェントが自分の判断で切り出せる工程は`review`・`review_fast`・`chores`・`chores_fast`・`write`の5つに限り、起動と読み込みを含めてもコストが下がる部分だけにする。
   `execute`は役割文書がその工程を定める`task`、`orchestrate`は規範が起動の形を定める最上位の統括だけに使う。
   定型走査・事実収集・コマンド実行は`chores_fast`、中規模の読み取り・要約・走査と確定済みの書き込みは`chores`へ渡す。
4. 候補列の直接指定と役割の工程の変更には、`model_override`と`model_override_evidence`を指定する。
   区分はユーザーの指示（`user_instruction`）、軽量モデルだけの実機観測（`observation`）、自動切替が働かなかった場合（`fallback_failure`）に限る。
   以後の初回の起動は工程の設定へ戻す。
5. `agents_server`では`start`で起動し、サーバーが候補を順に評価する（起動の入力は`agent-toolkit:atk-mode/playbooks/delegation.md`）。
   `Agent`ツールで起動する時は、直前に`atk config get <工程>_model`を1回実行し、先頭の候補をengine、model、effortへ分けて渡す。
   Claude以外のengineを実行する手段が無ければ、未完了として返すか続行できない理由を報告する。
   定義側でモデルを固定した委譲先には、modelを上書きしない。
6. 起動の応答で`session_id`と`status`を受け取り、候補が切り替わった時は除外の理由と採用した候補を確かめる。
   終了の`wait`で`model_type`、engine、model、effortを確かめ、WIの指定値があれば実際の値と比べて進捗ログか引き継ぎ記録へ残す。
7. 全ての候補で実行できなかった場合は、未完了か続行できない理由を委譲元へ返す。

### 候補列と設定の確認・変更

- 候補列は`<claude|codex|agy>:<model>[/<effort>]`か`opencode:<provider>/<model>[@<effort>]`をASCIIのカンマで区切って書き、先頭から試す。
  effortを省くと`medium`になる。
- 現在の値は`atk config show`か`atk config get <キー>...`、プリセットは`atk config apply-preset show`で表示する（初期値は`codex-near-primary`と同じ）。
  `atk config apply-preset <名前>`は`write_model`を残して他の6つのキーを更新する。
- 設定はユーザー単位の1つの値を全セッションが共有するため、`atk config set`と`apply-preset`はユーザーが変更を指示した時だけ実行する。
- 環境変数`AGENT_TOOLKIT_CONFIG_<キー名の大文字>`が空でない間は、保存値よりその値を使う。
- Codexの`astra`・`sol`・`terra`・`luna`は、起動時にApp Serverの`model/list`で同じ系列の最新の完全IDへ解決する。
  解決できない時は理由を示して止まる。
- 可用性の障害（CLIが無い、接続・認証の失敗を含む）では次の候補へ進む。
  利用上限による拒否は可用性の障害と分け、待つことを理由に候補やモデルを切り替えない（`agent-toolkit:atk-mode/references/usage-limit.md`、`agent-toolkit:atk-mode/playbooks/delegation-recovery.md`）。
- 初期値やプリセットの候補を変える前に、変更後の候補でその工程の役割文書を持つ担当（`review`なら実行レビュー担当）を実際の作業で1回以上起動する。
  最初の応答が返却形式どおりの完了になることを確かめ、候補、工程、作業と結果を、変える理由とともに設計記録かcommit本文へ残す。
  候補の適否はモデルの挙動で決まり、設定の保存と解決のテストでは検出できないためである。

## modelとreasoning effort

工程別設定の対象外の委譲（独自の名前付きagentなど）でも、前節の切り出せる工程の範囲と、WIやユーザーが明示した値を保つ。
内容が確定した書き込みは標準のモデル、判定の基準を明記できる定型走査は軽量のモデルを候補にする（努力目標）。
総コストには、初回の推論と出力に加えて、再試行、修正、追加の調査、委譲と検収の量を含める。
低いeffortで再試行や修正が増えると総コストは上がり、高いeffortも同じ品質への寄与で選ぶ。
外部の記事の構成例や節約率は、特定のモデルと課題の結果として扱い、比べられる実績と未計測の部分を分ける。

- モデルを明示する時はeffortも指定する。
- 保存済みの設定値、初期値、プリセットは、エージェントが選ぶeffortより優先する。
- `xhigh`・`max`は、同種の作業で品質の向上を測って確かめた場合だけ選ぶ（努力目標）。
  失敗例: 軽量のモデルを`max`で動かし、思考と出力が増えて自動の会話圧縮が10回起きた（ユーザーからの報告）。
- effortを指定できる起動では、思考を増減させる指示文を依頼へ足す前にeffortで調整する（努力目標）。

## 成果が受入れ基準に届かないときの引き上げ順

委譲した成果が受入れ基準に届かず、推論量の不足を示す具体的な根拠がある時に、次の順で判断する。
早期の返却、停滞、入力の不足は、その原因を直して同じeffortで続ける。
利用上限の待ちは失敗ではなく、引き上げやモデルの切り替えの根拠から外す。
引き上げは失敗したsessionと同じ要求に限り、工程別設定を使う委譲にも当てはめる。

1. 委譲先が自分の成果を確かめる手段（テスト、ビルド、対象を呼び出すスクリプトなど）を持っているかを確かめ、無ければ渡す。
2. 同じ会話のまま`atk agents effort up <子のsession_id>`でeffortを1段上げ、`send_message`で検証結果と残る作業を伝える。
   実行基盤が同じsessionでの変更に対応しない場合は、その診断を根拠に、同じ要求の後任を1段高いeffortで起動する。
3. effortの調整で届かなければ、未達の受入条件と検証結果を報告する。
   モデルはユーザーが指示した時だけ引き上げる。

モデルを変えて新しく委譲する時は前の文脈が引き継がれないため、届かなかった検証の出力と残る差分を入力に含める。
対象のsession、未達の受入条件、検証手段、変更の結果と次の段を、既存の引き継ぎ記録へ残す。

## 同じ会話のeffortの上げ下げ

`atk agents effort up|down [子のsession_id]`は、通常の委譲（`task`・`delegate`）の担当自身か直接の親が、次のturnのeffortを1段変える。
`orchestrate`、対話中のメイン、軽量起動（`explore`・`shell`・`write`）は対象外で、保存された工程の設定は変わらない。
適用の時点と端の扱いは`atk agents effort --help`が示す。

- 担当は、判断の複雑さと検証結果から推論量の不足の具体的な根拠を記録してから上げる。
  規範の量や早期の返却だけを根拠に上げない。
- 親は、子の成果が検証を満たすか、誤りや手戻りが続くか、推論を増やして改善する余地があるかを見て調整する。
  下げる場合も、同じ要求品質を満たせる見込みを確かめる。
