# Codex利用ガイド

Codexはagent-toolkitの標準構成に含まれる。単体インストーラーはCodexプラグインと共有スキルを設定する。
`agents_server` MCPはClaude CodeとCodexの双方へ共有され、工程別モデル設定の`model_type`で委譲先を選択する。

単体インストーラーは既存の`~/.codex/AGENTS.md`を保護するため、dotfiles固有のグローバル
`AGENTS.md`と共有リンク群を展開しない。dotfiles利用者は`update-dotfiles`または`chezmoi apply`により、
Codex向け`AGENTS.md`、共有ルール・スキルのリンク、プラグインを一括設定する。
プラグイン導入後は、次の手順で更新を反映する。

## フィードバックの常駐処理

次のコマンドはCodexの対話UIを起動し、対象リポジトリのフィードバックを継続して処理する。

```bash
atk config set orchestrate_model codex:gpt-5.6-sol/medium
atk mq process-loop
```

開始時点の項目に加え、処理中に追加されたready項目も同じセッションで順次処理する。
ready項目がなくなると、`agent-toolkit:completion-report`が必須の`agent-toolkit:session-review`と固定報告を完了し、続いて`agent-toolkit:exit-session`が`/goal`で登録した目的とセッションを終了する。
`agent-toolkit:process-feedbacks`は起動時に副作用のない終了能力probeを実行して分岐値を確定する。
probe未実行、読取失敗又は値の不一致は停止不能として扱う。
Linuxでremote-controlを使わない直接CLIを終了対象として確認できた場合は、Codexが自律終了して親の監視ループへ戻る。
終了対象を確認できない環境では対話UIに終了案内を表示し、利用者が`/exit`を入力すると親の監視ループへ戻る。
終了時の`agent-toolkit:exit-session`は起動時の分岐値を再利用せず、停止要求直前に終了能力probeを新規実行する。
表示済みPIDの開始時刻と実行ファイルのデバイス・inodeが再照合で一致した場合だけCodexを停止する。

初回と0件待機からの処理再開時は、private-notesを同期し、ready項目があれば
`update-dotfiles`とprivate-notesの再同期を終えてからCodexを起動する。
同期に失敗した場合はCodexを起動せず、変更検知を待って再試行する。

process-loopはCodexの承認方針とsandbox設定を上書きせず、利用者のCodex設定を継承する。
WindowsではCodexを親の監視ループと別のプロセスグループで起動するため、
Codexの実行中もCtrl+Cで親の監視ループを終了できる。
また、process-loop内のCodexに限り、Git for Windowsを介してbash形式のplugin hookへ
Windows絶対パスを渡す。Claude、`update-dotfiles`、process-loop外のCodexのPATHは変更しない。

## プラグイン更新の反映

dotfilesの`post_apply`が導入するagent-toolkitは、Codexが要求するversion付きの通常ディレクトリを維持し、その配下のplugin資源をdotfilesの`agent-toolkit/`原本へ接続する。
LinuxとmacOSでは、通常versionディレクトリ直下のファイルとディレクトリを原本への相対シンボリックリンクにする。
Windowsでは、直下のディレクトリをジャンクションにし、直下の通常ファイルを`update-dotfiles`のたびに原本と同じ内容へ同期する。
単体インストーラーで導入するagent-toolkitと外部プラグインは、Codexが管理するsnapshotを引き続き使用する。

`update-dotfiles`はローカルagent-toolkitが導入済みで有効な場合、versionが変化していても`codex plugin add`を省略し、現versionの通常ディレクトリを追加して原本接続を検査・修復する。
未導入時とdisabled時だけ`codex plugin add`を実行する。どちらも既存cacheがあればCLI実行前に全エントリを退避する。
ローカルまたは外部のプラグインを実際に追加または更新した場合と、公開インストーラーで`codex plugin add`前後のversionまたはenabledが変化した場合、daemonの稼働状態を確認する。
`codex app-server daemon version`が成功した場合に限り、次の再起動コマンドを案内する。

```bash
codex app-server daemon restart
```

公開インストーラーでは、プラグイン追加または`atk`配置が失敗した場合も、エラーの後の最終行へ
必要な再起動コマンドを表示し、非0の終了状態を維持する。`agents_server` MCPの登録や
`~/.claude.json`のUser scope設定は行わない。
進行中のセッションを保護するため、app-server daemonは自動再起動しない。

更新前に存在した安全なversion名は、`$CODEX_HOME/plugins/cache-compat/ak110-dotfiles/agent-toolkit/versions`へ保存される。`CODEX_HOME`が未設定の場合は`~/.codex`を使用する。
更新後は、保存済みの旧versionと現versionの通常ディレクトリを同じagent-toolkit原本へ接続する。
この配置により、起動済みまたは再開したセッションが保持する旧フックの絶対パスと、後続セッションが取得する現versionのパスを引き続き利用できる。
再起動案内は新versionを後続セッションへ反映する役割を持ち、原本接続は各versionパスから実行するplugin資源をdotfiles側へ統一する。

既存のversionパスがagent-toolkitのplugin構造として確認できない通常ファイル、通常ディレクトリ、特殊ファイルの場合、更新処理はそのエントリを置換せず失敗する。
原本接続の準備、置換、置換後のCodex状態確認に失敗した場合は、同じ処理で退避した更新前のcacheエントリ、version台帳、旧skillリンクを復元する。
`codex plugin add`を使う未導入状態、disabled状態では、Codexが旧cache rootを除去する前に既存の全エントリを退避し、CLIの偽返却・例外、後続処理の失敗時にsnapshotと旧互換リンクを元のパスへ戻す。外部、ローカルplugin追加後の失敗では、それまでに生成した再起動案内を失敗出力へ重複なく含める。CLIが有効化まで成功した後は有効状態を維持し、次回の`update-dotfiles`は復元済みcacheから原本接続を再構成する。
version台帳は保持されるため、競合、失敗原因を解消して`update-dotfiles`を再実行すると原本接続を再構成できる。
Codexが更新時に旧versionを除去する挙動は、
[Codexのstore実装](https://github.com/openai/codex/blob/main/codex-rs/core-plugins/src/store.rs)で確認できる。
起動済みセッションが旧キャッシュの絶対パスを保持する事象は、
[Codex issue #25285](https://github.com/openai/codex/issues/25285)でも報告されている。

再起動案内は、ローカルと外部のいずれかのプラグインを実際に追加または更新し、daemonの稼働状態を確認できた場合だけ表示される。
daemonの未起動、状態確認の失敗、マーケットプレイスの登録だけの変化、公開インストーラーでの導入前後の状態の一致、
プラグイン追加前の処理と追加自体のいずれかの失敗、外部プラグインの導入済みなど、それ以外の場合は表示しない。
案内されたコマンドは、Codex plugin、remote-controlを利用する実行中セッションの終了後に実行する。
daemonを利用しない既存のCLI・IDEセッションは、作業完了後に新しいセッションを開始する。

## agents_serverによる委譲

`agents_server`はCodex pluginから利用できる共有MCPである。`start(model_type, prompt, cwd)`は対応する工程別モデル設定からengine、model及びeffortを解決する。
CodexからClaudeへ委譲する場合も、`model_type`に対応する設定値のengine部が`claude`ならサーバーがClaude backendを選ぶ。調査専用の軽量起動には`start_explore(prompt, cwd)`を使い、出力量が大きいコマンドの実行には`start_shell(command, cwd, summary_policy)`を使う。
MCPは共有daemonや永続registryを使用せず、終了時に自身が起動した子プロセスだけを終了する。

公開ツールは`start`、`start_explore`、`start_shell`、`wait`、`send_message`、`kill`の6つである。`start`、`start_explore`及び`start_shell`の`cwd`は既存ディレクトリの絶対パスとし、
完了を待たず`session_id`を返す。`wait`はtimeoutまで状態を観測し、終端時は結果本文を返す。`timeout`を省略した場合の既定は、プロンプトキャッシュの保持期間から導出した上限とする。固有のtimeout要件がなければ`timeout`を省略し、呼び出し元がサブエージェントの場合は`request_bucket`へ`subagent`を渡す。`timeout=0`は待機せず現状態を返す。
`start`・`start_explore`・`start_shell`が返した`session_id`と、`send_message`で新しい指示を配送したsessionは、同じ応答の中で`wait`を発行して観測する。結果が不要な場合は`kill`で破棄する。観測を試みていない作業を残したままターンを終えると、当該作業を観測する主体が残らない。
`send_message(session_id, prompt, timeout=270)`は実行中turnへsteerし、終端済みturnでは結果回収を前提にせず同じsessionでreplyを開始する。send_messageの通常の既定は270秒であり、固有のtimeout要件がなければ引数を省略して通常既定を使う。timeoutは追加指示の配送結果が確定するまでの待機上限であり、委譲先の応答生成の完了は待たない。`0`以下は受理しない。上限到達時は配送の成否が確定しないため`wait`で状態を確認する。
`kill(session_id, timeout=270)`は実行中turnだけへ中断を要求する。killの通常の既定は270秒であり、固有のtimeout要件がなければ引数を省略して通常既定を使う。`timeout=0`は要求配送後の現状態を返し、正のtimeoutは終端結果を待つ。`timeout=0`でも中断要求の配送と`turn_control_lock`の取得には270秒の上限を適用し、終端は待たない。上限に達した場合は、中断要求が未配送か配送の成否が確定しないかを区別した`TimeoutError`を返し、sessionとbackend processは破棄しない。
timeout超過時もsessionを保持し、`wait`または終端後の`send_message`で同じsessionを再開できる。終端結果の保持期限30分を過ぎた場合と、sessionを所有する実行主体が終了した場合のいずれも、同じ`send_message`が保持済みの実効条件から会話を暗黙に再開する。`kill`の`kill_requested`、
`send_message`の`delivery`及び`wait`の終端応答で要求・配送・結果を確認する。

backendから承認・入力・認証・attestationなどの非対話要求を受信した場合は、MCPが非対応エラーを返し、
対応turnを`failed`としてwaiterを起床させる。承認・ユーザー入力・一覧操作は公開せず、明示的な中断は`kill`で行う。

### フックの信頼確認

Codexはplugin同梱フックの定義が変わると、利用者が再び信頼するまで当該フックをスキップする。
プラグイン更新後は新しいCodexセッションで`/hooks`を実行し、agent-toolkitについて
次の7イベントが含まれることを確認する。他の有効pluginは、独自のイベントを追加する場合がある。

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
信頼後の`PreToolUse`は、`apply_patch`の変更内容に口語的な日本語表現が含まれる場合、
検出語そのものを表示せず正式な書き言葉への書き直しを促す通知を返す。
動作を確かめる場合は、口語的な言い回しを含む短い変更を`apply_patch`で適用し、通知の有無を確認する。
Stopは自動振り返りを起動しない。手動で振り返る場合は`$agent-toolkit:session-review`を実行する。通常の作業完了時は`agent-toolkit:completion-report`が条件を判定し、必要な場合だけ振り返りを起動する。

## Codex CLI本体

dotfiles配布利用者では、`chezmoi apply`後の処理がCodexの公式インストーラーを非対話で実行する。
未導入時はスタンドアローン版を導入し、導入済みの場合は最新版へ更新する。
管理対象パッケージは`~/.codex/packages/standalone/`へ配置される。
可視コマンドの既定配置先はLinuxとmacOSで`~/.local/bin`、Windowsで`%LOCALAPPDATA%\Programs\OpenAI\Codex\bin`である。
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
公式インストーラーは競合版を検出したうえで削除の可否を対話で確認し、非対話実行では否定を既定値とする。
競合版を検出した実行では、シェルの起動ファイルへPATH設定が追記される場合がある。
起動ファイルはchezmoiの配布対象であるため、追記された内容は次回の`chezmoi apply`で配布内容へ戻る。

## 推奨構成

配布内容は以下の構成とする。

- `~/.codex/AGENTS.md`: Codex向けの基本記述と、agent-toolkitの基本原則・製品横断の実行運用を埋め込む
- `~/.codex/agent-toolkit/rules/99-claude-code.md`: リンク先には配置するが、Claude Code固有規範のためCodex向けAGENTS.mdへ埋め込まない
- `~/.codex/agent-toolkit/rules`: Claude Code側のagent-toolkitルール原本へのシンボリックリンク
- `~/.codex/skills/*`: `.chezmoi-source/dot_claude/skills/*`のうちdotfiles固有のグローバルスキルへのシンボリックリンク。agent-toolkit skillsはCodex plugin marketplace経由で配布する
- プロジェクト直下の`.agents/skills`: プロジェクト専用スキルディレクトリへのシンボリックリンク

CodexはClaude Codeの`CLAUDE.md`や`.claude/rules/`を同じ読み込み規則では扱わない。
そのため、常時ロードの入口は`AGENTS.md`へ集約し、本文は原本ファイルを参照する形にする。
ファイルコピーで同期すると改訂漏れが発生するため、共有対象はリンクで配布する。
chezmoiの`symlink_`はWindowsで特権不足により失敗するため採用しない。
代わりに`chezmoi apply`後処理（`pytools.post_apply`）の専用ステップがリンクを生成する。
Linux/macOSではシンボリックリンク、Windowsではディレクトリジャンクションを使う。

プロジェクト直下の`.claude/rules/`と`.claude/skills/`は、Claude Codeでは自動ロード・自動検出される。
Codexでは同じ挙動を前提にできないため、Codex側のプロジェクト専用スキルは`.agents/skills/`へ配置する。
`.claude/skills/`の原本を再利用する場合も、コピーせず`.agents/skills -> .claude/skills`のシンボリックリンクにする。
`.claude/rules/`はCodex側に対応する専用ディレクトリへ移さず、`~/.codex/AGENTS.md`から該当ファイルを読むよう指示する。

`~/.codex/rules`はCodexの承認ルール用ディレクトリであり、Claude CodeのMarkdownルールとは互換性がない。
agent-toolkitのMarkdownルールは`~/.codex/agent-toolkit/rules`に配置する。

プロジェクト固有設定は、原則として`AGENTS.md`を実体ファイル、
`CLAUDE.md`をClaude Codeのfile import記法`@AGENTS.md`を含むアダプターとして配置する。
両方を実体ファイルとすることで、コピー欠落やシンボリックリンク非対応環境での事故を回避する。
Codex専用の差分が必要な場合のみ、`AGENTS.md`本体に分岐記述を追加する。
