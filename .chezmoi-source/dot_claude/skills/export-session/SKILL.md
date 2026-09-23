---
name: export-session
description: >
  Claude Codeのセッション履歴をmarkdownにエクスポートする。
  セッション履歴（会話ログ）をmarkdownに出力・保存する依頼で使う
---

# セッション履歴のmarkdownエクスポート

`claude-session-export`コマンドを使い、Claude Codeのセッション履歴（JSONL）を人間が読みやすいmarkdownに変換する。

## 使い方

変換対象のスコープを指定し、必要に応じて件数・出力内容・出力先を限定する。
オプションの全容は`--help`で確認する。

### 実行例

最小（現在のセッションをstdoutへ出力）:

```bash
claude-session-export --current
```

標準（現在のプロジェクトの直近3件をディレクトリに保存）:

```bash
claude-session-export --project-dir=/path/to/project --latest=3 --output-dir=./exports
```

詳細（thinkingブロック・サブエージェント含む全セッション一括変換）:

```bash
claude-session-export --all --include-thinking --include-subagents --output-dir=~/claude-sessions
```
