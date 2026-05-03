---
name: export-session
description: >
  Claude Codeのセッション履歴をmarkdownにエクスポートする。
  「セッションをエクスポート」「会話履歴を保存」「セッション履歴をmarkdownに」「この会話を出力」などのキーワードで使用する
---

# セッション履歴のmarkdownエクスポート

`claude-session-export`コマンドを使い、Claude Codeのセッション履歴（JSONL）を人間が読みやすいmarkdownに変換する。

## 使い方

利用可能なオプション:

```text
スコープ（排他）:
  FILE...              指定したJSONLファイルを変換
  --current            現在のセッションを変換
  --project-dir DIR    指定ディレクトリの全セッションを変換
  --all                全プロジェクトの全セッションを変換

フィルター:
  --latest N           直近N件に限定

コンテンツ制御:
  --include-thinking   thinkingブロックを含める
  --include-subagents  サブエージェントの会話を含める
  --no-tool-details    ツール呼び出しを簡略化

出力:
  --output-dir DIR     出力先ディレクトリ（未指定時はstdout）
```

標準出力に直接表示する場合はstdoutへ出力される（`--output-dir`省略時）。

### 実行例

最小（現在のセッションをstdoutへ出力）:

```bash
uv tool run claude-session-export --current
```

標準（現在のプロジェクトの直近3件をディレクトリに保存）:

```bash
uv tool run claude-session-export --project-dir=/path/to/project --latest=3 --output-dir=./exports
```

詳細（thinkingブロック・サブエージェント含む全セッション一括変換）:

```bash
uv tool run claude-session-export --all --include-thinking --include-subagents --output-dir=~/claude-sessions
```
