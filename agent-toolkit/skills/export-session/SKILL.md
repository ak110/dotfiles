---
name: export-session
description: >
  Claude CodeやCodexのセッション記録をmarkdownに出力・保存する依頼（「会話ログ」「セッション履歴」の出力・保存を含む）で使う。
# descriptionの「会話ログ」「セッション履歴」は、起動条件をユーザーが依頼で使う語と一致させるために残す。
---

# セッション記録のmarkdown出力

`atk agents logs`でClaude CodeとCodexの記録をmarkdownへ変換する。

## 使い方

セッション識別子、`--project-dir`、`--all`のいずれかで対象を指定し、`--format markdown`を付ける。
必要に応じて`--latest`、`--include-thinking`、`--include-subagents`、`--output-dir`で範囲と出力内容を選ぶ。
オプションの全容は`atk agents logs --help`で確認する。

### 実行例

現在のセッションはClaude Codeでは`CLAUDE_CODE_SESSION_ID`、Codexでは`CODEX_THREAD_ID`を識別子に使う。
例えば、Claude Codeの現在の記録を標準出力へ変換するには次を実行する。

```bash
atk agents logs "$CLAUDE_CODE_SESSION_ID" --format markdown
```

プロジェクト単位なら`--project-dir`と`--latest 3`などで範囲を指定し、全件なら`--all`を使う。
保存先は`--output-dir`、thinkingと委譲先の記録は`--include-thinking`と`--include-subagents`で指定する。
