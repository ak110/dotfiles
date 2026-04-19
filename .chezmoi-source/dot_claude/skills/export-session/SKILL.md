---
name: export-session
description: >
  Claude Codeのセッション履歴をmarkdownにエクスポートする。
  「セッションをエクスポート」「会話履歴を保存」「セッション履歴をmarkdownに」「この会話を出力」などのキーワードで使用する
user-invocable: true
---

# セッション履歴のmarkdownエクスポート

`claude-session-export`コマンドを使い、Claude Codeのセッション履歴（JSONL）を人間が読みやすいmarkdownに変換する。

## 使い方

引数に応じて適切なオプションを構成し、`uv tool run claude-session-export`で実行する。

### 引数なし: 現在のセッションをエクスポート

```bash
uv tool run claude-session-export --current
```

標準出力にmarkdownが出力される。
ファイルに保存する場合は`--output-dir`を指定する。

### 引数あり: オプションを適切に構成して実行

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

### 実行例

```bash
# 現在のプロジェクトの直近3件をディレクトリに出力
uv tool run claude-session-export --project-dir /path/to/project --latest 3 --output-dir ./exports

# 全セッションを一括変換
uv tool run claude-session-export --all --output-dir ~/claude-sessions

# thinkingブロック付きで現在のセッションを出力
uv tool run claude-session-export --current --include-thinking
```
