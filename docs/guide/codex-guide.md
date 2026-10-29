# Codex利用ガイド

Codexはagent-toolkitの標準構成に含まれ、単体インストーラーがCodexプラグインと共有スキルを設定する。
導入と共通の操作は[agent-toolkit導入ガイド](claude-code-guide.md)を参照し、本書はCodexに固有の操作と注意をまとめる。

単体インストーラーは既存の`~/.codex/AGENTS.md`を保護するため、dotfiles固有のグローバル`AGENTS.md`と共有リンクを配置しない。
dotfilesユーザーは`update-dotfiles`または`chezmoi apply`で、Codex向け`AGENTS.md`、共有ルール、スキルのリンク、プラグインをまとめて設定する。
配布される構成は[architecture.md](../development/architecture.md#codex向けの配布構成)を参照。

## Codexで`atk wi process-loop`を使う

統括担当をCodexにする場合は、起動前に候補列を設定する。
起動・停止・再開などの操作は[agent-toolkit導入ガイド](claude-code-guide.md#推奨ワークフロー)と共通である。

```bash
atk config set orchestrate_model codex:sol/medium,claude:opus[1m]/medium
atk wi process-loop
```

process-loopはCodexの承認方針とsandbox設定を引き継ぐ。
Windowsでは親の監視ループをCtrl+Cで終了できる。
自動で終了できない環境では、完了報告の後に`/exit`を入力して監視ループへ戻る。

## プラグイン更新の反映

Codex向けプラグインは`update-dotfiles`または`chezmoi apply`で更新する。
実行中のセッションは更新前の版を使い続けるため、更新後は新しいセッションで新版を使う。

更新が延期または失敗した場合は、`update-dotfiles logs`で原因と次の操作を確認し、表示されたCodexを終了してから`update-dotfiles`を再実行する。

更新後にdaemonの再起動を案内された場合は、Codex pluginまたはremote controlを使うセッションを終了してから次のコマンドを実行する。

```bash
codex app-server daemon restart
```

再起動が失敗した場合だけ、`codex app-server daemon start`でdaemonを起動する。

更新時に接続を切断してよい場合は、daemonを自動で再起動できる。
PowerShellでは`$env:DOTFILES_CODEX_DAEMON_AUTO_RESTART = "1"`を設定してから`update-dotfiles`を実行する。

```bash
DOTFILES_CODEX_DAEMON_AUTO_RESTART=1 update-dotfiles
```

## フックの信頼確認

Codexはplugin同梱フックの定義が変わると、ユーザーが変更後のフックを再び信頼するまでそのフックを実行しない。
更新処理が信頼を求める案内を表示した場合は、新しいCodexセッションで`/hooks`を実行し、agent-toolkitのフックの定義を確認して信頼する。
信頼した後は新しいセッションを開始する（規範を読み込むフックは新しいセッションの開始時に動く）。

CodexのStopフックは説明言語だけを判定し、完了報告の起動忘れは通知しない。
フックの判定内容とClaude Codeとの差は[design-hooks.md](../development/design-hooks.md#フックごとの処理とcodexの対応範囲)を参照。

## CodexのサブスクとAPI接続先

`agents_server`のCodex委譲先と、Codexで動く`atk wi process-loop`の統括担当は、サブスクリプション・APIクレジット・従量課金APIを役割ごとの使用順で使い分けられる。
使用順と設定例は、Claudeと共通の[接続の使い分け](claude-code-guide.md#接続の使い分け)を参照する。
本節はCodex固有のprovider定義だけを扱う。

サブスクリプション（`subscription`）ではCodexへ`codex login`でChatGPTログインし、provider `openai`を使う。
APIクレジット（`credits`）と従量課金API（`payg`）ではCodexの`config.toml`に接続先とAPIキーの環境変数名をproviderとして定義し、APIキーをその環境変数へ設定する。
2つのAPIは接続先とキーが別のため、providerも別の名前で定義する。

```toml
[model_providers.my-credits]
name = "my-credits"
base_url = "https://credits.example.com/v1"
wire_api = "responses"
env_key = "MY_CREDITS_API_KEY"
requires_openai_auth = false

[model_providers.my-payg]
name = "my-payg"
base_url = "https://payg.example.com/v1"
wire_api = "responses"
env_key = "MY_PAYG_API_KEY"
requires_openai_auth = false
```

```bash
export MY_CREDITS_API_KEY='<APIクレジットのキー>'
export MY_PAYG_API_KEY='<従量課金APIのキー>'
atk config set codex_credits_provider my-credits
atk config set codex_payg_provider my-payg
```

APIキーでCodexへログインしている場合は、`openai`を`codex_payg_provider`（クレジットなら`codex_credits_provider`）へ設定する。
API料金は接続先の課金規則に従い、同じ会話の継続にも発生する。
ChatGPTのtokenをAPIキーとして使わない。
候補が使えない場合は診断を確認し、`config.toml`の接続先定義または認証を修正してから再試行する。
直接起動したCodexと`atk run-skill`の接続先は変えない。

## 利用上限後の再開

`atk run-skill`、`atk wi process-loop`、`agents_server`の委譲先は、Codexの利用上限の解除後に自動で作業を続ける。
待つ範囲は`codex_usage_limit_wait`で選ぶ（[工程別モデル設定](claude-code-guide.md#工程別モデル設定)を参照）。

ユーザーが端末で`codex`を実行して開いた対話画面は、利用上限の解除後に自動では再開しない。
解除後に`/goal resume`か新しい発話を送って続ける。

## Codex CLI本体

dotfilesユーザーでは、`chezmoi apply`後の処理がCodexの公式インストーラーを非対話で実行し、スタンドアローン版を導入・更新する。
置き換えたmiseのnpm版とPATH上のnpm版のCodexは自動で除去する。
認証情報、設定、セッションは除去しない。
WindowsでCodexが実行中の場合は停止せず、導入、更新、旧版の整理を次回へ延期する。

WindowsではPowerShell 7の利用を推奨する。
Windows PowerShellでは、環境によって公式インストーラーが使う`Get-FileHash`を解決できず、導入に失敗する。

`CODEX_HOME`を設定する場合は、インストーラーの実行前にそのディレクトリを作成しておく。
`CODEX_INSTALL_DIR`を設定すると、`codex`コマンドをそのディレクトリへ配置する。

### 完成済みパッケージの整理

次のコマンドで、蓄積したCodex本体とapp-server daemonの旧パッケージを整理できる。

```bash
prune-codex-packages
```

現在選択されている版と、稼働中のCodexが使っている版は保持する。
使用中だった旧版は、使用終了後に同じコマンドを再実行すると整理できる。
整理はCodex本体の導入後と自動更新の周期にも実行される。
失敗した場合は出力に示された原因を解消して再実行する。
引数は`prune-codex-packages --help`で確認できる。
