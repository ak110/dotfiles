# カスタム指示

## コマンド

```bash
make setup    # 初回セットアップ
make format   # 整形 + 軽量lint + 自動修正（開発時の手動実行用）
make test     # 全チェック実行（これが通ればコミットしてOK）
make update   # 依存アップグレード＋全チェック (pinactによるアクション更新含む)
```

## アーキテクチャ

- chezmoi管理のdotfilesリポジトリ
- `.chezmoiroot` でソースステート（`.chezmoi-source/`）とプロジェクトインフラを分離
- `.chezmoi-source/` 内が chezmoi のソースディレクトリ (`dot_` prefix → `~/.*` にデプロイ)
- `pytools/` — Pythonコマンドラインツール群 (uv tool installでインストール)
- テンプレートからリポジトリルートのファイルを参照する場合は `{{ .chezmoi.workingTree }}` を使用
  - 例: `{{ include (joinPath .chezmoi.workingTree "pyproject.toml") }}`

### プラットフォーム対応ファイル

以下のファイルはLinux/Windowsで対になっている。一方を変更する場合はもう一方も確認すること。

- `bin/executable_update-dotfiles`
  ↔ `bin/executable_update-dotfiles.cmd`
- `run_onchange_after_pytools.sh.tmpl`
  ↔ `run_onchange_after_pytools-windows.ps1.tmpl`
- `run_after_supply-chain-npm.sh.tmpl`
  ↔ `run_after_supply-chain-npm-windows.ps1.tmpl`
- `share/claude_settings_json_managed.posix.json`
  ↔ `share/claude_settings_json_managed.win32.json`
- `install-claude.sh`
  ↔ `install-claude.ps1`

新しいOS別 `run_*` スクリプトを追加する場合は `.chezmoiignore` にも除外エントリを追加すること。

### Windows PowerShell スクリプトの注意事項

- `.ps1.tmpl` は `.gitattributes` で `eol=crlf` を指定済み（Windows PowerShell 5.1はLF改行で行解釈が壊れる）
- 全スクリプト冒頭に `Set-StrictMode -Version Latest` と `$ErrorActionPreference = 'Stop'` を記述すること

### ディレクトリ構造の注意

- `.chezmoi-source/` → chezmoi のソースディレクトリ (`.chezmoiroot` で指定)
  - `dot_claude/` → chezmoi が `~/.claude/` にデプロイ (グローバルユーザー設定)
- `.claude/` → dotfilesプロジェクト自体のClaude Code設定 + claudizeテンプレート置き場
  - chezmoi はドットプレフィックスのディレクトリを自動無視するため衝突しない

### GitHub Actionsのピン留め (pinact)

- 全アクションはコミットハッシュでピン留め（pinactで管理）
- ローカル更新: `make update-actions`（mise経由で`pinact run --update --min-age 1`を実行）
- CI検証: `go install pinact@v3.9.0` + `pinact run --check`（バージョン固定）
- pinactのCIバージョンを更新する場合は全プロジェクトのワークフローを一括更新すること

## 外部ツール仕様の確認

- 本リポジトリで扱うツールの設定や最新仕様を参照する場合は `context7` MCP を優先する
- 対象例: chezmoi / mise / uv / pnpm / pinact / pre-commit など
- 呼び出し順: `resolve-library-id` → `query-docs`
- 知識のスナップショットではなく最新ドキュメントを確認する

## 関連ドキュメント

- @README.md
- @docs/claude-code.md
- @docs/security.md
- @docs/development.md
