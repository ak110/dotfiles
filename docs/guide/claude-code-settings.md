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
`/restart`と`/update`は、委譲先の終了を待つ背景コマンド`atk agents wait`を実行している場合、自身の会話の待機だけを停止する。
再起動後は同じ会話で待機と作業を自動で続ける。
ほかの背景処理やCode ModeのREPL内実行は停止せず、実行中はClaude Code本体が再起動を拒否することがある。
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
設定を保存した直後は効いていないように見えても、反映まで時間をおいてから新しい会話で確認する。
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
