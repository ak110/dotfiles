# Codex利用ガイド

Codexはagent-toolkitの標準構成に含まれる。単体インストーラーはCodexプラグインと共有スキルを設定する。
`agents_server` MCPはClaude CodeとCodexの双方へ共有され、工程別モデル設定の`model_type`で委譲先を選択する。

単体インストーラーは既存の`~/.codex/AGENTS.md`を保護するため、dotfiles固有のグローバル
`AGENTS.md`と共有リンク群を展開しない。dotfilesユーザーは`update-dotfiles`または`chezmoi apply`により、
Codex向け`AGENTS.md`、共有ルールの同期、共有スキルのリンク、プラグインを一括設定する。
更新の反映は「プラグイン更新の反映」の手順に従う。

## `atk wi process-loop`によるAWIの継続処理

次のコマンドはCodexの対話UIを起動し、対象リポジトリのAWIを継続して処理する。

```bash
atk config set orchestrate_engine codex,claude
atk wi process-loop
```

開始時点の候補を固定し、明示追加と即時対応で確定した差分を同じセッションへ加える。
告げられていない新着と振り返りAWIは次のセッションへ残す。
ready項目がなくなると、完了報告の後にセッションを自動で終了し、親の監視ループへ戻る。
自動で終了できない環境では対話UIに終了案内が表示されるため、`/exit`を入力して親の監視ループへ戻す。

初回と0件待機からの処理再開時は、private-notesを同期し、ready項目があれば
`update-dotfiles`とprivate-notesの再同期を終えてからCodexを起動する。
同期に失敗した場合はCodexを起動せず、変更検知を待って再試行する。

開始前は別セッションの活動に応じて10分を猶予し、新規WIの到着を検出するたびに検出時点から10分へ延長する。
対象は共有private-notesの新規AWIとUWIで、既存項目の編集・状態移動は延長に数えない。
延長の回数・累計時間に上限はなく、活動がなくなれば期限前でも開始判定へ戻る。
当該ループと子孫、終了済み、稼働中の子がないユーザー応答待ちは除き、別ループや別cwdの活動も調べる。
対象集合に未回答UWIがある場合は、保存済みUWIの最終更新から10分か全回答まで抑止する。
保留中（`hold`）の確認項目と、スキルの単発実行（`atk run-skill`）が登録した確認項目も判定に含める。
回答を終えて採用済み（`adopted`）へ移った確認項目も、保存済みの最終更新を判定に使う。
両条件を同時に評価し、解除後に候補を再確認する。
待機理由・期限・延長・開始は端末とログで確認できる。

ホームから登録した複数リポジトリを処理する場合は、次のように起動する。

```bash
atk config set process_loop_repositories '["/home/user/project-a", "/home/user/project-b"]'
atk wi list --configured-repos
atk wi process-loop all-repos
```

共通オプションは`atk wi process-loop --no-update all-repos`のように`all-repos`の前へ置く。
空登録は待機し、登録外へのWI投入はできる。
登録先の解決失敗は診断に従って設定を直し、同じoriginの重複は先頭の登録を使う。
取得済み候補を各対象cwdの通常delegateへ渡し、対象の選定・規範・prepare・実装・Git・公開は各担当が行う。
オーケストレーターは担当とユーザーの間を中継し、候補数とレーン上限、通常起動との処理所有は対象別に扱う。
開始猶予は登録対象全体へ作用し、開始後の1担当の失敗・所有待ちは別担当の進行を止めない。
登録変更は次の取得へ反映し、進行中の処理対象WIは維持する。
`abort`・`status`・`instruct`はOSアカウント単位で使う。
稼働状態は`atk wi process-loop status`、停止は`atk wi process-loop abort`で操作する。
次の処理への指示は`atk wi process-loop instruct '適用する指示'`で渡す。
`instruct`は単一起動では次の処理会話へ1回だけ渡る。
all-reposでは次の起動集合で1回受領し、各担当へ中継して、その担当の対象に関する部分を適用する。
all-reposの`--resume`は保持した担当session IDを指定する。

1レーンでも専用worktreeと独立文脈レビューを保ち、`atk lane create`・`integrate`・`delete`で準備・統合・回収を扱う。
対象のWorktreeCreate/Removeを資源操作へ接続し、設定済みhookの失敗では途中資源を保持する。
統合と計画保存・WI終端・統合済み記録を同じ操作で行い、失敗後は同じ入力で残工程から再開する。
共通の運用は[推奨ワークフロー](claude-code-guide.md#推奨ワークフロー)、担当への受渡しは[all-repos-orchestration.md](../../agent-toolkit/skills/process-wi/references/all-repos-orchestration.md)を参照。

process-loopはCodexの承認方針とsandbox設定を上書きせず、ユーザーのCodex設定を継承する。
WindowsではCodexを親の監視ループと別のプロセスグループで起動するため、
Codexの実行中もCtrl+Cで親の監視ループを終了できる。
また、process-loop内のCodexに限り、Git for Windowsを介してbash形式のplugin hookへ
Windows絶対パスを渡す。Claude、`update-dotfiles`、process-loop外のCodexのPATHは変更しない。

## プラグイン更新の反映

Codex向けのプラグインは`update-dotfiles`が生成して導入する。生成物の構成は[architecture.md](../development/architecture.md)「agent-toolkitの3形式配布」を参照。

`update-dotfiles`は未導入、disabled、version不一致のいずれかの場合に`codex plugin add`を実行し、導入後のversionと有効状態を再確認する。
euryaleで`DOTFILES_CODEX_DAEMON_AUTO_RESTART=1`を指定せずに更新する場合は、同じユーザーのCodexが稼働中なら、導入済みagent-toolkitへの追加・更新を延期する。
Linuxでは、Codexの診断ログDBを共有メモリーから通常ストレージへ戻す復元も、同じユーザーのCodexが稼働中なら延期する。
稼働判定の対象は、通常のCodexセッション、`agents_server`が委譲用に起動する`codex app-server --stdio`、およびapp-server daemon（`codex app-server daemon start`が起動する管理daemon）とその補助プロセスである。
管理daemonの停止後も残る更新ループ`codex app-server daemon pid-update-loop`はpluginと診断ログDBを開かないため、対象に含めない。
未導入plugin、他ホストの更新と、後述の自動再起動を明示した更新は、plugin更新の延期の対象に含めない。

### 管理daemonだけが残る場合の一時停止

Codexを使っていなくても、管理daemonは自動で起動して残ることがある。
次の条件をすべて満たす場合、Linuxの`update-dotfiles`は`chezmoi apply`の直前に`codex app-server daemon stop`で管理daemonを一時停止する。

- 通常のCodexセッションと`agents_server`の`codex app-server --stdio`が無い
- `DOTFILES_CODEX_DAEMON_AUTO_RESTART=1`を指定していない

`atk wi process-loop`の待機中と子セッション開始前の更新は通常この条件に当たり、Codexを手動で停止しなくてもpluginと診断ログを更新する。
一時停止した管理daemonは、更新の成否にかかわらず終了前に`codex app-server daemon start`で起動し直す。
更新前から管理daemonが停止していた場合は起動しない。
遠隔接続機能（remote control）の状態は条件に含めないため、遠隔クライアントが管理daemon経由で利用中の作業も一時停止の間は中断され得る。
`codex app-server daemon enable-remote-control`で有効にした遠隔接続機能の設定は、再起動した管理daemonにも引き継がれる。
一時停止と再起動の結果は`update-dotfiles`の出力と`update-dotfiles logs`で確認できる。
停止または再起動に失敗した場合は`update-dotfiles`が非0で終了し、標準エラーと`update-dotfiles logs`に失敗の内容を表示する。
再起動に失敗した場合は、次のコマンドで管理daemonを起動する。

```bash
codex app-server daemon start
```

### Codexの利用中に延期した更新の反映

Codexのセッションまたは`agents_server`の委譲が残る場合と、`DOTFILES_CODEX_DAEMON_AUTO_RESTART=1`を指定した場合は、管理daemonを停止しない。
このとき`update-dotfiles`は停止しない理由を表示し、post-applyはplugin更新と診断ログ復元の延期を更新ログへ記録する。
延期中も旧版のスキル・MCP実体と有効状態を保持し、dotfiles本体、snapshot生成、Claude Codeと`atk-serve`の更新は続行する。
ウォームアップは導入済みの有効版を使い、disabledのまま延期した場合はCodex分を除く。

plugin更新を延期した場合は、延期の案内がpost-applyの完了案内と同期記録に残る。
同期記録は状態ディレクトリ（`atk config get state_dir`が返すディレクトリ）の`sync-report.json`であり、案内は`post_apply.notices`にある。
案内は対象plugin、導入版と目標版（再有効化だけを延期した場合は無効から有効への切り替え）、延期の理由になった稼働中のCodexおよび再実行の操作を示す。
dotfiles全体の同期が成功でも、この案内がある間はCodex pluginの最新版が反映されていない。
Codexを常に稼働させている間は延期が続き、最新版は適用されない。
延期した更新は、Codexのセッションと委譲を全て終了してから`update-dotfiles`を再実行すると反映される。
自動更新タイマーは上流変更が無ければpost-applyを実行しないため、停止後の次の周期に必ず反映されるわけではない。

### プラグイン更新後のdaemonの再起動

ローカルまたは外部のプラグインを実際に追加または更新した場合と、公開インストーラーで`codex plugin add`前後のversionまたはenabledが変化した場合、daemonの稼働状態を確認する。
`codex app-server daemon version`が成功した場合に限り、次の再起動コマンドを案内する。

```bash
codex app-server daemon restart
```

公開インストーラーでは、プラグイン追加または`atk`配置が失敗した場合も、エラーの後の最終行へ
必要な再起動コマンドを表示し、非0の終了状態を維持する。`agents_server` MCPの登録や
`~/.claude.json`のUser scope設定は行わない。
通常は進行中のセッションを保護するため、app-server daemonを自動再起動しない。
`update-dotfiles`によるagent-toolkitの追加・更新後に稼働中daemonを自動再起動する場合は、実行環境へ次の設定を明示する。

```bash
DOTFILES_CODEX_DAEMON_AUTO_RESTART=1 update-dotfiles
```

PowerShellでは同じ実行に対して次のように設定する。

```powershell
$env:DOTFILES_CODEX_DAEMON_AUTO_RESTART = "1"
update-dotfiles
```

自動再起動は、pluginの追加または更新、導入済みversionと有効状態、hook状態の確認がすべて完了した後に1回だけ実行する。
daemonが停止中の場合、pluginが無変更の場合およびmarketplace登録だけが変化した場合は実行しない。
再起動に失敗した場合は終了コードをupdate-dotfilesログへ記録し、手動再起動の案内へ戻る。
Codex pluginまたはremote-controlを利用する実行中セッションは、自動再起動によって接続が切断される可能性がある。
これらのセッションを終了できる時点でだけ、自動再起動を有効にする。

`DOTFILES_CODEX_DAEMON_AUTO_RESTART=1`を指定しない場合は、更新中のセッションを作業完了後に終了し、再起動案内が表示された場合はdaemonを再起動して新versionを利用する。

再起動案内は、ローカルと外部のいずれかのプラグインを実際に追加または更新し、daemonの稼働状態を確認できた場合だけ表示される。
daemonの未起動、状態確認の失敗、マーケットプレイスの登録だけの変化、公開インストーラーでの導入前後の状態の一致、
プラグイン追加前の処理と追加自体のいずれかの失敗、外部プラグインの導入済みなど、それ以外の場合は表示しない。
案内されたコマンドは、Codex plugin、remote-controlを利用する実行中セッションの終了後に実行する。
daemonを利用しない既存のCLI・IDEセッションは、作業完了後に新しいセッションを開始する。

## agents_serverによる委譲

`agents_server`はClaude CodeとCodexが共有する委譲用のMCPであり、工程別モデル設定（[agent-toolkit導入ガイド](claude-code-guide.md)の「工程別モデル設定」）に従って、CodexとClaudeのどちらで委譲先を起動するかを選ぶ。

`agents_server`がCodexで起動した委譲先は、`~/.claude/rules/`配下に置いた規範ファイル（サブディレクトリを含み、`~/.codex/AGENTS.md`が埋め込むものを除く）の本文を起動時の指示として受け取り、Claudeで起動した委譲先と同じ規範で作業する。Codexのメインとネイティブのサブエージェントはこの指示を受け取らず、`~/.codex/AGENTS.md`の読込指示で`~/.claude/rules/`直下の`*.local.md`と`~/.claude/CLAUDE.md`を読む。Codexのメインにも適用したい規範は`~/.claude/rules/`直下の`*.local.md`へ置く。

委譲の状態は`atk agents list`で一覧し、`atk agents wait`で終端と結果を受け取る。
公開ツールの入力と応答の契約は[design-agents-server.md](../development/design-agents-server.md)を参照。

### CodexのサブスクとAPI接続先

`agents_server`の接続先の選択順は、`atk config`の`codex_model_providers`だけで指定する。
未設定の場合は、Codexの`config.toml`の実効`model_provider`と認証に従い、APIへ自動移行しない。
空文字列を保存し、対応する環境変数を解除した場合も同じ動作になる。
列を指定した場合は先頭を優先し、後続を代替接続先として使う。Codex側の`model_provider`を同時に変更する必要はない。

サブスクだけを使う場合はCodexへChatGPTログインし、通常設定の`model_provider`を省略するか`"openai"`にする。
`atk config set codex_model_providers ''`で通常設定へ戻せる。
サブスクとAPIを併用する場合は、APIの定義だけをCodexの`config.toml`へ置く。

```toml
[model_providers.custom]
name = "my-server"
base_url = "https://my-server.example.com/v1"
wire_api = "responses"
env_key = "MY_SERVER_API_KEY"
requires_openai_auth = false
```

APIキーを環境変数`MY_SERVER_API_KEY`へ設定してホストを起動し、次のコマンドで選択順を指定する。
ChatGPTのtokenをAPIキーとして使わない。接続先の定義と認証はCodexが所有し、atkへ複製しない。

```bash
atk config set codex_model_providers 'openai,custom'
atk config get codex_model_providers
```

ChatGPTログインの`openai`を先に使い、確定した利用上限で`custom`へ同じ会話を移す。
APIだけを使う場合は`atk config set codex_model_providers 'custom'`とする。
APIを優先して別のAPIへ代替する場合は`'custom,another'`とし、各IDをCodexへ定義する。
標準OpenAI providerをAPIキー認証で使う場合はCodexの既存の認証選択に従う。
API会話からChatGPTへは戻さない。API主接続の認証・モデル不受理等も、後続APIだけを順に一度ずつ試す。

前後空白と重複を除き、空要素とID内空白は拒否する。
未定義または独立したAPI認証のない候補は除外し、全候補が使えなければ失敗で終端する。
指定外の通常接続は候補として使わない。
`AGENT_TOOLKIT_CONFIG_CODEX_MODEL_PROVIDERS`の空でない値は保存値より優先するため、解除時はこの変数も外す。

適用先は`agents_server`がCodexへ解決した全`mode`とその会話の継続である。
直接Codex、`atk run-skill`、`atk wi process-loop`の起動設定は変えない。
現在アカウントの確定した利用枠不使用、または終端した利用上限失敗だけでサブスクからAPIへ移す。
回復後は新規sessionだけが指定列の先頭へ戻り、既存API会話は列変更・設定解除・保持期限・stop・
サーバー再起動を跨ぐ再開でも選択済みAPIを使う。API料金は接続先の課金規則に従い、継続にも発生する。
切替の診断は人向けの`agents-server.log`へ記録し、委譲元の応答や継続指示へ加えない。

## 利用上限後の再開

`atk run-skill`のCodex実行は、指定スキルの実行中に利用上限で失敗すると解除予定まで待ち、`codex exec resume`で同じ会話を再開する。
待機時間は`--timeout`へ含めない。
`atk run-skill`と`atk wi process-loop`の候補判定では次の候補を先に試す。
全候補が失敗し、そのうちに利用上限の失敗がある場合は、最も早い解除予定まで待って先頭から判定し直す。
解除予定が不明なら300秒後に再確認する。

process-loopが起動したCodexの子は、上限で停止したことを親が検出して終了させ、次の反復の候補判定を経て作業を続ける。
新しい会話で保持中のレーン・WI・計画を読み、残る工程から再開する。
`agents_server`の委譲は前節の接続先移行と、終端後の引き継ぎ起動を使う。

ユーザーが端末で`codex`を実行して開いた対話画面では、解除後の自動再開は現在の対応範囲に含まれない。
解除後に`/goal resume`か新しい発話を送って続ける。
この直接起動の対話画面の自動再開は、解除後の実機観測と実装を別の作業で扱う。

## フックの信頼確認

Codexはplugin同梱フックの定義が変わると、ユーザーが変更後のフックを再び信頼するまで、そのフックを実行しない。
更新処理は先にapp-serverの`hooks/list`で登録状態を確認する。次の9イベントがすべて登録済みかつ有効で、`trustStatus`だけが`untrusted`の場合に限り、`/hooks`で定義を確認して信頼する案内を表示する。
登録が0件または不足している場合はmanifest・配布rootの問題であり、信頼不足として案内しない。
信頼後に新しいセッションを開始し、SessionStartの規範注入を確認する。
再信頼の操作だけではSessionStartの規範注入を検収できない。
プラグイン更新後は新しいCodexセッションで`/hooks`を実行し、agent-toolkitについて
次の9イベントが含まれることを確認する。他の有効pluginは、独自のイベントを追加する場合がある。

- `SessionStart`
- `SubagentStart`
- `PreToolUse`
- `PostToolUse`
- `PermissionRequest`
- `UserPromptSubmit`
- `Stop`
- `SubagentStop`
- `SessionEnd`

イベントごとの処理内容とClaude Codeとの対応差は、
[Claude Code利用ガイド](claude-code-guide.md)の「常時有効な仕組み」にある対応表を参照する。

表示内容を確認してフックを信頼する。
信頼後の`PreToolUse`は`apply_patch`が`uv.lock`などのlockfileを直接編集する場合、
`uv add`などのパッケージ管理ツールでの更新を促す通知を返す。
動作を確かめる場合は、`uv.lock`へ1行を加える変更を`apply_patch`で適用し、通知の有無を確認する。
Stopは`agent-toolkit:completion-report`の呼び忘れだけを判定する。成果作業（WI処理・AWI登録の開始、リポジトリの編集、計画・WIの保存、commit・push）を行ったのに、同じ作業で同スキルの`SKILL.md`を読んだ結果が無い場合に、`decision: "block"`と`reason`で起動と残りの工程への続行を案内する。読取パスの文字列や失敗した読取は起動とみなさない。報告本文は判定せず、起動後の工程の完遂と報告の内容は同スキルの規範に従う。対処と中止・待機の判断の記録は`agent-toolkit:completion-report`に従う。Stopは自動振り返りを起動しない。手動で振り返る場合は`$agent-toolkit:session-review`を実行する。通常の作業完了時は`agent-toolkit:completion-report`が条件を判定し、必要な場合だけ振り返りを起動する。

## Codex CLI本体

dotfilesユーザーでは、`chezmoi apply`後の処理がCodexの公式インストーラーを非対話で実行する。
未導入時はスタンドアローン版を導入し、導入済みの場合は最新版へ更新する。
管理対象パッケージは`~/.codex/packages/standalone/`へ配置される。
配置先を指定しない場合の可視コマンドの配置先はLinuxとmacOSで`~/.local/bin`、Windowsで`%LOCALAPPDATA%\Programs\OpenAI\Codex\bin`である。
`CODEX_HOME`を設定した場合、パッケージは`$CODEX_HOME/packages/standalone`へ配置される。
`CODEX_HOME`に指定するディレクトリは、インストーラーの実行前に作成する必要がある。
`CODEX_INSTALL_DIR`を設定した場合、可視コマンドはそのディレクトリへ配置される。
WindowsではPowerShell 7の利用を推奨する。
導入処理は`pwsh`を優先し、見つからない場合は`powershell`へフォールバックする。
Windows PowerShellでは、環境によって公式インストーラーが使用する`Get-FileHash`を解決できず、導入に失敗する。

スタンドアローン版の起動を確認した後、mise npmバックエンドの全版を除去する。
PATHから解決される非正規npm版も、package帰属を確認したうえで除去する。
PATH外の非アクティブNode環境は自動削除しない。
認証情報、設定、セッションは除去しない。
WindowsでCodexが実行中の場合は停止せず、導入、更新、旧版の整理を次回へ延期する。

### 完成済みパッケージの整理

次のコマンドで、既に蓄積したCodex本体とapp-server daemonの完成済み旧パッケージを整理できる。

```bash
prune-codex-packages
```

対象は`CODEX_HOME`を設定した場合はその配下、未設定の場合は`~/.codex`配下の`packages/standalone/`と`packages/app-server-daemon/`である。
現在選択されている版と、稼働中のCodexや補助プロセスが使っている版を保持する。
使用中だった旧版は、使用終了後に同じコマンドを実行すると整理できる。
WindowsでCodexが実行中の場合は整理を延期するため、セッションと委譲を終了してから再実行する。

整理はCodex本体の導入成功後にも実行される。
自動更新タイマーを導入したホストでは、dotfilesの上流に変更が無い周期も実行する。
WindowsでのCodex実行中と公式インストーラーの通常のロック競合は、正常な延期として終了コード0を返す。
選択先の不良や必要な使用中情報の取得失敗では対象を保持し、理由と次の操作を示して非0で終了する。
配置の権限エラーや削除失敗も非0で終了するため、出力に示された原因を解消して再実行する。
詳しい引数は`prune-codex-packages --help`で確認できる。

## 推奨構成

配布内容は以下の構成とする。

- `~/.codex/AGENTS.md`: Codex向けの基本記述と、agent-toolkitの基本原則・製品横断の実行運用を埋め込む
- `~/.codex/agent-toolkit/rules`: Claude Code側のagent-toolkitルール原本を、`atk-auto`の境界を付けた本文としてコピーで同期する
- `~/.codex/skills/ak110-projects-operations`: dotfiles固有のグローバルスキル`ak110-projects-operations`へのリンク。agent-toolkit skillsはCodex plugin marketplace経由で配布する
- `~/.codex/docs`: `.chezmoi-source/dot_claude/docs`へのリンク
- プロジェクト直下の`.agents/skills`: プロジェクト専用スキルディレクトリへのシンボリックリンク

CodexとClaude Code 2.1.277以上は、プロジェクト規範を書くファイルとして`AGENTS.md`を共用できる。
そのため、常時読み込む設定は`AGENTS.md`へ集約し、本文は原本ファイルを参照する形にする。
`agent-toolkit/rules`は配布先で境界を付けた本文へ書き換えるためコピーで同期し、書き換えを要さない共有対象はリンクで配布する。
chezmoiの`symlink_`はWindowsで特権不足により失敗するため採用しない。
代わりに`chezmoi apply`後処理（`pytools.post_apply`）の専用ステップがリンクを生成する。
Linux/macOSではシンボリックリンク、Windowsではディレクトリジャンクションを使う。

プロジェクト直下の`.claude/rules/`と`.claude/skills/`はClaude Codeでは自動ロード・自動検出される。
Codexでは同じ挙動を前提にできないため、Codex側のプロジェクト専用スキルは`.agents/skills/`へ配置する。
`.claude/skills/`の原本を再利用する場合も、コピーせず`.agents/skills -> .claude/skills`のシンボリックリンクにする。
`.claude/rules/`はCodex側に対応する専用ディレクトリへ移さず、`~/.codex/AGENTS.md`から該当ファイルを読むよう指示する。
ユーザー単位の`~/.claude/CLAUDE.md`と`~/.claude/rules/`直下の`*.local.md`（実行ホストごとに置く規範）も、同じ`~/.codex/AGENTS.md`の読込指示でCodexの主体が作業の開始時に読む。プロジェクト直下の`CLAUDE.local.md`も読込の対象とする。

`~/.codex/rules`はCodexの承認ルール用ディレクトリであり、Claude CodeのMarkdownルールとは互換性がない。
agent-toolkitのMarkdownルールは`~/.codex/agent-toolkit/rules`に配置する。

プロジェクト固有設定は、原則として`AGENTS.md`を実体ファイルとし、`CLAUDE.md`は置かない。
Claude Codeは`CLAUDE.md`・`.claude/CLAUDE.md`・`CLAUDE.local.md`のいずれかがあると`AGENTS.md`を読まない。
そのため個人用の`CLAUDE.local.md`を置いたプロジェクトでは、`atk setup-project`が`@AGENTS.md`を取り込むだけの`CLAUDE.md`アダプターを置き、
リポジトリの`.git/info/exclude`へ加えて追跡対象から外す。アダプターは手元の`CLAUDE.local.md`に付随する設定であり、リポジトリへは入れない。
実体ファイルとすることで、コピー欠落やシンボリックリンク非対応環境での障害を回避する。
Codex専用の差分が必要な場合のみ、`AGENTS.md`本体に分岐記述を追加する。
