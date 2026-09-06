# myprojects-common.md: ホスト共通の個人プロジェクト規範

実行ホストとコーディングエージェントの種別によらず、同一作者の個人プロジェクト全体へ適用する規範を置く。
ホストごとに異なるプロジェクト一覧と同期方針は、Claude Codeへ配布する`myprojects.md`が扱う。

## AWI処理の開始時の横断同期

個人プロジェクトで`agent-toolkit:process-wi`を起動したときは、pickerの起動より前に`sync-cross-project`スキルを起動する。
同期調査と依存更新の要否の判定結果は、当該セッションの後続の工程へ渡す。
起動名はClaude Codeでは`/sync-cross-project`、Codexでは`sync-cross-project`とする。
