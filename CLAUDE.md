# カスタム指示

## プロジェクト概要

chezmoi管理のdotfilesリポジトリ。`.chezmoi-source/`配下を`~/.*`にデプロイする。
配布対象のルール・プラグインはClaude Code用の共有設定を兼ねる。

## コマンド

```bash
make setup    # 初回セットアップ
make format   # 整形 + 軽量lint + 自動修正（開発時の手動実行用）
make test     # 全チェック実行（これが通ればコミット可）
make update   # 依存アップグレード＋全チェック（pinactによるアクション更新含む）
```

## ディレクトリ構造の要点

- `.chezmoi-source/` — chezmoiソースディレクトリ（配布対象。`dot_` prefix → `~/.*`）
- `pytools/` — Pythonコマンドラインツール群（uv tool installでインストール）
- `plugins/` — Claude Code用プラグイン（Marketplace経由で他人にも配布）
- `scripts/` — リポジトリ開発専用スクリプト（pre-commit/Makefileから呼ばれる。配布対象外）
- `.claude/` — dotfilesリポ自身のClaude Codeプロジェクト設定（配布対象外）

## 重大な注意点

- `.claude`を含むディレクトリが3系統あり取り違えやすい（`.chezmoi-source/dot_claude/` / `~/.claude/` / `.claude/`）。指示の対象を必ず確認する。詳細は[docs/development/development.md](docs/development/development.md)の「ディレクトリ構造の注意」参照
- ホーム配下のファイルを編集する前に`chezmoi managed | grep <相対パス>`で配布対象か確認する。配布対象は`.chezmoi-source/`側を編集する
- 配布対象（Linux/Windows両対応）と開発対象（Linuxのみ）でサポート範囲が異なる。ファイル追加時にどちら用か意識する
- プラットフォーム対応ファイル（Linux/Windowsのペア）は一方を変更したらもう一方も確認する。対応ファイル一覧は[docs/development/development.md](docs/development/development.md)の「プラットフォーム対応ファイル」参照
- 依存の追加・更新は通常どおり`uv add`/`uv remove`/`uv lock --upgrade-package`を使う。`UV_FROZEN`はCI/make内で自動適用される

## 関連ドキュメント

- @README.md
- @docs/index.md
- @docs/guide/claude-code.md
- @docs/guide/security.md
- @docs/development/development.md
