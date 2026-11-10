# dotfilesの検証で使う値

本書は`.claude/skills/develop-dotfiles/SKILL.md`の読込表から参照する資料である。
変更範囲の検証において本リポジトリで指定する具体的な値、push前に実行すべきチェックとCIのみが実行するチェックの一覧、および`agent-doc-tone`の指摘への対処手順をまとめている。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:check-execution/SKILL.md` | 判断 | `read:agent-basics:*/skills/check-execution/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:/share/verification-operations.md` | 判断 | `read:agent-toolkit:*/share/verification-operations.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `./testing.md` | 判断 | `read:*/skills/develop-dotfiles/references/testing.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:commit/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/commit/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/SKILL.md` | 判断 | `read:agent-basics:*/skills/writing-standards/SKILL.md` |
| テストとCIの所要基準を使う前 | `../../../../agent-basics/skills/writing-standards/references/testing-speed.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/testing-speed.md` |
| テストとCIの所要基準を使う前 | `agent-basics:check-execution/references/diagnostics-results.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/diagnostics-results.md` |
| 共有実行枠のコマンドをシェルから起動する前 | `agent-toolkit:/share/check-execution-operations-launch.md` | 判断 | `read:agent-toolkit:*/share/check-execution-operations-launch.md` |

## 参照資料の取得

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:check-execution/references/verification-scope.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/verification-scope.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 変更範囲の検証の値に関する判断・作業の前 | `verification-values-scope.md` | 判断 | `read:*/.claude/skills/develop-dotfiles/references/verification-values-scope.md` |
| テストとCIの所要基準に関する判断・作業の前 | `verification-values-speed.md` | 判断 | `read:*/.claude/skills/develop-dotfiles/references/verification-values-speed.md` |
| push前のチェックとCIだけが実行するチェックに関する判断・作業の前 | `verification-values-publishing.md` | 判断 | `read:*/.claude/skills/develop-dotfiles/references/verification-values-publishing.md` |
| agent-doc-toneに関する判断・作業の前 | `verification-values-tone.md` | 判断 | `read:*/.claude/skills/develop-dotfiles/references/verification-values-tone.md` |
| 検証コマンドの実行に関する判断・作業の前 | `verification-values-execution.md` | 判断 | `read:*/.claude/skills/develop-dotfiles/references/verification-values-execution.md` |
