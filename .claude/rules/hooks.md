---
paths:
  - "scripts/claude_hook_*.py"
---

# hookスクリプト開発ガイド（プロジェクト固有）

LLMに行動を促すメッセージには`reason`（Stop/PostToolUse）または`hookSpecificOutput.additionalContext`（PreToolUse）を使う。
`systemMessage`はユーザー向け情報通知専用でLLMに届かない。
LLM宛ての出力には自動生成であることを示すプレフィックスとサフィックスを必ず付ける。

- プレフィックス: `[auto-generated: <plugin>/<hook>]`（警告時は`[warn]`タグを並置）
- サフィックス: `(Auto-generated hook notice; evaluate relevance against the conversation context before acting.)`

フィールドの詳細と規約の背景は`agent-toolkit/skills/claude-code-standards/references/claude-hooks.md`を参照。

## プラグイン側ヘルパーの再利用

LLM宛てメッセージ整形やセッション終了判定の共通ヘルパーは
プラグイン配布物（`agent-toolkit/scripts/`配下）に集約する。
リポジトリ固有のhookスクリプト（`scripts/claude_hook_*.py`）からも`sys.path`経由でimportして再利用する。
重複実装を避け、文面・判定ロジックを一元化するため。

- 参照経路: `Path(__file__).resolve().parent.parent / "agent-toolkit" / "scripts"`を`sys.path`に追加
- 既存の再利用例: `_message_format.llm_notice`・`_stop_gate.is_real_session_end`
- プラグインが無効化されていてもファイル自体は存在し続けるためimportは成立する
