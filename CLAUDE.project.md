# カスタム指示 (プロジェクト固有)

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

## 関連ドキュメント

- @README.md
