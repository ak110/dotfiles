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

## 起動方針

本スキルは`agent-toolkit:session-review`スキルと必ず併用する。
Stopフックの誘導でどちらか一方のみを起動して報告を終えてはならない。
両スキルを起動したうえで、両者の章を1つのレポートにまとめて提示する。
提示する章は最大3章（プロジェクトドキュメント章・pyfltr改善提案章・agent-toolkit改善提案章）とする。
各章の取捨（pyfltr未使用時の省略、作業中プロジェクト自身に関わる章のプロジェクトドキュメント章への統合）は
「提示フォーマット」節の規定に従う。

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

### pyfltr改善提案

対象: pyfltr本体の挙動・メッセージ。

pyfltrプロジェクトで作業中の場合は、`agent-toolkit:session-review`スキルのプロジェクトドキュメント章へ統合する。

### agent-toolkit改善提案

対象: `agent-toolkit`プラグイン（スキル・フック・サブエージェント。`skills/pyfltr-usage/SKILL.md`を含む）と
`~/.claude/rules/agent-toolkit/`配下のルール。

dotfilesプロジェクトで作業中の場合は、`agent-toolkit:session-review`スキルのプロジェクトドキュメント章へ統合する。

## ステップ4の適用範囲

`agent-toolkit:session-review`スキルのステップ4（Apply with Approval）は
プロジェクトドキュメント改善提案のみを対象とする。
本スキルが追加するpyfltr章・agent-toolkit章は適用対象外（ユーザー側で別途反映する）。
