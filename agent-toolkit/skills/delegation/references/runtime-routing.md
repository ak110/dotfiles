# 実行手段の選択

本書は委譲先とモデルを選ぶときに、実際に利用できる実行手段の取り決めを定める。委譲先固有の作業手順は`<役割名>.subagent.md`側に置く。

## 実行手段

`agents_server`の`start`のうち、`mode`が`delegate`の起動と、`mode:`を宣言しないか`delegate`を宣言した`<役割名>.subagent.md`による`task`の起動を通常起動と呼ぶ。`mode`が`explore`・`shell`・`write`の起動と、それらを宣言した`<役割名>.subagent.md`による`task`の起動を軽量起動と呼ぶ。区分は`agent_toolkit/_agents_server/launch_prompts.py`の`LIGHTWEIGHT_LAUNCH_KINDS`と一致させる。両者は読み込む設定と利用できるツールが異なるため、委譲先の判断に要るプロジェクト規範が自動で届かない起動形態では委譲プロンプトへ渡す。

- 起動手段は、その手段が提供する終端観測手段と組で選ぶ。機能と権限が同等なら、`atk agents wait`で機械的に終端を観測できる`agents_server`を選ぶ。ホストの組み込み委譲を選んだ場合は、そのホストの委譲一覧のstatusと指定した成果物で終端を観測する
- Claude Codeからの委譲は`agents_server`を標準とする（工程別モデル設定のeffortを渡せるため）。`Agent`ツールを使うのは次の場合に限る
  - `agents_server`のMCPツールを呼び出せない場合。切り替えた工程、本来の設定値（engine・モデル・effort）と理由を報告へ記録する
  - 専用agent定義がある作業（`agent-toolkit`は専用agent定義を配布しないため、ホスト組込とプロジェクト側の定義に限る）
  - ユーザーか上位のエージェントの明示指示がある場合
  - `routing.md`「会話を引き継ぐ委譲」がforkを選ぶ場合。`subagent_type: "fork"`で起動し、親と同じモデルとeffortを使う
- Codexから別のエージェントへの委譲は、利用できれば`agents_server`を使う。forkを選ぶ場合は`spawn_agent`の`fork_turns`を省略するか`"all"`にし、モデルとeffortを上書きしない
- 専用定義もCodexの委譲手段も使えない場合だけ汎用Agentを使う
- 役割文書を持つ工程にはその文書を渡す。読み取りだけの調査、参照実装に沿う定型的な書込、出力量が大きいコマンド実行は、各専用ツールの公開説明を確かめて割り当てる。切り詰めや上限到達が報告された調査結果を網羅性・件数・不在の根拠にしない
- 起動直後に可用性失敗が確定したときは、元担当の不在と書込所有権の解放を先に確かめる。候補の除外と再起動は公開応答に従い、同じ失敗が続けば自ら実施できる範囲を進め、委譲が不可欠な工程は未完了として返す。実行中の担当を経過時間や無応答だけで置き換えない
- 委譲先の即時通知の手段は`agent-toolkit/share/rules-subagent.md`「確認事項の即時通知」が定め、通知を受けた後の待機は`references/waiting-and-monitoring.md`「待機区間の構成」に従う
- 実績として記録するのは、起動結果として返されたrouteと識別子とする

forkの監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/delegation/references/runtime-routing.md：実行手段：2026年9月27日」にある。

## 工程別モデル設定

次の工程では、表の用途のengine順に従ってengineごとの段位のモデル設定を並べた候補を使う。表の「起動直前に解決するエージェント」が指すメインは、`agent-toolkit:process-wi`のメインか`agent-toolkit:plan-mode`を起動したエージェントとする。

| 対応工程（呼び出し・役割） | 用途 | 段位 | 起動直前に解決するエージェント | 起動手段 |
| --- | --- | --- | --- | --- |
| WIの選定とレーン分け、計画起草、実装、変更範囲の検証、レビュー修正、CI失敗修正、即時対応の修正、既存不良の調査、マージなしの統合、AWI投入、自動コードレビュー監査、公開工程の終端工程（`add-wi`・`copilot-review-audit`・`defect-investigation`・`exec`・`lane-integration`・`session-termination`の`task`、`delegate`の`high_tier`） | base | high | 各担当を委譲するメイン | `agents_server` MCP |
| 新しい候補・評価軸を導く調査と案出し（`explore`・`delegate`の`high_tier`） | base | high | 委譲するエージェント | `agents_server` MCP |
| 実装後の実行レビュー、ユーザビリティレビュー（`exec-review`・`usability-review`の`task`、`delegate`の`medium_tier`） | review | medium | 各担当を委譲するメイン | `agents_server` MCP |
| プロンプト評価（`refine-prompt`の`task`） | base | medium | 評価を委譲するエージェント | `agents_server` MCP |
| 小規模なレビューと小さい作業（`bulk-replace-review`・`external-write-review`・`reader-fit-review`・`pick-wi-explain`の`task`、`delegate`の`low_tier`、`explore`の省略時と`low_tier`・`medium_tier`指定、`shell`） | chores | 省略時はlow、明示時は指定した段位 | 調査、レビューまたはshellを委譲するエージェント | `agents_server` MCP |
| `start`の`write`による文章起草（`model_type`の省略時と`write`） | - | `write_model`を直接使う | 文章を委譲するエージェント | `agents_server` MCP |
| `start`の`write`へ段位を明示した起動 | base | 指定した段位 | 文章を委譲するエージェント | `agents_server` MCP |
| `atk commit` | base | 省略時はmedium、明示時は指定した段位 | `atk` | `atk` |
| `atk wi process-loop`の新しいセッション、`atk run-skill`、`model_type="orchestrate"` | orchestrate | high | process-loop、run-skillまたは委譲するエージェント | `atk`（`orchestrate`の指定は`agents_server` MCP） |

設定は`atk config`のキーで持ち、各キーは独立に変えられる。

- 段位のモデル: `claude_high_tier_model`・`claude_medium_tier_model`・`claude_low_tier_model`・`codex_high_tier_model`・`codex_medium_tier_model`・`codex_low_tier_model`。値はengine接頭辞を付けない単一の`<model>[/<effort>]`で、effort省略時は`medium`とする。1個を変えると、そのengineと段位を使う工程のモデルだけが変わる
- 用途のengine順: `base_engine`・`review_engine`・`chores_engine`・`orchestrate_engine`。値は`codex`と`claude`の一方または両方を重複なしで並べたASCIIカンマ区切りで、先頭のengineから試す。ClaudeとCodexはモデル実行環境の可用性に起因する失敗で次のengineへ進む
- `write_model`: 日本語文章の起草に使う候補列で、段位と用途に属さない。書式は`<engine>:<model>[/<effort>]`（`engine`は`claude`、`codex`、`agy`）のASCIIカンマ区切りで、agyは起動・turnの失敗で次の候補へ進む
- `atk config apply-preset <プリセット名>`は用途のengine順の4キーだけを保存し、段位のモデルと`write_model`を保持する。プリセットは`codex-balanced`・`codex-near-primary`・`codex-primary`・`claude-balanced`・`claude-primary`で、balancedのレビュー用途はbaseと逆のengineを先頭にする。未設定時は`codex-balanced`のengine順を使う
- `codex`の段位モデルの`astra`、`sol`、`terra`、`luna`は起動時にCodex App Serverの`model/list`から同系列の最新の完全IDへ解決する。`write_model`の`codex:<系列名>`も同じとする。指定effortを受理しない、系列が無い、一覧取得に失敗した場合は理由を示して停止する（推測したIDや旧版への置換は禁止）。バージョン付きのCodex完全IDは固定指定として扱い、Claudeとagyの候補の受理可否は各engineへ委ねる
- `AGENT_TOOLKIT_CONFIG_<キー名の大文字>`の環境変数が空でない間は、保存済みの設定より優先し、その値は保存しない。`atk config set`は保存先だけを更新し、参考一覧に無い値も警告付きで受理する。`atk config show`は値が参考一覧に無い場合に標準エラーへ警告を書く
- `model_type`へは`high_tier`・`medium_tier`・`low_tier`・`orchestrate`・`write`のほか、`write_model`と同じ書式の候補列を直接渡せる（`delegate`では必須、他の`mode`では省略可）。`task`で段位を明示した場合は役割の用途を保って段位だけを変える。候補列を直接渡すとサーバーは設定を読まずその候補列を使う。起動区分は各`mode`（`task`では`<役割名>.subagent.md`の`mode:`）のままとする。恒常的な変更は`atk config set`で行う
- 廃止した旧キー（`high_tier_model`・`medium_tier_model`・`low_tier_model`・`orchestrate_model`）の保存値は、`atk config show`・`get`と設定からの候補解決の初回に新しいキーへ一括移行する。新しいキーで表せない旧値（agyの候補、同じengineの複数候補、同じengineと段位へ対応する異なるモデル）があると移行せずに停止し、競合した旧キー・候補と修正する新キーを示す。示された新キーを`atk config set`で保存してから再実行する。旧キーの環境変数が空でない間は設定からの起動が止まるため、新キーの環境変数へ置き換える

起動と確認は次のとおりとする。

1. `agents_server`では`<役割名>.subagent.md`を`start`の`task`へ、自由本文と種別か候補列を`start`の`delegate`へ渡す。役割の段位は`agent_toolkit/_agents_server/launch_prompts.py`の`TASK_MODEL_TYPES`、用途は同ファイルの`TASK_USAGES`が決め、設定の読込と候補の選択はサーバーが行う
2. `Agent`ツールを使う場合は、起動直前に`atk config get <用途>_engine`で先頭のengineを取得する。続けて`atk config get <engine>_<段位>_tier_model`でそのengineのモデルとeffortを取得する（文章起草は`write_model`の先頭候補を分解する）。`engine`が`claude`でなければ未完了として返すか続行できない理由を返す。分解した値は表の`対応工程`を実行する委譲先の起動にだけ渡す（定義がモデルを固定する委譲先へ`model`を渡すと遮断される）
3. 起動応答は`session_id`と`status`を含み、候補を切り替えた場合は除外理由と採用候補も含む。終端時に`wait`の結果から`model_type`、`engine`、`model`、`effort`を確かめる。保持期限後の`show`を検収の前提にしない
4. サーバーが候補列を使い尽くした場合は、設定外のengineへ自動で切り替えず、未完了として返すか続行できない理由を返す

WI（選定結果の`担当モデル`）が担当とモデル・effortを指定した工程では、その担当を起動する委譲元が指定値を`model_type`へ渡す。同じ起動の対象WIで指定が異なる場合は、WIと指定値を示して起動を止め、起動の単位を分ける。終端結果の実際の`engine`、`model`、`effort`を指定値と比べ、WI、指定値と実際値を計画の進捗ログか引き継ぎ記録へ残す。各工程の文書は、自身が使う担当名（`担当モデル.実装担当`など）と本段落の参照を持つ。

調査と案出しは成果から段位を選ぶ。新しい候補や評価軸を導く調査は、`mode`が`explore`でも`delegate`でも`model_type="high_tier"`を明示する（両者が混在する間も同じ）。既に決めた問いの所在・値・件数などを機械的に確かめる事実確認は含めない。`explore`で指定を省略した場合の`low_tier`と、実行レビューの`medium_tier`は維持する。

工程別モデル設定は表の工程に限り、他の委譲には「modelとreasoning effort」を適用する。工程別モデル設定のキーを持たない名前付きagentは、その定義のfrontmatterに従って起動する。

## 継続と新規起動

- レーン担当とCI修正担当は前の担当の識別子を再利用せず新規に起動する。Codexで元担当を置換する新規threadは`references/codex-runtime.md`「後続操作の共通先行条件」を適用してから起動する
- 継続接続は、同じ担当へ同じタスクの未完了作業、指摘への対応、再レビューを返す場合だけ使う。継続直前に、現在のsessionの起動に使った`model_type`と委譲する工程の`model_type`が一致する場合だけ同じ`session_id`へ`send_message`を送る。engine、model、effortはサーバーが起動時の値を保持するため、可用性で候補が切り替わった担当や、起動後に候補列が変わった担当へも継続できる
- 実際の起動routeが`agents_server`なら、engineと実行ホストによらず`send_message(session_id, prompt, timeout)`を使う。サーバーが実行中turnへのsteer、終端後のreply、保持期限後の暗黙再開を選び、所有する実行主体が終了していても再開する。Claude Codeの`Agent`ツールで起動した担当だけ`SendMessage`を使う（「Agentツールの継続」）
- `model_type`の不一致、異なる担当かタスクへの切替、中断済み、完了配送不能、前提の無効化のいずれかに当たる場合は、検収済み状態を渡して新規起動する。指摘対応ではレーン担当のsessionを継続し、継続不能の場合だけ検収済み状態とレビュー指摘の所在を渡してレビュー修正担当を新規起動する
- 継続不能は実際に継続手段を呼んだ結果で判定し、`agents_server`では`send_message`が`unknown session: <session_id>`を返した場合だけとする。識別子体系の相違を示すエラーと、`wait`の`session retention expired: <session_id>`は継続不能に含めない。保持識別子を失った場合は公開の一覧・個別照会で回復し、終了や失敗を推定しない
- `send_message`のtimeoutは配送の成否が未確定であることだけを示す。その継続要求の受理結果か、要求で始まった新しいreplyの開始を確認できた場合だけ配送済みとして待機を続ける（`wait`の`status=completed`や`agent_message`の文字列一致は根拠から外す）。確認できなければ再送も継続不能の確定もせず、続行できない理由を委譲元へ返す
- レーン担当を新規sessionへ移す場合は、検収済みの先行commitの7文字以上の一意な短縮OID、完了した工程と残る工程を渡す。計画、進捗ログ、保存済みのレビュー指摘管理表で状態を参照できる場合は、絶対パス、対象ID、未記録の差分だけを渡す。記録が無ければ委譲元がmanaged-tempへレビュー指摘管理表を作成して絶対パスで渡す
- レビュー担当が継続不能の場合は、同じ`model_type`で新しいレビュー担当を起動する。渡すのは`レビュー種別: 引き継ぎ再レビュー`、レビュー指摘管理表の絶対パスと`track`、`round: <ラウンド番号>`の行、直前修正の直接影響範囲、読み取り専用の範囲である。実行レビューではレビュー対象HEADの7文字以上の一意な短縮OIDも渡し、`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`の生成規則で`review_contract`を再生成する。ラウンド番号は`${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`の`## ラウンド番号の保持先`に従い、表の最大値から続ける。新しい担当は解消済みの行を再走査せず、未解消の行とそのラウンドの走査範囲だけを扱う

同じ計画の起草から実装、変更範囲の検証までは、同じレーン担当が同一worktreeで順に実施する。継続できない場合だけ新しい担当を起動し、検収済み状態と残る工程を1つの担当へまとめて渡す。

### Agentツールの継続

- 完了報告を返して停止済みの識別子でも、同じ担当へ同じタスクの未完了作業、指摘への対応、再レビューを返し、継続直前に再取得した実効`engine`・`model`・`effort`が起動時と一致する場合に限り`SendMessage`で再開してよい。継続の本文には、継続前に作成・読取したファイルも書込直前に現行本文を取得する条件を含める
- 完了通知の配送先はproviderと構成に依存し、3階層以上での配送先は未検証である。最上位や直接の親を配送先と決めつけず、起動結果IDへの`SendMessage`の成功と、送信後に対応する完了通知を受け取れたことで継続の成立を判定し、`references/claude-code-runtime.md`「完了通知と中継の実行順」へ対応付ける。監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/delegation/references/claude-code-runtime.md：完了通知と中継の実行順：同一セッション内の再開：2026年8月」にある
- 担当とタスクの組の相違・実効3値の不一致・完了報告の配送不能・前提の無効化・中断済みのいずれかでは、停止済みの識別子を再利用せず、必要な前提を明示的に再構成して新規起動する。参照できる記録があれば絶対パス、対象ID、未記録の差分だけを渡す
- `SendMessage`が`No transcript found for agent ID: <識別子>`で失敗したら配送不能と判定し、同じ識別子への再開を控えて新規起動する。セッション識別子が変わった場合は、タスク出力先ディレクトリの識別子を`ls`で比べ、旧識別子配下の委譲先への失敗文面と照らす

## 代替起動

代替起動は`Agent`ツールの利用時にだけ適用する（`agents_server`では候補列によるサーバー側の自動切替が同じ役割を担う）。

- 指定した組合せを一時的に利用できない事象（過負荷、レート制限、サーバー障害、モデルや実行手段の利用不能など、実行環境の可用性に起因する失敗）を実際に観測した場合に限り、設定を解決して委譲を起動したエージェントが、セッション内の起動時指定だけで代替の組合せへ切り替えて再起動してよい。委譲した作業自体の失敗は対象外とする
- 設定値の書換えと`atk config set`の使用は禁止する。設定はユーザー単位の単一値を全セッションが共有し、並行セッションへ波及するためである
- Claude CodeのWeekly limitと5時間の利用上限による拒否は代替起動の対象にせず、解除を待って同じClaudeで続ける（ユーザー指示。待てば確実に解除され、切替で文脈を失う方が損失が大きい）。`agents_server`は候補を切り替えず同じsessionで待ち、委譲元へは`api_error`の種別`usage_limit`で示す
- Codexの二層待機で外側の実行セルがyieldしたことは、内側の`atk agents wait`の継続を示すだけであり、代替起動の契機にしない
- 候補は「代替時の組合せの目安」の表から、失敗を観測した時点の内容で選ぶ。上位行から軽量行へ、各行で`codex`列、`claude`列の順を目安とし、最終的には観測した失敗と各組合せの可用性から判断する。組合せが表のどれに当たるかは`engine`と`model`の一致で決める。失敗した組合せが表にあれば同じ行のもう一方から試す。engineの実行機能自体を呼び出せない組合せは試さず利用不能とする。残る全てで失敗か利用不能なら打ち切り、「工程別モデル設定」の手順4へ戻る
- 代替を試みた場合は、観測した失敗、`atk config get`の設定値、実際に用いた組合せ、対象工程、試さなかった組合せとその理由を報告へ記録する（努力目標）
- 委譲先は自身の判断で設定と異なるengineやモデルへ切り替えず、「工程別モデル設定」の手順4のとおり返す

## modelとreasoning effort

工程別モデル設定の対象外の委譲では、設計判断を伴う実装とレビュー、新しい候補・評価軸を導く調査と案出しに上位モデル、その他に標準モデルを選ぶ（努力目標。調査の起動方法だけで段位を下げず、機械的な事実確認と区別する）。reasoning effortの省略時は`medium`とし、高いeffortは読むファイルが少なく推論の深さが結果を左右すると事前に分かる工程で選ぶ（努力目標）。モデルを明示する場合はeffortも指定する。指定モデルを使えない場合は、価格や利用枠の固定比較でなく再試行を含む総コストで、作業を成立させるモデルへ切り替える。
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
