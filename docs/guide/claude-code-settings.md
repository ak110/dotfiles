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
- `env.PLAYWRIGHT_MCP_OUTPUT_DIR`: ホーム配下の`.cache/playwright-mcp`の絶対パス（Playwright MCPが作業中のリポジトリへ`.playwright-mcp/`を残さないようにする）
- `permissions`: 許可・拒否するツールやパターンを記述
 （[例](https://github.com/ak110/dotfiles/blob/master/share/claude_settings_json_managed.json)）

### `/config`コマンド

- `Verbose output`: 有効
- `Default permission mode`: `Auto mode`
- `Language`: `Japanese`

### `/plugin`コマンド

claude-plugins-officialのプラグインは次の方針で扱う。

- 推奨: `context7`・`typescript-lsp`（`npm install -g typescript-language-server typescript`が必要）
- 任意: `skill-creator`
- 参照のみ（有効化しない）: `plugin-dev`・`mcp-server-dev`（エージェントが品質基準の案内に従って資料として読むため導入は不要。有効化するとスキルが自動で起動し、質問が増える）
- 無効: `pyright-lsp`（Claude Codeがインストールを推奨するが誤動作が発生するため、インストール後に`Disable`）
- 無効: `claude-md-management`（`CLAUDE.local.md`を`.claude.local.md`と誤認し、`AGENTS.md`を走査しないため、インストール済みなら自動で無効化）

### VSCode設定（任意）

ターミナル内での右クリックによる意図しない貼り付けを防ぐ場合は、`settings.json`へ以下を追加。

```json
{
  "terminal.integrated.rightClickBehavior": "nothing"
}
```
