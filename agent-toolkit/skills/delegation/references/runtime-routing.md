# 実行手段の選択

本書は委譲先とモデルを選ぶときに、実際に利用できる実行手段の取り決めを定める。委譲先固有の作業手順は`<役割名>.subagent.md`側に置く。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

<!-- read-table-source: runtime-routing-delivery-1.md -->

## 実行手段

`agents_server`の`start`のうち、`mode`が`delegate`の起動と、`mode:`を宣言しないか`delegate`を宣言した`<役割名>.subagent.md`による`task`の起動を通常起動と呼ぶ。`mode`が`explore`・`shell`・`write`の起動と、それらを宣言した`<役割名>.subagent.md`による`task`の起動を軽量起動と呼ぶ。区分は`agent_toolkit/_agents_server/launch_prompts.py`の`LIGHTWEIGHT_LAUNCH_KINDS`と一致させる。両者は読み込む設定と利用できるツールが異なるため、委譲先の判断に要るプロジェクト規範が自動で届かない起動形態では委譲プロンプトへ渡す。

終端した担当を前任、その作業を引き継ぐ新しい担当を後任と呼ぶ。
`start`へ`handoff_from`だけを指定し、前任の起動条件と記録から後任を開始する操作を引き継ぎ起動と呼ぶ。
引き継ぎ起動は通常起動・軽量起動の区分と用途を前任から保持する。

- 起動手段はその手段が提供する終端観測手段と組で選ぶ。機能と権限が同等なら、`atk agents wait`で機械的に終端を観測できる`agents_server`を選ぶ。ホストの組み込み委譲を選んだ場合は、そのホストの委譲一覧のstatusと指定した成果物で終端を観測する
- Claude Codeからの委譲は`agents_server`を標準とする（工程別モデル設定のeffortを渡せるため）。`Agent`ツールを使うのは次の場合に限る
  - `agents_server`のMCPツールを呼び出せない場合。切り替えた工程、本来の設定値（engine・モデル・effort）と理由を報告へ記録する
  - 専用agent定義がある作業（`agent-toolkit`は専用agent定義を配布しないため、ホスト組込とプロジェクト側の定義に限る）
  - ユーザーか上位のエージェントの明示指示がある場合
  - `routing.md`「会話を引き継ぐ委譲」がforkを選ぶ場合。`subagent_type: "fork"`で起動し、親と同じモデルとeffortを使う
- Codexから別のエージェントへの委譲は、利用できれば`agents_server`を使う。forkを選ぶ場合は`spawn_agent`の`fork_turns`を省略するか`"all"`にし、モデルとeffortを上書きしない
- 専用定義もCodexの委譲手段も使えない場合だけ汎用Agentを使う
- 役割文書を持つ工程にはその文書を渡す。読み取りだけの調査、参照実装に沿う定型的な書込、出力量が大きいコマンド実行は、各専用ツールの公開説明を確かめて割り当てる。切り詰めや上限到達が報告された調査結果を網羅性・件数・不在の根拠にしない
- 起動時の可用性失敗による候補切替はサーバーへ委ねる。Claudeの`api_error.type`が`usage_limit`の場合は「代替起動」の同じsessionでの継続を優先する。起動後のturnで、それ以外の可用性失敗が確定したときは、前任の終端と書込所有権の解放を確かめ、終端結果の`next_action`に従って`start`へ`handoff_from`だけを渡して1回起動する。後任の`session_id`を追送先と待機対象にする。同じ失敗が続けば自ら実施できる範囲を進め、委譲が不可欠な工程は未完了として返す。実行中の担当を経過時間や無応答だけで置き換えない
- 委譲先の即時通知の手段は`agent-toolkit/share/rules-subagent.md`「確認事項の即時通知」が定め、通知を受けた後の待機は`references/waiting-and-monitoring.md`「待機区間の構成」に従う
- 実績として記録するのは、起動結果として返されたrouteと識別子とする

forkの監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/delegation/references/runtime-routing.md：実行手段：2026年9月27日」にある。

## 工程別モデル設定

各工程の`model_type`は対応する`<工程>_model`の候補列を直接参照する。表のメインは`agent-toolkit:process-wi`のメインか`agent-toolkit:plan-mode`を起動したエージェントを指す。

| 対応工程・役割 | model_type | 起動直前に解決する主体 | 起動手段 |
| --- | --- | --- | --- |
| WI選定・レーン分け・計画・設計判断を伴う実装と修正・原因分析・観点選定・候補判定・結論の統合。`exec`・`add-wi`・`copilot-review-audit`・`defect-investigation`・`lane-integration`・`session-termination` | `execute` | 各担当を委譲するメインか工程担当 | `agents_server` |
| 内容と完成判定が確定した単発コーディング・修正・追随、中規模走査。`refine-prompt` | `chores` | 分担を所有する担当 | `agents_server` |
| 定型走査・軽微な作業・コマンド実行。自由本文の`explore`・`shell`の省略時 | `chores_fast` | 委譲する担当 | `agents_server` |
| 実行レビュー・ユーザビリティレビュー・投入前レビュー・再発防止セルフチェック。`exec-review`・`usability-review`・`wi-draft-review`・`recurrence-self-check` | `review` | 各レビューを委譲する担当 | `agents_server` |
| 小規模レビュー。`bulk-replace-review`・`external-write-review`・`reader-fit-review` | `review_fast` | 各レビューを委譲する担当 | `agents_server` |
| 最上位オーケストレーター、process-loopの新規セッション、run-skill | `orchestrate` | process-loop・run-skill・委譲する担当 | `atk`か`agents_server` |
| 文章起草、`write`の省略時 | `write` | 委譲する担当 | `agents_server` |
| `atk commit`の省略時 | `chores` | `atk` | `atk` |

設定は`execute_model`・`review_model`・`review_fast_model`・`chores_model`・`chores_fast_model`・`orchestrate_model`・`write_model`の7キーで持ち、独立に変更できる。

- 候補列は`<claude|codex|agy>:<model>[/<effort>]`または`opencode:<provider>/<model>[@<effort>]`をASCIIカンマで区切り、先頭から試す。effortの省略時は`medium`。OpenCodeのmodel内の`/`を保持し、末尾の`@`より後をvariantとする。`orchestrate_model`はOpenCodeを受理しない
- 可用性に起因する失敗で次の候補へ進む。OpenCodeのCLI不在・接続・認証失敗も可用性の失敗として扱う。Claudeの利用上限の待機は「代替起動」に従う
- 初期値はexecute・orchestrateがClaude Opusを先頭、Codex solを予備とする。review・choresはCodex terraを先頭、Claude Sonnetを予備とし、review_fast・chores_fastはCodex lunaを先頭、Claude Haikuを予備とする。writeはAntigravity Gemini Flash、Claude Opusの順とする
- `atk config apply-preset <名前>`はwriteを保ち、他の6キーを一括保存する。5プリセットは次の値とする。`codex-balanced`は初期値、`codex-near-primary`はexecuteをCodex先頭、`codex-primary`はexecuteとorchestrateをCodex先頭にする。`claude-balanced`はchoresとchores_fastもClaude先頭、`claude-primary`は全6工程をClaude先頭にする。OpenCodeは各キーへ直接指定する
- Codexの`astra`・`sol`・`terra`・`luna`は起動時にApp Serverの`model/list`から同系列の最新の完全IDへ解決する。指定effortが使えない、系列がない、一覧取得が失敗した場合は理由を示して停止する。完全IDは固定指定として扱い、Claude・agyの候補の受理可否は各engineへ委ねる
- `AGENT_TOOLKIT_CONFIG_<キー名の大文字>`が空でない間は保存値より優先し、その値を保存しない。setは保存先を更新し、参考一覧外のモデルとeffortを警告付きで受理する。show・getは参考一覧外の値も表示する
- `model_type`へ7工程名か候補列を直接渡せる。delegateでは指定必須、explore・shellの省略時はchores_fast、writeの省略時はwriteを使う。taskの省略時はrole frontmatterの工程を使い、明示時は指定した工程か候補列を使う。候補列を直接指定すると保存設定を読まない。起動区分はmode（taskはroleのmode）を保持する。恒常的な変更は`atk config set`で行う
- OpenCodeの各turnは`opencode run --format json --auto`で実行し、モデルを`--model`、effortを`--variant`、継続IDを`--session`へ渡す。共通規範・委譲先規範・ユーザー規範とplugin rootを本文へ届け、必要なスキルを同rootから読む。実行中turnへの追送は終端を待って同じIDへ送る
- 旧段位9キーと用途別engine順5キーが保存されている場合、初回のshow・get・設定候補解決で6工程へ合成し、廃止キーを一括除去する。共存する古い工程キーより旧段位・engine順からの合成値を優先し、writeと非モデル設定を保持する。旧旧4キーから段位制への移行は撤去する。廃止キーの環境変数がある場合は、対応する工程別環境変数への置換を案内して停止する

起動と確認は次のとおりとする。

1. `agents_server`では役割文書をtask、自由本文と工程名か候補列をdelegateへ渡す。roleのmode・model_type・入力・書込はfrontmatterが所有し、`usage`は使わない。共通の`agent_toolkit/_common/task_documents.py`で読み、サーバーが候補を選択する
2. `Agent`ツールでは起動直前に`atk config get <工程>_model`で1回取得し、先頭候補をengine・model・effortへ分解する。Claude以外のengineは実行手段を確保できなければ未完了として返すか続行できない理由を返す。値は表の工程を実行する委譲先にだけ渡し、定義がモデルを固定する委譲先へmodelを上書きしない
3. 起動応答のsession_id・statusと、切替があれば除外理由・採用候補を受け取る。終端時のwaitでmodel_type・engine・model・effortを確認する。保持期限後のshowを検収の前提にしない
4. 全候補を使い尽くした場合は、未完了か続行できない理由を返す

WIの`担当モデル`が担当・モデル・effortを指定した工程では、委譲元が指定値をmodel_typeへ渡す。同じ起動に異なる指定があれば、WIと指定値を示して単位を分ける。終端のengine・model・effortと指定値を比べ、WI・指定・実際値を進捗ログか引き継ぎ記録へ残す。各工程は自身の担当名（`担当モデル.実装担当`など）と本段落の参照を持つ。

作業の分担と総費用の比較は`routing.md`「要否判定」に従う。表の`model_type`は役割文書を渡す工程の起動へ適用し、役割全体の工程指定を保持する。役割担当は観点選定・案出し・候補判定・結論の統合を自身で行う。役割の内部では、作業を判断部分と、読取・調査・要約・走査・確定済みの編集・コマンド実行の部分へ極力細分化する。細分化は作業の分類であり、同じ背景と実行条件を使う確定済みの作業は1件の起動へまとめる（`routing.md`「要否判定」）。後者を委譲するときは、定型走査・事実収集・コマンド実行を`chores_fast`へ、その能力では材料が足りない中規模の読取・要約・走査と確定済みの編集を`chores`へ渡す。表の`execute`を指定するのは役割文書を渡す工程と、計画・設計判断・原因分析など分割できない判断を独立した担当へ任せる場合に限る。その判断のための調査を`execute`の`explore`へ渡さない。

工程別設定の対象外の委譲には「modelとreasoning effort」を適用し、独自の名前付きagentは自身のfrontmatterに従う。

## 継続と新規起動

自ら実行できる工程を残した返却が反復する場合は、同じ担当へ再送する前に、担当・作業単位・モデル・実行手段の変更を総費用で比較する。担当を交代するときは前任の終端を確認し、確認済みの成果と未完了部分を引き継ぐ。共有モデル設定は保ち、必要な変更は今回の起動時指定へ限定する。これは通信上の継続不能とは別の判断とする。

- レーン担当とCI修正担当は前の担当の識別子を再利用せず新規に起動する。Codexで元担当を置換する新規threadは`references/codex-runtime.md`「後続操作の共通先行条件」を適用してから起動する
- 継続接続は同じ担当へ同じタスクの未完了作業、指摘への対応、再レビューを返す場合だけ使う。継続直前に、現在のsessionの起動に使った`model_type`と委譲する工程の`model_type`が一致する場合だけ同じ`session_id`へ`send_message`を送る。engine、model、effortはサーバーが起動時の値を保持するため、可用性で候補が切り替わった担当や、起動後に候補列が変わった担当へも継続できる
- 実際の起動routeが`agents_server`なら、engineと実行ホストによらず`send_message(session_id, prompt, timeout)`を使う。サーバーが実行中turnへのsteer、終端後のreply、保持期限後の暗黙再開を選び、所有する実行主体が終了していても再開する。Claude Codeの`Agent`ツールで起動した担当だけ`SendMessage`を使う（「Agentツールの継続」）
- Codexの組み込み委譲で`spawn_agent`から起動した担当へ追加作業を依頼する場合、終端した担当と待機中の担当には`followup_task`を使い、実行中の担当へ入力を追加する場合だけ`send_message`を使う。Codexの`send_message`は担当のturnを起動せず、終端済みの担当へ送った内容はキューに残るだけである。終端済みsessionで新しいreplyを開始する`agents_server`の`send_message`とは契約が異なる。待機対象は`followup_task`の受理と、担当の新しいturnの開始または返却を観測してから成立する。`wait_agent`のtimeoutが続く場合は、再送の前に`list_agents`で対象の状態を確かめる
- `model_type`の不一致、異なる担当かタスクへの切替、中断済み、完了配送不能、前提の無効化のいずれかに当たる場合は、検収済み状態を渡して新規起動する。指摘対応ではレーン担当のsessionを継続し、継続不能の場合だけ検収済み状態とレビュー指摘の所在を渡してレビュー修正担当を新規起動する
- 起動後の可用性失敗は「実行手段」の引き継ぎ起動で扱う。それ以外の継続不能は実際に継続手段を呼んだ結果で判定し、`agents_server`では`send_message`が`unknown session: <session_id>`を返した場合だけとする。識別子体系の相違を示すエラーと、`wait`の`session retention expired: <session_id>`は継続不能に含めない。保持識別子を失った場合は公開の一覧・個別照会で回復し、終了や失敗を推定しない
- `send_message`のtimeoutは配送の成否が未確定であることだけを示す。その継続要求の受理結果か、要求で始まった新しいreplyの開始を確認できた場合だけ配送済みとして待機を続ける（`wait`の`status=completed`や`agent_message`の文字列一致は根拠から外す）。確認できなければ再送も継続不能の確定もせず、続行できない理由を委譲元へ返す
- レーン担当を新規sessionへ移す場合は、検収済みの先行commitの7文字以上の一意な短縮OID、完了した工程と残る工程を渡す。計画、進捗ログ、保存済みのレビュー指摘管理表で状態を参照できる場合は、絶対パス、対象ID、未記録の差分だけを渡す。記録が無ければ委譲元がmanaged-tempへレビュー指摘管理表を作成して絶対パスで渡す
- レビュー担当が継続不能の場合は、同じ`model_type`で新しいレビュー担当を起動する。渡すのは`レビュー種別: 引き継ぎ再レビュー`、レビュー指摘管理表の絶対パスと`track`、`round: <ラウンド番号>`の行、直前修正の直接影響範囲、読み取り専用の範囲である。実行レビューではレビュー対象HEADの7文字以上の一意な短縮OIDも渡し、`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`の生成規則で`review_contract`を再生成する。ラウンド番号は`${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`の`## ラウンド番号の保持先`に従い、表の最大値から続ける。新しい担当は解消済みの行を再走査せず、未解消の行とそのラウンドの走査範囲だけを扱う

同じ計画の起草から実装、変更範囲の検証までは、同じレーン担当が同一worktreeで順に実施する。継続できない場合だけ新しい担当を起動し、検収済み状態と残る工程を1つの担当へまとめて渡す。

### Agentツールの継続

- 完了報告を返して停止済みの識別子でも、同じ担当へ同じタスクの未完了作業、指摘への対応、再レビューを返し、継続直前に再取得した実効`engine`・`model`・`effort`が起動時と一致する場合に限り`SendMessage`で再開してよい。継続の本文には、継続前に作成・読取したファイルも書込直前に現行本文を取得する条件を含める
- 完了通知の配送先はproviderと構成で異なり、3階層以上での配送先は未検証である。最上位や直接の親を配送先と決めつけず、起動結果IDへの`SendMessage`の成功と、送信後に対応する完了通知を受け取れたことで継続の成立を判定し、`references/claude-code-runtime.md`「完了通知と中継の実行順」へ対応付ける。監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/delegation/references/claude-code-runtime.md：完了通知と中継の実行順：同一セッション内の再開：2026年8月」にある
- 担当とタスクの組の相違・実効3値の不一致・完了報告の配送不能・前提の無効化・中断済みのいずれかでは、停止済みの識別子を再利用せず、必要な前提を明示的に再構成して新規起動する。参照できる記録があれば絶対パス、対象ID、未記録の差分だけを渡す
- `SendMessage`が`No transcript found for agent ID: <識別子>`で失敗したら配送不能と判定し、同じ識別子への再開を控えて新規起動する。セッション識別子が変わった場合は、タスク出力先ディレクトリの識別子を`ls`で比べ、旧識別子配下の委譲先への失敗文面と照らす

## 代替起動

本節の代替起動は`Agent`ツールへ適用する。
`agents_server`は起動時には候補列の自動切替、起動後の可用性失敗には「実行手段」の引き継ぎ起動を使う。

- 指定した組合せを一時的に利用できない事象（過負荷、レート制限、サーバー障害、モデルや実行手段の利用不能など、実行環境の可用性に起因する失敗）を実際に観測した場合に限り、設定を解決して委譲を起動したエージェントが、セッション内の起動時指定だけで代替の組合せへ切り替えて再起動してよい。委譲した作業自体の失敗は対象外とする
- 後任へは前任の起動本文と、あれば`引き継ぎ記録先`を`（継続）`として渡す。`atk run-script session-review-prepare`の`conversation.md`と記録の両方を読み、現在の確定事項と残工程を復元し、追送と各turnの最終応答は全文を渡す。詳細照会は`atk run-script session-review-evidence -- --codex-thread-id <id>`または`--claude-session-id <id>`の`--detail <記録位置>`を示し、コマンドを実行できない後任へは記録ファイルの絶対パスを渡す。Antigravityの前任には`atk agents logs <session_id> --format markdown`の出力を渡す。後任には完了済みの操作と成果物を現物で確かめ、最新の指示と残る認可内の工程を続けるよう伝える
- 設定値の書換えと`atk config set`の使用は禁止する。設定はユーザー単位の単一値を全セッションが共有し、並行セッションへ波及するためである
- Claude CodeのWeekly limitと5時間の利用上限による拒否は代替起動の対象にせず、解除を待って同じClaudeで続ける（ユーザー指示。待てば確実に解除され、切替で文脈を失う方が損失が大きい）。`agents_server`は候補を切り替えず同じsessionで待ち、委譲元へは`api_error`の種別`usage_limit`で示す
- Codexの外側セルと内側コマンドの待機中の扱いは`references/codex-runtime.md`「agents_serverの二層待機」に従う
- 候補は「代替時の組合せの目安」の表から、失敗を観測した時点の内容で選ぶ。上位行から軽量行へ、各行で`codex`列、`claude`列の順を目安とし、最終的には観測した失敗と各組合せの可用性から判断する。組合せが表のどれに当たるかは`engine`と`model`の一致で決める。失敗した組合せが表にあれば同じ行のもう一方から試す。engineの実行機能自体を呼び出せない組合せは試さず利用不能とする。残る全てで失敗か利用不能なら打ち切り、「工程別モデル設定」の手順4へ戻る
- 代替を試みた場合は、観測した失敗、`atk config get`の設定値、実際に用いた組合せ、対象工程、試さなかった組合せとその理由を報告へ記録する（努力目標）
- 委譲先は自身の判断で設定と異なるengineやモデルへ切り替えず、「工程別モデル設定」の手順4のとおり返す

## modelとreasoning effort

工程別モデル設定の対象外の委譲では、設計判断を伴う実装とレビュー、観点・判定基準の選定と新しい候補の案出し、走査が返した候補の判定と結論の統合に上位モデル、内容の確定した書込作業を含むその他に標準モデルを選ぶ（努力目標）。判定基準を言語化して渡せる多数の対象の走査は軽量モデルへ分ける（努力目標。モデルは作業の性質から選び、走査と書込作業の分担は「工程別モデル設定」の工程分担の段落に従う）。reasoning effortの省略時は`medium`とし、高いeffortは読むファイルが少なく推論の深さが結果を左右すると事前に分かる工程で選ぶ（努力目標）。モデルを明示する場合はeffortも指定する。指定モデルを使えない場合は、再試行を含む総コストで、作業を成立させるモデルへ切り替える。
監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/delegation/references/runtime-routing.md：modelとreasoning effort：2026年8月」にある。

## 代替時の組合せの目安

本節は「代替起動」の候補選定にだけ適用する。対応関係と向き不向きはユーザーが運用上の目安として指定した値であり、能力比較の検証結果とは別のものとする。

| レベル | `codex` | `claude` |
| --- | --- | --- |
| 上位 | `gpt-6-sol/medium` | `opus/medium` |
| 中位 | `terra/medium` | 用途に応じて`opus/medium`または`sonnet/medium` |
| 軽量 | `gpt-6-luna/medium` | `haiku/medium` |

- 同じ行の`codex`と`claude`を代替候補とみなし、effortは表の値を使う。`terra`は系列名として同系列の最新版へ解決し、各行で世代をそろえる前提は置かない
- 通常は上位の行を選び、中位・軽量の行は工程別モデル設定の値がその行に当たる場合と、内容が確定済みで低リスクな機械作業に限る
- 表に無いモデルは、表の組合せと同等以上と確認できる場合に限って置き換えに使う
- effortを指定できない環境で表の組合せを満たせない場合は、`codex`列へ切り替えるか上位の行を選ぶ。Claudeのeffortはエージェント定義のfrontmatterで確定し、frontmatterを持たない起動方法では指定できない
- 差分の逐条確認と欠陥の列挙を成果とする工程では`codex`列、代替案の比較と設計の確定を成果とする工程では`claude`列を選ぶ

## 実装担当とworktree

- 同じworktreeの書込の分担と交代は`references/mandatory-rules.md`「作業ツリーの保護」、レーン内の一時的な内部群は`references/routing.md`「委譲単位の確定」、作業ディレクトリの渡し方は`references/sending.md`「起動と観測」が定める
- レーン担当の起動前に、上流追随済みで、staged、unstaged、non-ignored untrackedが全て空であることを確かめる。新規レビュー修正担当を起動する場合は、レーン担当の終端確認後に修正の引き継ぎ記録と現行のdirty差分を比べて渡す
- 実行レビュー担当は実装担当の終端後に起動し、同じ最終HEADへ1ラウンド1回だけ割り当てる
- 作業用の複製で起動する委譲先には、着手前に`git fetch`後の分岐元との差分を双方向で確かめ、分岐元が進んでいれば先に追随するよう指示する（努力目標）
