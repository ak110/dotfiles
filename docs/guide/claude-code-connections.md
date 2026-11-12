# agent-toolkitの接続設定

接続の準備と切替条件を扱う。
保存するコマンドは[接続の設定例](claude-code-connection-examples.md)を参照。

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
- [APIクレジット](https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers)はClaude APIとAgent SDKを対象とし、Claude Codeを対象外とする。
  そのため`claude` CLIで起動するClaudeの統括担当（`atk wi process-loop`・`atk run-skill`）は`credits`を候補から除く。
  Agent SDKで起動する`agents_server`のClaudeは`credits`を使う。
- Codexの`subscription`はprovider `openai`をChatGPTログインで使う場合だけを指す。
  APIキーでログインしている場合は`subscription`を候補から除いて診断を表示するため、`codex_payg_provider`（クレジットなら`codex_credits_provider`）へ`openai`を設定する。

委譲先は`subscription`の利用上限、`credits`の残高不足、`payg`の不受理で、同じ会話のまま使用順の次の接続へ移る。
解除を待つ設定（`claude_usage_limit_wait`・`codex_usage_limit_wait`）の対象の利用上限では、移らずに解除を待つ。
工程別モデル設定を変えた後、変更前のモデルで動く委譲先が利用上限で拒否された場合は、別モデルへの引き継ぎ起動を案内する。
案内に従った後任は新しい会話として、変更後の設定と、前任の起動本文・追加指示・最終応答の記録を受け取る。
設定を変えていない場合と、モデル候補を直接指定した委譲先では、元の待機・接続切替を保つ。
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
切替と保持の詳細は[design-agents-runtime-connections.md](../development/design-agents-runtime-connections.md#api失敗過負荷利用上限の待機)を参照。

### 委譲先のモデルを指定して切り替える

作業を依頼しているエージェントへ、委譲先と切り替え先のモデルを指示する。
実行中の前任は終了してから、MCPの`start`へ`handoff_from`と任意の`model_type`を渡して引き継ぐ。
`model_type`は通常の起動と同じ候補列で、指定しなければ前任の工程の現在設定を使う。
前任の会話を要約するモデルは呼ばず、新しい会話へ記録を渡す。
切替元・切替先はClaude・Codex・Antigravity・OpenCodeに対応する。

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

### 旧設定からの移行

`claude_api_env_file`・`claude_orchestrator_api_only`・`claude_worker_api_first`・`claude_api_mode`・`codex_model_providers`は廃止した。
保存済みの値は、次に`atk config`を実行するか委譲先を起動した時に新しいキーへ自動で移し、旧キーを削除する。

- Claudeのenvファイルは、委譲先をAPI優先にしていた（旧`api-credits`）場合は`claude_credits_env_file`へ、それ以外は`claude_payg_env_file`へ移り、旧設定の役割別の方針を使用順へ書き換える。
- Codexの`openai`は`subscription`、それ以外のprovider IDは`codex_payg_provider`へ移り、元の順序を両役割の使用順へ書く。
  `openai`以外のprovider IDが2つ以上ある場合は自動で移さず、新しいキーの設定を求めるエラーを表示する。
- Codexの移行はアカウント種別を見ないため、APIキーでログインして`openai`を並べていた環境は、移行後の診断に従って`codex_payg_provider`か`codex_credits_provider`へ`openai`を設定し直す。

旧キーの環境変数を設定している場合と、`atk config set`・`get`へ旧キーを渡した場合は、移行先のキー名を示すエラーで終わる。
