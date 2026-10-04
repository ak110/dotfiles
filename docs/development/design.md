# 設計記録の索引

機構の目的、構造の理由、知識境界と却下した代替案を、主題別の本文から選んで読むための索引である。
ユーザーが確定した方針・意向は[concepts.md](concepts.md)、障害の経緯は[incidents.md](incidents.md)が扱う。
実行時に適用する規範は現行のルールファイルとスキルが定め、設計記録はその背景と構造の理由を保持する。
既存の主題の記録は対応する本文の節へ追記する。独立した新しい主題は主題別の本文へ置き、本索引の対応表へ行を追加する。
各節の見出しと機構の詳細は主題別の本文で読み、本索引には節の一覧や本文を複製しない。

| 主題 | 対象と役割 | 記録本文 |
| --- | --- | --- |
| WIキューと要求本文 | WIは依頼・確認を記録する項目。AWIはエージェントが処理する作業項目で、キューは項目の状態と順序を管理する | [design-wi.md](design-wi.md) |
| 継続処理の起動・隔離・制御 | process-loopは作業項目の処理を繰り返し起動する機構。レーンは並行して作業する担当単位、worktreeは担当ごとのGit作業ツリー | [design-process-loop.md](design-process-loop.md) |
| 委譲サーバーの公開ツールと構造 | agents_serverは別のエージェントへ作業を委譲するサーバー。MCPはツールの呼び出し口、backendは委譲先を動かす実装 | [design-agents-server.md](design-agents-server.md) |
| 委譲先の稼働状態・起動診断・通知 | agents_serverで起動した会話の状態を把握する機構。statuslineはその状態を表示する欄 | [design-agents-runtime.md](design-agents-runtime.md) |
| 委譲の継続・待機・成果受渡し | 別のエージェントへ渡した作業を継続し、終了を待ち、結果を受け取る手順 | [design-delegation.md](design-delegation.md) |
| 担当の責務と工程間の知識境界 | 作業の選定・計画・実装・レビューの担当間で、入力と結果を渡す境界 | [design-workflow-boundaries.md](design-workflow-boundaries.md) |
| ホスト間の規範配置と適用境界 | Claude Code・Codexなどの実行ホストへ、エージェントの指示を配置して適用する仕組み | [design-hosts.md](design-hosts.md) |
| 振り返り・知見集約・原因分析・監査 | session-reviewは作業終了時の振り返り。AWIなどから得た知見や欠陥の原因を次の作業へ残す工程 | [design-session-review.md](design-session-review.md) |
| 計画と実行レビューへの受渡し | 作業項目から計画を作成し、実装結果が要件に沿うか確認する実行レビューへ渡す工程 | [design-planning.md](design-planning.md) |
| 検収根拠とレビュー担当間の知識境界 | 完成条件を満たす証拠を保存し、実行レビューと画面の使いやすさを確認するレビューへ渡す仕組み | [design-review-evidence.md](design-review-evidence.md) |
| フックの責務・通知・セッション状態 | hookはエージェントの操作前後などに自動で起動する処理。通知や操作の警告・遮断を担う | [design-hooks.md](design-hooks.md) |
| 一時成果物とキューリポジトリの回復 | managed-tempは一時成果物の管理領域。MQはWIキューの管理層で、項目を保存するGitリポジトリの分岐回復を扱う | [design-storage.md](design-storage.md) |
| branch・CI所有権・公開・終端 | developは開発用、masterはリリース用のGit branch。CIは変更の自動検証で、公開と作業終了に関わる | [design-release.md](design-release.md) |
| CLIの入力・結果・公開境界 | atkはagent-toolkitのコマンド。CLIはコマンドラインからの呼び出し口で、引数と結果の契約を扱う | [design-cli.md](design-cli.md) |
| 画面統合と計画一覧の対象判定 | atk serveは作業状況や計画をブラウザーで確認する画面を提供するコマンド | [design-serve.md](design-serve.md) |
| パッケージと検証環境の配置 | agent-toolkitのPythonパッケージとテスト環境の配置。pyfltrは整形・静的解析・テストをまとめて実行するツール | [design-packages.md](design-packages.md) |
