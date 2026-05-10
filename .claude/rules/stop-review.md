---
paths:
  - "agent-toolkit/scripts/stop_advisor.py"
  - "scripts/claude_hook_stop.py"
  - ".chezmoi-source/dot_claude/skills/session-review/**"
---

# 振り返りHook/Skill

振り返りを促すHook/Skillが3カ所に組み込まれている。
配布先・タイミング・対象スコープが異なるため分けて管理する。

- `agent-toolkit/scripts/stop_advisor.py` — 配布物。プロジェクトドキュメント全般が対象
- `scripts/claude_hook_stop.py` — dotfiles個人環境専用。
  agent-toolkit本体・agent-toolkitのルールファイル・pyfltrの振り返りを担当。
  対象プロジェクトはセッションのcwdに応じて切り替わる
- `.chezmoi-source/dot_claude/skills/session-review/SKILL.md` — ユーザー手動起動スキル

3カ所は同じStopイベントで並列発火する前提のため、共通指示（自己完結性・行フォーマット・出力スタイル等）は
`stop_advisor.py`側のreasonへ集約し、`claude_hook_stop.py`側は章固有の指示のみ記述する。
3カ所の内容を変更する際は同期漏れに注意する。
