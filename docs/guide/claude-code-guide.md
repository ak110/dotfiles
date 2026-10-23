# agent-toolkit導入ガイド（Agent Plugins・Claude Code・Codex）

agent-toolkitはAgent Plugins、Claude Code、Codexで共有できるコーディングエージェント向けツールキットである。
Claude Codeは対話、フック、ルールの読み込み、作業全体の統括を担う。Codex CLIは`agents_server` MCP経由の
調査・実装・レビューに加え、Codexセッションで共有スキルを直接実行する。uvは配布スクリプトと
`atk`コマンドの実行基盤である。

## コンセプト

1. 標準動作のカスタマイズ: 判断基準が曖昧な場面での事前相談の徹底、lint抑制時のユーザー確認の必須化、
   検証からコミットまでの流れの自動化などコーディングエージェントの動作を変更する。
   auto mode下でも確認・計画工程を省略しない方針を維持する
2. 品質水準の維持: コードスタイルや設計が乱れたプロジェクトではコーディングエージェントも
   既存コードの影響を受けて同水準のコードを生成する（割れ窓理論）。
   各言語のイディオム、セキュリティ、テスト方針などの一般的な作法は方向性と代表語で示して想起を促し、
   プロジェクト固有の事実と守るべき制約は具体的に示して品質水準を維持する
3. 知識の補完: LLMの学習データに含まれない情報を補う。
   Claude Code関連の仕様は改訂が頻繁なため、`agent-basics:writing-standards`の
   `references/agent-skills.md`と`references/claude-hooks.md`で現行仕様を参照できるようにする。
   個人製作のツール（pytilpackなど）は学習データに含まれないため、
   `agent-toolkit:pytilpack-usage`等でリファレンスを提供する

Anthropic公式のsuperpowersスキルと重複する内容は多いが、
日本語環境での確実なトリガーと大規模開発向けの細かな制御のために独自に作成している。
性質上、頻繁な改訂が発生する。

agent-toolkitはルールファイル、共通のプラグインパッケージ、Codex専用生成rootで構成される。

- ルールファイル: `~/.claude/rules/agent-toolkit/`に配置されるルールファイル。
  自動読み込みされ、行動原則・運用方針・言語表現などの共通指示を提供する
- Agent Plugins: `agent-toolkit/`をパッケージルートとして扱う。
  Agent Plugins仕様の範囲で利用できるスキルとpyfltr MCPを提供する
- Claude Code: `agent-toolkit/`を参照し、Claude固有のmanifest、ルール、フック、実行資源を利用する
- Codex: `agent-toolkit/`を元に生成した`agent-toolkit-codex/`を参照する。
  Agent Plugins用のroot manifestを除外し、Codex固有manifestから同じskill、ルール、フック、実行資源へ到達する

`agent-toolkit-codex/`はCodex 0.154.0のsnapshotへ全資源を含めるため、相対シンボリックリンクを使わない。

ルールファイルとプラグインは相互依存しており、基本的に同時に導入することを前提とする。

部分的に動作を変えたい場合は、
ユーザー側の`~/.claude/CLAUDE.md`・プロジェクトの`AGENTS.md`・プロンプトでの指示などで上書きできる。
優先度はルールファイル側に明記している。

本書ではエージェントへ渡す作業の単位を2種類の略語で呼ぶ。
AWIはユーザーまたはエージェントが登録し、後で自動処理する作業要求である。
UWIはユーザー対応を要する未完了事項と、手順が定める完了報告を扱う項目である。
個別UWIは、ユーザーの判断・資料・操作が次の作業に必要な場面などに使い、回答をエージェントが元の作業へ反映する。
手順が定める完了報告用UWIは保存され、ユーザーが結果を確認する。
会話中の選択式の確認でその場で回答した事項はUWIに重ねず、回答しないまま終わった必要な確認は、対話型・自律型を問わずUWIへ移る。
どちらもユーザーの手元のキューで管理し、対象リポジトリには保存しない。

## 前提条件

単体インストーラーを実行する前に、次の3コマンドを公式手順で導入する。
インストーラーはCLI本体を導入しない。

- `claude`: [Claude Code](https://docs.anthropic.com/ja/docs/claude-code/overview)のCLI
- `codex`: [Codex CLI](https://developers.openai.com/codex/cli/)本体
- `uv`: [uv](https://docs.astral.sh/uv/)による配布スクリプト実行環境

Stopフックが`hookSpecificOutput.additionalContext`を利用するため、Claude Code 2.1.163以上を要求する。
agent-toolkit単体のユーザーでは非強制の前提条件、dotfiles配布の管理設定では`requiredMinimumVersion`で強制する。

## クイックスタート

dotfilesユーザーでは、`chezmoi apply`後の処理がAnthropic公式ネイティブ版を管理する。
未導入時は公式インストーラーで導入し、導入済みの場合は`claude update`で更新する。
WindowsでClaude Codeが実行中の場合は停止せず、更新と旧npm版の整理を次回へ延期する。

Agent Plugins互換クライアントでは、`agent-toolkit/`をプラグインのパッケージルートとして指定する。
導入操作はクライアントごとに異なるため、利用するクライアントの公式手順を参照する。

### ツールキットのインストール

`install-claude.sh`と`install-claude.ps1`は公開済みの互換名を維持しているが、現在はClaude Codeと
Codexの双方を設定する。3コマンドのいずれかを検出できない場合は、ファイルを書き込まずエラー終了する。

ツールキットのインストールには以下のワンライナーを実行する。

- Linux:

    ```bash
    curl -fsSL https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.sh | bash
    ```

- Windows:

    ```cmd
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.ps1 | iex"
    ```

ルールファイルが`~/.claude/rules/agent-toolkit/`へ配置され、Claude CodeとCodexの
agent-toolkitプラグイン、共有`agents_server` MCP、`atk`ラッパーが設定される。
再実行すると最新版へ同期される。

両インストーラーは再実行時にCodexプラグインも更新する。
更新でCodexプラグインの状態が変化し、app-server daemonの稼働を確認できた場合は、daemonの再起動コマンドが案内される。
実行中のセッションは導入時のsnapshotを参照するため、案内されたコマンドはセッションの完了後に実行する。
専用生成rootと再起動案内の条件は[Codex利用ガイド](codex-guide.md)の「プラグイン更新の反映」を参照。

インストール後、非公式のプラグインマーケットプレイスはデフォルトで自動更新が無効のため、初回のみ手動で有効化する。

1. Claude Code内で`/plugin`を実行
2. `Marketplaces`タブで`ak110-dotfiles`を選択
3. `Enable auto-update`を選択

## Claudeの各環境への導入

使う環境に応じて、次の導入方法を選ぶ。

| 環境 | 導入操作と使える機能 |
| --- | --- |
| claude.aiのChat・DesktopのChat | アカウントの`Customize > Plugins`からマーケットプレイスを追加するか、プラグインのZIPをアップロードする。スキル内の品質基準を読む。CLI・hook・local MCPを使う操作は、それらを提供する環境で行う |
| DesktopのCowork | Coworkタブを開き、同じアカウントの`Customize > Plugins`から追加する。hook・local MCPはホストの対応、uvとネットワークの提供、組織の許可条件を確かめて使う |
| DesktopのCode・ローカルClaude Code | 前述の単体インストーラーを実行し、Codeセッションで`/plugin`から導入状態を確認する。CLI・hook・MCPを使う |
| Claude Codeクラウド | 環境の`Setup script`欄へ`install-claude-cloud.sh`の1行を置き、`Environment variables`欄へ`CLAUDE_CODE_PLUGIN_DIRS`など2行を保存する（後述「Claude Codeクラウドの環境設定」） |

Chat・CoworkでGitマーケットプレイスを追加する場合は、`Customize > Plugins > Add > Add marketplace`で`Add from a repository`を選び、`ak110/dotfiles`を入力する。登録された`agent-toolkit`を選んで`Add`を実行する。ZIPで導入する場合は同じPlugins画面のカスタムプラグインのアップロードを使う。アカウント側に追加したプラグインの同期範囲と組織による制限は、[Claudeのプラグイン利用手順](https://support.claude.com/en/articles/13837440-use-plugins-in-claude)を参照。

### アップロード用ZIP

dotfilesのリポジトリで、公開済みcommitのプラグインだけをZIPへ入れる。

```bash
git archive --format=zip --output=agent-toolkit.zip HEAD:agent-toolkit
```

ZIPの直下には`.claude-plugin/plugin.json`と`skills/`が置かれ、`bin/`は含まれない。Gitの追跡対象だけを収めるため、ローカルの`.venv`やキャッシュは含まれない。アップロード前に、展開後の容量200 MB・5,000ファイル以内、参照先の存在、シンボリックリンクと入れ子のZIPが無いことを確認する。受付条件は[公式のプラグインアップロード要件](https://platform.claude.com/docs/en/manage-claude/plugins-api#upload-requirements)を参照。

### Claude Codeクラウドの環境設定

Claude Code 2.1.280以上のクラウド環境で、環境の設定画面の2つの欄へ次を保存する。

`Environment variables`欄:

```dotenv
CLAUDE_CODE_PLUGIN_DIRS=/opt/agent-toolkit-src/agent-toolkit
AGENT_TOOLKIT_RUNTIME_DATA=/opt/agent-toolkit-data
```

`Setup script`欄:

```bash
curl -fsSL https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude-cloud.sh | bash
```

`install-claude-cloud.sh`は配布物を`/opt/agent-toolkit-src`へ取得し（取得済みなら最新へ更新し）、agents_serverの依存を`/opt/agent-toolkit-data`へ事前に準備する。最後に`Environment variables`欄へ保存する2行を表示する。取得先と依存領域を変える場合は、`Setup script`欄の1行を次の形にする。変数はパイプの右側の`bash`の直前へ置く。行頭へ置くと`curl`だけに渡り、スクリプトへ届かない。表示された2行を`Environment variables`欄へ保存する。

```bash
curl -fsSL https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude-cloud.sh | AGENT_TOOLKIT_CLOUD_SRC=<取得先> AGENT_TOOLKIT_RUNTIME_DATA=<依存領域> bash
```

[CLAUDE_CODE_PLUGIN_DIRSの仕様](https://code.claude.com/docs/en/env-vars)は相対パスを読み込まないことと必要なCLI版を定めるため、取得先は絶対パスにする。

ネットワークは`Trusted`などGitHubとPythonパッケージ配布先へ接続できる設定にする。環境の`git`・`uv`とPython 3.15の取得を使う。環境変数は`Environment variables`欄へ保存する。セットアップ内だけの`export`は後続セッションへ引き継がれない。リポジトリの`enabledPlugins`だけではクラウドがプラグインを導入しない。セットアップの終了0、新しいセッションのスキル一覧・CLI案内・MCPの接続を順に確認する。セットアップが失敗した場合は、失敗した段と確認事項が出力の末尾に表示される。setupの制限時間とネットワーク設定は[クラウド環境の設定](https://code.claude.com/docs/en/cloud-environments)を参照。

## 処理順序

インストーラーは次の順で設定する。途中で必須処理が失敗した場合は非0で終了する。

1. `claude`、`codex`、`uv`の存在を確認する
2. Claude Codeルールを原子的に配置する
3. Claude Codeのマーケットプレイスとプラグインを設定する
4. Codexのマーケットプレイスとプラグインを設定する
5. User scopeに残る旧Codex MCPの完全一致定義を確認し、該当時だけ移行する
6. `atk`ラッパーを配置する

新しいMCPサーバーはplugin内の共有`agents_server`であり、User scopeへ登録しない。
インストーラーと`chezmoi apply`後処理は過去の手順で登録されたUser scope
`codex`が`codex mcp-server`の完全一致定義である場合だけ、直前に再確認して削除する。
追加フィールド、別のcommand・args・timeout、Local・Project scopeの設定は変更しない。
plugin更新だけの処理では外部設定を変更せず、必要な場合は診断と手動削除手順を表示する。

## 設定確認

次のコマンドで導入結果を確認できる。

```bash
claude mcp get codex
codex plugin list
claude plugin list
```

`claude mcp get codex`は旧User scope定義の有無を確認する診断である。`agents_server` MCPは
Claude CodeまたはCodex pluginから読み込まれるため、`codex plugin list`と`claude plugin list`で各pluginの状態を確認する。

委譲の状態は次のコマンドで確かめる。
`atk agents list`は実行中と保持中の委譲先を一覧し、`--watch`を付けると約2秒ごとに表示を更新する。
`atk agents logs <session-id>`は選んだ委譲先の記録を表示し、`--follow`を付けると追記分を表示し続ける。`--verbose`を付けない表示はツール呼び出し・ツール結果・思考を省いて会話の流れを要約し、`--verbose`を付けると全イベントを全文で表示する。
`atk agents wait`は委譲先の終端を待ち、回収した結果を表示する。
`agents_server`の公開ツール（`start`、`send_message`、`kill`、`list`、`show`、`stop`）の入力と応答の契約は[design-agents-server.md](../development/design-agents-server.md)を参照。

Bashの静的な検出は、for・算術for・select・while・until・if/elif/else・caseの条件と本体、入れ子、文全体のパイプ・リダイレクト・背景化にも及ぶ。未引用の予約語前置`time`・`time -p`・`!`・`coproc`を共通解析で扱い、coprocの暗黙の出力パイプと背景実行を保持する。引用やheredoc本文、算術式のデータ、未実行の関数定義と未確定の動的コマンドは実行位置とみなさない。未完了の背景出力の読取は進捗確認として警告にとどめ、反復しても遮断しない。終端結果は完了後に同じ保存先の全量を読み直して確認する。

Claude CodeのprojectsとCodexのsessionsにある原セッション記録の本文をRead・Grep・静的に判定できるBashで参照すると、PreToolUseが`atk run-script session-review-evidence`を案内する。本文検索は`--grep`、位置の詳細は`--detail`、ツール入力は`--tool-calls`、期間からの探索はカタログで選べる。属性確認やパスの言及、正式な抽出と派生証拠の処理は対象外である。大容量の原記録も同じ専用照会へ案内する。原記録以外の保存本文は、Claude Codeでは`Read`に`allow_large: true`を指定して全文を読み、Codexでは`atk read-file`で終端まで読む。

カタログの親セッション一覧は`--catalog-workflow process-wi`の完全一致、`--catalog-known-workflow`のunknown以外、`--catalog-cwd-contains <文字列>`の条件で選べる。
`--catalog-fields session_id,workflow,workflow_locator,started_at,tokens`を付けると行の種類を表す`kind`に続いて指定順の項目を返す。
項目の指定を省くと全項目を返す。
条件を併記すると全てに一致する行を返し、末尾の走査要約では走査した親セッションの記録数と選択した行数を区別できる。
これらの指定は両runtimeに共通する。
走査対象はClaude Codeでは`--catalog-claude-project`へprojectディレクトリ（`~/.claude/projects/<project>`）、Codexでは`--catalog-codex-history`へ履歴ディレクトリを渡して選ぶ。
カタログの走査では走査期間の開始の`--since`と終了の`--observation-boundary`が必須であり、ISO 8601の時刻を渡す。
次の例はClaude Codeのprojectから、`process-wi`の親セッションを5項目で一覧する。

```sh
atk run-script session-review-evidence -- --catalog-claude-project ~/.claude/projects/<project> --since 2026-10-01T00:00:00+09:00 --observation-boundary 2026-10-08T00:00:00+09:00 --catalog-workflow process-wi --catalog-fields session_id,workflow,workflow_locator,started_at,tokens
```

振り返りの準備後や最終報告後に届いた新しい`気付いた改善点:`の行は`agent-toolkit:completion-report`の工程に従って振り返りへ取り込み、結果報告を更新してから終了する。
Stopはこの行の有無と報告本文を判定しない。

### 週ごとの費用とレビュー指標

費用改善の前後比較と再悪化の確認には、開始と終了を指定して手動で集計する。
次の例は2026年10月5日0時から19日0時までを集計し、終了を省くと実行時刻までを集計する。
日付と時差のない日時は実行ホストのローカル時刻として解釈する。

```sh
atk run-script cost-metrics -- --since 2026-10-05 --until 2026-10-19
```

JSON Linesの`cost-metrics-week`は月曜0時から始まる週ごとの次の値を返す。

| 項目 | 定義 |
| --- | --- |
| `adopted_awi_count` | private-notesのGit履歴で期間内に採用状態へ変わったAWI数。初出から採用済みの項目も含む。状態を保つ配置移行・本文編集とUWIを除き、全対象リポジトリを合算して同名ファイルを週内で1件とする |
| `tokens` | 開始時刻が期間内のルートと全委譲先のトークン。全ルートとprocess-wiを分け、それぞれ合計とruntime別に、非キャッシュ入力・キャッシュ作成入力・キャッシュ読取入力・出力の4成分を示す |
| `tokens_per_awi` | 全ルートとprocess-wiそれぞれの4成分を同じ週の採用AWI数で割った値。0件の週はnull |
| `review.rounds`・`five_or_more_ratio` | plans/配下の保存済みexec-review.tsvを最初の追加commitの週へ割り当て、exec-review行の最大roundが0・1・2・3・4・5・6以上の表数と、5以上の表の割合を示す。指摘なしは0 |
| `review.levels` | exec-reviewの指摘を要件・仕様・詳細・実装・旧形式の未記録に分けた件数 |
| `review.without_modification_count`・`without_modification_ratio` | exec-review行のうちno-response-reasonがありresponseが空の件数と、全exec-review指摘数に対する割合 |

各記録は1回だけ数え、委譲先はルートの開始週へ合算する。
Codexのreasoning_output_tokensは出力に内包される。
週の`tokens`はトークンのまま示し、金額は`cost-metrics-session`の別行へ出す。
レビュー表の旧trackであるimplementation-reviewはexec-reviewとして読み、他のtrackは対象外とする。
`~/.claude/plans`だけにある未保存の表は集計から除き、`unsaved_table_count`で件数を示す。

末尾の`cost-metrics-coverage`はruntimeごとの読めた最古の記録時刻、読めなかった記録・表、特定できなかった委譲先の件数を示す。
同じ出力の`cost-metrics-session`は期間内に開始したルートごとに、engine・session_id・開始時刻・cwdと子孫を含む推定費用（USD）を返す。
`estimated_usd`は単価不明や記録不足がある場合にnullとなり、`known_usd`が算出できた部分、`unknown_responses`と`unknown_tokens`が単価不明の応答数と使用量を表す。
`breakdown.self`はその記録のClaude累積額を含み、`claude_subagents`はその額に含まれない追加分、`delegates`は別プロセスの委譲先からの追加分である。
親の算出額へ含まれる子の追加分が0でも、子自身の費用が無料という意味ではない。
Claudeの最新の算出額を優先し、残りはモデルごとのトークン単価で補う。実際の支払額とは区別する。
料金表のURL・基準日・区分・算出式と、TTL不明時の仮定は末尾の`pricing`に含まれる。
最古の記録より前に始まる週は`missing_token_records`がtrueとなり、トークンの記録が欠けた週として扱う。
記録が欠けた週の0は、使用量0の証拠にはならない。
ルート候補の本文は更新時刻が開始より前なら読込前に除くため、最古の時刻は今回の走査で読めた範囲を示す。選んだルートの子孫は更新時刻で除外せず、同じ家族の費用へ含める。
`--claude-projects-dir`・`--codex-sessions-dir`・`--private-notes`で各入力のディレクトリを上書きできる。
指定を省くと`~/.claude/projects`・`~/.codex/sessions`・`atk config get private_notes`と同じ値を使う。

## 工程別モデル設定

各工程は`<工程>_model`に設定した候補列を直接使う。
複雑な作業・計画・設計判断・原因分析・観点選定と統合はexecute、確定した単発コーディング・中規模走査はchores、定型走査・軽微な作業・コマンド実行はchores_fastへ分ける。
実行レビューはreview、小規模レビューはreview_fastを使う。役割全体の工程指定を保ち、部分作業の分担は起動・読込・受領レビュー・統合・再試行を含む総費用で選ぶ。

モデル実行環境の可用性に起因する失敗を観測した場合だけ次の候補へ進む。
Claude Codeの利用上限では、`claude_usage_limit_wait`の方針に従って解除を待ち、同じ会話で作業を続ける。
ユーザーが起動したClaude Codeのメインの会話では、agent-toolkitのコマンドhookとFunction hooksが解除後の再開を予約する。
セッション名の自動設定と利用上限後の再開は、組織向け設定で一部のイベントが省略される環境にも対応する。
再開予約にはagent-toolkitのFunction hooksを有効にした状態でClaude Codeを起動する。
利用上限による拒否か、ターン終了時の5時間枠・週次枠の100%以上の使用を検出すると、解除予定時刻の後に同じ会話へ継続指示を送る。
解除予定が不明な場合は300秒ごとに再確認するため、待機中もClaude Codeを起動したままにする。
解除予定時刻後に新しいターンが始まっていれば重複した継続指示を送らず、作業完了済み・ユーザーによる中止の場合は指示を受けても作業を再開しない。
Remote Controlは、手元で動くClaude Codeの会話へブラウザーやClaudeアプリから接続する機能であり、その接続中もこの再開予約が働く。
接続する場合は会話内で`/remote-control`を実行し、ログインと接続条件は[公式のRemote Control手順](https://code.claude.com/docs/en/remote-control)を参照。
`agents_server`、`atk wi process-loop`、`atk run-skill`と`atk commit`も再送を要さず、解除待ちの種類と予定時刻を状態表示または端末へ示す。
`atk run-skill`は指定スキルの実行中に待機対象の上限へ達した場合も同じ会話を再開する。
`agents_server`のClaude委譲は、後述の役割別接続設定と利用上限の待機方針に従って同じ会話を継続する。
解除待ちの委譲先と背景wait・定期再確認のtaskは保持し、作業の継続が不要になった場合だけ停止する。
変更できるモデルキーと初期値は次のとおりである。

| キー | 用途 | 初期値 |
| --- | --- | --- |
| `execute_model` | 複雑な作業・計画・実装・原因分析・統合 | `claude:opus[1m]/medium,codex:sol/medium` |
| `review_model` | 実行レビュー等 | `codex:terra/medium,claude:sonnet[1m]/medium` |
| `review_fast_model` | 小規模レビュー | `codex:luna/medium,claude:haiku/medium` |
| `chores_model` | 確定済み作業・単発コーディング・中規模走査 | `codex:terra/medium,claude:sonnet[1m]/medium` |
| `chores_fast_model` | 定型走査・軽微な作業・コマンド実行 | `codex:luna/medium,claude:haiku/medium` |
| `orchestrate_model` | process-loop・run-skill・最上位 | `claude:opus[1m]/medium,codex:sol/medium` |
| `write_model` | 文章起草 | `agy:gemini-3.8-flash/medium,claude:claude-opus-5-5/medium` |

各値は`<claude|codex|agy>:<model>[/<effort>]`または`opencode:<provider>/<model>[@<effort>]`をASCIIカンマで区切る。
effort省略時はmedium。OpenCodeのmodel内の/を保持し、末尾の@より後をvariantとする。
orchestrate_modelはOpenCodeを受理しない。
OpenCodeの導入・provider定義とQwenの例は[README](../../README.md#opencodeの委譲先)を参照する。

```bash
atk config set review_model agy:gemini-4-argon/medium
atk config set chores_model opencode:local/Qwen/Qwen3.8-27B@thinking,claude:sonnet/medium
```

startのmodel_typeは7工程名か候補列を直接受け取る。delegateは指定必須、explore・shellはchores_fast、writeはwriteが既定値である。
taskでは役割frontmatterのmodel_typeを使い、明示指定はその工程か候補列に置き換える。modeは役割の宣言を保つ。
Agentツールで解決する場合は`atk config get <工程>_model`を1回実行し、先頭候補をengine・model・effortへ分解する。

showはパス・7工程・その他の設定を1キー1行で表示し、getは指定順に値を返す。
`AGENT_TOOLKIT_CONFIG_<キー名の大文字>`の空でない値が保存設定より優先し、解除すると保存値へ戻る。環境変数の値を保存しない。
setは保存先を更新し、環境変数の優先中はその旨を示す。参考一覧外のモデル・effortも警告付きで保存する。
Codexのastra・sol・terra・lunaは起動時に同系列の最新IDへ解決し、採用値をagents_serverのshowで確認できる。完全IDは固定指定になる。

apply-presetはwrite_modelを保ち、他の6工程を一括保存する。次表の初期値からの変更を使う。

| プリセット | 初期値から変更する工程 |
| --- | --- |
| `codex-balanced` | 初期値と同一 |
| `codex-near-primary` | executeを`codex:sol/medium,claude:opus[1m]/medium`へ変更 |
| `codex-primary` | executeとorchestrateを`codex:sol/medium,claude:opus[1m]/medium`へ変更 |
| `claude-balanced` | choresを`claude:sonnet[1m]/medium,codex:terra/medium`、chores_fastを`claude:haiku/medium,codex:luna/medium`へ変更 |
| `claude-primary` | review・choresを`claude:sonnet[1m]/medium,codex:terra/medium`、review_fast・chores_fastを`claude:haiku/medium,codex:luna/medium`へ変更 |

`atk config apply-preset show`か引数を省略したapply-presetは、5プリセット各6キーを保存せずに表示する。未知名は終了コード2になる。
OpenCodeは工程別キーへ直接指定する。

旧段位9キーと用途別engine順5キーが残る設定は、初回のshow・get・候補解決で6工程へ一括合成する。
共存する古いexecute_model等より合成値を優先し、段位・engine順と段位制導入前の残骸キーを除去する。
write_modelと非モデル設定は保つ。旧旧4キーから段位制への移行は撤去する。
廃止キーの環境変数がある場合は停止するため、表示された工程別環境変数へ置き換える。

`codex_fast_mode`のtrueでfast mode、falseで標準速度（初期値）とする。次のturnから反映し、MCP再起動を要さない。
Codexのモデル表示はeffortがあれば`codex:<model>/<effort>@fast`とする。
`codex_model_providers`は主接続先を先頭にしたID列で、空なら通常設定・代替なし。前後空白・重複を除き、空要素を拒否する。
`claude_usage_limit_wait`と`codex_usage_limit_wait`は、それぞれの利用上限の待機方針を指定する。
`never`は解除を待たず、`five-hour`（既定）は5時間枠だけを待ち、`always`は週次枠なども待つ。
待機対象外の上限では、設定済みの代替接続またはモデル候補へ進む。
ClaudeのAPI接続ファイルと役割別接続方針は、次の節で設定する。

CodexのAPI移行先は工程別モデル候補とは別の設定である。サブスクのみ、サブスク＋API、APIのみの
設定と解除、API認証と課金は[Codex利用ガイド](codex-guide.md#codexのサブスクとapi接続先)を参照する。
この機能は`agents_server`のCodexだけに作用し、Claudeの解除待ちと直接起動の接続先を変えない。

### Claudeの役割別API接続

`claude_api_env_file`が未設定（空文字列）の場合、process-loopとagents_serverは親の環境変数とClaude Codeの通常の認証解決を使う。
旧`claude_api_mode=api-only`の互換設定ではAPI用envファイルを必要とする。
APIキー、Claude Codeの設定、Bedrock・Vertexなどをそのまま利用できる。
この場合は認証検出による起動拒否、認証環境の隔離、役割別の接続割当や切替を行わない。
その会話の再開も通常の認証解決を維持し、後からenvファイルを設定しても既存会話には適用しない。
接続情報もAPIパスも持たない旧形式の登録も同じ扱いとなる。

envファイルを設定した場合は、以下の役割別接続管理を適用する。
通常の`atk wi process-loop`のオーケストレーター、`all-repos`のリポジトリ担当、および`agents_server`のClaude委譲へ適用する。
ユーザーが直接起動するClaude Code、組み込みAgentとfork、`atk run-skill`、`atk commit`、Codexの接続先は対象外である。
`all-repos`のリポジトリ担当はオーケストレーターとして扱う。

| 設定 | `true` | `false` |
| --- | --- | --- |
| `claude_orchestrator_api_only` | オーケストレーターはAPIだけを使用（既定） | オーケストレーターはサブスクリプションだけを使用 |
| `claude_worker_api_first` | 通常のClaude委譲先はAPI、サブスクリプションの順 | 通常のClaude委譲先はサブスクリプション、APIの順（既定） |

委譲先は利用できる認証の候補から選ぶ。
オーケストレーターの指定接続が使えない場合は、復旧案内を表示して起動を止める。
旧`claude_api_mode`の`pay-as-you-go`と`api-credits`は、この2設定へ移行する。
旧キーの`api-only`は両役割をAPIだけにする互換設定として保持し、新しいbool設定の明示保存で解除する。
[APIクレジットの公式対象範囲](https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers)はClaude APIとClaude Agent SDKを含み、Claude Codeは対象外である。
APIクレジットを委譲先で使う場合は`claude_worker_api_first`を`true`、`claude_orchestrator_api_only`を`false`にする。
API接続に使う環境変数をenvファイルへ保存し、パスを設定する。
形式は1行1つの`KEY=VALUE`で、先頭の`export`と空白、値全体を囲む一対の引用符、空行と`#`で始まるコメント行も使える。
AnthropicのAPIへ直接接続する場合は、[Claude ConsoleのAPI keys](https://platform.claude.com/settings/keys)で発行したAPIキーを`ANTHROPIC_API_KEY`へ記入する。
APIの利用料金はサブスクと別であり、ConsoleでAPIの課金設定も用意する。
たとえば`~/.config/claude-api.env`をエディターで作成し、次のプレースホルダーを取得した値へ置き換える。

```dotenv
ANTHROPIC_API_KEY=YOUR_API_KEY
```

別のAPI接続先を使う場合は、その提供元が指定するURLを`ANTHROPIC_BASE_URL`へ記入する。
Bearer認証を指定された接続先では、提供元から取得したトークンを`ANTHROPIC_AUTH_TOKEN`へ記入する。
`ANTHROPIC_AUTH_TOKEN`は`ANTHROPIC_API_KEY`より優先するため、直接接続用ファイルにはAPIキーだけを記入する。
変数の役割と認証の優先順は[Claude Codeの認証仕様](https://code.claude.com/docs/en/authentication#authentication-precedence)、URLの設定は[環境変数の仕様](https://code.claude.com/docs/en/env-vars)を参照。
ファイルはGit管理外へ置き、Linux・macOSでは`chmod 600 ~/.config/claude-api.env`で自分だけが読み書きできるようにする。
Windowsではファイルのプロパティのセキュリティ設定で、使用するOSアカウントだけに読取権限を与える。
ファイルを用意した後に次のコマンドでパスを設定する。

```bash
atk config set claude_api_env_file '~/.config/claude-api.env'
atk config get claude_api_env_file
atk config set claude_worker_api_first true
atk config set claude_orchestrator_api_only false
# 従量課金APIをオーケストレーターへ割り当てる場合
atk config set claude_orchestrator_api_only true
atk config set claude_worker_api_first false
# 解除する場合
atk config set claude_api_env_file ''
```

setは先頭の`~`を展開してパスだけを保存する。
ファイルの読取は新規起動・切替・再開時に行う。setは将来作成するパスも受理する。
`AGENT_TOOLKIT_CONFIG_CLAUDE_API_ENV_FILE`の空でない値は保存値より優先するため、解除時はこの変数も外す。
空、存在しない、読み取れない、形式が不正なファイルではAPI候補を選ばない。
非空の無効パスは起動時に対象パスと修正・解除操作を示す。
`AGENT_TOOLKIT_CONFIG_CLAUDE_ORCHESTRATOR_API_ONLY`と`AGENT_TOOLKIT_CONFIG_CLAUDE_WORKER_API_FIRST`で役割別設定を一時上書きできる。
`api-only`は新規起動からAPIを使い、既存のサブスクリプション・通常認証の会話も次の継続から同じ会話をAPIへ移す。
保存後の再開やサーバー再起動後も適用され、上限解除後もAPIを使う。
APIの料金はサブスクリプションと別に発生する。
API環境ファイルの未設定・空・不正では、`atk config set claude_api_env_file <パス>`でファイルを設定・修正してから再送する。
API認証や残高で失敗した場合は認証・接続先・課金設定を確認して同じ会話へ再送する。
以下の接続切替・解除待ち・選択済み接続の保持はpay-as-you-goとapi-creditsに適用する。
通常の委譲先は利用枠またはAPI残高の不足時に同じsession ID・model・effortで次の接続へ移る。
認証手段が1つだけの場合はその接続の解除を待つ。両認証を持ち全候補が尽きた場合と、認証手段が無い場合は、理由と復旧操作を返して終了する。
オーケストレーターは選択済み接続を固定し、解除待ち・次のengine候補・不成立理由の返却へ進む。
接続済みの会話はstop・保持期限・サーバー再起動後も選択済みの接続を保持する。
`pay-as-you-go`で利用上限によりAPIへ移った委譲先は、上限の解除予定時刻の後の最初の追送か自動再開からサブスクリプションへ戻る。
新規sessionは毎回モードの先頭候補から評価する。
親環境と親settingsの認証・接続・モデル指定を隔離し、API会話には選択したenvファイルの値を渡す。
ユーザー設定などのsettingsの`env`へ接続関連の変数を書いた場合、委譲先のコマンドへその変数が空の値で渡ることがある。
サブスクリプション起動では同じ指定を除き、選択した接続を新規起動と再開の両方へ適用する。

## 選定結果の確認設定

対象リポジトリの`pyproject.toml`に`[tool.agent-toolkit.pick-wi-check]`の`norm-spec`を置ける。置いた場合は`agent-toolkit:process-wi`の選定結果を確かめる`atk run-script pick-wi-check`が、条件に当たる項目の`プロジェクト規範の指定`の欠落も報告する。
各条件は`paths`と`suffixes`か`agent-doc = true`（エージェント向け文書）で対象のパスを選ぶ。`require-paths = true`は当たった対象ファイルのパスを、`require-text`は指定の文字列を、`プロジェクト規範の指定`へ書くことを求める。
表を置かないリポジトリでは指定を求めない。設定の構文と型の誤りは終了コード2で、設定の場所と直し方を示す。

## Claude Codeの推奨設定

以下の設定を適用することを推奨する。

### `~/.claude/settings.json`

- `autoMemoryEnabled`: `false`（自動メモリー機能を無効化）
- `showClearContextOnPlanAccept`: `true`（plan mode承認時にコンテキストクリアの選択肢を表示）
- `env.CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR`: `"1"`（Bash呼び出しごとに作業ディレクトリをプロジェクトへ戻す）
- `env.CLAUDE_CODE_NO_FLICKER`: `"1"`（画面のちらつきを抑制）
- `env.PLAYWRIGHT_MCP_OUTPUT_DIR`: ホーム配下の`.cache/playwright-mcp`の絶対パス（Playwright MCPが作業中のリポジトリへ`.playwright-mcp/`を残さないようにする）
- `permissions`: 許可・拒否するツールやパターンを記述
 （[例](https://github.com/ak110/dotfiles/blob/master/share/claude_settings_json_managed.json)）

### `/config`コマンド

- `Verbose output`: 有効
- `Default permission mode`: `Auto mode`
- `Language`: `Japanese`

### `/plugin`コマンド

claude-plugins-officialのプラグインは次の方針で扱う。

- 推奨: `context7`・`typescript-lsp`（`npm install -g typescript-language-server typescript`が必要）
- 任意: `skill-creator`
- 無効: `pyright-lsp`（Claude Codeがインストールを推奨するが誤動作が発生するため、インストール後に`Disable`）
- 無効: `claude-md-management`（`CLAUDE.local.md`を`.claude.local.md`と誤認し、`AGENTS.md`を走査しないため、インストール済みなら自動で無効化）

### VSCode設定（任意）

ターミナル内での右クリックによる意図しない貼り付けを防ぐ場合は、`settings.json`へ以下を追加。

```json
{
  "terminal.integrated.rightClickBehavior": "nothing"
}
```

## 推奨ワークフロー

`/agent-toolkit:process-wi`を会話から起動する場合も、`atk wi process-loop`から起動する場合も、初期選定が1レーンなら、専用worktreeを持つ1人の担当エージェントが計画・実装・独立した実行レビューの調整・統合を続ける。
`all-repos`で複数リポジトリを扱う場合は、リポジトリごとにこの担当を置く。
ユーザーと会話する起動元のエージェントが、判断を要する事項と最終結果をユーザーへ届ける。
`all-repos`では起動元がエージェントではないprocess-loop（`atk wi process-loop`）のため、各リポジトリ担当がその役を担い、確認のUWIと完了報告・振り返りの報告用UWIでユーザーへ届ける。
各利用形態で、エージェントは実装前に使う操作・入力・期待する結果を決め、実装担当がその操作で結果を確かめる。
実装担当は確認できた結果と未確認事項、要求が変わった場合の判断を、実装を担当していないレビュー担当へ渡す。
未確認事項は、起動元のエージェントが担当者と作業を再開する方法を整理して次の担当へ引き継ぐ。
ユーザーの判断や操作が必要な事項は、その内容をユーザーへ伝える。
処理中に発見した既存不良の即時対応では、進行に必要な範囲だけ修正して安全に利用できることを確認し、テスト・独立文脈レビュー・原因分析・類似見直し・再発防止を含む正式対応を次のセッション用AWIへ登録する。
今回の作業で導入した不良と、処理開始時の要求は、同じセッションで完成条件まで満たす。

作業の進め方は次の4パターンを推奨する。要件がどこまで固まっているかと、AWIの発生頻度で選ぶ。決まったスキルを定期的に実行したい場合は定期実行型を選ぶ。

### 対話型

エージェントへ作業を直接依頼する。エージェントは原則として計画ファイルを内部資料として作成する。既存の値や文字列の差し替えだけで完結し、恒久化・リファクタリングの候補が構造上生じない変更だけ作成を省く。
要件と公開範囲へ既存の認可を適用し、実装、検証、公開まで進める。
ユーザーだけの選好・認可・取得できない入力が残る場合は、その事項を着手前に確認する。技術的に確定できる事項はエージェントが判断して報告する。
対話の途中で要件が変わる作業や、方針をその場で確定したい作業に向く。

### 自律型

AWI処理の常駐実行（`atk wi process-loop`）を起動し、依頼したい内容を要求として登録する。
登録した要求は、調査・計画・実装・レビュー・公開まで順に自動で処理される。
対象リポジトリへWIを投入中の`/agent-toolkit:add-awi-by-user`が活動していれば、開始前に処理可能WIの検出から10分を猶予する。
投入中かどうかは同スキルが投入先の確定時に記録し、完了・中止時に削除する。投入先は同じoriginの別worktreeや別表記のURLも同じリポジトリとして扱う。
WIキューを保存する共有リポジトリ（private-notes）へ新しいAWIかUWIが届くたびに、検出時点から10分へ延長する。
延長の回数・累計時間に上限はなく、活動がなくなれば期限前でも開始判定へ戻る。
終了済みのセッションと、稼働中の子エージェントがないユーザー応答待ちは猶予の理由にしない。
同じリポジトリの通常の作業、process-wi、session-review、別リポジトリへの投入、会話を持たない常駐プロセスも猶予の理由にしない。
既存WIの編集・状態移動は新規到着として数えない。

対象リポジトリに未回答UWIがあれば、保存済みUWIの最終更新から10分が経過するか全回答になるまで開始を待つ。
holdとrun-skill由来もこの判定へ含め、回答後にadoptedへ移ったUWIの更新も反映する。
活動猶予とUWI待機は同時に評価し、両方の解除後に処理可能集合を確認する。
待機理由・期限・延長・開始は端末とprocess-loopのログで確認でき、待機中も`abort`で中断できる。
すぐに処理を始めたい場合は、process-loopを動かす端末（tmuxではそのペイン）でEnterを押すと、活動猶予とUWI待機をまとめて今回だけ解除し、処理可能なWIの再評価へ進む。
解除は次回の待機へ持ち越さない。標準入力が端末でない実行、入力が閉じた実行、バックグラウンドジョブでは入力を読まず、従来どおり期限まで待つ。
選定はClaude CodeとCodexのどちらもメインが直接行い、別の選定担当は起動しない。候補本文から分類・割当・モデル・配置根拠を判断し、取得済み候補と意味判断をpick-wi-checkへ渡して定型欄の生成・確認・保存を1回で行う。意味判断と要求の被覆、レーン数上限、同じ担当thread、単一writer、実行レビューと直列統合は維持する。
圧縮ツールが提供されるClaude Codeでは、各段階の全初期通常レーンの初回起動後と、終端担当の起動後の長時間待機前に会話圧縮を予約する。独立した工程を先に開始し、セッションのmanaged-temp直下のparent-handoff.mdへ再開状態を保存する。引数なしで予約し、背景待機を始めてターンを終える。受付は圧縮完了と区別し、通知後に保存記録から同じ工程へ戻る。ツールが無い環境では、その旨を記録して通常の待機へ進む。Codex、通常0件、処理中の追加レーン起動と実行レビュー待機は予約の対象外とする。

レーン資源は`atk lane create`が専用worktree・branchと所有session、準備・撤去手順を登録し、準備後に担当を起動する。process-wiは対象リポジトリごとに1つだけ起動する前提で、専用worktreeは対象リポジトリの`.claude/worktrees/process-wi-lane-NN`に固定され、実行を繰り返してもパスは増えない。統合後も追加に備えて同じ資源を保持する。通常終了の`atk agents-exit-session`とprocess-loopの子終了後が`atk lane delete`と同じ安全条件で回収する。自動回収は解放を待たず、その時点で適格な資源を回収する。未統合・dirty・稼働中・未解放・確認不能・途中失敗の資源は保持し、次回の開始時に`atk lane delete --list`から理由と残る操作を読む。worktreeパス・Git登録・branchが全て無いと確定した登録は、警告と最後に記録したHEADを残して回収する。worktreeパスが無い場合はdelete手順を実行しない。セッションのmanaged-tempと検証の証拠は専用資源とともに削除しない。選定結果の一時ファイルが失効しても、所有登録が写した選定情報で統合と回収を続ける。選定情報を持たない古い登録は`atk lane recover --session-id <所有session>`で元のbranchを残して専用worktreeを解放する。
要件を本文だけで説明できる作業に向く。
1レーンの場合も隔離と独立文脈レビューを保ち、`atk lane integrate`が統合・計画保存・WI終端・統合済み記録をまとめて行う。
途中失敗は進行記録と成果を残し、同じ入力で未完了の工程から再開する。
操作の詳細は[run-lanes.md](../../agent-toolkit/skills/process-wi/references/run-lanes.md)を参照。
要求を登録するときは、先行する要求の成果が無いと安全に実施できないか完成を判定できない場合だけ、その先行する要求への依存を設定する。

自律型でも対話型と同じ基準で確認の要否を判定し、技術と既存認可で確定する事項はエージェントが実施して報告する。
ユーザーの判断・資料・操作が次の作業に必要な場面などは個別UWIとして記録され、回答を必要とする部分だけを保留して他の作業を続ける。
自明な取得・再実行の案内と環境制約による未確認範囲は、実施した検証の結果とともに完了報告で伝える。
process-wiなど手順が定める完了報告用UWIは保存され、ユーザーが確認する。
作業中に資格情報が見つかった場合は、値を再掲せず、同じ所在の報告UWIが無い場合に所在ごと1件の報告UWIを届ける。報告だけで扱った対応の可否を回答できる。
`atk wi process-loop`から起動したセッションでは、事前に承認を要する事項が回答期限付きの質問として表示されることもあり、期限内に回答が無ければUWIへ移る。
UWIの確認と回答は後述の「UWIへの回答と状態確認」の操作で行う。

オーケストレーターの候補列は`atk config`の`orchestrate_model`へ設定する。
前述の工程別モデル設定と同じく、設定した候補列の先頭から順に起動を試す。
設定していない場合はClaudeの`opus[1m]/medium`、Codexの`sol/medium`の順に使う。
Claude Codeを使う場合は設定を変更せず、次のコマンドを実行する。

```bash
atk wi process-loop
```

Codexへ切り替える場合は、設定を保存してから起動する。常駐中に保存した設定は、次のセッションの起動前に反映される。

```bash
atk config set orchestrate_model codex:sol/medium,claude:opus[1m]/medium
atk wi process-loop
```

dotfiles以外のリポジトリでworktree隔離を使う場合は、`atk wi process-loop --worktree[=NAME]`を指定する。
`NAME`を省略すると`process-loop`を使い、dotfilesリポジトリではオプションを指定しなくても自動的にworktreeを使う。

複数リポジトリのWIを`atk wi process-loop`で継続して処理する場合は、ローカルworktreeの絶対パスをJSON配列で登録し、ホームから起動する。

```bash
atk config set process_loop_repositories '["/home/user/project-a", "/home/user/project-b"]'
atk wi list --configured-repos
atk wi process-loop all-repos
```

共通オプションは`atk wi process-loop --no-update all-repos`のように`all-repos`の前へ指定する。
通常の`atk wi process-loop`は現在のリポジトリだけを処理する。
登録が空配列なら待機し、登録外のリポジトリへのWI投入はできる。
登録先を解決できない場合は表示された対象を直して再実行する。
同じoriginの登録は先頭へまとめ、登録変更は次の取得へ反映する。

all-reposは一括取得した候補を各対象cwdの通常delegateへ渡し、担当の起動・所有・完遂判定・続行指示を行う。
各担当は対象リポジトリのprocess-wiのメインとして、ユーザーへの確認、完了報告と振り返りまで自ら行う。
対象の規範・準備・監査・実装・Git・公開は各担当が扱い、レーン上限もリポジトリごとの候補数から求める。
開始前の猶予は登録対象全体に作用し、開始後は1担当の失敗や所有待ちで別担当を止めない。
通常起動と同じoriginの処理所有を共有し、対象のWorktreeCreate/Removeがあれば作成先と撤去へ反映する。
設定済みhookが失敗した場合は診断と途中資源を保持する。
`abort`・`status`・`instruct`は同じOSアカウントの制御である。
`instruct`は単一起動では次の処理会話へ1回だけ渡る。
all-reposでは次の起動集合で1回受領し、各担当へ中継して、その担当の対象に関する部分を適用する。
再開は記録されたall-repos担当のsession IDを`--resume`へ指定する。
詳細は[all-repos-orchestration.md](../../agent-toolkit/skills/process-wi/references/all-repos-orchestration.md)を参照。

CI失敗だけを1回確認してAWIへ投入する場合は、エージェントが次のコマンドを呼べる。対象を省略すると現在の作業ツリーを使う。

```bash
atk wi check-alerts --target-repo /home/user/repo
```

常駐監視と同じCI収集・重複除外・保存を使い、実行後に終了する。投入0件も成功なら終了コード0、全体または一部の取得失敗は投入件数と取得不能の理由を分けて示し、終了コード1となる。
process-loop、process-wiとDependabot監査は起動しない。呼び出す時機は各環境が選び、process-wiの1回の実行への定期組込みはこのコマンドが行わない。

### まとめ処理型

AWIが常時発生しないリポジトリでは、常駐実行を起動せず、数件のAWIがたまった時点で
`/agent-toolkit:single-lane-process`を手動で起動してまとめて処理する。
起動したセッションの中で、調査から実装、レビュー、公開までが進む。
計画を要するAWIが含まれる場合も、同じセッションの中で計画の作成から実装まで進む。
複数の対象リポジトリを扱う場合は、計画と実行レビューを対象worktreeごとに分ける。同じ対象worktreeの計画対象と直接実装対象は、同じ実行レビューでまとめて確認する。
Claude Codeではpush後のCI待機でも、独立した公開工程を先に開始し、対象commit・監視入力・baseline・保存出力と後続工程をparent-handoff.mdへ保存して同じ圧縮予約手順を使う。通知後も同じ監視対象と保存結果から続ける。

自律型とまとめ処理型は、AWIの発生頻度と常駐実行の要否で選ぶ。
常駐実行を動かし続けるだけのAWIが継続して発生する場合は自律型を選ぶ。
数日に数件の頻度でしか発生しない場合はまとめ処理型を選ぶ。

### 定期実行型

ログの点検のように、決まったスキルを数日に1回などの間隔で自律的に実行させたい場合は、cronなどの定期実行へ`atk run-skill`を登録する。
`atk run-skill`はWIキューを経由せずに、指定した対象リポジトリで指定したスキルを自律モードのセッションとして1回実行し、終了コードを返す。
モデルの選択、利用上限の解除待ち、同じスキルの多重起動の防止、時間上限（省略時6時間）とログの保存はコマンドが行う。
ClaudeとCodexのどちらも指定スキルの実行中に利用上限へ達した場合は解除後に同じ会話を再開し、待機時間を`--timeout`へ含めない。
Codexの候補判定では次の候補を先に試し、全候補が失敗して利用上限の失敗がある場合だけ、最も早い解除予定まで待って先頭から判定し直す。

cronへ登録する例を次に示す。cronは端末のシェルと異なる`PATH`で起動するため、`atk`・`claude`・`codex`の実行ファイルの場所を`PATH`へ含める。
標準出力を`/dev/null`へ捨てると、成功時は何も届かず、失敗時だけ標準エラーの失敗行がcronのメール通知で届く。

```bash
PATH=/home/user/.local/bin:/usr/local/bin:/usr/bin:/bin
0 3 */3 * * atk run-skill --target-repo /home/user/repo my-skill >/dev/null
```

毎回の結果は報告用UWIとして届く。
終了コード0は、子の正常終了と今回の報告用UWIのcommit、upstreamがある場合はpush到達まで成立したことを示す。
報告の保存未達は終了コード1で、診断、ログの所在と次の操作が標準エラーへ届く。124は時間上限の到達を示す。
保存未達の場合は、標準エラーが示すログを開き、次の手順で保存を完了する。

1. ログの「実行識別子」を控える。`atk wi show --all --type uwi --state all --source run-skill --target-repo /home/user/repo --skip-pull`で報告を表示する。`/home/user/repo`には実行時の対象リポジトリを指定する。frontmatterの`run_skill_run_id`が実行識別子と一致するUWIを探す。
2. 対象リポジトリのディレクトリで、実行した会話を再開する。Claude Codeではログの「Claudeのセッション識別子」を使って`AGENT_TOOLKIT_RUN_SKILL_ID=<実行識別子> claude --resume <セッション識別子>`を実行する。Codexではログの`thread.started`の`thread_id`を使って`AGENT_TOOLKIT_RUN_SKILL_ID=<実行識別子> codex resume <thread_id>`を実行する。山括弧の値はログの値に置き換える。
3. 再開した会話で「`agent-toolkit:completion-report`で今回の報告用UWIの保存を完了して」と依頼する。対応UWIが既にあればそのファイル名も伝え、「WI保存先のcommitと、upstreamがある場合のpushを完了して」と依頼する。保存完了の報告を確認したら、次回の定期実行を待つ。

ログに会話の識別子が無い場合や再開に失敗する場合は、対象リポジトリで新しい会話を開き、ログの所在・実行識別子・実行したスキル名を伝えて保存未達の調査と復旧を依頼する。
破壊的な操作など判断が要る対応は、実施せずに事前承認型UWIとして届く。
そのUWIへ回答すると、同じ対象リポジトリと同じスキルの次回の`atk run-skill`がその対応を実施してUWIを終端する。
未回答の間は、同じ対応のUWIを重ねて投入せず、報告用UWIの未達・持ち越しの欄へ実施しなかった対応と既存のUWIを示す。
これらのUWIは`atk wi process-loop`と`/agent-toolkit:single-lane-process`の処理対象にならない。
実行ごとのログは`atk config get state_dir`が示すディレクトリの`run-skill/`に保存され、30日を過ぎたログは次の実行で削除される。
手動起動で標準エラーが端末につながる場合は、開始時にログの絶対パスと子の識別子、実行中に発言とtool呼出の要約が標準エラーへ表示される。
標準出力だけをファイルへ向けた場合も表示する。cronなど端末の無い起動では追加表示しない。
ログには端末に表示した進捗と、子の標準出力・標準エラーの生診断を残す。思考内容や生JSON全体は端末に表示しない。
オプションと終了コードは`atk run-skill --help`で確認する。

### 登録方法

登録方法は要求が既に確定しているか、対話で確定する必要があるか、別環境の項目をまとめて移行・復元するかで選ぶ。

| 登録方法 | 選ぶ場面 |
| --- | --- |
| `atk wi add` | 依頼内容が既に固まっており、本文をそのまま登録したい |
| `atk wi add --batch` | 別環境の`atk wi show --all`の出力を複数件まとめて移行・復元したい |
| `/agent-toolkit:add-awi-by-user` | 依頼内容を対話で確定してから登録したい |

一括での移行・復元は`atk serve`の新規追加ダイアログでも種別「一括登録（show形式）」から実行できる。
種別を`awi`または`uwi`のまま`atk wi show`の出力を送った場合も、本文がshow形式の構造を持てば単件として保存せず一括登録として取り込み、その旨を通知に示す。
show形式の構造とは、直後にfrontmatterが続く`### <ファイル名>.md`の行を持ち、その行より前が空行と`# awi`・`# uwi`・`## target_repo: ...`の見出しだけで構成される本文を指す。
このときtarget-repo欄とUWI用の入力欄の値は使わない。

add-awi-by-userでは、件数や原因分析の有無によらず、メインが調査・方針の確定・起草・検収・保存まで担当するのを原則とする。
本来の作業に付随する別件の投入など、文脈を分ける意味がある場合はWI投入担当へ委譲する。独立した投入前レビューの要否は要求の性質から選び、基準付きの大量走査は軽量な読取探索を使える。
WI投入担当は`execute_model`を使う。
委譲の粒度は起動・自動配送・明示読取・親の検収・統合と再試行の総費用で選ぶ。同じ要求の取得済み証拠を再利用し、独立要求と独立文脈レビューの文脈、単一writerは保つ。

登録したAWIの計画、実装と実行レビューはエージェントが進める。登録者が行う操作は、UWIへの回答と、次に述べる登録済み項目の修正である。
実行レビューの役割と証拠の扱いの設計は[design-review-evidence.md](../development/design-review-evidence.md)、実行時の手順は`agent-toolkit/share/exec-review.subagent.md`にある。

登録済みの未終端WIを直したい場合や、既存WIの前提へ懸念がある場合は、エージェントが対話や調査より先にその項目を`atk wi hold`で自動処理から外し、更新した本文を確かめてから`atk wi unhold`で戻す。処理中の項目は保留せず、変更内容を新しい項目として登録する。

`atk wi reject`はprocess-loopが要求の全てを不採用と確定した場合だけに使用する。
採用内容を統合した元項目または別リポジトリへ移管した元項目は、統合先または移管先をnoteへ記録して`atk wi rm --force`で除去する。
技術的な失敗、入力不足および外部条件待ちは、不採用にせずactive項目として保持する。ユーザーの回答を待つ項目は保留し（処理中の項目は`atk wi hold --state=processing <ファイル名>`、それ以外は`atk wi hold <ファイル名>`）、回答後に`atk wi unhold`で自動処理へ戻す。

### atk serveの3画面

WI一覧の「全てコピー」は、現在のフィルターに一致する全ページの項目をコピーする。
1項目1行で、個別コピーと同じファイル名と要約を取得できる。
取得中、0件とコピーの失敗は画面に表示される。
詳細の「重要設定」「重要解除」で重要フラグを切り替えると、一覧の「重要」バッジへ反映される。
一覧の表示対象は重要・非重要で限定できる。
通常の自動処理の順序は重要フラグで変わらず、重要項目だけの処理は対象として明示した場合に行われる。

画面を使うには、ターミナルで`atk serve`を実行してサーバーを起動する。起動ログの`atk serveを http://<ホスト>:<ポート>/ で配信します`に表示されたURLをブラウザーで開く。待受のホストとポートを指定しない場合は`127.0.0.1`のポート28766（Windowsでは28876）で待ち受ける。変更には`--host`・`--port`（例: `atk serve --port=28767`）、環境変数`AGENT_TOOLKIT_SERVE_HOST`・`AGENT_TOOLKIT_SERVE_PORT`、設定ファイル`serve.toml`を使い、この順に優先する。前景で起動した`atk serve`を終了するには、そのターミナルで`Ctrl+C`を押す。

待受の設定と画面の参照元の設定は、どちらも同じ設定ファイル`serve.toml`に書く。
環境変数`AGENT_TOOLKIT_SERVE_CONFIG`にファイルのパスを指定した場合は、そのファイルを読む。
指定しない場合は、OSごとに次の場所のファイルを読み、ファイルが無ければ設定ファイルなしで起動する。

- Linux: `~/.config/agent-toolkit/serve.toml`。環境変数`XDG_CONFIG_HOME`に絶対パスを設定した場合は`$XDG_CONFIG_HOME/agent-toolkit/serve.toml`
- macOS: `~/Library/Application Support/agent-toolkit/serve.toml`。環境変数`XDG_CONFIG_HOME`に絶対パスを設定した場合は`$XDG_CONFIG_HOME/agent-toolkit/serve.toml`
- Windows: `%LOCALAPPDATA%\agent-toolkit\serve.toml`

設定例を次に示す。`host`と参照元のパスは文字列、`port`は整数、`remote_hosts`はSSH接続先名の文字列配列とする。
各キーは省略でき、省略した参照元は後述の場所を使う。パスとSSH接続先名は利用する環境へ置き換える。

```toml
host = "127.0.0.1"
port = 28767

[plans]
root = "/home/user/private-notes/plans"
remote_hosts = ["remote-host"]

[sessions]
claude_home = "/home/user/.claude"
codex_home = "/home/user/.codex"
remote_hosts = ["remote-host"]
```

`atk serve`は同じナビゲーションから「WI」「計画ファイル」「セッション」の3画面を提供する。WI画面はキューの登録・編集・状態遷移を扱い、計画ファイル画面はローカルと設定済みリモートホストの計画ファイルを一覧・全文検索してMarkdownを表示する。セッション画面はClaude CodeとCodexの保存済みセッションを作業ディレクトリと開始時刻で一覧し、最初のユーザー発話を検索対象に使う。右ペインには発話・思考・ツール呼び出しを時系列に表示する。アシスタントの発言は見出し・箇条書き・表・コードをMarkdownとして整形して表示し、それ以外の本文は記号を残した等幅で表示する。実行環境が挿入した本文は「自動挿入」として折りたたみ、一覧の検索対象となる最初の発話からも除く。計画ファイルとセッションの参照元は、前述の設定ファイル`serve.toml`の`[plans]`・`[sessions]`で指定する。未指定の場合、計画ファイル画面はprivate-notesの`plans`と`~/.claude/plans`の両方を参照し、セッション画面は`~/.claude`と、`CODEX_HOME`が空のときは`~/.codex`を参照する。

WI画面で状態が`needs-verify`と表示される項目は、反映後の観測だけが残っているため、観測できる時刻まで`inbox`へ戻されたAWIである。保存状態は`inbox`のままで、状態フィルターの`inbox`にも含まれ、次の`agent-toolkit:process-wi`が観測から再開する。

`cooldown`は冷却期限までは自動処理で次に選ばれる対象にならないinboxのAWIを示す。バッジはinboxと同じ青色で、マウスを乗せるとJSTの解除日時を確認できる。needs-verifyにも当たる項目ではcooldownを優先し、期限を迎えるとneeds-verifyへ、通常の項目はinboxへ戻る。画面を開いたままでも表示は更新される。保存状態はinboxのままで、状態フィルター・件数・操作の可否も保存状態に従う。holdなどinbox以外の項目は、冷却期限があっても元の状態を表示する。

`answered`は回答済みのinboxのUWIを示す。回答は次の`agent-toolkit:process-wi`が元の作業へ反映する。バッジはinboxと同じ青色で、保存状態はinboxのままである。状態フィルター・件数・操作の可否も保存状態に従う。未回答のUWIと、inbox以外の状態の回答済みUWIは元の状態を表示する。

CLIでは`atk wi add --cooldown-until '2026-10-11T12:00:00+09:00' --body-file <本文ファイル>`でAWIの初回保存から期限を設定できる。複数本文には同じ期限を適用する。空・不正日時・タイムゾーンなし、UWI・batchとの併用と通常本文のfrontmatterによる期限指定は拒否する。`inbox`または`hold`のAWIの期限は`atk wi edit <ファイル名> --cooldown-until <日時>`で変更し、この編集だけは空文字列で解除できる。`processing`では設定と解除の双方を拒否する。

セッション画面を開いたままでも、ローカルと設定済みリモートホストで新しいセッションの記録が保存されると一覧へ自動で加わり、表示中の記録へ追記されたイベントも再読み込みせずに右ペインへ現れる。右ペインは記録の先頭100件を描画し、末尾までスクロールすると続きを自動で描画して、記録の最後のイベントまで読み進められる。右ペインの更新では、開いたイベント、描画した件数およびスクロール位置を保つ。末尾を表示している間は追記に合わせて末尾へ進み、それより上を読んでいる間は位置を保ち、末尾まで読み進めた時点で追記したイベントが現れる。
右ペインの見出しにはAPI料金換算の推定費用（USD）を表示し、本文に料金表の基準日とトークン数を残す。
親を開くと子孫込み、子や委譲先を開くとその記録自身の額になる。単価不明や読み取れない記録がある場合は、既知分と不明の表示を区別する。
親の費用は一覧で子孫と確定した記録の更新通知でも更新する。リモートの費用算出にはリモートホスト側のdotfiles更新が必要で、未対応のヘルパーからは費用を取得できない旨を示す。
一覧への掲載は、記録にユーザーのロールを持つ行が1件でもあるかで決める。ユーザーのロールを持つ行が無い記録（Codexを起動しただけで入力しなかった記録など）は一覧に出ず、その行が保存されると一覧へ加わる。実行環境の挿入本文だけを持つ記録は一覧に残るが、最初の発話の検索値は空になる。リモートホストの自動更新には、リモートホスト側のdotfilesの更新が必要である。

### エージェント由来AWIへのユーザーコメント

`atk serve`の詳細画面では、inboxまたはholdにあるエージェント由来のawiにユーザーコメントの編集操作が表示される。エージェント由来とは、frontmatterの`source`が設定されており、その値が`human`でないものを指す。
コメントがない場合は末尾の`## ユーザーコメント`節へ追記し、既存のコメントを保存すると同じ節だけを置換する。
コメント欄を空にして保存すると、`## ユーザーコメント`節を削除する。節がない項目で空のまま保存した場合は本文を変更しない。
コードフェンス外のH2を含めるコメント、同名節が複数ある本文、末尾以外に予約節がある本文は保存できない。
processing、UWI、終端項目および人間由来の項目では操作を使用できない。

ユーザーコメントに操作、対象および範囲を明記すると、その範囲の外部操作に対する承認として処理される。
一般的な「進めて」だけでは外部操作の範囲が確定しないため、実行してよい操作を具体的に記載する。

### UWIへの回答と状態確認

未回答UWIは`atk wi answer`で順に確認して回答できる。ファイル名と回答を指定する場合は次の形式を使う。

```bash
atk wi answer <UWIファイル名> '<回答本文>'
```

回答後はUWIが先に終端し、そのUWIを待っていたAWIが次回の処理対象へ戻る。1つのAWIを複数のUWIが待つ場合は、全てのUWIへ回答した後の処理で戻る。
処理中の`agent-toolkit:process-wi`のセッションへ「UWI回答した」のように回答を告げると、そのUWIと待っていたAWIを同じ実行で処理する。告げていない回答は次回の処理で取り込まれる。
現在の項目は`atk wi list --status=active`で確認できる。
自動処理へ渡せる状態だけを確認する場合は`--status=processable`、未回答UWIだけを確認する場合は`--type=uwi --answered=no`を指定する。
対象リポジトリはカレントディレクトリが属するリポジトリとなる。全てのリポジトリの項目を確認する場合は`--target-repo=all`を指定する。

## atkコマンドのPATH設定

`agent-toolkit`プラグインは`atk`ラッパースクリプトを`agent-toolkit/scripts/atk`に配置する。
Claude Codeがマーケットプレイス経由でインストールした実体は
`~/.claude/plugins/cache/<marketplace-name>/agent-toolkit/<version>/scripts/atk`にある。
バージョン部は更新ごとに変わる。追随処理は`install-claude.sh`（Linux/macOS）または`install-claude.ps1`（Windows）で自動化する。

- Linux/macOS: `~/.local/bin/atk`に最新バージョンを動的解決するラッパーを配置する
- Windows: `~/.local/bin/atk.cmd`に同等のバッチラッパーを配置する
- いずれも`~/.local/bin`がPATHに含まれていない環境では警告を表示する

dotfilesユーザーは`chezmoi apply`で`~/dotfiles/agent-toolkit/scripts`がPATHへ自動配置されるため、上記スクリプトの実行は不要。

Claude CodeのSessionStartは、`CLAUDE_ENV_FILE`が提供される場合に、CLIのPATHとデータ領域を後続のBashへ渡す。別hookの設定と既存PATHは保持する。このファイルが提供されない場合も、開始時のコンテキストにCLIの絶対パスを表示する。WindowsのPowerShellでは、案内された`.cmd`の絶対パスを`& '絶対パス'`の形で呼び出す。MCPと`agents_server`の委譲先には起動設定で同じ配布物のCLIとデータ領域を渡す。

CLI移設前に起動した`update-dotfiles`はpullと未コミット内容の復元後、旧`agent-toolkit/bin/atk`の不在により退避の回収で終了1となる場合がある。「復元は成功しましたが退避の回収は未完了です」と表示された場合は、更新したリポジトリで作業内容とindexが復元済みであることを保存refの内容と比べて確かめる。その実行が表示した完全なrefだけを`./agent-toolkit/scripts/atk worktree-stash drop <ref>`で回収し、`./bin/update-dotfiles`を再実行する。Windowsではそれぞれ`agent-toolkit\scripts\atk.cmd`と`bin\update-dotfiles.cmd`を使う。復元を確認できない場合はrefを保持して原因を調べる。これは開始済みの旧プロセスからの移行操作であり、再実行後の終了状態まで確認する。

## 構成と機能

### 定型の委譲を追加する場合

agent-toolkitの定型チェックは、`share/<役割名>.subagent.md`の先頭のYAML frontmatterへmode・工程別model_type・入力・継続・書込を宣言し、本文へ観点・手順・返却形式を書く。
読取専用の軽量チェックは`mode: explore`と`model_type: chores_fast`で定義でき、役割名別のPython対応表への登録を要しない。
起動と受領に必要な入力の決め方・検収は呼び元の規範が持つ。呼び元が1カ所ならその工程へ統合し、複数から共有する場合は`<役割名>.parent.md`を保持できる。
たとえば`quick-check`を定義した後は、MCPの`start`へ次の形で渡す。

```json
{"cwd": "/repo", "subagent_md_path": "quick-check", "extra_params": {"対象": "/repo/report.md"}}
```

静的な設定の整合性は配布対象全件のテストで保証する。起動時は設定を読み、実行に必要な条件と呼出し入力を確かめる。モデルの明示上書きと既存sessionの継続を保つ。
委譲元は起動準備のために定義本文を取得せず、呼び元の契約から値を決める。
設定項目と作成基準は[サブエージェントの記述基準](../../agent-toolkit/skills/writing-standards/references/sub-agents.md#agents_serverのfrontmatter)を参照する。

### 常時有効な仕組み

ルールファイル（`~/.claude/rules/agent-toolkit/`配下）は自動ロードされる。
`01-agent.md`が基本原則・運用方針・言語表現・検証とコミットの流れを提供する。
ルールファイルには、メインエージェント、サブエージェントおよび委譲先の全てへ適用する条文だけを置く。
メインエージェントだけに適用する条文は`share/rules-main.md`と`share/rules-main.claude-code.md`、サブエージェントと委譲先だけに適用する条文は`share/rules-subagent.md`に置き、フックと`agents_server`が起動時に文脈へ追加する。
文体の核はJIS規格・公的な標準仕様書のスタイルとし、
対話型UI向けの敬体はNHKの案内放送原稿のスタイルを例外として割り当てる。

agent-toolkitプラグインはpyfltrのMCPサーバーを同梱する。
プラグインを導入するとClaude CodeとCodexの双方で自動的に利用可能になり、
lintやテストの実行・横断検索・横断置換・実行履歴の参照をシェルを経由せずに呼び出せる。
サーバーは`uvx`でpyfltrを取得して起動するため、pyfltr自体の事前インストールは要らない。

agent-toolkitは以下のフックを常時有効化する。
会話圧縮後はSessionStart hookが全ユーザー発話を`compaction-user-events.jsonl`へ保存し、原文と出所の`record`・`line`から要求と回答を復元できるよう案内する。外部のcompact-plus pluginは不要となり、dotfiles更新時に導入済みのものを除去する。
識別子はイベント名と処理名の組で示し、`plugin`はagent-toolkitプラグインの配布分、
`個人設定`はdotfilesユーザーの`~/.claude/settings.json`へ配布される分を指す。
Codex欄の「対応」「部分対応」「非対応」は、Codex 0.154.0の実機検証で確認した範囲を示す。

| フック識別子 | 観測できる働き | Claude対応状況 | Codex対応状況 |
| --- | --- | --- | --- |
| plugin `PreToolUse/pretooluse` | 元へ戻せない結果を生む操作と、入力から機械的に判定できる明らかな誤り（編集内容の文字化け、lockfileの直接編集、atkの出力のパイプとリダイレクトなど）を実行前に警告または遮断する。commit本文を確定でき、2行目が空行でない場合は遮断し、件名の直後へ空行を加える操作を案内する。commitの帰属行（`Co-Authored-By`）はエージェントが書き、このフックが実行中のモデルと推論量に一致するかを確かめる | 対応 | 部分対応 |
| plugin `PostToolUse/posttooluse` | 成功したツール実行の結果を記録し、計画ファイルの書き込み後に計画構造の自動チェックを案内する。メインの成功した前景Bashのcommit・push後にはcompletion-reportの起動条件を通知する。そのセッションが投入したUWIへの回答を通知し、上限を超えて退避されたシェル出力を保存先と次の操作を示す本文へ置き換える | 対応 | 部分対応 |
| plugin `SessionStart/rules_context` | セッションの開始、再開、`/clear`および会話圧縮の後に、メインエージェントだけに適用する条文を文脈へ追加する | 対応 | 対応 |
| plugin `SubagentStart/rules_context` | サブエージェントの起動時に、サブエージェントと委譲先だけに適用する条文を文脈へ追加する | 対応 | 対応 |
| plugin `SubagentStop/subagent_stop_advisor` | 空の完了報告での終了をブロックする | 対応 | 対応 |
| plugin `SessionEnd/session_end_cleanup` | 期限を過ぎたセッション状態を回収する | 対応 | 対応 |
| plugin `Stop/stop` | Claudeでは未観測の委譲・背景処理や終了工程の記録を確認し、completion-reportの起動忘れを非遮断で通知する。報告本文の意味や固定句は判定しない。Codexでは説明言語だけを判定する | 対応 | 部分対応 |
| plugin `UserPromptSubmit/user_prompt_submit` | 発話の内容を現物で確かめる手順や、発話に応じて適用する規範の所在を示す注記を返す | 対応 | 対応 |
| plugin `PermissionRequest/permissionrequest_codex` | 厳密な入力一致と所有者確認が成立したmanaged-tempのcleanupだけを限定的に許可する | 非対応 | 対応 |
| plugin `PermissionRequest/permissionrequest` | 全ツールの確認ダイアログを自動許可し、許可した要求をログへ記録する | 対応 | 非対応 |
| plugin `PostToolUseFailure/posttooluse` | Bashの背景実行が失敗した場合も、そのタスクをバックグラウンドタスクの記録へ残す | 対応 | 非対応 |
| plugin `PermissionDenied/posttooluse` | 許可拒否時に状態を変更せず終了する | 対応 | 非対応 |
| plugin `StopFailure/stopfailure_notifier` | APIエラーでターンが終わったとき、発火内容とセッション記録の要約をログへ残す | 対応 | 非対応 |
| 個人設定 `PreToolUse/pretooluse` | dotfilesの配布元ファイルと個人の命名規約に基づき、編集前にチェックする | 対応 | 非対応 |

各フックの判定条件と、Codexでの対応範囲の詳細は[design-hooks.md](../development/design-hooks.md)「フックごとの処理とCodexの対応範囲」を参照。

Claude Codeの背景処理が終わり、完了通知がまだ届いていない場合、ほかに作業を続ける必要があるとしてターンの終了を止める判定が無ければ現在のターンを終える。
通知を受けた次のターンで保存出力と終了状態を受け取り、作業を続ける。ターンの終了を止める判定が残る場合は、その理由と結果受領の案内を保持する。
同じ結果を得るための待機コマンドの再発行は求めない。

対話で直接作業を依頼したClaude Codeの主担当が自ら加えた変更をcommitせずに残し、同じ作業をどこまで公開するか決めず、結果の報告など作業を終える手順にも進んでいない場合は、その範囲の確定か終了手順への移行を警告する。
指定・回答・適用済みの許可から公開範囲が確定した作業、commitしないと決めた作業、背景処理の結果を正しく待っている場合は、この警告の対象外である。
commitされていないことだけを理由にcommitを求めない。

Claude CodeとCodexでは、`git rev-parse --short HEAD HEAD~1`のように、`--short`や`--verify`の後へリビジョンを複数並べると、PreToolUseが実行前に遮断する。通知に従い、同じオプションを付けたままリビジョンを1つずつ実行する。

PreToolUseは、未取得のdelegationを委譲操作の前に、未取得のwriting-standardsを本文の作成・更新の前に全文で取得するよう案内する。本文編集は`Write`・`Edit`・`MultiEdit`と`apply_patch`を言語やファイル名によらず対象にする。Bashでは、静的に確定した書込先へのheredoc・here-string、インプレース置換、Pythonの本文書込も対象にする。managed-tempにはWIや投稿文の下書きも置くため、通常本文と同じ基準を使う。コマンドの生成出力の保存と複製は通常本文の作成から除く。親子の取得済み記録は分け、Codexにも同じ取得契約を適用する。
commit・push後のPostToolUseの案内は、成果報告へ進むメインへcompletion-reportの適用条件を示す。Claude CodeではSkillの起動、Codexでは通知されたSKILL.mdの読取へ進む。失敗・背景・dry-run・help・委譲先は対象外で、後続の該当操作にも案内する。

plugin `PreToolUse/pretooluse`がatkの呼び出しを遮断するのは、静的に分かるatkの実行位置と出力の接続先に限る。
検索語やheredoc本文に現れたatkと、ホストが管理する背景実行は遮断しない。ただし区切り語を引用しないheredocの本文にあるコマンド置換は、`bash`が実行するため置換の中身によらず遮断する。
この限定と、一般の検証コマンドの出力切り詰めチェックを復元しなかった理由は、[フックの責務境界](../development/design-hooks.md#フックの責務境界)を参照。
遮断されたときは、次の操作で同じ目的を達成できる。

- `atk agents wait`はシェルの`&`と標準出力の破棄を外して単独で発行する。
  Claude Codeで背景で待つ場合はBashの`run_in_background`を使い、返されたtask識別子で結果を受領する。
- atkの出力はパイプにもリダイレクトにもつながず、単独で呼び出す。出力量はサブコマンドの対象限定で減らし、生成側が返す標準出力・標準エラーの保存先から全量を読む。エージェント環境で量によらず保存されるのは、`atk agents wait`の回収結果と複数件の`atk wi show`、`atk run-script session-review-evidence`の`--user-events`であり、短い単発の照会は直接表示される。保存後の標準出力には、`atk agents wait`では通知・終端の内訳と直接表示できる量の本文が、`--user-events`では発話ごとの記録位置と本文の冒頭を示す`発話:`行が続く。表記診断の詳細は`標準エラー保存先:`が示す別のファイルに保持される。人の端末は直接表示と各ストリームのリダイレクトを使える。
  保存した本文の選別は別の呼び出しで行う。

Codexの`SessionStart`は`startup`・`resume`・`clear`・`compact`の全てで条文を追加し、`compact`では`QUALITY_CHECKPOINT_NOTICE`も追加する。
pluginをインストールまたは更新した後は、Codexの`/hooks`で、導入済みagent-toolkit pluginをsourceとする9イベントの定義を確認して信頼する。
登録集合は`SessionStart`、`SubagentStart`、`PreToolUse`、`PostToolUse`、`PermissionRequest`、`UserPromptSubmit`、`Stop`、`SubagentStop`、`SessionEnd`である。
登録が0件の場合は信頼操作へ進まず、`agent-toolkit-codex/`のmanifest選択を確認する。信頼前は登録済みHookがスキップされるため、条文の追加と圧縮後通知は発火しない。
信頼後に新しいセッションを開始し、最初の応答の前に自動生成通知が現れることを確認する。

### 一時成果物の配置と回収

AWIとUWIは`private-notes/yyyy/MM/<filename>`へ保存され、状態変更後も同じパスと画面URLで参照できる。
保存状態はfrontmatterの`state`、重要フラグは`important: true`に記録される。

セッション開始時に通知されるmanaged-tempは、一時成果物を置く管理領域である。
検証記録、引き継ぎ記録と再開入力はこの領域へ保存され、最終更新から7日を超えた後の`atk`実行時に回収される。
turn終了時にも残るため、同じ会話の再開や委譲先の結果確認に使える。

抽出した配布物、cloneとGit fixtureなどの再生成可能な作業用複製は、保存証拠と分けて置く。
作成した工程が全消費工程と外部プロセスの終了後に回収し、後続turnで読む証拠と再開入力を残す。
領域の確認には`atk managed-temp list`を使える。
配置と回収の手順は`agent-toolkit:managed-temp`、寿命を分ける理由は[design-storage.md](../development/design-storage.md#managed-temp)を参照。

版付きプラグインから起動する`agents_server`は同じ配布元・版の保持実体を複数プロセスで共有する。
使用中の実体を保護するため、配布元の更新後も起動時のスキルと内部資源を参照できる。

### `~/.claude/plans`の計画ファイルと実行レビュー後の`private-notes/plans/`への保存

メインが`agent-toolkit:plan-mode`を起動した場合、新規計画は内部の保存処理を使って`~/.claude/plans`へ1ファイルとして保存される。

```text
~/.claude/plans/dd-HHmm_<日本語の簡潔な名詞>.md
```

`agent-toolkit:process-wi`のレーンでは`dd-HHmm_process-wi_レーンNN.md`（`NN`は2桁）とする。
`agent-toolkit:single-lane-process`では`dd-HHmm_single-lane-process.md`とし、同じ名前が既にある場合だけ`-<小文字16進数4桁>`が付く。

viewerのパスコピーは選択中の実体を指すため、作業中は`~/.claude/plans/...`、保存後はprivate-notes側の可搬表記を返す。
実装後レビューが収束した後だけ、`~/.claude/plans`直下のファイル名またはそのroot配下の絶対パスを指定して計画バンドルを保存する。
現行名は`dd-HHmm_<名称>.md`で、衝突時は名称の後に`-<小文字16進数4桁>`が付く。
保存済み計画には`private-notes/plans/`相対パスと、そのroot配下の絶対パスを指定できる。

```bash
atk plans commit dd-HHmm_<名詞>.md
```

`atk plans commit`は同じstemの計画、バグ調査ファイルおよびレビュー指摘管理表を、計画ファイルの作成日に対応する`private-notes/plans/yyyy/MM/`へ移動し、対象限定commit・pushの成功後に作業側を回収する。失敗時は作業側を保持する。
pushを行わずローカルcommitまでで止める場合は`--skip-push`を指定する。この場合もローカルcommitの成功後に作業側を回収する。
計画作成基準は`agent-toolkit:plan-mode`の`plan-file-standards.md`、可搬参照の書式は同スキルの`plan-file-storage.md`が定める。

計画ファイルの固定見出し、進捗ログおよびレビュー指摘管理表の規則は`agent-toolkit:plan-mode`が定め、計画ファイルの書き込み時に上表の`PreToolUse`・`PostToolUse`が構造をチェックする。

### オンデマンドのスキル

該当作業に着手したときエージェントが起動する。Claude Codeで`/`を付けて手動起動できるのは、`user-invocable: false`を持たないスキルだけである。Codexの`$`による手動起動はこの設定の対象外とする。

- `agent-basics:writing-standards`: ドキュメントとコード内コメント、コードとテストコード、エージェント向け文書の品質基準。成果物の種別ごとに`references/`配下の資料を読み分ける
- `agent-toolkit:refine-prompt`: プロンプトの指摘を独立した実行者から集め、共通の文書基準で改善案を組み立てる
- `agent-toolkit:commit`: commitを作成・取り込む操作（通常commit、amend、fixup、cherry-pick、merge、rebase、revertなど）の前に起動する手順とConventional Commits規約
- `agent-toolkit:bugfix`: 問題対処の原因分析と、類似見直し・再発防止の唯一の判断基準。成果物の品質に影響する問題では、費用や所要時間を理由に、同じ原因の問題を探して直す範囲を狭めず、同じ原因の問題を今後起こさない対策を省かない。探す範囲には同じ原因の過去の作業と他のリポジトリも含める
- `agent-toolkit:delegation`: 起動方式の選択、継続、停滞検知または複数主体調整が必要な高度な委譲の手順。
  自動的に適用せず、対象工程が高度な委譲の条件を持つ場合にだけ読み込む
- `agent-toolkit:plan-mode`: 計画ファイル作成と、実装後の実行レビューを含む実行引き継ぎ
  - 計画確定時は計画構造の自動チェックで固定H2と表、計画メタ情報、見出し階層、参照実在を確認する
  - ユーザー発言は`## 変更履歴`、実装時の進捗は`## 進捗ログ`へ記録する
  - 実行レビューでは計画ファイルと同じディレクトリの`<計画stem>.exec-review.tsv`をレビュー担当が作成・更新し、全ラウンドで同じ表を使う
  - バグ対応計画は計画メタ情報の固定記法から判定し、関連WIの`## 原因分析`に記録した原因と調査を参照する。入力WIが無い場合とWIの原因分析を訂正・追加する場合だけ、計画ファイル（バグ）の原因分析表と固定の調査表で両要因を分析し、原因起点の類似見直し、対策・横展開・再発防止を記録する
  - 変更履歴は見出しごとの記録でレビュー指摘をラウンド単位に集約し、指摘原文・個別採否・対応内容はレビュー指摘管理表へ記録し、変更履歴には書かない
  - 進捗ログは日時・完了した工程・結果の3列表で実行工程の作業状況を追跡し、異常終了からの再開に使う
- `agent-toolkit:review-standards`: レビュー担当とレビューイーの判断基準。
  レビュー担当にはコードレビュー・ドキュメントレビューの実施基準を、レビュー指摘、改善提案、ユーザーの割り込み・是正要求と想定外の発見を受領したレビューイーには修正要否の立証、安全な修正、自己点検と公開可能性の検証基準を与える
- `agent-toolkit:wi-standards`: AWIとUWIの本文、由来、状態、承認および投入の共通規範
- `agent-toolkit:workflow-overview`: 対話型、自律型、まとめ処理型をまたぐWI運用の全体像と、運用変更時に確認する主体・操作・適用先
- `agent-toolkit:add-awi-by-user`: ユーザーの要件を対話で確定し、AWIまたはUWIを手動投入する
- `agent-toolkit:single-lane-process`: AWIをレーンへ分けずに、1回の起動で対応する作業ツリーへ実装して終端する。計画を要する項目は同じセッションの中で計画の作成から実装まで進め、複数リポジトリでは計画と実行レビューを対象worktreeごとに分ける
- `agent-toolkit:process-wi`: 選定工程（選定とレーン分け）、レーン工程（並列レーン実行）、公開工程（全レーン後のpush・CI・終了）の3段階でAWIを処理する。広域AWIが独立項目を1つのレーンへ連結する場合は完了見込みを比較し、独立レーンを先行して統合した後、広域AWIの後段レーンを現行HEADから開始する。同じ段階で同じ定義を変更するレーンは並行させない。
  処理中にユーザーが追加を明示したAWIと、処理中のセッションへ回答を告げたUWIは同じ実行へ加える。初期選定中なら候補集合へ合流する。選定後なら追加選定を検収して元の選定結果へ保存する。指示の無い新着はprocess-wiの次の実行で扱う。
  計画を要する通常レーンは1つの計画を使い、レーン担当が計画・実装・変更範囲の検証を続けて行う。初期選定が1レーンなら同じ担当が独立した実行レビューを起動して収束まで調整し、複数レーンではメインへ実行レビューを要求する。
  計画を省くレーンはWIの要求と完成条件を基準に実装・レビューする。計画がある場合は自動チェックでWI集合の一致と人間由来行の根拠欄を確認する。複数レーンでは、人間由来の縮小、エージェント向け文書の編集、明示禁止条件のいずれかがある場合だけメインの判断を待つ。初期選定が1レーンの場合は単一担当の実行契約に従い、ユーザーだけの判断が残る場合にメインへ通知して回答を待つ。
  実装不要またはholdの項目は計画やworktreeを作成せず終端する。要求の不採用と既存の変更による充足は計画工程で確定する。
  前回の実行で中断したAWIは既存の計画の続きから再開し、新しく選ばれたAWIとは別に扱う。終端しなかったAWIの計画も実行の終わりに保存され、次の実行で作業場所へ戻される。
  各レーンはffマージ直後に`adopt`と後始末を完了し、固有指示で延期した項目だけを全レーン後の終端工程で処理する。反映後の新プロセスでしか完成条件を観測できない場合は、観測可能なら終端し、できなければ実装済み計画の再開位置を残して次の実行で観測する
- `agent-toolkit:pytilpack-usage`: pytilpackのモジュール構成とAPI参照のリファレンス
- `agent-toolkit:gitlab-ci-usage`: `.gitlab-ci.yml`編集時のキーワード仕様・典型パターンのリファレンス
- `atk agents-exit-session`: ユーザー指示時または自律モードのスキル完遂時に、現在のClaude CodeまたはCodexの対話セッションへ終了を要求するCLI。管理設定の`CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1`が有効でagent-toolkitのFunction hooks moduleを読み込んだClaude Codeでは、ターンの完了後に`/exit`を実行してセッション記録の末尾まで残す。moduleが読み込まれていないClaude CodeとCodexでは従来のプロセス停止方式を使う。
  （本体を一意に識別できない実行環境では停止せず、終了理由と対話CLIの終了案内を最終応答としてターンを完了する）
  `all-repos`のリポジトリ担当は実行せず、常駐側への最終返却でターンを終える。常駐側が返却を受け取る前に担当を止めないためである
- `compact_conversation`: Function hooks moduleを読み込んだClaude Codeのメインが会話圧縮を予約するツール。完全名は`mcp__agent-toolkit__compact_conversation`で、引数は取らず追加指示なしでcompactを予約する。必要な再開状態はセッションのmanaged-temp直下のparent-handoff.mdへ保存し、圧縮後の領域通知からその記録を読む。対話UIと非対話の`claude -p`に対応し、Codexと委譲先では使わない。受付応答は圧縮完了を示さない。予約後は現在のターンを終え、ホストの圧縮成功表示を確認する。未完了の予約を重ねても実行は増えず、成功・失敗後は再予約できる。失敗は同じ会話へ原因と再予約可能である旨が通知される。バックグラウンドタスクを待つ場合は予約後に背景待機を開始してターンを終え、通知後に同じ担当と保存結果から継続する
- `send_to_user`: 管理設定の`CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1`が有効でagent-toolkitのFunction hooks moduleを読み込んだClaude Codeで、メインが使えるツール。途中報告と最後の回答を運び、画面へMarkdownとして表示する。完全名は`mcp__agent-toolkit__send_to_user`で、初回から読み込む定義を登録する。遅延一覧にだけ現れる場合は`ToolSearch`の`select:mcp__agent-toolkit__send_to_user`で読み込む。ツール呼び出しより前の地の文は要約に置き換わることがあるため、このツールで送る。確認質問には既存の質問手段を使う。moduleを読み込まないClaude CodeとCodexでは、ターンを終える本文へ書く
- `agent-toolkit:completion-report`: メインの作業完了時に、成果と振り返り結果を固定形式で報告する。対策のAWIを投入する場合は`## 振り返り結果の予告`で予告し、投入後にAWI投入結果を報告する。投入が無い場合は振り返り結果を報告する。適用するプロジェクト規範が本スキルを不要と定める場合は、スキルを起動せず`references/termination-evidence.md`を読む。`atk run-script termination-evidence -- --decision-file <判断JSONの絶対パス>`へ`omit-completion`の根拠を記録する。省略は指定した作業だけへ作用し、別作業の起動不足と、process-loopの会話が終了時に`atk agents-exit-session`を実行する義務（`all-repos`のリポジトリ担当を除く）は保つ。ClaudeのStopフックは起動忘れを非遮断で通知し、CodexのStopではこの通知を扱わない。起動後の工程の完遂と報告の内容は本スキルの規範に従ってエージェントが確かめる。ユーザーの中止・置換の指示は、その指示が作用する作業だけを止める
- `agent-toolkit:export-session`: `atk agents logs`でClaude CodeとCodexの記録をmarkdownへ出力し、一括変換も行う
- `agent-toolkit:session-review`: セッションで交わされた会話の流れと問題候補を調べ、原因と恒久対策を確定して、対策を作業依頼（AWI）として投入する。手動または`agent-toolkit:completion-report`から起動し、メインが同じセッション内で分析する

## 更新と削除

ルールファイル・プラグインとも頻繁に更新されるため、定期的に最新化する。

「ツールキットのインストール」のワンライナーを再実行すると更新される。
dotfiles（chezmoi）管理下のマシンでは`chezmoi apply`を実行しても更新できる。
いずれの単体インストーラーも初回導入と更新の両方に使える。`agents_server`はClaude CodeまたはCodex pluginが
セッション単位でstdioプロセスとして所有するため、共有daemonの再起動は行わない。
Codex plugin本体の更新で既存セッションへ反映できない場合は、Codex pluginの案内に従い、
進行中の作業を回収してから新しいセッションを開始する。

アンインストール時は双方のプラグインを除去し、
`~/.claude/rules/agent-toolkit/`と生成された`atk`ラッパーを削除する。plugin更新だけでは旧Codex MCPを
自動削除しない。単体インストーラーまたは`chezmoi apply`後処理では完全一致した旧User scope定義だけを移行する。

自動登録に失敗した場合は、設定ファイルのJSON構造を修復してから次の診断・復旧コマンドを実行する。

```bash
claude mcp get codex
claude mcp remove --scope user codex
```

上記の削除は、`claude mcp get codex`で旧`codex mcp-server`定義を確認した場合だけ実行する。
custom定義を削除する場合はユーザーが内容を確認してから明示的に実行する。2時間のtimeoutは新しい設定へ引き継がない。

```bash
chezmoi apply
```

Codex単独セッションとdotfiles固有の配布内容については[Codex利用ガイド](codex-guide.md)を参照してください。

## 品質基準だけを導入する

`agent-basics`はコード・設計・テスト・レビュー・文章の品質スキルを提供する。単独導入と利用時の起動条件は[agent-basicsの導入案内](../../agent-basics/README.md)を参照する。agent-toolkitはWI・委譲・承認・完了報告を担当し、品質基準としてagent-basicsへ依存する。Claude Codeはその依存を自動導入し、Codexは2件を明示的に導入する。dotfilesの更新処理は両方を同期する。
