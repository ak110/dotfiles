# CLAUDE.md: dotfiles

本リポジトリはchezmoi管理のdotfilesリポジトリ。
`.chezmoi-source/`配下を`~/.*`にデプロイする。
多数の小規模なコマンドラインツールや、Claude Code用の共有設定（ルール・プラグイン）も持つ。

## 主なディレクトリ

- `.chezmoi-source/` — chezmoiソースディレクトリ（`dot_` prefix → `~/.*`に反映される）
- `pytools/` — Pythonコマンドラインツール群（uv tool installでインストール）
- `plugins/` — Claude Code用プラグイン（Marketplace経由で他人にも配布）
- `scripts/` — リポジトリ開発専用スクリプト（pre-commit/Makefileから呼ばれる。配布対象外）
- `.claude/` — dotfilesリポ自身のClaude Codeプロジェクト設定（配布対象外）

## 開発手順

- `make update`: 依存更新 + pre-commit autoupdate + pinactアクション更新 + 全テスト実行
  - `make update-actions`: GitHub Actionsのハッシュピン更新のみ（mise経由でpinact実行）
- コミット前の検証方法: `uv run  pyfltr run-for-agent | tail -30`
  - ドキュメントなどのみの変更の場合は省略可（pre-commitで実行されるため）
  - テストコードの単体実行なども極力 `uv run  pyfltr run-for-agent <path>` を使う（pytestを直接呼び出さない）
    - 詳細な情報などが必要な場合に限り `uv run pytest -vv <path>` などを使用

## 注意点

- `.claude`を含むディレクトリが3系統あり取り違えやすい（`.chezmoi-source/dot_claude/` / `~/.claude/` / `.claude/`）。指示の対象を必ず確認する。詳細は[docs/development/development.md](docs/development/development.md)の「ディレクトリ構造の注意」参照
- ホーム配下のファイルを編集する前に`chezmoi managed | grep <相対パス>`で配布対象か確認する。配布対象は`.chezmoi-source/`側を編集する
- `.chezmoi-source/`配下のファイルを削除した場合、chezmoiは配布先を自動削除しない。配布先から除去するには`pytools/post_apply.py`の`_REMOVED_PATHS`に対象パスを追記する（`chezmoi apply`後処理で削除される）
- 配布対象（Linux/Windows両対応）と開発対象（Linuxのみ）でサポート範囲が異なる。ファイル追加時にどちら用か意識する
- プラットフォーム対応ファイル（Linux/Windowsのペア）は一方を変更したらもう一方も確認する。対応ファイル一覧は[docs/development/development.md](docs/development/development.md)の「プラットフォーム対応ファイル」参照
- `agent-basics/*.md`（配布ルール本体）を改訂する際、`docs/guide/claude-code-guide.md`に要約・ステップ数などが再掲されていることが多い。本体変更前に`grep`で参照箇所を確認する
- `.chezmoi-source/dot_claude/rules/agent-basics/agent.md`と`.chezmoi-source/dot_claude/CLAUDE.md`の使い分けに注意する
  - 前者は環境に依存しない汎用ルール、後者は環境依存の前提（codexの利用可否など）を含む指示を書く
  - 後者も参考として他人に提示する想定はあるが、そのまま適用できるとは限らない点で前者と分ける
  - 新規の指示を追加する際は環境依存性の有無で振り分ける
- 依存の追加・更新は通常どおり`uv add`/`uv remove`/`uv lock --upgrade-package`を使う。`UV_FROZEN`はCI/make内で自動適用される

## 関連ドキュメント

- README.md（若干記述量が多いため必要時のみ読み込む）
- @docs/index.md
- @docs/guide/claude-code.md
- @docs/guide/claude-code-guide.md
- @docs/guide/security.md
- @docs/development/development.md
