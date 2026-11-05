# agent-toolkit導入ガイド（Agent Plugins・Claude Code・Codex）

本書はagent-toolkitの詳細設定・操作とdotfiles固有の運用を扱う。
概要・導入・初回確認は[agent-toolkitのREADME](../../agent-toolkit/README.md)を参照。

## コンセプト

用途、品質基準との責務分担、AWI・UWIの意味は[README](../../agent-toolkit/README.md)を参照。
常時規範とスキル・フック・MCPを併用する構成と、ホストごとの機能範囲は[対応ホストと機能](../../agent-toolkit/README.md#対応ホストと機能)にまとめている。

## 前提条件

単体導入に必要なCLIは[READMEの前提条件](../../agent-toolkit/README.md#前提条件)を参照。
dotfilesユーザーでは、`chezmoi apply`後の処理がClaude Code（Anthropic公式ネイティブ版）とCodex CLIを導入・更新する。
WindowsでClaude CodeやCodexが実行中の場合は停止せず、更新を次回へ延期する。

## クイックスタート

### ツールキットのインストール

[READMEの導入](../../agent-toolkit/README.md#導入)にある統合インストーラーを実行し、[導入確認](../../agent-toolkit/README.md#導入確認)と[最初の操作](../../agent-toolkit/README.md#最初の操作)へ進む。
dotfilesユーザーは`chezmoi apply`で同じプラグインを設定する。

## Claudeの各環境への導入

使う環境と対応機能は[READMEの対応ホストと機能](../../agent-toolkit/README.md#対応ホストと機能)を参照。
ここではマーケットプレイス・ZIPからの追加とクラウドの詳細設定を扱う。

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

初回の確認コマンドと期待結果は[READMEの導入確認](../../agent-toolkit/README.md#導入確認)を参照。

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
| `codex_auto_reset` | `true`でChatGPT接続の週次利用枠を自動resetする。初期値は`false`。条件と設定例は[Codex利用ガイド](codex-guide.md#利用上限後の再開)を参照 |
| `claude_usage_limit_wait`・`codex_usage_limit_wait` | 利用上限に達した時に解除を待つ範囲。`five-hour`（初期値）は5時間枠だけ、`always`は週次枠なども待ち、`never`は待たずに次の候補へ進む |
| `claude_*_connections`・`codex_*_connections`ほか | Claude・Codexのサブスクリプション・APIクレジット・従量課金APIの使い分け。設定は[接続の使い分け](#接続の使い分け)を参照 |

利用上限で待つ間は、`agents_server`の委譲先、`atk wi process-loop`、`atk run-skill`が解除後に同じ会話で作業を続け、待機理由と予定時刻を表示する。
ユーザーが起動したClaude Codeの会話も、agent-toolkitのFunction hooksを有効にしていれば解除後に自動で再開する。
解除予定が不明な場合は300秒ごとに再確認するため、待機中もClaude Codeを起動したままにする。
[Remote Control](https://code.claude.com/docs/en/remote-control)で接続している間も同じく再開する。

## 接続の使い分け

`agents_server`の委譲先と、`atk wi process-loop`・all-reposの統括担当（オーケストレーター）は、Claude・Codexのそれぞれで次の3種類の接続を役割ごとの順序で使い分けられる。
`atk run-skill`のClaude起動も統括担当の使用順を使う。
ユーザーが直接起動するClaude Code・Codex、組み込みのサブエージェント、Codexの`atk run-skill`、`atk commit`は対象外である。

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
- [APIクレジット](https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers)はClaude APIとAgent SDKを対象とし、Claude Codeを対象外とする。そのため`claude` CLIで起動するClaudeの統括担当（`atk wi process-loop`・`atk run-skill`）は`credits`を候補から除く。Agent SDKで起動する`agents_server`のClaudeは`credits`を使う。
- Codexの`subscription`はprovider `openai`をChatGPTログインで使う場合だけを指す。APIキーでログインしている場合は`subscription`を候補から除いて診断を表示するため、`codex_payg_provider`（クレジットなら`codex_credits_provider`）へ`openai`を設定する。

委譲先は`subscription`の利用上限、`credits`の残高不足、`payg`の不受理で、同じ会話のまま使用順の次の接続へ移る。
解除を待つ設定（`claude_usage_limit_wait`・`codex_usage_limit_wait`）の対象の利用上限では、移らずに解除を待つ。
`credits`の残高不足を観測してから1時間は、新しい委譲先で`credits`を使用順の最後へ回す。
Claudeの統括担当は、起動時と再開時に利用可能な接続を確かめ、本作業にも同じ接続を使う。
利用上限で中断した場合は旧CLIの終了を確認してから、同じ会話を次の利用可能な接続で再開する。
代替がない場合の待機には既存の待機設定を使う。
Codexの統括担当は起動時に使用順の先頭の使える接続を選び、会話の途中では切り替えない。

継続・再開する会話は、Claude統括の利用上限時の切替を除き、選んだ接続を使い続ける。
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
達成したい目標と対象リポジトリを伝えれば、必要な調査・実装・検証の構成も任せられる。
処理中のprocess-wiにも、同じリポジトリの目標を追加指示として伝えられる。
目的や範囲の選択が残る場合は候補と推奨案を確認し、確定した目的内の技術判断はエージェントが進める。
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
ログは`atk config get state_dir`が示すディレクトリの`run-skill/`に保存される。
未完了の実行入力とログは保持し、完了済みの記録とログは最終更新から30日を過ぎた実行開始時に回収する。
破壊的な操作など判断が要る対応は実施されずにUWIとして届き、回答すると次回の実行がその対応を実施する。
オプションは`atk run-skill --help`で確認する。

報告の保存に失敗した場合（終了コード1）は、標準エラーが示すログを開いて次の手順で保存を完了する。

1. ログの「実行識別子」を控える
2. `RUN_ID`を控えた実行識別子へ置き換え、`atk run-skill --resume RUN_ID`を実行する
3. 元の会話・実行ID・報告先・batchの準備範囲・残時間を引き継いだ続行が、報告の同期を確認して成功することを確認する

再開時はスキル名・対象・cwd・引数・時間上限を変更しない。
完了済みの実行を再度指定しても、会話の再起動は行わない。

再開できない場合は、新しい会話でログの所在・実行識別子・スキル名を伝えて調査と復旧を依頼する。

### 登録方法

| 登録方法 | 選ぶ場面 |
| --- | --- |
| `atk wi add` | 依頼内容が既に固まっており、本文をそのまま登録したい |
| `atk wi add --batch` | 別環境の`atk wi show --all`の出力を複数件まとめて移行・復元したい |
| `/agent-toolkit:add-awi-by-user <要求>` | 依頼内容を具体化し、必要な要件確認を対話で行って登録したい |
| `atk serve`の新規追加 | 画面から登録したい。種別「一括登録（show形式）」で移行・復元もできる |

通常の登録は、本文を書いたファイルを渡して`atk wi add --body-file <本文ファイル>`を実行する。
`atk wi add --cooldown-until '2026-10-11T12:00:00+09:00' --body-file <本文ファイル>`のように、指定時刻まで処理を始めないAWIも登録できる。
登録済みのAWIを直したい場合は、`atk wi hold <ファイル名>`で自動処理から外し、本文を直してから`atk wi unhold <ファイル名>`で戻す。
処理中の項目は保留せず、変更内容を新しいAWIとして登録する。

エージェントは目的・範囲・選好・認可など、ユーザーの判断が必要な点を確認してから要求を具体化し、意味の検収を経て登録する。
モードを選ぶ引数や、本文を書く前の一律の最終承認は不要である。
考慮漏れの指摘には原因を分析し、同じ理由の見落としへの処置と再発防止策を要求へ反映する。

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
振り返りのbatchが登録したAWIも、エージェントが採否を検討して自動処理する。
未回答のUWIの回答を待つAWIには`blocked`バッジが付き、UWIへ回答すると外れて次の処理で回答とともに処理される。処理中のUWIは`processing`と表示される。

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

インストーラーで導入するユーザーは[READMEの導入](../../agent-toolkit/README.md#導入)を参照。
dotfilesユーザーは`chezmoi apply`で`~/dotfiles/agent-toolkit/scripts`がPATHへ追加される。

## 構成と機能

### 常時有効な仕組み

規範・フック・MCPの役割は[READMEの構成と機能](../../agent-toolkit/README.md#構成と機能)を参照。
各フックの判定条件とCodexでの対応範囲は[design-hooks.md](../development/design-hooks.md#フックごとの処理とcodexの対応範囲)、Codexでの信頼操作は[Codex利用ガイド](codex-guide.md#フックの信頼確認)を参照。

### 手動で起動する主なスキル

最初に使う登録・処理の操作は[READMEの最初の操作](../../agent-toolkit/README.md#最初の操作)、運用ごとの例は[推奨ワークフロー](#推奨ワークフロー)を参照。
Claude Codeでは`/`、Codexでは`$`を付けて起動する。

- `agent-toolkit:review-session`: 現在のセッションを振り返り、原因と恒久対策をAWIとして登録する。引数で着眼点を追加できる
- `agent-toolkit:review-session batch`: 振り返り待ちのセッションをまとめて分析する。既定で自動起動し、`atk config set session_review_auto_start false`で停止できる
- `agent-toolkit:export-session`: Claude CodeとCodexのセッション記録をMarkdownへ出力する
- `agent-toolkit:refine-prompt`: プロンプトやスキルの改善点を独立した実行者から集めて反映する

## 更新と削除

一般の更新・削除は[READMEの更新と削除](../../agent-toolkit/README.md#更新と削除)を参照。
dotfiles管理下のマシンでは`chezmoi apply`でも更新できる。
ここでは既存環境の移行時に必要な補足を扱う。

`agents_server`のClaude委譲先は、有効なプラグインと設定ファイルの変更を監視する。
更新を検知すると進行中のturnの終了を待ち、同じ接続でプラグインを再読込してから次の入力を処理する。
再読込に失敗した場合は診断ログを確認し、表示されたプラグインや設定の問題を解消する。
この自動反映は`agents_server`のClaude委譲先が対象で、ユーザーが直接起動したClaude Codeの会話は含まない。

Codexの会話から実行する`update-dotfiles`は更新処理を独立したjobとして実行する。
更新後の結果取得とdaemonの反映条件は[Codex利用ガイド](codex-guide.md#プラグイン更新の反映)を参照。

過去の手順で登録したUser scopeのMCP定義`codex`（`codex mcp-server`）は、インストーラーと`chezmoi apply`が自動で除去する。
自動で除去できなかった場合は、次のコマンドで定義を確認し、旧`codex mcp-server`定義だけを削除する。
独自に追加した定義は内容を確認してから削除する。

```bash
claude mcp get codex
claude mcp remove --scope user codex
```

Codex単独セッションとdotfiles固有の配布内容については[Codex利用ガイド](codex-guide.md)を参照。

## 品質基準だけを導入する

品質スキルの単独導入と適用条件は[agent-basicsのREADME](../../agent-basics/README.md)を参照。
