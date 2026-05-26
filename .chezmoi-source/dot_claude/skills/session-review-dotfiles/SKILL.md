---
name: session-review-dotfiles
description: >
  ユーザー手動起動またはStopフックからの明示的な呼び出し指示でのみ起動する。
  `agent-toolkit:session-review`スキルへのdotfiles個人環境向け拡張章として、
  pyfltrとagent-toolkitの改善提案章を提供する。
# 本スキルは`agent-toolkit:session-review`スキルと併用される拡張章。
# 4ステップ手順は`agent-toolkit:session-review`が担い、本スキルは拡張章のみを提供する。
# 本ファイル修正時は`scripts/claude_hook_stop.py`の誘導文も同期させる。
---

# セッション振り返り（dotfiles拡張章）

## 提示フォーマット

`agent-toolkit:session-review`スキルのステップ3で示すプロジェクトドキュメント章に続き、
以下の2章を同一フォーマットで追加する。

```markdown
## pyfltr改善提案

- 対象ファイル — 提案内容
- ...

## agent-toolkit改善提案

- 対象ファイル — 提案内容
- ...
```

各項目はセクション見出し配下に「- 対象ファイル — 提案内容」の形で1項目1行・1コンセプトで簡潔に書く。
提案が無い章には同見出し配下に「提案無し」とのみ書く。
当該セッションで利用しなかった項目（例: pyfltr未使用）はスキップしてよい。

自己完結性の要件（観測した具体事象・改善後の振る舞いと根拠の明記、暗黙参照表現の禁止）は
`agent-toolkit:session-review`スキルのステップ3に従う。

## 1. pyfltr

対象: pyfltr本体の挙動・メッセージ。
反映はユーザーが別途行う前提のため、提案までにとどめる。

pyfltrプロジェクトで作業中の場合に限り、`agent-toolkit:session-review`スキルの
プロジェクトドキュメント章へ統合する。

## 2. agent-toolkit

対象: `agent-toolkit`プラグイン（スキル・フック・サブエージェント。`skills/pyfltr-usage/SKILL.md`を含む）と
`~/.claude/rules/agent-toolkit/`配下のルール。
反映はユーザーが別途行う前提のため、提案までにとどめる。

dotfilesプロジェクトで作業中の場合に限り、`agent-toolkit:session-review`スキルの
プロジェクトドキュメント章へ統合する。

## ステップ4の適用範囲

`agent-toolkit:session-review`スキルのステップ4（Apply with Approval）は
プロジェクトドキュメント改善提案のみを対象とする。
本スキルが追加するpyfltr章・agent-toolkit章は適用対象外（ユーザー側で別途反映する）。
