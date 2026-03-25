# カスタム指示

@CLAUDE.base.md

## コマンド

```bash
make setup    # 初回セットアップ
make test     # format + lint + test (CI相当)
make fix      # ruff自動修正
make format   # フォーマットのみ
```

## アーキテクチャ

- chezmoi管理のdotfilesリポジトリ (`dot_` prefix → `~/.*` にデプロイ)
- `pytools/` — Pythonコマンドラインツール群 (uv tool installでインストール)
- `run_onchange_after_*.sh.tmpl` — chezmoi apply時に実行されるスクリプト

## ファイル構成

- `CLAUDE.md` -- プロジェクト固有の指示 (このファイル)
- `CLAUDE.base.md` -- 汎用的なエージェント向けベース指示 (`claudize` コマンドで同期)
  - 編集は ~/dotfiles/CLAUDE.base.md で行い、`claudize` で各プロジェクトへ配布する
  - プロジェクト側では直接編集しない

## 関連ドキュメント

- @README.md
- docs/ssh-config.md
