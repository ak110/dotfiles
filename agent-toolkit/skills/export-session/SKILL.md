---
name: export-session
description: >
  Claude CodeやCodexのセッション記録をmarkdownに出力・保存する依頼（「会話ログ」「セッション履歴」の出力・保存を含む）で使う。
# descriptionの「会話ログ」「セッション履歴」は、起動条件をユーザーが依頼で使う語と一致させるために残す。
---

# セッション記録のmarkdown出力

`atk agents logs <対象> --format markdown`でClaude CodeとCodexの記録をmarkdownへ変換する。
対象はセッション識別子、`--project-dir`、`--all`のいずれかで指定する。現在のセッションの識別子はClaude Codeでは`CLAUDE_CODE_SESSION_ID`、Codexでは`CODEX_THREAD_ID`にある。
範囲と出力内容（`--latest`、`--include-thinking`、`--include-subagents`、`--output-dir`など）は`atk agents logs --help`で選ぶ。
