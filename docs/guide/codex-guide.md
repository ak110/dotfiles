# Codex利用ガイド

Codexはagent-toolkitの標準構成に含まれ、単体インストーラーがCodexプラグインと共有スキルを設定する。
概要と導入・初回確認は[agent-toolkitのREADME](../../agent-toolkit/README.md)、共通の詳細操作は[agent-toolkit利用ガイド](claude-code-guide.md)を参照。
本書はCodexに固有の操作と注意をまとめる。

単体インストーラーは既存の`~/.codex/AGENTS.md`を保護するため、dotfiles固有のグローバル`AGENTS.md`と共有リンクを配置しない。
dotfilesユーザーは`update-dotfiles`または`chezmoi apply`で、Codex向け`AGENTS.md`（dotfilesの個人用の規範）、スキルのリンク、プラグインをまとめて設定する。
agent-toolkitの規範は`AGENTS.md`へ埋め込まず、SessionStartフックが入口のスキル`atk-mode`の起動を求める。
配布される構成は[architecture-agents-codex.md](../development/architecture-agents-codex.md#codex向けの配布構成)を参照。

## Codexで`atk wi process-loop`を使う

統括担当をCodexにする場合は、起動前に候補列を設定する。
起動・停止・再開などの操作は[agent-toolkit導入ガイド](claude-code-workflows.md#推奨ワークフロー)と共通である。

```bash
atk config set orchestrate_model codex:sol/medium,claude:opus[1m]/medium
atk wi process-loop
```

process-loopはCodexの承認方針とsandbox設定を引き継ぐ。
Windowsでは親の監視ループをCtrl+Cで終了できる。
自動で終了できない環境では、完了報告の後に`/exit`を入力して監視ループへ戻る。

## モデル別ランチャー

dotfilesの`astra`、`sol`、`terra`は、Codex自身が返すモデル一覧から、対応する系列の利用可能な最新版を選ぶ。
`--model VALUE`、`--model=VALUE`、`-m VALUE`で指定すればそのモデルを使い、一覧を取得しない。
`--`以降のプロンプトはモデルの指定に使わない。
ヘルプ、版の表示、補完、ログインなどの管理操作にも一覧は不要である。

系列を解決できない場合は、起動時の接続とログインを確認するか、完全なモデルIDを明示する。
外部App Serverに接続する`--remote`では同じ外部接続先から一覧を取得する。
接続先の一覧を利用できない場合や、現行Codexの一覧機能では指定のprofileや設定無視条件を再現できない場合は、完全なモデルIDを明示する。
OSS providerを選んだ場合は、そのprovider向けのモデルを指定する。
外部接続の`codex exec`はモデル指定を受け付けない。
外部接続でモデルを選ぶ場合は対話起動を使う。

## daemon再起動後の作業継続

dotfilesで導入した通常の`codex`、`codex-medium`、`codex-xhigh`は、管理daemonが再起動しても同じ端末・会話・作業ディレクトリで進行中の仕事を自動で続ける。
特別な起動コマンドは不要で、通常終了・Ctrl+C・完了済みの仕事は自動で再実行しない。
権限と承認の方針を保持し、再起動前の承認要求への応答を新しい要求へ転用しない。
新しい承認や認証が必要な場合は、Codexの表示に従って操作する。

`atk run-skill`とprocess-loopの非対話Codex、および`agents_server`の専用App Serverは、共有daemonとは独立した実行を継続する。
再開した親の子孫は保存済みの委譲関係から復旧し、元の親へ結果を届ける。
自動復旧が失敗した場合は表示された診断を確認し、`codex doctor`で状態を調べてから`codex resume`で元の会話を選ぶ。
明示した`--remote`や`--no-daemon`の接続管理は、その指定に従う。
Windowsで初めて起動管理を導入した後は、新しい端末を開いて更新されたPATHを使用する。

## プラグイン更新の反映

Codex向けプラグインは`update-dotfiles`または`chezmoi apply`で更新する。
実行中のセッションは更新前の版を使い続けるため、更新後は新しいセッションで新版を使う。
更新前の版は、次の版の導入から7日を過ぎると以降の更新で削除する。
7日より前に置き換わった版で始めた会話を再開すると、会話の履歴が指す旧版のスキルなどのファイルを読めないため、新しいセッションで続ける。
agent-toolkitの追加・更新後は、利用セッションの有無にかかわらず稼働中のapp-server daemonを自動で再起動し、[既存の復旧処理](#daemon再起動後の作業継続)へ接続する。
変更が無い場合とdaemonが停止している場合、自動再起動は実行しない。
Linuxの`update-dotfiles`による適用前の一時停止も、適用差分があり利用セッションが無い場合に限る。

Codexのコマンド実行から`update-dotfiles`を起動すると、`CODEX_THREAD_ID`を使って更新を独立したjobへ分離する。
起動元がdaemon再起動で終了しても、jobは更新処理を継続して出力と終了コードを保存する。
再開した同じ会話では次のコマンドで最新の自己jobの結果を確認できる。

```bash
update-dotfiles job
```

別の端末や会話からは、開始時に表示されたUUIDを指定して`update-dotfiles job <UUID>`を実行する。
これらのコマンドは保存済みの出力と終了状態を取得し、更新を再実行しない。
未完了の場合は表示された同じUUIDで後から再取得する。

更新が延期または失敗した場合は、`update-dotfiles logs`で原因と次の操作を確認し、表示された操作（Codexの終了と`update-dotfiles`の再実行など）を行う。

更新後にdaemonの再起動を案内された場合は、Codex pluginまたはremote controlを使うセッションを終了してから次のコマンドを実行する。

```bash
codex app-server daemon restart
```

再起動が失敗した場合だけ、`codex app-server daemon start`でdaemonを起動する。

## フックの信頼確認

Codexはplugin同梱フックの定義が変わると、ユーザーが変更後のフックを再び信頼するまでそのフックを実行しない。
更新処理が信頼を求める案内を表示した場合は、次の手順でフックを信頼する。

1. Codexの会話の入力欄に`/hooks`を入力して実行する。
2. agent-toolkitの未信頼・変更済みフックを選び、表示された定義を確認して信頼する。
3. そのセッションを終了し、新しいセッションを開始する。
4. 新しいセッションで「起動時に`atk-mode`の起動を求める通知を受け取ったか確認して」とエージェントへ尋ねる。

`/hooks`はCodex内の操作であり、PowerShellやbashへ入力するコマンドではない。
入口の起動を求めるSessionStartフックはセッション開始時に実行されるため、信頼した後にもう一度新しいセッションを開始する。
永続的な信頼登録の公開手段は確認できていないため、この確認と信頼操作は手動で行う。
daemonの自動再起動の条件は[プラグイン更新の反映](#プラグイン更新の反映)を参照。

CodexのStopフックは説明言語だけを判定し、完了報告の起動忘れは通知しない。
フックの判定内容とClaude Codeとの差は[design-hooks-host-support.md](../development/design-hooks-host-support.md#フックごとの処理とcodexの対応範囲)を参照。

## CodexのサブスクとAPI接続先

`agents_server`のCodex委譲先と、Codexで動く`atk wi process-loop`の統括担当は、サブスクリプション・APIクレジット・従量課金APIを役割ごとの使用順で使い分けられる。
使用順と設定例は、Claudeと共通の[接続の使い分け](claude-code-connections.md#接続の使い分け)を参照する。
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
待つ範囲は`codex_usage_limit_wait`で選ぶ（[工程別モデル設定](claude-code-models.md#工程別モデル設定)を参照）。

`agents_server`のCodex委譲先では、`codex_auto_reset`を明示的に有効にすると、ChatGPTの週次枠の使用率が99%以上でreset creditの在庫がある場合に1個を消費する。
初期値は`false`である。
設定の確認、有効化、無効化は次のコマンドで行う。

```bash
atk config get codex_auto_reset
atk config set codex_auto_reset true
atk config set codex_auto_reset false
```

有効な間はモデルを呼ばずに利用枠を確認し、reset後に利用枠を再取得して同じsessionの再開を判定する。
APIキー接続は対象外で、アカウントのemailを取得できない場合もcreditを消費せず診断を記録する。
在庫が無い場合やresetが成立しない場合は、既存の利用上限待機設定に従う。

ユーザーが端末で`codex`を実行して開いた対話画面は、利用上限の解除後に自動では再開しない。
解除後に新しい発話を送って続ける。

## Codex CLI本体

dotfilesユーザーでは、`chezmoi apply`後の処理がCodexの公式インストーラーを非対話で実行し、スタンドアローン版を導入・更新する。
置き換えたmiseのnpm版とPATH上のnpm版のCodexは自動で除去する。
認証情報、設定、セッションは除去しない。
Windowsでは、公式インストーラーが導入したCodexが実行中の場合は停止せず、導入、更新、旧版の整理を次回へ延期する。
公式インストーラーは、導入する版の配置が不完全な場合にその版を削除して入れ直すため、実行中のファイルと衝突し得る。
除去するmise版・npm版のCodexが実行中の場合は、その版の除去だけを次回へ延期する。
権限が無く実行ファイルの所在や起動引数を読めないプロセスも、書き換える版から動いている可能性を否定できないため延期の理由にする。
延期の表示は、原因のプロセスのPIDと読めた実行パスを示し、実行を確かめたのか否定できないだけなのかを区別する。
除去を延期した場合は、表示されたプロセスが除去する版なら終了してから、表示された削除コマンドをPowerShellで実行して除去する。
除去するまで旧版がPATHで先に見つかる環境では、表示された更新済みの起動先（`dotfiles\bin\codex.cmd`）をフルパスで起動する。
npmはnpm版の除去で同名の起動ファイル（`codex.cmd`など）も削除する。
そのうちnpmが作ったままと確かめられないファイル（ユーザーが置いたラッパー、npmが作ったものに行や起動オプションを足したもの、参照先を別のスクリプトや版の切替用のリンクに変えたリンク、Windowsで置いたリンク）がある場合は、npm版を自動では除去せず、そのファイルと削除コマンドを表示する。
残すファイルを別の場所へ移してから、表示された削除コマンドを実行して除去する。
npm版の`package.json`を読めない場合は、除去する起動ファイルを確定できないため自動では除去せず、そのパスを表示する。
同じ版の`package.json`を復元するか読取権限を直してから、`update-dotfiles`を再実行する。
npm版の本体か、同じ場所のnpmが無いのに起動ファイル（`codex.ps1`など）が残っている場合は、自動では削除せず、そのファイルと正規の起動先を表示する。
意図して置いたラッパーなら保持する。
起動に失敗するか不要なら、別の場所へ移すか削除し、表示された正規の起動先で起動する。
npm版との関係を確かめられない起動ファイル（PATH上の別の場所に置いた、npm版の起動ファイルへのリンクなど）も、自動では削除せず、そのファイルと正規の起動先を表示する。
表示されたファイルがPATHで先に見つかる間は、正規の起動先ではなくそのファイル（旧版など）が起動するため、不要と確かめたら別の場所へ移す。
mise版・npm版の導入先を照会できない場合はその版の除去を見送り、失敗したコマンドと診断を表示する。
app-server daemonとその更新処理、デスクトップアプリ、npxで起動したCodexは、導入と除去が書き換えない場所から動くため、実行中でも延期の理由にしない。

WindowsではPowerShell 7の利用を推奨する。
Windows PowerShellでは、環境によって公式インストーラーが使う`Get-FileHash`を解決できず、導入に失敗する。

`CODEX_HOME`を設定する場合は、インストーラーの実行前にそのディレクトリを作成しておく。
実行時に`CODEX_HOME`で設定・認証・会話の保存先を切り替えても、導入済みのCodex本体を使用する。
`CODEX_INSTALL_DIR`を設定すると、`codex`コマンドをそのディレクトリへ配置する。

### 完成済みパッケージの整理

次のコマンドで、蓄積したCodex本体とapp-server daemonの旧パッケージを整理できる。

```bash
prune-codex-packages
```

現在選択されている版、直前に選択されていた版、稼働中のCodexが使っている版は保持する。
WindowsでCodexのセッションが実行中の場合は、整理を次回へ延期する。
常駐するapp-server daemonと更新処理は延期の理由にせず、使っている版を保持して整理する。
使用中だった旧版は、使用終了後に同じコマンドを再実行すると整理できる。
整理はCodex本体の導入後と自動更新の周期にも実行される。
失敗した場合は出力に示された原因を解消して再実行する。
引数は`prune-codex-packages --help`で確認できる。
