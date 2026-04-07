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
- `run_after_post-apply.sh.tmpl`
  ↔ `run_after_post-apply-windows.ps1.tmpl`
- `share/claude_settings_json_managed.posix.json`
  ↔ `share/claude_settings_json_managed.win32.json`
- `install.sh`
  ↔ `install.ps1`
- `install-claude.sh`
  ↔ `install-claude.ps1`

新しいOS別 `run_*` スクリプトを追加する場合は `.chezmoiignore` にも除外エントリを追加すること。

### Windows PowerShell スクリプトの注意事項

- `.ps1.tmpl` は `.gitattributes` で `eol=crlf` を指定済み（Windows PowerShell 5.1はLF改行で行解釈が壊れる）
- 全スクリプト冒頭に `Set-StrictMode -Version Latest` と `$ErrorActionPreference = 'Stop'` を記述すること

### ディレクトリ構造の注意

本リポジトリには `.claude` を含むディレクトリが3系統あり、取り違えると影響範囲が全く異なる事故につながる。指示を受けた際はどの階層を指すか必ず確認すること。

- `.chezmoi-source/dot_claude/` — 配布元。chezmoi が `~/.claude/` にデプロイする。ここを書き換えると `chezmoi apply` 後に全環境へ反映される (グローバルユーザー設定の原本)
- `~/.claude/` — デプロイ先 (個人ホーム)。`chezmoi apply` で上書きされるため直接編集してはならない。
  ユーザーが「`~/.claude` の設定を変えて」と言った場合、実際に編集すべきは上記の `.chezmoi-source/dot_claude/` である
- `.claude/` (本リポジトリルート) — dotfiles リポ自身の Claude Code プロジェクト設定 + claudize テンプレート置き場。配布対象外で、このリポジトリで作業する Claude にしか影響しない

chezmoi はドットプレフィックスのディレクトリ (`.claude/` など) を自動無視するため `.chezmoi-source/dot_claude/` と衝突しない。

### ユーザー指示の解釈

- 用語・パスがあいまいな場合は推測で進めず、必ず確認する
- 特に `.claude` 系ディレクトリに関する指示は上記の通り混同しやすいので注意
- 「グローバル」「ユーザースコープ」「本プロジェクト用」のどれかを明示してもらうと取り違えにくい
- 確認して決まった運用ルールは CLAUDE.md や `rules/` 配下に追記し、次セッションへ引き継ぐ

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
