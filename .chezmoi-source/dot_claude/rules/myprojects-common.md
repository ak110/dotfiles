<agent-toolkit-auto-inserted source="dotfiles" kind="rules" path=".chezmoi-source/dot_claude/rules/myprojects-common.md">
# myprojects-common.md: ホスト共通の個人プロジェクト規範

実行ホストとコーディングエージェントの種別によらず、同一作者の個人プロジェクト全体へ適用する規範を置く。
ホストごとに異なるプロジェクト一覧と同期方針は、Claude Codeへ配布する`myprojects.md`が扱う。

## AWI処理の開始時の横断同期

個人プロジェクトで`agent-toolkit:process-wi`を起動したときは、pickerの起動より前に`ak110-projects-operations`スキルを起動する。
同期調査と依存更新の要否の判定結果は、当該セッションの後続の工程へ渡す。
起動名はClaude Codeでは`/ak110-projects-operations`、Codexでは`ak110-projects-operations`とする。

## 個人プロジェクトのリリース入口

個人プロジェクトでpatch、minor又はmajorのリリースを求められたときは、具体的な公開コマンドを選ぶ前に
`ak110-projects-operations`スキルを起動し、同スキルの「リリース運用」に従う。
agent-toolkit自身のversion bump、個人プロジェクト外、Dockerイメージの再構築及びworkflow内部の処理は対象外とする。
</agent-toolkit-auto-inserted>
