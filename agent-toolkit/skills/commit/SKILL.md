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

## 通常commit

- 安全にレビュー・revertできる単位を保つため、変更目的、依存関係、レビュー・revertのしやすさで分割を決める。
  計画にcommit単位があれば基準にする。
  commitの単位は作業項目（WI）の識別と切り離して決め、同じファイル群へ一体で適用され同じ検証で確かめる変更は、複数の作業項目にまたがっても1つのcommitにまとめてよい。
  作業項目ごとの採否とcommitとの対応は、`references/commit-wi-records.md`「WI実装commitの対応」の記録で保つ。
  各コミットの中間状態（中間`HEAD`）でも、その時点の公開契約とテストを満たすように整える（努力目標。git bisectやrevertで各コミットを単独で安全に扱えるようにするため）。
  付帯作業を関連する開発のコミットに含める場合も、その開発作業と同じ変更目的として扱う。
- commit権限はメインが持つ。
  サブエージェントは明示的な許可か`exec.subagent.md`の正規手順がある場合に限りcommitする。
  `git push`は委譲先への明示指示がある場合を除き、委譲元が行う。
- WI実装はcommit前に`references/commit-wi-records.md`「WI実装commitの対応」の手順1を行う。
  他セッションの成果を守るため、自身の担当範囲だけをステージングし、ユーザーや他セッションの未コミット変更を残す。
  commit直前に開始時からベースcommitが変わっていれば、`agent-toolkit/share/rules/common/02-agent-operations.md`「作業中に観測したリポジトリ状態変化の扱い」に従う。
- コミットメッセージの書式は`references/message.md`に従う。
  計画ファイルやプルリクエストの説明文に記載するコミットメッセージ案にも、同じ基準を適用する。

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| Git識別子・worktree・履歴・公開済みの状態を判定または操作する前 | `references/git-identifier.md` | | | |
| 公開工程またはリリース操作に着手する前 | `references/publish.md` | | | |
| amend、fixup、autosquash、rebaseまたはpush済み判定の前 | `references/history-rewrite.md` | | | |
| 実際にpushする前 | `references/push-and-ci.md` | 操作 | `bash:git push` | |
| CI失敗を扱う前 | `references/ci-failure-handling.md` | | | |
| コミットメッセージを書く前（計画ファイルやPR説明へコミットメッセージ案を書く前を含む） | `references/message.md` | | | |
| 既存未コミット差分を不採用として破棄する前 | `references/stash-cleanup.md` | | | |
| 一時ブランチ、`git stash`の退避、hookの退避ファイルまたは別パスへの複製を削除する前と、委譲先の完了報告が退避識別子か複製パスを開示したとき | `references/stash-cleanup.md` | | | |
| commitの実行前（ステージング対象の確認、hookの迂回、hookの打ち切りと退避の復元） | `references/commit-creation.md` | | | |
| 検証に関する判断・作業の前 | `references/commit-verification.md` | | | |
| WI実装commitの対応に関する判断・作業の前 | `references/commit-wi-records.md` | | | |
