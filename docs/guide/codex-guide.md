# Codex利用ガイド

Codexはagent-toolkitの標準構成に含まれる。単体インストーラーはCodexプラグインと共有スキルを設定する。
`agents_server` MCPはClaude CodeとCodexの双方へ共有され、工程別モデル設定の`model_type`で委譲先を選択する。

単体インストーラーは既存の`~/.codex/AGENTS.md`を保護するため、dotfiles固有のグローバル
`AGENTS.md`と共有リンク群を展開しない。dotfilesユーザーは`update-dotfiles`または`chezmoi apply`により、
Codex向け`AGENTS.md`、共有ルールの同期、共有スキルのリンク、プラグインを一括設定する。
更新の反映は「プラグイン更新の反映」の手順に従う。

## `atk wi process-loop`によるAWIの継続処理

共通の起動、複数リポジトリ登録、待機、状態確認、停止、次回への指示、UWI回答は[agent-toolkit導入ガイド](claude-code-guide.md#推奨ワークフロー)を参照する。

Codexで処理する場合は、起動前に候補列を設定する。

```bash
atk config set orchestrate_model codex:sol/medium,claude:opus[1m]/medium
atk wi process-loop
```

process-loopはCodexの承認方針とsandbox設定を継承する。Windowsでは親の監視ループをCtrl+Cで終了できる。自動終了できない環境では、完了報告の後に`/exit`を入力して監視ループへ戻る。

## プラグイン更新の反映

Codex向けプラグインは`update-dotfiles`または`chezmoi apply`で更新する。更新前に実行中のCodexセッションと委譲の完了時刻を確認する。実行中のセッションは更新前の版を使い続けるため、更新後は新しいセッションで新版を使う。

更新が延期または失敗した場合は、`update-dotfiles logs`で原因と次の操作を確認し、表示されたCodexを終了してから`update-dotfiles`を再実行する。WindowsでCodexが実行中の場合も、終了後の再実行で更新と整理を続ける。

更新後にdaemonの再起動を案内された場合は、Codex pluginまたはremote controlを使うセッションを終了してから実行する。再起動が失敗した場合はdaemonを起動する。

```bash
codex app-server daemon restart
```

再起動が失敗した場合だけ、次のコマンドでdaemonを起動する。

```bash
codex app-server daemon start
```

更新時に接続を切断してよい場合だけ、daemonを自動再起動できる。

```bash
DOTFILES_CODEX_DAEMON_AUTO_RESTART=1 update-dotfiles
```

PowerShellでは`$env:DOTFILES_CODEX_DAEMON_AUTO_RESTART = "1"`を設定してから`update-dotfiles`を実行する。更新の内部的な版保持、診断ログ移行およびdaemon制御の条件は[architecture.md](../development/architecture.md)と[operations.md](../development/operations.md)を参照する。

## agents_serverによる委譲

`agents_server`はClaude CodeとCodexが共有する委譲用MCPである。委譲の状態は`atk agents list`で確認し、`atk agents wait`で終端と結果を受け取る。委譲するエージェントの選択は[agent-toolkit導入ガイド](claude-code-guide.md#工程別モデル設定)を参照する。

### CodexのサブスクとAPI接続先

接続先の選択順は`atk config`の`codex_model_providers`で指定する。未設定または空文字列ではCodexの通常の`model_provider`と認証を使う。ChatGPTサブスクリプションだけを使う場合はCodexへログインし、通常設定のproviderを省略するか`"openai"`にする。

APIを併用する場合は、Codexの`config.toml`に接続先とAPIキーの環境変数名を定義し、APIキーをその環境変数へ設定してから、候補の順序を設定する。

```toml
[model_providers.custom]
name = "my-server"
base_url = "https://my-server.example.com/v1"
wire_api = "responses"
env_key = "MY_SERVER_API_KEY"
requires_openai_auth = false
```

```bash
export MY_SERVER_API_KEY='<APIキー>'
atk config set codex_model_providers 'openai,custom'
atk config get codex_model_providers
```

`openai`はChatGPTログイン、`custom`は`config.toml`で定義したAPI接続先を表す。APIだけを使う場合は`'custom'`を指定し、通常設定へ戻す場合は空文字列を保存する。

```bash
atk config set codex_model_providers ''
```

API料金は接続先の課金規則に従う。ChatGPTのtokenをAPIキーとして使わない。`AGENT_TOOLKIT_CONFIG_CODEX_MODEL_PROVIDERS`に空でない値が設定されている場合は保存値より優先するため、設定解除時はこの環境変数も解除する。

候補が使えない場合は診断を確認し、Codexの接続先定義または認証を修正してから再試行する。この設定はagents_serverが起動する全modeと継続へ作用し、直接Codex、`atk run-skill`、`atk wi process-loop`の起動設定は変えない。`codex_usage_limit_wait`は、既定の`five-hour`で5時間枠だけを待ち、`always`で週次枠なども待ち、`never`では待たない。API料金は継続にも発生し、選択済みAPI会話は継続で同じ接続先を使う。接続先の変更は新しい会話から適用される。会話状態の互換性を確認できない場合は、表示された引き継ぎ案内に従って新しい会話で再開する。内部の再開契約は[design-agents-server.md](../development/design-agents-server.md)を参照する。

## 利用上限後の再開

`atk run-skill`のCodex実行は、指定スキルの実行中に待機対象の利用上限で失敗すると解除予定まで待ち、`codex exec resume`で同じ会話を再開する。
待機対象は`codex_usage_limit_wait`で選び、5時間枠と週次枠は通知の`window_minutes`から判定する。
待機時間は`--timeout`へ含めない。
`atk run-skill`と`atk wi process-loop`の候補判定では次の候補を先に試す。
全候補が失敗し、そのうちに待機対象の利用上限の失敗がある場合は、最も早い解除予定まで待って先頭から判定し直す。
解除予定が不明なら300秒後に再確認する。

process-loopが起動したCodexの子は、上限で停止したことを親が検出して終了させ、次の反復の候補判定を経て作業を続ける。
新しい会話で保持中のレーン・WI・計画を読み、残る工程から再開する。
`agents_server`の委譲は前節の接続先移行と、終端後の引き継ぎ起動を使う。

ユーザーが端末で`codex`を実行して開いた対話画面は、利用上限の解除後に自動では再開しない。
解除後に`/goal resume`か新しい発話を送って続ける。
共有daemonへ接続した画面でも、画面を閉じた後に外部から積んだ指示が画面の無いまま実行されるため、agent-toolkitは外部からの再開を送らない。

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
CodexのStopは説明言語を判定する。
completion-reportの起動忘れ通知と報告本文の判定は対象外である。
工程の完遂と報告は`agent-toolkit:completion-report`の規範に従ってエージェントが確かめる。
手動で振り返る場合は`$agent-toolkit:session-review`を実行する。
通常の作業完了時は`agent-toolkit:completion-report`が条件を判定し、必要な場合だけ振り返りを起動する。

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
