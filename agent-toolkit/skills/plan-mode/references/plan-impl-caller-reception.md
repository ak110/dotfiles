# 計画実装担当の起動と完了報告の受領

`plan-impl-executor`の起動前準備、完了報告の検収、fresh sessionへの引き継ぎを定める。

## 起動前の準備

対象worktreeの絶対パス、計画ファイル、feedback filename、`git rev-parse HEAD`で取得した計画着手前SHAを記録する。
writerを起動するworktreeは上流追随済みで、staged・unstaged・non-ignored untrackedの全てが空でなければならない。
1つのworktreeへ同時に複数のwriterを起動しない。

起動promptはagent定義、計画ファイル、feedback filename、worktreeの参照を中心とし、
agent定義やtask referenceにある規範、schema、報告書式を再掲しない。

## 完了報告の検収

最初に作業ツリー、commit、変更ファイル、検証結果を実測し、次に報告本文を照合する。
完了報告は少なくとも次を含む。

- `status`、`summary`、`changed`、`external_operations`、`verification`、`plan_check`、`commit_sha`
- `review_status`、`review_rounds`、`review_routes`、`review_targets`、`review_findings`、`review_resolution`
- `pending_confirmations`、`plan_gaps`、`applied_instructions`、`blockers`
- `status: completed | needs_escalation`
- 現行commitと計画着手前SHA
- 実行した検証と警告
- 計画検査の終了コードと警告件数
- 二系統それぞれの対象commit、点検範囲、route/thread
- 正規化した全実指摘、採否、修正状態
- 未解決指摘
- 最終検証と再レビューの結果
- 外部操作、計画外事項、blocker

`completed`は計画、検証、commit、二系統レビュー、計画検査が完了し、未解決の実指摘が無い場合だけ受理する。
最大5ラウンドに達して指摘が残る場合は`needs_escalation`とする。各roundの応答全文、
coverage・impactの専用表や、上限到達時だけの別完了状態は要求しない。

ユーザー指示でレビューを省略する計画では、その原文と適用範囲を計画から照合する。
追加指示がユーザー合意を変える場合は、完了扱いにせずモード別の確認へ送る。

## fresh sessionへの引き継ぎ

セッションを切り替える場合は、計画ファイルの`## 進捗ログ`へ次を正規化して記録する。

- feedback filename
- worktreeとbranch
- 現行commit
- 未解決指摘
- 最終検証
- 二系統のroute/thread

各roundの応答全文は記録しない。fresh sessionは計画ファイルのパスから状態を復元し、
実体と進捗ログが異なる場合は実体を優先してログを更新する。

## pushとCI

実装委譲の範囲はcommitと二系統レビューまでとする。`git push`とCI通過確認は呼び出し元が行う。
push後にCIが失敗した場合は、ログを取得して修正、検証、commit、二系統レビューを再実行してから再度pushする。

## 実体照合と後続工程

完了報告はGit実体と照合し、push、CI通過確認、feedback後始末の順で後続工程へ進む。
