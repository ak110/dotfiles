# Codex利用ガイド

agents_serverでは起動候補と終端後の観測identityを分ける。Codexの観測元は`turn_context`とし、表示名はモデルカタログを優先し、無い完全IDだけを決定論的に変換する。取得不能値を推測しない。

Codexはagent-toolkitの標準構成に含まれる。単体インストーラーはCodexプラグインと共有スキルを設定する。
`agents_server` MCPはClaude CodeとCodexの双方へ共有され、工程別モデル設定の`model_type`で委譲先を選択する。

単体インストーラーは既存の`~/.codex/AGENTS.md`を保護するため、dotfiles固有のグローバル
`AGENTS.md`と共有リンク群を展開しない。dotfilesユーザーは`update-dotfiles`または`chezmoi apply`により、
Codex向け`AGENTS.md`、共有ルール・スキルのリンク、プラグインを一括設定する。
プラグイン導入後は、次の手順で更新を反映する。

## `atk wi process-loop`によるAWIの継続処理

次のコマンドはCodexの対話UIを起動し、対象リポジトリのAWIを継続して処理する。

```bash
atk config set orchestrate_model codex:gpt-6-sol/medium
atk wi process-loop
```

開始時点の項目に加え、処理中に追加されたready項目も同じセッションで順次処理する。
ready項目がなくなると、`agent-toolkit:completion-report`が選定工程で完了した振り返りの結果を含む完了報告を完了し、続いて`atk agents-exit-session`が`/goal`で登録した目的とセッションを終了する。
`agent-toolkit:process-wi`は起動時に副作用のない終了能力probeを実行して分岐値を確定する。
probe未実行、読取失敗または値の不一致は停止不能として扱う。
Linuxでremote-controlを使わない直接CLIを終了対象として確認できた場合は、Codexが自律終了して親の監視ループへ戻る。
終了対象を確認できない環境では対話UIに終了案内を表示し、ユーザーが`/exit`を入力すると親の監視ループへ戻る。
終了時の`atk agents-exit-session`は起動時の分岐値を再利用せず、停止要求直前に終了対象を新規識別する。
表示済みPIDの開始時刻と実行ファイルのデバイス・inodeが再確認で一致した場合だけCodexを停止する。

初回と0件待機からの処理再開時は、private-notesを同期し、ready項目があれば
`update-dotfiles`とprivate-notesの再同期を終えてからCodexを起動する。
同期に失敗した場合はCodexを起動せず、変更検知を待って再試行する。

process-loopはCodexの承認方針とsandbox設定を上書きせず、ユーザーのCodex設定を継承する。
WindowsではCodexを親の監視ループと別のプロセスグループで起動するため、
Codexの実行中もCtrl+Cで親の監視ループを終了できる。
また、process-loop内のCodexに限り、Git for Windowsを介してbash形式のplugin hookへ
Windows絶対パスを渡す。Claude、`update-dotfiles`、process-loop外のCodexのPATHは変更しない。

## プラグイン更新の反映

dotfilesはClaude Code・Agent Plugins向けの`agent-toolkit/`を元にし、Codex向けには`agent-toolkit-codex/`を生成する。
`agent-toolkit-codex/`はAgent Plugins用の直下`plugin.json`と`mcp.json`を除き、`.codex-plugin/plugin.json`、hook、skill、Python実装、lockfileその他の実行資源を通常ファイルとして含む。
Codex 0.154.0はプラグイン導入時にsourceをsnapshotするため、`agent-toolkit-codex/`は相対シンボリックリンクを含めない。
`.agents/plugins/marketplace.json`だけが`./agent-toolkit-codex`を参照し、Claude CodeとAgent Pluginsは引き続き`agent-toolkit/`を参照する。
`agent-toolkit-codex/`はGitで追跡せず、`update-dotfiles`のpost-applyがCodex plugin導入の直前に生成する。
手動で再生成する場合は`scripts/sync_codex_plugin_manifests.py`を実行し、`--check`で大元の定義との一致を確認する。
生成に失敗した場合はpost-applyが非0で終了し、失敗したstep名と詳細を更新logへ記録する。

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

延期した更新は、Codexのセッションと委譲を終了してから次の`update-dotfiles`を実行すると反映される。
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

Codex hookはPATH上の`~/.local/bin/atk-hook`（Windowsでは`atk-hook.cmd`）から起動する。
このコマンドは`codex plugin list --json`に示された有効な現行版を毎回解決し、イベント名、標準入出力および終了状態をhook本体へ渡す。
インストーラーは`codex plugin add`より先にこのコマンドを配置し、導入後に現行版のhook実体を確認する。
初回切替時に限り、更新前の版付きhookコマンドを保持したセッションのために旧キャッシュを一時退避し、CLIが削除した場合は復元する。以降の更新に旧版保存台帳は設けない。
プラグインの通常のversion別cache管理はCodex公式CLIへ委ねる。
`DOTFILES_CODEX_DAEMON_AUTO_RESTART=1`を指定しない場合は、更新中のセッションを作業完了後に終了し、再起動案内が表示された場合はdaemonを再起動して新versionを利用する。

再起動案内は、ローカルと外部のいずれかのプラグインを実際に追加または更新し、daemonの稼働状態を確認できた場合だけ表示される。
daemonの未起動、状態確認の失敗、マーケットプレイスの登録だけの変化、公開インストーラーでの導入前後の状態の一致、
プラグイン追加前の処理と追加自体のいずれかの失敗、外部プラグインの導入済みなど、それ以外の場合は表示しない。
案内されたコマンドは、Codex plugin、remote-controlを利用する実行中セッションの終了後に実行する。
daemonを利用しない既存のCLI・IDEセッションは、作業完了後に新しいセッションを開始する。

## agents_serverによる委譲

`agents_server`はCodex pluginから利用できる共有MCPである。起動は`start`の1つで行い、`mode`で入力の形を選ぶ。`start`は`model_type`に対応する工程別モデル設定からengine、modelおよびeffortを解決する。
CodexからClaudeへ委譲する場合も、`model_type`に対応する設定値のengine部が`claude`ならサーバーがClaude backendを選ぶ。調査専用の軽量起動には`mode`を`explore`として`prompt`と`cwd`を渡し、出力量が大きいコマンドの実行には`mode`を`shell`として`command`、`cwd`、`summary_policy`を渡す。
`explore`で`model_type`を省略すると`low_tier`を使う。所在の特定や該当箇所の列挙のように結論だけで後続の判断が成立する調査は`model_type`を省略したまま使い、軽量な探索では判断材料が不足する調査だけ`model_type="medium_tier"`を指定する。
MCPは共有daemonや永続registryを使用せず、終了時に自身が起動した子プロセスだけを終了する。

公開ツールは`start`、`send_message`、`kill`、`list`、`show`、`stop`の6つであり、終端と結果の受領は`atk agents wait`が担う。`start`の`cwd`は全`mode`で既存ディレクトリの絶対パスとし、
完了を待たず`session_id`を返す。`atk agents wait`は引数を受け取らず、登録済みのsessionの終端を待ち、回収できた終端結果を全件返す。待機上限は実行ホストの1回のツール呼び出しの上限からサーバーが確定し、委譲先として起動されたセッションでは240秒とする。ホストの上限により`wait`の呼び出し自体が失敗した場合も、待機対象のsessionは終端せず実行を続ける。`wait`を再発行する前に、`list`で待機対象のsessionの`status`を確認する。
`start`が返した`session_id`と、`send_message`で新しい指示を配送したsessionについては、同じ応答の中で`wait`を発行して観測する。結果が不要な場合は`kill`で破棄する。作業の観測を試みずにターンを終えると、その作業を観測する主体が残らない。
`send_message(session_id, prompt, timeout=270)`は実行中turnへsteerし、終端済みturnでは結果回収を前提にせず同じsessionでreplyを開始する。`timeout`を省略すると270秒を使うため、固有のtimeout要件がなければ引数を省略する。timeoutは追加指示の配送結果が確定するまでの待機上限であり、委譲先の応答生成の完了は待たない。`0`以下は受理しない。上限到達時は配送の成否が確定しないため`wait`で状態を確認する。
`kill(session_id, timeout=270)`は実行中turnだけへ中断を要求する。`timeout`を省略すると270秒を使うため、固有のtimeout要件がなければ引数を省略する。`timeout=0`は要求配送後の現状態を返し、正のtimeoutは終端結果を待つ。`timeout=0`でも中断要求の配送と`turn_control_lock`の取得には270秒の上限を適用し、終端は待たない。上限に達した場合は、中断要求が未配送か配送の成否が確定しないかを区別した`TimeoutError`を返し、sessionとbackend processは破棄しない。
timeout超過時もsessionを保持し、`wait`または終端後の`send_message`で同じsessionを再開できる。終端結果の保持期限30分を過ぎた場合と、sessionを所有する実行主体が終了した場合のいずれも、同じ`send_message`が保持済みの実効条件から会話を暗黙に再開する。`kill`の`kill_requested`、
`send_message`の`delivery`および`wait`の終端応答で要求・配送・結果を確認する。
`list`は保持中のsessionの状態を開始順に返し、結果本文を含めない。`stop(session_id)`は保持中で終端済みのsessionを破棄し、実行中turnを持つsessionは拒否する。`kill`へ`stop=true`を渡した場合は、終端結果を返した応答に限って同じ破棄が生じる。`wait`は引数を受け取らないため、受領した終端結果のsessionを破棄する場合は`stop`を発行する。破棄したsessionへの`send_message`は暗黙再開する。

backendから承認・入力・認証・attestationなどの非対話要求を受信した場合は、MCPが非対応エラーを返し、
対応turnを`failed`としてwaiterを起床させる。承認・ユーザー入力・一覧操作は公開せず、明示的な中断は`kill`で行う。

### フックの信頼確認

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
Stopは終了工程の証拠だけを判定する。振り返りの準備と直接発話の報告見出しから足りない報告段階と報告本文の不備を示し、`decision: "block"`と`reason`で同じターンを続けさせる。報告本文の不備は、対策行に根拠（AWIのファイル名・投入予定・同一セッションの実装）が無いこと、見送りの判定済み行の根拠の欠落か未確定、未確定行の照会・再現・残る理由の欠落、AWI投入結果報告に残った投入予定の4つである。対処と中止・待機の判断の記録は`agent-toolkit:completion-report`に従う。Stopは自動振り返りを起動しない。手動で振り返る場合は`$agent-toolkit:session-review`を実行する。通常の作業完了時は`agent-toolkit:completion-report`が条件を判定し、必要な場合だけ振り返りを起動する。

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

旧版の整理を`chezmoi apply`後の処理が担うのは、公式インストーラーが非対話実行時に競合するnpm版を残すためである。
公式インストーラーは競合版を検出したうえで削除の可否を対話で確認し、非対話実行では削除しない側を選ぶ。
競合版を検出した実行では、シェルの起動ファイルへPATH設定が追記される場合がある。
起動ファイルはchezmoiの配布対象であるため、追記された内容は次回の`chezmoi apply`で配布内容へ戻る。

## 推奨構成

配布内容は以下の構成とする。

- `~/.codex/AGENTS.md`: Codex向けの基本記述と、agent-toolkitの基本原則・製品横断の実行運用を埋め込む
- `~/.codex/agent-toolkit/rules`: Claude Code側のagent-toolkitルール原本へのシンボリックリンク
- `~/.codex/skills/*`: `.chezmoi-source/dot_claude/skills/*`のうちdotfiles固有のグローバルスキルへのシンボリックリンク。agent-toolkit skillsはCodex plugin marketplace経由で配布する
- プロジェクト直下の`.agents/skills`: プロジェクト専用スキルディレクトリへのシンボリックリンク

CodexとClaude Code 2.1.277以上は、プロジェクト規範を書くファイルとして`AGENTS.md`を共用できる。
そのため、常時読み込む設定は`AGENTS.md`へ集約し、本文は原本ファイルを参照する形にする。
ファイルコピーで同期すると一部のコピーに更新が反映されないため、共有対象はリンクで配布する。
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
