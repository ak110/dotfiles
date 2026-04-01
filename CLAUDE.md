# カスタム指示

## コマンド

```bash
make setup    # 初回セットアップ
make test     # format + lint + test (CI相当)
make fix      # ruff自動修正
make format   # フォーマットのみ
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

## ファイル構成

- `CLAUDE.md` -- プロジェクト固有の指示 (このファイル)
- `.claude/rules/agent.md` -- 汎用的なエージェント向けベース指示 (`claudize` コマンドで同期)
  - 編集は ~/dotfiles/.claude/rules/agent.md で行い、`claudize` で各プロジェクトへ配布する
  - プロジェクト側では直接編集しない
- `.claude/rules/*.md` -- 言語別ルール (`claudize` コマンドで初回のみ配布)

## 関連ドキュメント

- @README.md
- docs/ssh-config.md
