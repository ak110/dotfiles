# agent-toolkitの接続設定

Codexの認証の所在と各エンジンの使用順は、`atk serve`の「設定」の「接続」で変更する。

接続の準備と切替条件を扱う。
保存するコマンドは[接続の設定例](claude-code-connection-examples.md)を参照。

## 接続の使い分け

設定画面では、ローカルの共有設定を「全ホスト共通」で編集する。
共有先が一致しないホストの値は「設定対象ホスト」で選んだホストの欄で編集する。

同じprivate-notesを同期する環境では、接続の使用順、利用上限の待機方針、Codexのprovider ID（`codex_credits_provider`・`codex_payg_provider`）を共有する。
provider IDの定義と認証は各端末の`config.toml`と環境変数に置き、共有しない。
共有したIDがある端末の`config.toml`に無い場合は、「設定」に端末名・キーとともに警告が出る。
共有設定ファイルの場所は`atk config show`の標準エラーで確認できる。
特定のホストだけ変えるには`atk config temporary`か環境変数を使う。

`agents_server`の委譲先と、`atk wi process-loop`・all-reposの統括担当（オーケストレーター）は、Claude・Codexのそれぞれで次の3種類の接続を役割ごとの順序で使い分けられる。
`atk run-skill`のClaude起動も統括担当の使用順を使う。
ユーザーが直接起動するClaude Code・Codex、組み込みのサブエージェント、Codexの`atk run-skill`、`atk commit`は対象外である。

| 種別 | 接続 | 用意するもの |
| --- | --- | --- |
| `subscription` | サブスクリプション（ClaudeのPro・Max、ChatGPTのプラン） | Claudeは`claude auth login`、Codexは`codex login`によるChatGPTログイン |
| `credits` | 有限のAPIクレジット | ClaudeはAPI接続のenvファイル、Codexは`config.toml`のprovider定義 |
| `payg` | 従量課金API | `credits`と同じ形式で、接続先とキーを別に用意する |

Claudeの認証には[種別ごとのenvファイル](#認証の用意)を用意する。
画面を開けない場合は`atk config set <キー> <値>`で保存する。

| キー | 内容 | 初期値 |
| --- | --- | --- |
| `codex_credits_provider`・`codex_payg_provider` | Codexの`config.toml`の`model_providers`に定義したprovider ID（`openai`も指定できる） | 空 |
| `claude_orchestrator_connections`・`codex_orchestrator_connections` | 統括担当の使用順 | `subscription,credits` |
| `claude_worker_connections`・`codex_worker_connections` | 委譲先の使用順 | `credits,subscription` |

使用順は種別名を重複なしでカンマ区切りに並べ、先頭から評価する。
認証を設定していない種別は候補から除くため、使える接続が1〜2種類の環境でも同じ使用順のまま使える。
従量課金は追加料金が発生するため初期値の使用順に含めず、使用順へ`payg`を書いた役割だけが使う。

- エンジンごとに使用順を設定せず、API認証も用意していない場合は、親の環境変数と通常の認証（ClaudeはAPIキー・Bedrock・Vertexなど、Codexは`config.toml`の通常設定）をそのまま使い、別の接続へ切り替えない。
- 接続を設定していて、ある役割で使える接続が1つも無い場合は、原因と設定方法を表示して起動を止める。
- 空・不在・不正なenvファイルの種別は候補から除く。
  使用順にあるファイルが空か不正なら対象パスと修正方法を表示する。
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
利用上限で中断した場合は、起動元の`atk wi process-loop`が旧CLIの終了を確認し、
次の利用可能な接続で同じ会話を圧縮してから再開する。
手動で圧縮する必要はない。
代替がない場合の待機には既存の待機設定を使う。
Codexの統括担当は起動時に使用順の先頭の使える接続を選び、会話の途中では切り替えない。

継続・再開する会話は、Claude統括の利用上限時の切替を除き、選んだ接続を使い続ける。
使用順からその種別を外した場合は、次の継続で使用順の先頭の使える接続へ移り、外した種別へは戻らない。
利用上限で`subscription`から移ったClaudeの委譲先は、使用順で`subscription`が前にあれば、解除予定時刻の後の最初の継続で`subscription`へ戻る。
Codexの会話は移った接続を使い続け、新しい会話だけを使用順の先頭から評価する。

`AGENT_TOOLKIT_CONFIG_<キー名の大文字>`の環境変数を設定すると保存値より優先するため、設定を変える場合はこの変数も確認する。
切替と保持の詳細は[design-agents-runtime-connections.md](../development/design-agents-runtime-connections.md#api失敗過負荷利用上限の待機)を参照。

### 共有設定への移行

共有にするキーのローカル値が残っていると、`set`・`apply-preset`・設定画面の共有更新は移行を案内して止まる。
移行の手順は[共有設定への移行](claude-code-settings.md#共有設定への移行)を参照。

`set` で保存した値が環境変数や一時指定に覆われると、設定元と解除方法を案内して非0で終わる。
覆っている設定を解除すれば再保存せずに `get` で保存値を確かめられる。

### 緊急時だけ従量課金を許可する

通常は`credits,subscription`を使い、追加支出が必要なときだけ接続使用順と上限待機方針を一時指定する。

```sh
atk config temporary set claude_worker_connections credits,subscription,payg
atk config temporary set codex_worker_connections credits,subscription,payg
atk config temporary set claude_orchestrator_connections subscription,payg
atk config temporary set claude_usage_limit_wait never
atk config temporary set codex_usage_limit_wait never
atk config temporary show
```

必要なAPI認証は既存のローカル接続定義を使う。
仕事の社内プロキシの実値や認証情報はリポジトリへ保存しない。
設定変更だけで実行中CLIの接続は変わらず、次の継続境界で適用する。
Claude統括が上限で停止した場合は、旧CLIの終了確認後に同じ会話IDで次の接続へ再開する既存経路を使う。
上限待機中の巡回を手動で再開する場合は、所有する巡回を停止して終了を確認し、`atk wi process-loop resume SESSION_ID`で同じ会話の再開を予約する。
巡回が停止中なら `atk wi process-loop` で起動する。
緊急指定を解除する操作は`atk config temporary clear`であり、その時点の通常設定へ戻る。
モデルの一時指定だけでは、従量課金やresetの消費を許可しない。

### 委譲先のモデルを指定して切り替える

作業を依頼しているエージェントへ、委譲先と切り替え先のモデルを指示する。
前任の終了後に`start`の`handoff_from`に前任の識別子を渡して引き継ぐ。
前任が候補列を直接指定していた場合は、`model_type`に前任の役割の工程名（役割がなければ起動区分の既定の工程名）を指定する。
許可された例外として候補列を直接渡す場合は、`model_type`に候補列、`model_override`に区分、`model_override_evidence`にその根拠を指定する。
前任の会話を要約するモデルは呼ばず、新しい会話へ記録を渡す。
切替元・切替先はClaude・Codex・Antigravity・OpenCodeに対応する。

### 同時に動かす委譲先の上限

`agents_server`は、マシン全体で作業中の委譲先の数が上限に達すると、新しい起動と止まった委譲先への追送を空きが出るまで待たせる。
役割・エンジン・起動ディレクトリを問わず合算する。
子の結果を`atk agents wait`で待っている委譲元と、子の起動が待たされている委譲元は数えない（委譲元の枠で子や孫の起動が止まらないようにするため）。
上限は起動を許可する時点で判定する。
許可の後に依頼元が待機へ入らず作業を続けると、依頼元1件につき最大1件だけ一時的に上限を超える。
上限は既定で論理CPU数の2.5倍で、`atk config set agents_concurrency <値>`で変えられる。
値は係数（例: `3x`）、絶対値（例: `12`）、`off`（制限しない）のいずれかとする。
待たされた起動は、起動結果の`admission_wait_seconds`に待った秒数が入る。
止まった委譲先への追送で待った場合は、`atk agents list`と`show`の最後の操作に待った秒数が現れる。

### 認証の用意

ClaudeのAPI接続は、接続用の環境変数を種別ごとのenvファイルへ保存する。
Anthropicへ直接接続する場合は、[Claude ConsoleのAPI keys](https://platform.claude.com/settings/keys)で発行したキーを`ANTHROPIC_API_KEY`へ記入し、Consoleで課金設定も用意する。
`atk config get config_dir`が返すディレクトリに、APIクレジットなら`claude-credits.env`、従量課金APIなら`claude-payg.env`を作る。

```dotenv
ANTHROPIC_API_KEY=YOUR_API_KEY
```

形式は1行1つの`KEY=VALUE`で、`export`、引用符、`#`のコメント行も使える。
別の接続先を使う場合は、提供元が指定するURLを`ANTHROPIC_BASE_URL`へ、Bearer認証のトークンを`ANTHROPIC_AUTH_TOKEN`へ記入する。
`ANTHROPIC_AUTH_TOKEN`は`ANTHROPIC_API_KEY`より優先するため、直接接続用のファイルにはAPIキーだけを書く。
変数の意味は[Claude Codeの認証仕様](https://code.claude.com/docs/en/authentication#authentication-precedence)を参照。

ファイルはGit管理外へ置き、Linux・macOSでは`chmod 600 <ファイル>`で自分だけが読み書きできるようにする。
Windowsではファイルのプロパティのセキュリティ設定で、使用するOSアカウントだけに読み取り権限を与える。
envファイルは新しい会話の開始・再開時に読む。
ユーザー設定（`~/.claude/settings.json`）などのsettingsの`env`へ接続関連の変数（`ANTHROPIC_API_KEY`、`AWS_PROFILE`など）を書いた場合、委譲先とオーケストレーターが実行するコマンドにはその変数が空の値で渡る。

CodexのAPI接続は、`config.toml`へ接続先とAPIキーの環境変数名をproviderとして定義する。
定義の書き方は[Codex利用ガイド](codex-guide.md#codexのサブスクとapi接続先)を参照。
以降の設定例では、APIクレジットのproviderを`my-credits`、従量課金APIのproviderを`my-payg`という名前で定義した場合を示す。

### 旧設定からの移行

`claude_api_env_file`・`claude_orchestrator_api_only`・`claude_worker_api_first`・`claude_api_mode`・`codex_model_providers`は廃止した。
保存済みの値は、次に`atk config`を実行するか委譲先を起動した時に新しいキーへ自動で移し、旧キーを削除する。

- 旧`claude_credits_env_file`・`claude_payg_env_file`で指定したファイルは、種別ごとの固定名へ移る。
  環境変数で旧パスを指定していた場合は、移行後にその変数を解除する。
  さらに古い`claude_api_env_file`の保存値は旧`api-credits`ならクレジット、それ以外なら従量課金の固定名へ移り、役割ごとの方針を使用順へ移す。
- Codexの`openai`は`subscription`、それ以外のprovider IDは`codex_payg_provider`へ移り、元の順序を両役割の使用順へ書く。
  `openai`以外のprovider IDが2つ以上ある場合は自動で移さず、新しいキーの設定を求めるエラーを表示する。
- Codexの移行はアカウント種別を見ないため、APIキーでログインして`openai`を並べていた環境は、移行後の診断に従って`codex_payg_provider`か`codex_credits_provider`へ`openai`を設定し直す。

`atk config set`・`get`へ旧キーを渡した場合は、移行先を示すエラーで終わる。
