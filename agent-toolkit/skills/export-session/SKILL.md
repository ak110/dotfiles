---
name: export-session
description: >
  Claude CodeやCodexのセッション履歴（会話ログ）をmarkdownに出力・保存する依頼で使う。
---

# セッション履歴のmarkdown出力

`atk agents logs`でClaude CodeとCodexの記録をmarkdownへ変換する。

## 使い方

セッション識別子、`--project-dir`、`--all`のいずれかで対象を指定し、`--format markdown`を付ける。
必要に応じて`--latest`、`--include-thinking`、`--include-subagents`、`--output-dir`で範囲と出力内容を選ぶ。
オプションの全容は`atk agents logs --help`で確認する。

### 実行例

現在のClaude Codeセッションを標準出力へ変換する:

```bash
atk agents logs "$CLAUDE_CODE_SESSION_ID" --format markdown
```

Codexでは`CODEX_THREAD_ID`を使う:

```bash
atk agents logs "$CODEX_THREAD_ID" --format markdown
```

プロジェクトの直近3件をディレクトリへ保存する:

```bash
atk agents logs --project-dir /path/to/project --latest 3 --format markdown --output-dir /path/to/exports
```

thinkingとサブエージェントを含めて全件を変換する:

```bash
atk agents logs --all --format markdown --include-thinking --include-subagents --output-dir /path/to/exports
```
