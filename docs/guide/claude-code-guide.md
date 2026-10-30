# agent-toolkit導入ガイド（Agent Plugins・Claude Code・Codex）

agent-toolkitはAgent Plugins、Claude Code、Codexで共有できるコーディングエージェント向けツールキットである。
Claude Codeは対話と作業全体の統括を担い、Codex CLIは`agents_server` MCP経由の調査・実装・レビューと、Codexセッションでのスキル実行を担う。
uvは配布スクリプトと`atk`コマンドの実行基盤である。

## コンセプト

1. 標準動作のカスタマイズ: 判断基準が曖昧な場面での事前相談、lint抑制時のユーザー確認、検証からコミットまでの流れの自動化など、コーディングエージェントの動作を変える
2. 品質水準の維持: 各言語のイディオム、セキュリティ、テスト方針などの作法とプロジェクト固有の制約を示し、既存コードの乱れにエージェントが引きずられないようにする
3. 知識の補完: Claude Codeの現行仕様や個人製作のツール（pytilpackなど）のように、LLMの学習データに含まれない情報をスキルで補う

agent-toolkitは、`~/.claude/rules/agent-toolkit/`へ配置して自動で読み込まれるルールファイルと、スキル・フック・MCPを含むプラグインで構成する。
両者は同時に導入する前提で作られている。
部分的に動作を変えたい場合は、`~/.claude/CLAUDE.md`、プロジェクトの`AGENTS.md`、プロンプトでの指示で上書きできる。

本書ではエージェントへ渡す作業の単位を2種類の略語で呼ぶ。
AWIはユーザーまたはエージェントが登録し、後でエージェントが自動処理する作業要求である。
UWIはユーザーの回答・判断・操作を待つ項目と、作業結果の報告である。
どちらもユーザーの手元のキュー（private-notesリポジトリ）で管理し、対象リポジトリには保存しない。

## 前提条件

単体インストーラーを実行する前に、次の3コマンドを公式手順で導入する。
インストーラーはCLI本体を導入しない。

- `claude`: [Claude Code](https://docs.anthropic.com/ja/docs/claude-code/overview)のCLI（2.1.163以上）
- `codex`: [Codex CLI](https://developers.openai.com/codex/cli/)本体
- `uv`: [uv](https://docs.astral.sh/uv/)による配布スクリプト実行環境

dotfilesユーザーでは、`chezmoi apply`後の処理がClaude Code（Anthropic公式ネイティブ版）とCodex CLIを導入・更新する。
WindowsでClaude CodeやCodexが実行中の場合は停止せず、更新を次回へ延期する。

## クイックスタート

### ツールキットのインストール

次のワンライナーを実行する。
`install-claude.sh`と`install-claude.ps1`は名前に反してClaude CodeとCodexの双方を設定する。

- Linux:

    ```bash
    curl -fsSL https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.sh | bash
    ```

- Windows:

    ```cmd
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.ps1 | iex"
    ```

ルールファイル、Claude CodeとCodexのagent-toolkitプラグイン、共有`agents_server` MCP、`atk`コマンドが設定される。
3コマンドのいずれかが見つからない場合は、何も書き込まずにエラー終了する。
再実行すると最新版へ更新される。
Codexのdaemon再起動を案内された場合の操作は[Codex利用ガイド](codex-guide.md#プラグイン更新の反映)を参照。

インストール後、Claude Codeでマーケットプレイスの自動更新を初回だけ手動で有効にする。

1. Claude Code内で`/plugin`を実行
2. `Marketplaces`タブで`ak110-dotfiles`を選択
3. `Enable auto-update`を選択

Agent Plugins互換クライアントでは、`agent-toolkit/`をプラグインのパッケージルートとして指定する。
導入操作は利用するクライアントの公式手順を参照。

## Claudeの各環境への導入

使う環境に応じて、次の導入方法を選ぶ。

| 環境 | 導入操作と使える機能 |
| --- | --- |
| claude.aiのChat・DesktopのChat | `Customize > Plugins`からマーケットプレイスを追加するか、プラグインのZIPをアップロードする。スキル内の品質基準を使える。CLI・hook・local MCPは使えない |
| DesktopのCowork | Coworkタブで同じ`Customize > Plugins`から追加する。hook・local MCPはホストの対応、uvとネットワーク、組織の許可がそろう場合に使える |
| DesktopのCode・ローカルClaude Code | 前述の単体インストーラーを実行し、`/plugin`で導入状態を確認する。CLI・hook・MCPを使える |
| Claude Codeクラウド | 後述「Claude Codeクラウドの環境設定」の2欄を保存する |

マーケットプレイスを追加する場合は、`Customize > Plugins > Add > Add marketplace`で`Add from a repository`を選び、`ak110/dotfiles`を入力して`agent-toolkit`を`Add`する。
同期範囲と組織による制限は[Claudeのプラグイン利用手順](https://support.claude.com/en/articles/13837440-use-plugins-in-claude)を参照。

### アップロード用ZIP

dotfilesのリポジトリで、公開済みcommitのプラグインだけをZIPへ入れる。

```bash
git archive --format=zip --output=agent-toolkit.zip HEAD:agent-toolkit
```

ZIPにはGitの追跡対象だけが入る。
展開後の容量200 MB・5,000ファイル以内などの受付条件は[公式のプラグインアップロード要件](https://platform.claude.com/docs/en/manage-claude/plugins-api#upload-requirements)を参照。

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

ネットワークは`Trusted`など、GitHubとPythonパッケージ配布先へ接続できる設定にする。
環境変数は`Environment variables`欄へ保存する（セットアップ内の`export`は後続セッションへ引き継がれない）。
保存後、新しいセッションでスキル一覧とMCPの接続を確認する。
セットアップが失敗した場合は、出力の末尾に失敗した段と確認事項が表示される。

取得先と依存領域を変える場合は、変数をパイプの右側の`bash`の直前へ置き（行頭へ置くと`curl`だけに渡る）、表示された2行を`Environment variables`欄へ保存する。
取得先は絶対パスにする。

```bash
curl -fsSL https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude-cloud.sh | AGENT_TOOLKIT_CLOUD_SRC=<取得先> AGENT_TOOLKIT_RUNTIME_DATA=<依存領域> bash
```

setupの制限時間とネットワーク設定は[クラウド環境の設定](https://code.claude.com/docs/en/cloud-environments)を参照。

## 設定確認

次のコマンドで導入結果を確認できる。

```bash
codex plugin list
claude plugin list
```

委譲先（エージェントが作業を任せた別のエージェント）の状態は次のコマンドで確認する。

- `atk agents list`: 実行中と保持中の委譲先を一覧する。`--watch`で約2秒ごとに更新する
- `atk agents logs <session-id>`: 委譲先の会話を要約して表示する。`--follow`で追記分を表示し続け、`--verbose`で全イベントを表示する

### 費用とレビュー指標の集計

期間を指定して、トークン使用量、API料金換算の推定費用、レビューの反復回数を週ごとに集計できる。
終了を省くと実行時刻までを集計する。

```sh
atk run-script cost-metrics -- --since 2026-10-05 --until 2026-10-19
```

出力はJSON Linesで、費用は実際の支払額とは異なる推定値である。
記録が欠けた週の0は使用量0を意味しない。
各出力項目の定義は[operations.md](../development/operations.md#週次の費用指標cost-metricsの出力)を参照。

## 工程別モデル設定

エージェントが作業の工程ごとに使うモデルを`atk config`で設定する。
各キーには候補をカンマ区切りで並べ、先頭の候補が使えない場合に次の候補へ進む。

| キー | 用途 | 初期値 |
| --- | --- | --- |
| `execute_model` | 計画・設計判断・原因分析・統合 | `claude:opus[1m]/medium,codex:sol/medium` |
| `review_model` | 実行レビュー | `codex:sol/medium,claude:sonnet[1m]/medium` |
| `review_fast_model` | 小規模レビュー | `codex:luna/medium,claude:haiku/medium` |
| `chores_model` | 確定済みの作業・中規模の読取 | `codex:sol/medium,claude:sonnet[1m]/medium` |
| `chores_fast_model` | 定型の走査・軽微な作業・コマンド実行 | `codex:luna/medium,claude:haiku/medium` |
| `orchestrate_model` | process-loop・run-skillの最上位 | `claude:opus[1m]/medium,codex:sol/medium` |
| `write_model` | 文章起草 | `agy:gemini-3.8-flash/medium,claude:claude-opus-5-5/medium` |

各候補は`<claude|codex|agy>:<model>[/<effort>]`または`opencode:<provider>/<model>[@<effort>]`で書き、effortを省略するとmediumになる。
`orchestrate_model`はOpenCodeを受け付けない。
OpenCodeの導入とprovider定義は[README](../../README.md#opencodeの委譲先)を参照。

```bash
atk config show
atk config set review_model agy:gemini-4-argon/medium
atk config set chores_model opencode:local/Qwen/Qwen3.8-27B@thinking,claude:sonnet/medium
```

`atk config show`は現在値を一覧し、`atk config get <キー>`は指定キーの値を返す。
`AGENT_TOOLKIT_CONFIG_<キー名の大文字>`の環境変数を設定すると保存値より優先する。

`atk config apply-preset <名前>`は`write_model`を除く6工程を一括で設定する。
引数を省くか`show`を指定すると、各プリセットの内容を保存せずに表示する。

| プリセット | 初期値から変更する工程 |
| --- | --- |
| `codex-balanced` | 初期値と同一 |
| `codex-near-primary` | executeを`codex:sol/medium,claude:opus[1m]/medium`へ変更 |
| `codex-primary` | executeとorchestrateを`codex:sol/medium,claude:opus[1m]/medium`へ変更 |
| `claude-balanced` | choresを`claude:sonnet[1m]/medium,codex:sol/medium`、chores_fastを`claude:haiku/medium,codex:luna/medium`へ変更 |
| `claude-primary` | review・choresを`claude:sonnet[1m]/medium,codex:sol/medium`、review_fast・chores_fastを`claude:haiku/medium,codex:luna/medium`へ変更 |

その他の主な設定は次のとおりである。

| キー | 内容 |
| --- | --- |
| `codex_fast_mode` | `true`でCodexのfast modeを使う。初期値は`false` |
| `claude_usage_limit_wait`・`codex_usage_limit_wait` | 利用上限に達した時に解除を待つ範囲。`five-hour`（初期値）は5時間枠だけ、`always`は週次枠なども待ち、`never`は待たずに次の候補へ進む |
| `claude_*_connections`・`codex_*_connections`ほか | Claude・Codexのサブスクリプション・APIクレジット・従量課金APIの使い分け。設定は[接続の使い分け](#接続の使い分け)を参照 |

利用上限で待つ間は、`agents_server`の委譲先、`atk wi process-loop`、`atk run-skill`が解除後に同じ会話で作業を続け、待機理由と予定時刻を表示する。
ユーザーが起動したClaude Codeの会話も、agent-toolkitのFunction hooksを有効にしていれば解除後に自動で再開する。
解除予定が不明な場合は300秒ごとに再確認するため、待機中もClaude Codeを起動したままにする。
[Remote Control](https://code.claude.com/docs/en/remote-control)で接続している間も同じく再開する。

## 接続の使い分け

`agents_server`の委譲先と、`atk wi process-loop`・all-reposの統括担当（オーケストレーター）は、Claude・Codexのそれぞれで次の3種類の接続を役割ごとの順序で使い分けられる。
ユーザーが直接起動するClaude Code・Codex、組み込みのサブエージェント、`atk run-skill`、`atk commit`は対象外である。

| 種別 | 接続 | 用意するもの |
| --- | --- | --- |
| `subscription` | サブスクリプション（ClaudeのPro・Max、ChatGPTのプラン） | Claudeは`claude auth login`、Codexは`codex login`によるChatGPTログイン |
| `credits` | 有限のAPIクレジット | ClaudeはAPI接続のenvファイル、Codexは`config.toml`のprovider定義 |
| `payg` | 従量課金API | `credits`と同じ形式で、接続先とキーを別に用意する |

設定キーは認証の所在と使用順の2系統で、どちらも`atk config set <キー> <値>`で保存する。

| キー | 内容 | 初期値 |
| --- | --- | --- |
| `claude_credits_env_file`・`claude_payg_env_file` | ClaudeのAPIクレジット・従量課金APIの環境変数を書いたenvファイル | 空 |
| `codex_credits_provider`・`codex_payg_provider` | Codexの`config.toml`の`model_providers`に定義したprovider ID（`openai`も指定できる） | 空 |
| `claude_orchestrator_connections`・`codex_orchestrator_connections` | 統括担当の使用順 | `subscription,credits` |
| `claude_worker_connections`・`codex_worker_connections` | 委譲先の使用順 | `credits,subscription` |

使用順は種別名を重複なしでカンマ区切りに並べ、先頭から評価する。
認証を設定していない種別は候補から除くため、使える接続が1〜2種類の環境でも同じ使用順のまま使える。
従量課金は追加料金が発生するため初期値の使用順に含めず、使用順へ`payg`を書いた役割だけが使う。

- エンジンごとに4キーのどれも設定しない場合は、親の環境変数と通常の認証（ClaudeはAPIキー・Bedrock・Vertexなど、Codexは`config.toml`の通常設定）をそのまま使い、別の接続へ切り替えない。
- いずれかのキーを設定し、ある役割で使える接続が1つも無い場合は、原因と設定方法を表示して起動を止める。
- envファイルが空・不在・不正な場合は、その種別を除いて対象パスと修正方法を表示する。
- [APIクレジット](https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers)はClaude APIとAgent SDKを対象とし、Claude Codeを対象外とする。そのため`claude` CLIで起動するClaudeの統括担当（`atk wi process-loop`）は`credits`を候補から除く。Agent SDKで起動する`agents_server`のClaudeは`credits`を使う。
- Codexの`subscription`はprovider `openai`をChatGPTログインで使う場合だけを指す。APIキーでログインしている場合は`subscription`を候補から除いて診断を表示するため、`codex_payg_provider`（クレジットなら`codex_credits_provider`）へ`openai`を設定する。

委譲先は`subscription`の利用上限、`credits`の残高不足、`payg`の不受理で、同じ会話のまま使用順の次の接続へ移る。
解除を待つ設定（`claude_usage_limit_wait`・`codex_usage_limit_wait`）の対象の利用上限では、移らずに解除を待つ。
`credits`の残高不足を観測してから1時間は、新しい委譲先で`credits`を使用順の最後へ回す。
統括担当は起動時に使用順の先頭の使える接続を選び、会話の途中では切り替えない。

継続・再開する会話は選んだ接続を使い続ける。
使用順からその種別を外した場合は、次の継続で使用順の先頭の使える接続へ移り、外した種別へは戻らない。
利用上限で`subscription`から移ったClaudeの委譲先は、使用順で`subscription`が前にあれば、解除予定時刻の後の最初の継続で`subscription`へ戻る。
Codexの会話は移った接続を使い続け、新しい会話だけを使用順の先頭から評価する。

`AGENT_TOOLKIT_CONFIG_<キー名の大文字>`の環境変数を設定すると保存値より優先するため、設定を変える場合はこの変数も確認する。
切替と保持の詳細は[design-agents-runtime.md](../development/design-agents-runtime.md#api失敗過負荷利用上限の待機)を参照。

### 認証の用意

ClaudeのAPI接続は、接続用の環境変数をenvファイルへ保存してパスを設定する。
Anthropicへ直接接続する場合は、[Claude ConsoleのAPI keys](https://platform.claude.com/settings/keys)で発行したキーを`ANTHROPIC_API_KEY`へ記入し、Consoleで課金設定も用意する。
APIクレジットは`~/.config/claude-credits.env`、従量課金APIは`~/.config/claude-payg.env`を例とし、それぞれ次の内容で作成する。

```dotenv
ANTHROPIC_API_KEY=YOUR_API_KEY
```

形式は1行1つの`KEY=VALUE`で、`export`、引用符、`#`のコメント行も使える。
別の接続先を使う場合は、提供元が指定するURLを`ANTHROPIC_BASE_URL`へ、Bearer認証のトークンを`ANTHROPIC_AUTH_TOKEN`へ記入する。
`ANTHROPIC_AUTH_TOKEN`は`ANTHROPIC_API_KEY`より優先するため、直接接続用のファイルにはAPIキーだけを書く。
変数の意味は[Claude Codeの認証仕様](https://code.claude.com/docs/en/authentication#authentication-precedence)を参照。

ファイルはGit管理外へ置き、Linux・macOSでは`chmod 600 <ファイル>`で自分だけが読み書きできるようにする。
Windowsではファイルのプロパティのセキュリティ設定で、使用するOSアカウントだけに読取権限を与える。
envファイルは新しい会話の開始・再開時に読む。
ユーザー設定（`~/.claude/settings.json`）などのsettingsの`env`へ接続関連の変数（`ANTHROPIC_API_KEY`、`AWS_PROFILE`など）を書いた場合、委譲先とオーケストレーターが実行するコマンドにはその変数が空の値で渡る。

CodexのAPI接続は、`config.toml`へ接続先とAPIキーの環境変数名をproviderとして定義する。
定義の書き方は[Codex利用ガイド](codex-guide.md#codexのサブスクとapi接続先)を参照。
以降の設定例では、APIクレジットのproviderを`my-credits`、従量課金APIのproviderを`my-payg`という名前で定義した場合を示す。

### 設定例

各例のコードブロックは、そのまま貼り付けて実行すると両エンジンの接続設定8キーを全て保存する。
envファイルのパスとprovider IDは、前節で用意した名前と異なる場合だけ置き換える。
使わない種別は空文字列を保存して解除する。

サブスクリプションだけを使う環境では、次のとおり設定する。
両エンジンとも統括担当と委譲先はサブスクリプションだけを使い、親の環境変数にあるAPIキーなどは使わない。

```bash
atk config set claude_credits_env_file ''
atk config set claude_payg_env_file ''
atk config set claude_orchestrator_connections subscription
atk config set claude_worker_connections subscription
atk config set codex_credits_provider ''
atk config set codex_payg_provider ''
atk config set codex_orchestrator_connections subscription
atk config set codex_worker_connections subscription
```

サブスクリプションとAPIクレジットを使う環境では、次のとおり設定する。
委譲先はAPIクレジット、サブスクリプションの順に使う。
統括担当はサブスクリプション、APIクレジットの順に使う（Claudeの`atk wi process-loop`はAPIクレジットを使えないためサブスクリプションだけを使う）。

```bash
atk config set claude_credits_env_file '~/.config/claude-credits.env'
atk config set claude_payg_env_file ''
atk config set claude_orchestrator_connections subscription,credits
atk config set claude_worker_connections credits,subscription
atk config set codex_credits_provider my-credits
atk config set codex_payg_provider ''
atk config set codex_orchestrator_connections subscription,credits
atk config set codex_worker_connections credits,subscription
```

サブスクリプションと従量課金APIを使う環境では、次のとおり設定する。
統括担当と委譲先はサブスクリプションを先に使い、利用上限に達した委譲先と、サブスクリプションを使えない統括担当だけが従量課金APIを使う。

```bash
atk config set claude_credits_env_file ''
atk config set claude_payg_env_file '~/.config/claude-payg.env'
atk config set claude_orchestrator_connections subscription,payg
atk config set claude_worker_connections subscription,payg
atk config set codex_credits_provider ''
atk config set codex_payg_provider my-payg
atk config set codex_orchestrator_connections subscription,payg
atk config set codex_worker_connections subscription,payg
```

3種類すべてを使う環境では、次のとおり設定する。
委譲先はAPIクレジット、サブスクリプション、従量課金APIの順に使う。
統括担当はサブスクリプション、APIクレジット、従量課金APIの順に使う（Claudeの`atk wi process-loop`はAPIクレジットを使わない）。

```bash
atk config set claude_credits_env_file '~/.config/claude-credits.env'
atk config set claude_payg_env_file '~/.config/claude-payg.env'
atk config set claude_orchestrator_connections subscription,credits,payg
atk config set claude_worker_connections credits,subscription,payg
atk config set codex_credits_provider my-credits
atk config set codex_payg_provider my-payg
atk config set codex_orchestrator_connections subscription,credits,payg
atk config set codex_worker_connections credits,subscription,payg
```

APIだけを使う環境では、次のとおり設定する。
両エンジンとも統括担当と委譲先は従量課金APIだけを使い、使用中の会話も次の継続から従量課金APIへ移る。

```bash
atk config set claude_credits_env_file ''
atk config set claude_payg_env_file '~/.config/claude-payg.env'
atk config set claude_orchestrator_connections payg
atk config set claude_worker_connections payg
atk config set codex_credits_provider ''
atk config set codex_payg_provider my-payg
atk config set codex_orchestrator_connections payg
atk config set codex_worker_connections payg
```

設定した値は`atk config show`で確かめる。

### 旧設定からの移行

`claude_api_env_file`・`claude_orchestrator_api_only`・`claude_worker_api_first`・`claude_api_mode`・`codex_model_providers`は廃止した。
保存済みの値は、次に`atk config`を実行するか委譲先を起動した時に新しいキーへ自動で移し、旧キーを削除する。

- Claudeのenvファイルは、委譲先をAPI優先にしていた（旧`api-credits`）場合は`claude_credits_env_file`へ、それ以外は`claude_payg_env_file`へ移り、旧設定の役割別の方針を使用順へ書き換える。
- Codexの`openai`は`subscription`、それ以外のprovider IDは`codex_payg_provider`へ移り、元の順序を両役割の使用順へ書く。`openai`以外のprovider IDが2つ以上ある場合は自動で移さず、新しいキーの設定を求めるエラーを表示する。
- Codexの移行はアカウント種別を見ないため、APIキーでログインして`openai`を並べていた環境は、移行後の診断に従って`codex_payg_provider`か`codex_credits_provider`へ`openai`を設定し直す。

旧キーの環境変数を設定している場合と、`atk config set`・`get`へ旧キーを渡した場合は、移行先のキー名を示すエラーで終わる。

## Claude Codeの推奨設定

以下の設定を推奨する。

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
- 参照のみ（有効化しない）: `plugin-dev`・`mcp-server-dev`（エージェントが品質基準の案内に従って資料として読むため導入は不要。有効化するとスキルが自動で起動し、質問が増える）
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

作業の進め方は次の4パターンから選ぶ。

| パターン | 向く場面 |
| --- | --- |
| 対話型 | 要件をその場で確定しながら進めたい |
| 自律型 | AWIが継続して発生し、常駐実行で順に処理させたい |
| まとめ処理型 | 数日に数件の頻度でたまったAWIをまとめて処理したい |
| 定期実行型 | 決まったスキルを一定の間隔で自動実行したい |

どのパターンでも、エージェントは技術的に決められる事項を自分で判断して結果を報告し、ユーザーの判断・資料・操作が必要な事項だけを確認する。
回答できなかった確認は、UWIとして保存される。

### 対話型

エージェントへ作業を直接依頼する。
エージェントは調査・計画・実装・検証・公開まで進め、ユーザーだけが決められる事項を着手前に確認する。

### 自律型

`atk wi process-loop`を常駐させ、依頼したい内容をAWIとして登録する。
登録したAWIは、調査・計画・実装・レビュー・公開まで自動で処理される。

処理するリポジトリのローカルworktreeを絶対パスのJSON配列で登録し、ホームなどから起動する。

```bash
atk config set process_loop_repositories '["/home/user/project-a", "/home/user/project-b"]'
atk wi list --configured-repos
atk wi process-loop
```

登録していないリポジトリへもAWIを登録できる（常駐実行の処理対象にはならない）。
dotfilesリポジトリでは自動で専用worktreeを使い、ほかのリポジトリでworktreeを分ける場合は`--worktree[=NAME]`を指定する。
統括担当をCodexにする場合は、起動前に`atk config set orchestrate_model codex:sol/medium,claude:opus[1m]/medium`を実行する。

`/agent-toolkit:add-awi-by-user`でAWIを登録している間や、未回答のUWIがある間は、処理の開始を最長10分程度待つ。
待機理由と期限は端末に表示される。
すぐに始めたい場合は、process-loopを動かす端末（tmuxではそのペイン）でEnterを押すと今回だけ待機を解除できる。

常駐中の操作は次のコマンドで行う。

```bash
atk wi process-loop status
atk wi process-loop abort
atk wi process-loop instruct '次の処理へ適用する指示'
```

`abort`は処理中のセッションが終わった時点でループを止める。
`instruct`は次の処理へ1回だけ指示を渡す。
中断した処理を再開する場合は、`atk wi process-loop --resume <session-ID>`へ統括担当の会話IDを指定するか、`--auto-resume`で保持された未完了の対象を再開する。

常駐させずに登録全体を1回だけ処理する場合は、`atk wi process-loop --once`を実行する（会話からは`/agent-toolkit:process-wi all-repos`）。
対象が0件なら直ちに終了し、未完了の対象が残ると非0で終了するため、表示された記録を確認してから`atk wi process-loop --once --auto-resume`で再開する。

CIの失敗だけを1回確認してAWIとして登録する場合は、`atk wi check-alerts --target-repo <リポジトリの絶対パス>`を実行する（省略時は現在の作業ツリー）。
AWIの処理は始めず、取得に失敗した場合は理由を示して終了コード1で終わる。

### まとめ処理型

常駐実行を起動せず、数件のAWIがたまった時点で対象リポジトリの会話から`/agent-toolkit:process-wi single-lane`を実行する。
同じセッションの中で調査から実装、レビュー、公開まで進む。
並列のレーンへ分けて処理する場合は`/agent-toolkit:process-wi`を実行する。

### 定期実行型

ログの点検のように、決まったスキルを一定の間隔で実行させたい場合は、cronなどへ`atk run-skill`を登録する。
`atk run-skill`は指定したリポジトリで指定したスキルを1回実行し、結果を報告用UWIとして保存する。
モデルの選択、利用上限の解除待ち、多重起動の防止、時間上限（省略時6時間）、ログの保存はコマンドが行う。

cronは端末と異なる`PATH`で起動するため、`atk`・`claude`・`codex`の場所を`PATH`へ含める。
標準出力を捨てると、失敗時だけ標準エラーの失敗行がcronのメールで届く。

```bash
PATH=/home/user/.local/bin:/usr/local/bin:/usr/bin:/bin
0 3 */3 * * atk run-skill --target-repo /home/user/repo my-skill >/dev/null
```

終了コード0は報告用UWIの保存（upstreamがあればpush）まで成功したこと、1は報告の保存に失敗したこと、124は時間上限に達したことを示す。
ログは`atk config get state_dir`が示すディレクトリの`run-skill/`に30日保存される。
破壊的な操作など判断が要る対応は実施されずにUWIとして届き、回答すると次回の実行がその対応を実施する。
オプションは`atk run-skill --help`で確認する。

報告の保存に失敗した場合（終了コード1）は、標準エラーが示すログを開いて次の手順で保存を完了する。

1. ログの「実行識別子」を控える
2. 対象リポジトリのディレクトリで、実行した会話を再開する
    - Claude Code: `AGENT_TOOLKIT_RUN_SKILL_ID=<実行識別子> claude --resume <ログのClaudeのセッション識別子>`
    - Codex: `AGENT_TOOLKIT_RUN_SKILL_ID=<実行識別子> codex resume <ログのthread_id>`
3. 再開した会話で「今回の報告用UWIの保存を完了して」と依頼し、保存完了の報告を確認する

再開できない場合は、新しい会話でログの所在・実行識別子・スキル名を伝えて調査と復旧を依頼する。

### 登録方法

| 登録方法 | 選ぶ場面 |
| --- | --- |
| `atk wi add` | 依頼内容が既に固まっており、本文をそのまま登録したい |
| `atk wi add --batch` | 別環境の`atk wi show --all`の出力を複数件まとめて移行・復元したい |
| `/agent-toolkit:add-awi-by-user` | 依頼内容を対話で確定してから登録したい |
| `/agent-toolkit:add-awi-by-user carefully <要求>` | 考慮漏れを洗い出したい要求を、本文を書く前に方針から確認したい |
| `atk serve`の新規追加 | 画面から登録したい。種別「一括登録（show形式）」で移行・復元もできる |

通常の登録は、本文を書いたファイルを渡して`atk wi add --body-file <本文ファイル>`を実行する。
`atk wi add --cooldown-until '2026-10-11T12:00:00+09:00' --body-file <本文ファイル>`のように、指定時刻まで処理を始めないAWIも登録できる。
登録済みのAWIを直したい場合は、`atk wi hold <ファイル名>`で自動処理から外し、本文を直してから`atk wi unhold <ファイル名>`で戻す。
処理中の項目は保留せず、変更内容を新しいAWIとして登録する。

`carefully`を付けると、エージェントは本文を書く前に方針の承認を求める。
考慮漏れを指摘するたびに原因を分析し、ほかの方針にも同じ理由の見落としがないか確認する。
その結果を方針へ反映して再確認し、再発防止策も別AWIとして登録する。
通常の起動では、要件の確定後に登録する。

### UWIへの回答と状態確認

未回答のUWIは`atk wi answer`で順に確認して回答できる。
ファイル名と回答を指定する場合は次の形式を使う。

```bash
atk wi answer <UWIファイル名> '<回答本文>'
```

エージェントが実施済みの対応への承認を求めるUWI（本文の先頭に`question_type: post-approval`）では、「その対応で問題無い」と回答すると保存時に完了する。
それ以外の回答は、次の処理でエージェントが元の作業へ反映する。
処理中の会話へ「UWI回答した」と伝えると、同じ実行で反映される。

項目の確認には次の指定を使う。
対象はカレントディレクトリのリポジトリで、全リポジトリを確認する場合は`--target-repo=all`を加える。

- `atk wi list --status=active`: 現在の項目
- `atk wi list --status=processable`: 自動処理へ渡せる項目
- `atk wi list --type=uwi --answered=no`: 未回答のUWI

### atk serveの画面

`atk serve`を実行し、起動ログに表示されたURL（初期値は`http://127.0.0.1:28766/`、Windowsではポート28876）をブラウザーで開く。
終了するにはそのターミナルで`Ctrl+C`を押す。
画面は次の3つである。

- WI: AWI・UWIの登録、編集、回答、状態の変更
- 計画ファイル: ローカルと設定済みリモートホストの計画ファイルの一覧・全文検索・表示
- セッション: Claude CodeとCodexの保存済みセッションの一覧と会話の表示。右ペインの見出しにAPI料金換算の推定費用を表示する

WI画面の状態表示のうち、`answered`は回答済みで次の処理を待つUWI、`cooldown`は期限まで処理を始めないAWI、`needs-verify`は反映後の観測待ちで再処理を待つAWIを示す。
振り返りのbatchが登録したAWIには「要確認」バッジが付き、内容を確認するまで自動処理されない。
詳細の「確認済みにする」で処理を許可し、誤って押した場合は「要確認に戻す」で取り消せる。

エージェントが登録したinboxまたはholdのAWIには、詳細画面から`## ユーザーコメント`を追記できる。
コメントに操作・対象・範囲を具体的に書くと、その範囲の外部操作への承認として扱われる。
「進めて」だけでは範囲が確定しないため承認にならない。

待受と参照元は`atk config`で変更し、次回の起動から反映する。

| キー | 内容 | 未指定時 |
| --- | --- | --- |
| `serve_host` | 待受ホスト | `127.0.0.1` |
| `serve_port` | 待受ポート | 28766（Windowsでは28876） |
| `serve_plans_root` | 計画ファイル画面の参照元 | private-notesの`plans`と`~/.claude/plans`の両方 |
| `serve_plans_remote_hosts` | 計画ファイル画面のリモートホスト（SSH接続先名のJSON配列） | `[]` |
| `serve_sessions_claude_home` | セッション画面のClaude Codeの参照元 | `~/.claude` |
| `serve_sessions_codex_home` | セッション画面のCodexの参照元 | `CODEX_HOME`、空なら`~/.codex` |
| `serve_sessions_remote_hosts` | セッション画面のリモートホスト（SSH接続先名のJSON配列） | `[]` |

```bash
atk config set serve_port 28767
atk config set serve_sessions_remote_hosts '["remote-host"]'
```

リモートホストの自動更新と費用表示には、リモートホスト側のdotfilesの更新が必要である。
画面の詳細な振る舞いは[design-serve.md](../development/design-serve.md)を参照。

### 選定結果の確認設定

対象リポジトリの`pyproject.toml`に`[tool.agent-toolkit.pick-wi-check]`の`norm-spec`を置くと、AWIの選定時に、条件に当たる項目へ指定の規範を書くことを求められる。
各条件は`paths`と`suffixes`か`agent-doc = true`（エージェント向け文書）で対象を選び、`require-paths = true`か`require-text`で求める記載を指定する。
設定の誤りは、場所と直し方を示して終了コード2で報告される。

## atkコマンドのPATH設定

インストーラーは`~/.local/bin/atk`（Windowsでは`~/.local/bin/atk.cmd`）に、導入済みの最新版を呼び出すラッパーを配置する。
`~/.local/bin`がPATHに含まれていない場合は警告が表示されるため、PATHへ追加する。
dotfilesユーザーは`chezmoi apply`で`~/dotfiles/agent-toolkit/scripts`がPATHへ追加されるため、この設定は不要である。

## 構成と機能

### 常時有効な仕組み

ルールファイルは自動で読み込まれ、行動原則、運用方針、言語表現、検証とコミットの流れを提供する。
プラグインはpyfltrのMCPサーバーを同梱し、lint・テスト・横断検索・横断置換をシェルを経由せずに実行できる（pyfltr自体の事前インストールは不要）。

常時有効なフックは、主に次の働きをする。

- 元へ戻せない操作や明らかな誤りを、実行前に警告または遮断する
- 英語主体の最終応答や空の完了報告を検出し、日本語での書き直しや報告を求める
- セッションの開始時と会話圧縮の後に、エージェントへ必要な規範を追加する
- ツールの確認ダイアログを自動で許可し、許可した要求をログへ残す（Claude Codeのみ）

警告や遮断が表示された場合は、本文に示された次の操作に従う。
各フックの判定条件とCodexでの対応範囲は[design-hooks.md](../development/design-hooks.md#フックごとの処理とcodexの対応範囲)を参照。
Codexでフックを信頼する操作は[Codex利用ガイド](codex-guide.md#フックの信頼確認)を参照。

### 手動で起動する主なスキル

スキルの多くは、該当する作業に着手した時点でエージェントが自動で起動する。
ユーザーが手動で起動する主なスキルは次のとおりである（Claude Codeでは`/`、Codexでは`$`を付けて起動する）。

- `agent-toolkit:add-awi-by-user`: 要件を対話で確定し、AWIまたはUWIを登録する
  - 考慮漏れを洗い出したい要求では、先頭に`carefully`を付けて起動する（Claude Codeでは`/agent-toolkit:add-awi-by-user carefully <要求>`）。本文を書く前に方針を確認し、考慮漏れの指摘ごとに原因分析して再確認する。通常の起動では要件の確定後に登録する
- `agent-toolkit:process-wi`: 対象リポジトリのAWIをレーンへ分けて計画・実装・レビュー・公開する
- `agent-toolkit:process-wi single-lane`: AWIをレーンへ分けずに1回の起動でまとめて処理する
- `agent-toolkit:process-wi all-repos`: `process_loop_repositories`の登録全体を1回処理する
- `agent-toolkit:session-review`: 現在のセッションを振り返り、原因と恒久対策をAWIとして登録する。引数で着眼点を追加できる
- `agent-toolkit:session-review batch`: 振り返り待ちのセッションをまとめて分析する。既定で自動起動し、`atk config set session_review_auto_start false`で停止できる
- `agent-toolkit:export-session`: Claude CodeとCodexのセッション記録をMarkdownへ出力する
- `agent-toolkit:refine-prompt`: プロンプトやスキルの改善点を独立した実行者から集めて反映する

## 更新と削除

ルールファイル・プラグインとも頻繁に更新されるため、定期的に最新化する。
「ツールキットのインストール」のワンライナーを再実行すると更新される。
dotfiles（chezmoi）管理下のマシンでは`chezmoi apply`でも更新できる。
実行中のセッションは更新前の版を使い続けるため、新しい版は新しいセッションから使う。

アンインストール時は、Claude CodeとCodexのagent-toolkitプラグインを除去し、`~/.claude/rules/agent-toolkit/`と`~/.local/bin`の`atk`ラッパーを削除する。

過去の手順で登録したUser scopeのMCP定義`codex`（`codex mcp-server`）は、インストーラーと`chezmoi apply`が自動で除去する。
自動で除去できなかった場合は、次のコマンドで定義を確認し、旧`codex mcp-server`定義だけを削除する。
独自に追加した定義は内容を確認してから削除する。

```bash
claude mcp get codex
claude mcp remove --scope user codex
```

Codex単独セッションとdotfiles固有の配布内容については[Codex利用ガイド](codex-guide.md)を参照。

## 品質基準だけを導入する

`agent-basics`はコード・設計・テスト・レビュー・文章の品質スキルを提供する。単独導入と利用時の起動条件は[agent-basicsの導入案内](../../agent-basics/README.md)を参照する。agent-toolkitはWI・委譲・承認・完了報告を担当し、品質基準としてagent-basicsへ依存する。Claude Codeはその依存を自動導入し、Codexは2件を明示的に導入する。dotfilesの更新処理は両方を同期する。
