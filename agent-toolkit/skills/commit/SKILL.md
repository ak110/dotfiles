---
name: commit
user-invocable: false
description: >
  commitを作成・取り込む操作（通常commit、amend、fixup、cherry-pick、merge、rebase、revertなど）に着手する直前、
  push、CI確認、公開工程、リリースに着手する前、
  既存未コミット差分を不採用として破棄する前、
  またはコミットメッセージ案（計画ファイル・PR説明など）を書く時点で起動する。
# 編集時の注意点:
# コミット境界判断のSSOTは本ファイル「通常commit」節。
# 本文中の判断分岐・規範削除・機能的な規範変更はchoreへ分類しない。
---

# コミット運用とコミットメッセージ

履歴変更と公開にはGit、対象リポジトリ、公開先を操作できるCLIが必要である。
Gitを操作できない環境では差分からメッセージ案と確認条件を返し、commit・push・CI監視はClaude CodeやCodexの機能で行う。
案の作成は[文章の品質基準](references/quality-standards.md)に従う。

本書はgit commitの運用とメッセージを定める。
識別子確認・履歴操作・公開の前に`references/git-identifier.md`を読む。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文で基準を使う資料の選択・取得時 | `agent-basics:writing-standards/references/investigation-sources.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/investigation-sources.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `references/quality-standards.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/quality-standards.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:commit/references/ci-failure-handling.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/ci-failure-handling.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:wi-standards/references/analysis-records.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/analysis-records.md` |
| 保存済みの検証結果を再利用する前 | `agent-basics:check-execution/references/verification-scope-reruns.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/verification-scope-reruns.md` |
| 未コミット検証の実行前とcommit後の適用確認前 | `../../share/verification-evidence-observation.md` | 判断 | `read:agent-toolkit:*/share/verification-evidence-observation.md` |
| 取得版の異なる結果をレビューへ渡す前 | `../../share/exec-review-recording-grounds.md` | 判断 | `read:agent-toolkit:*/share/exec-review-recording-grounds.md` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| Git識別子・worktree・履歴・公開済みの状態を判定または操作する前 | `references/git-identifier.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/git-identifier.md` | |
| 公開工程またはリリース操作に着手する前 | `references/publish.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/publish.md` | |
| amend、fixup、autosquash、rebaseまたはpush済み判定の前 | `references/history-rewrite.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/history-rewrite.md` | |
| 実際にpushする前 | `references/push-and-ci.md` | 操作 | `bash:git push` | |
| コミットメッセージを書く前（計画ファイルやPR説明へコミットメッセージ案を書く前を含む） | `references/message.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/message.md` | |
| 既存未コミット差分を不採用として破棄する前 | `references/stash-cleanup.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/stash-cleanup.md` | |
| 一時ブランチ、`git stash`の退避、hookの退避ファイルまたは別パスへの複製を削除する前と、委譲先の完了報告が退避識別子か複製パスを開示したとき | `references/stash-cleanup.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/stash-cleanup.md` | |

## コミットメッセージ

件名のフォーマット、type、description、本文、および`Co-Authored-By:`の書式は`references/message.md`に従う。
計画ファイルやプルリクエストの説明文に記載するコミットメッセージ案にも、同じ基準を適用する。

## 工程で参照する運用資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| CI失敗を扱う前 | `agent-toolkit:commit/references/ci-failure-handling.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/ci-failure-handling.md` | |
| 計画ファイルを作成する時と、公開インターフェース、エンドユーザー向けのメッセージ、永続データの形式を新設・変更する時 | `agent-toolkit:commit/references/design-time.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/design-time.md` | |
| テストコードを書く時とレビューする時、条件分岐と判定条件を新設・変更する時 | `agent-toolkit:commit/references/testing.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/testing.md` | |

## 参照資料の取得

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/ci-failure-handling.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/ci-failure-handling.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 検証に関する判断・作業の前 | `references/commit-verification.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/commit-verification.md` |
| 通常commitに関する判断・作業の前 | `references/commit-creation.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/commit-creation.md` |
| WI実装commitの対応に関する判断・作業の前 | `references/commit-wi-records.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/commit-wi-records.md` |
