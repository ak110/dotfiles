# Claude CodeとVSCodeの設定

## Claude Codeの推奨設定

以下の設定を推奨する。

agent-toolkitの実効設定は`atk config list`で一覧表示し、`atk config get <KEY>`で値を確認する。
`atk config show`も同じ一覧を表示する。
リポジトリごとの編集可否・処理対象・検証OSは`atk config repos list`で確認する（[登録例](claude-code-workflows.md#自律型)）。

### `~/.claude/settings.json`

- `autoMemoryEnabled`: `false`（自動メモリー機能を無効化）
- `showClearContextOnPlanAccept`: `true`（plan mode承認時にコンテキストクリアの選択肢を表示）
- `env.CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR`: `"1"`（Bash呼び出しごとに作業ディレクトリをプロジェクトへ戻す）
- `env.CLAUDE_CODE_NO_FLICKER`: `"1"`（画面のちらつきを抑制）
- `env.CLAUDE_CODE_EXPERIMENTAL_CODE_MODE`: `"1"`（対応する新規ターミナル会話でCode Modeを有効化）
- `env.PLAYWRIGHT_MCP_OUTPUT_DIR`: ホーム配下の`.cache/playwright-mcp`の絶対パス（Playwright MCPが作業中のリポジトリへ`.playwright-mcp/`を残さないようにする）
- `permissions`: 許可・拒否するツールやパターンを記述
 （[例](https://github.com/ak110/dotfiles/blob/master/share/claude_settings_json_managed.json)）
- `autoCompactWindow`: `400000`（自動圧縮が始まる文脈の大きさ。全モデル共通の既定値）

自動圧縮の境界は、モデルごとに`/autocompact <値>`で変えられる（`modelSettings`へ保存され、トップレベルの値より優先される）。
`/autocompact auto`はモデル向けに調整された標準の値へ戻すもので、配布した`autoCompactWindow`へは戻らない。
配布した値へ戻すには、`~/.claude/settings.json`の`modelSettings.<モデル>.autoCompactWindow`を削除する。
トップレベルの`autoCompactWindow`は`update-dotfiles`が設定を更新するたびに配布した値で上書きするため、全モデル共通の値を手で変えても次の更新で戻る。
値を変えて残すには`/autocompact`を使う（[設定の仕様](https://code.claude.com/docs/en/settings-reference#modelsettings)）。
以前に配布していた`env.CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`と`env.CLAUDE_CODE_AUTO_COMPACT_WINDOW`は、`/autocompact`より優先されて会話の途中で変えられないため、設定更新時に除去する。

### Code Mode

Code ModeはClaude Code 2.1.323以降のネイティブ版と対応アカウント・モデルを必要とする。
初回起動では確認に回答し、画面の`Code Mode (experimental) is on`を確認する。
Agent SDK、`-p`、VSCodeパネルは対象外である。
Remote Control、Chrome連携、computer useは併用できず、Code Modeの会話をDesktopやcloudへ移動できない。
会話の再開にもCode Modeを有効にしたターミナルを使う。
詳細な対応条件は[公式のCode Mode手順](https://code.claude.com/docs/en/code-mode#requirements)を参照。

新しい会話で無効にする場合は、Linuxでは`CLAUDE_CODE_EXPERIMENTAL_CODE_MODE=0 claude`を使う。
PowerShellでは`$env:CLAUDE_CODE_EXPERIMENTAL_CODE_MODE='0'; claude`を使う。
Code Modeで始めた会話を再開するときは、同じ変数を`1`に戻す。

### `/config`コマンド

- `Verbose output`: 有効
- `Default permission mode`: `Auto mode`
- `Language`: `Japanese`

### `/plugin`コマンド

claude-plugins-officialのプラグインは次の方針で扱う。

- 推奨: `typescript-lsp`（必要な`typescript-language-server`と`typescript`は`update-dotfiles`の`full`モードで導入する）
- 無効: `context7`（認証が必要なため推奨を廃止し、インストール済みなら自動で無効化）
- 任意: `skill-creator`
- 参照のみ（有効化しない）: `plugin-dev`・`mcp-server-dev`（エージェントが品質基準の案内に従って資料として読むため導入は不要。有効化するとスキルが自動で起動し、質問が増える）
- 無効: `pyright-lsp`（Claude Codeがインストールを推奨するが誤動作が発生するため、インストール後に`Disable`）
- 無効: `claude-md-management`（`CLAUDE.local.md`を`.claude.local.md`と誤認し、`AGENTS.md`を走査しないため、インストール済みなら自動で無効化）

TypeScriptのコンパイラーは最新版を使い、`typescript-lsp`には言語サーバーが必要とする6系列を自動で併設・接続する。

### Modsの確認と停止

CLIは`claude --version`で2.1.287以降か確認する。
旧`CLAUDE_CODE_ENABLE_FUNCTION_HOOKS`は不要で、設定更新時に除去する。
`/plugin`のInstalledタブで読み込まれたModを確認し、個別に無効化できる。
シェルから更新した後は会話で`/reload-plugins`を実行する。
`/restart`と`/update`は、委譲先の終了を待つバックグラウンドコマンド`atk agents wait`を実行している場合、自身の会話の待機だけを停止する。
再起動後は同じ会話で待機と作業を自動で続ける。
ほかのバックグラウンド処理やCode ModeのREPL内実行は停止せず、実行中はClaude Code本体が再起動を拒否することがある。
`disableAllHooks: true`はユーザーのModsに加えて設定hookとstatuslineも止めるため、個別停止を優先する。
組織の制御や読み込まれない場合は[公式の読み込み条件](https://code.claude.com/docs/en/plugins/mods/overview#turn-mods-on-or-off)を参照。

agent-toolkitのModを読み込んだメインのターミナル会話で`/atk`を実行すると、担当を選び、指示を追送し、effortを相対変更できるパネルを開く。
パネルには既存の集計を使った、会話と子孫の推定費用も表示する。
effortはモデルの推論量を調整する設定であり、変更は対象担当の次のターンから適用される。
パネルを閉じるには「閉じる」またはEscを使う。

### 組織でModsを利用する設定

組織の管理者は、組み込みガードを保持したまま必要なModを組織のModとして配布できる。
組織のOwnerがclaude.aiで「Organization settings」「Claude Code」「Managed settings」の順に開いて設定する。
Claude Code 2.1.324以降でリモートmarketplaceを使う場合は、管理設定で次の3点を指定する。

- `extraKnownMarketplaces`でmarketplaceを宣言する
- `enabledPlugins`で対象プラグインを`true`にする
- `prependPlugins`または`appendPlugins`へ対象プラグインのIDを列挙する

agent-toolkitのIDは`agent-toolkit@ak110-dotfiles`である。
ローカルmarketplaceを組織配布する場合は、管理設定の絶対パスと各端末の配置を合わせ、marketplaceからプラグインを相対パスで列挙する。
そのディレクトリと上位ディレクトリは管理者だけが書き込めるようにする。

`prependPlugins`を指定する場合は、組み込みガードを保持するため`sec-default@builtin`も列挙する。
`strictKnownMarketplaces`はインストールできるmarketplaceを制限する設定であり、Modを組織のModとして区分する設定ではない。
旧版2.1.322時点のdigestを使う設定例とは版を区別し、導入版に対応する[公式の組織Mod配布手順](https://code.claude.com/docs/en/plugins/mods/admin#install-your-organizations-mods-and-set-the-order)を参照。

組み込みガードを使わない組織では、管理設定へ`{"prependPlugins": []}`を指定する方法もある。
この指定は組み込みガード全体を外し、すべてのModのhookが省略されずに動く。
特定のhookだけを有効にする設定ではないため、ガードを保持したい組織では前述の組織Modの配布を使う。
server-managed settingsで配信する場合は、サーバーがその会話の設定を確認するまでガードが残ることがある。
設定を保存した直後に反映されない場合は、時間をおいて新しい会話で確認する。
適用後は`/plugin`で対象Modの制限通知を確認する。
必要なら新しい会話を`claude --debug`で起動し、対象Modが`tier prepend`で読み込まれたかを確認する。
管理設定の実配信は組織ごとに異なり、ここでは個別組織での適用成功を確認していない。

### VSCode設定（任意）

ターミナル内での右クリックによる意図しない貼り付けを防ぐ場合は、`settings.json`へ以下を追加。

```json
{
  "terminal.integrated.rightClickBehavior": "nothing"
}
```

## atk config

`atk serve`の「設定」で値の出所と反映の時機を確認し、保存値を変更する。
画面を開けない環境では、`atk config show`で実効値を確認し、`atk config --help`で利用できる操作を調べる。

### 設定の共有

同じprivate-notesを同期する端末では、`atk config set`で変更できる設定を、次の例外を除いてprivate-notesの`atk-config.json`で共有する。

- `agents_concurrency`、`dotfiles_mode`、`serve_host`、`serve_port`、`serve_plans_root`、`remote_hosts`
- 週次処理を実行する端末を選ぶ`weekly_*_auto_start`と`weekly_research_x_api`

リポジトリでは編集可否、検証OS、系統、GitHubの取り込み設定を共有する。
cloneの場所と巡回の有無は端末ごとに保存する。
保存先のパスや認証情報は共有しない。
Codexは[接続先のIDだけを共有する](claude-code-connections.md#接続の使い分け)。

同期先の無い環境では全ての設定を端末に保存する。
環境変数と一時指定は従来どおり保存値より優先する。

別の端末で登録済みのリポジトリは、その端末のcloneを1コマンドで関連付ける。

```bash
atk config repos set github.com/example/project --path ~/project
```

cloneのoriginが登録と違う場合は関連付けない。
新しく関連付けた端末で巡回するには `--process-loop` を指定する。
既存の端末の巡回有無は移行で変わらない。
pathの無い登録は`atk config repos list`に「この端末には未登録」と表示され、その端末では巡回しない。
originが無くパスでしか識別できない登録は、端末側だけに残る。

### 共有設定への移行

`atk serve`や巡回の起動時、共有設定を保存する時に、同じ値と安全に確定できる値を自動で整理する。
設定の表示だけでは同期も移行もしない。

旧版の委譲先が従来のリポジトリ登録で作業している間は、その登録を変えず、他の共有設定は保存できる。
新しい管理サービスへの切替には、このリポジトリを管理する端末で `update-dotfiles` を実行する。
`atk agents list` で作業中の委譲先を確認し、そのまま終えられる作業は終了を待つ。
途中の作業を移す必要がある場合は、依頼元のエージェントに[前任の終了と引き継ぎ](claude-code-connections.md#委譲先のモデルを指定して切り替える)を依頼する。
新しい管理サービスへ切り替え、旧版の作業が終了するか引き継がれると、次の起動や共有設定の保存でリポジトリの移行を再び判定する。
旧版の登録が共有側の編集権限を上書きしうる間は、リポジトリの登録・権限変更も保留する。

この端末と共有先のどちらの値を残すか決める必要がある場合は、atk serveの「設定」で対象端末（ローカルまたはSSH先）を選ぶ。
「共有設定の選択」で残す値を選び、「選んだ値を保存」を押す。
選んだ値は同じ共有先を使う全端末に届く。

不正な値や同期先で確認できなかった値は端末に残り、移行済みにならない。
同期障害が解消したら通常の起動・保存で再開する。
別の端末が先に値を変更した場合は、更新後の候補から選び直す。

旧Codex許可は、移行前の消費を全端末で確認できないため、新しい消費を止める。
引き続き使う場合は「利用枠」で旧許可を開き、「共有許可を発行」を押す。
回数の指定を省くと、この端末で確認できた消費を引いた残りで発行する。
他端末の未共有の消費は差し引かれない。
CLIでは `atk config reissue-reset <ID>` で発行する。

SSH先のatkが旧版の場合は、その端末のatkを更新すると設定画面から競合を解決できる。
CLIでは`atk config migrate-shared`で同じ比較を表示し、表示された`--apply`のコマンドで適用する。
