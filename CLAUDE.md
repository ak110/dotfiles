# カスタム指示

## コマンド

```bash
make setup    # 初回セットアップ
make test     # format + lint + test (CI相当)
make fix      # ruff自動修正
make format   # フォーマットのみ
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

### Windows PowerShell スクリプトの注意事項

- chezmoi実行環境のPowerShellでは `$HOME`, `$env:USERPROFILE` 等の環境変数やPowerShell変数が不安定
- パスには `{{ .chezmoi.homeDir }}` テンプレート変数を使い、リテラル文字列として埋め込むこと

### ディレクトリ構造の注意

- `.chezmoi-source/` → chezmoi のソースディレクトリ (`.chezmoiroot` で指定)
  - `dot_claude/` → chezmoi が `~/.claude/` にデプロイ (グローバルユーザー設定)
- `.claude/` → dotfilesプロジェクト自体のClaude Code設定 + claudizeテンプレート置き場
  - chezmoi はドットプレフィックスのディレクトリを自動無視するため衝突しない

## ファイル構成

- `CLAUDE.md` -- プロジェクト固有の指示 (このファイル)
- `.claude/rules/agent.md` -- 汎用的なエージェント向けベース指示 (`claudize` コマンドで同期)
  - 編集は ~/dotfiles/.claude/rules/agent.md で行い、`claudize` で各プロジェクトへ配布する
  - プロジェクト側では直接編集しない
- `.claude/rules/*.md` -- 言語別ルール (`claudize` コマンドで初回のみ配布)

## 関連ドキュメント

- @README.md
- docs/ssh-config.md
