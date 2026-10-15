---
name: export-session
description: >
  Claude CodeやCodexのセッション記録をmarkdownに出力・保存する依頼（「会話ログ」「セッション履歴」の出力・保存を含む）で使う。
# descriptionの「会話ログ」「セッション履歴」は、起動条件をユーザーが依頼で使う語と一致させるために残す。
---

# セッション記録のmarkdown出力

記録の取得と保存には、対象の記録へ届くファイル操作と`atk`が必要である。これらが無い環境では、ユーザーが提供した会話本文だけをMarkdownへ整え、出所をその提供範囲へ限定する。ホストの記録の取得とファイルへの保存は、CLIを使えるClaude CodeまたはCodexで行う。見出しや説明の文には[文章の品質基準](references/quality-standards.md)を使う。

`atk agents logs <対象> --format markdown`でClaude CodeとCodexの記録をmarkdownへ変換する。
対象はセッション識別子、`--project-dir`、`--all`のいずれかで指定する。
現在のセッションの識別子は、Claude Codeのメインでは`rules-main.claude-code.md`「自身の会話の記録」が案内するSessionStart通知の「この会話のセッションID」の値を使う。
Claude Codeのそれ以外のエージェントでは`CLAUDE_CODE_SESSION_ID`、Codexでは`CODEX_THREAD_ID`を使う。
範囲と出力内容（`--latest`、`--include-thinking`、`--include-subagents`、`--output-dir`など）は`atk agents logs --help`で選ぶ。
