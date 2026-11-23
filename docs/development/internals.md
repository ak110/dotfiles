# 実装者向けの内部仕様の索引

`agents_server`の共有状態、hookが共有するセッション状態とフラグ、委譲先が動かない事象の調査手順、環境変数の一覧を、実装を変更・調査する時に選んで読むための索引である。
エージェントが作業のたびに読む規範ではなく、該当する実装に触れる時だけ読む。
状態の保存先、更新する主体、状態ファイルの構成、ログの所在、診断項目、委譲先CLIへ渡す引数、環境変数を変える変更では、同じ変更で該当する資料も直す。

## 共有状態

対象の実装は、`agent-toolkit/agent_toolkit/agents_server_mcp.py`と`agent-toolkit/agent_toolkit/_agents_server/`配下である。
statuslineの`rust/claude-statusline/src/agents_server.rs`と`rust/claude-statusline/src/agents_server/`配下も含む。
これらの共有状態の読書き、保存先、更新主体、利用関係を変える・調べる時に読む。
読み書きする状態、更新する主体、消費側との利用関係から必要な資料を選ぶ。
複数の状態を扱う変更では、共通事項（`docs/development/internals-agents-server-state.md`）と該当する主題別資料の全文を読む。
ファイル名だけで対象を限定せず、判断に必要な前提・例外が別の資料にある場合も、その資料を加える。

| 変更・調査する対象 | 読む資料 |
| --- | --- |
| 共通事項、実行主体、管理サービスの世代登録、判定を確定する前に確認すること | `docs/development/internals-agents-server-state.md` |
| 待機run、終了結果の保持・回収、通知、保留結果や自動再開 | `docs/development/internals-agents-server-state-waiting.md`、`docs/development/internals-agents-server-state-sessions.md`、`docs/development/internals-agents-server-state-projection.md` |
| セッションの登録、追送、引き継ぎ、再開、所有の解決や解放 | `docs/development/internals-agents-server-state-sessions.md` |
| ルート・書込主体の索引、statusline・CLIの状態表示、生存確認やコンテキスト使用量 | `docs/development/internals-agents-server-state-projection.md` |
| バックエンドの接続・環境継承、API失敗・利用上限、モデル候補や可用性 | `docs/development/internals-agents-server-state-backends.md` |
| process-loopの所有・再開・無進捗記録、活動記録やレーンの統合・回収 | `docs/development/internals-agents-server-state-sessions.md`、`docs/development/internals-agents-server-state-process-loop.md` |
| 配布物の保持・リース、managed-tempや共有状態の掃引 | `docs/development/internals-agents-server-state-retention.md`、`docs/development/internals-agents-server-state-diagnostics.md` |
| 診断・公開イベントログ、コンパクション計測やリソース集計 | `docs/development/internals-agents-server-state-diagnostics.md` |
| run-skillの再開入力 | `docs/development/internals-agents-server-state-run-skill.md` |

## セッション状態

hook間で共有する状態ファイルの設計、生存期間、排他制御、各フラグの記録元と参照先を扱う。
状態ファイルの共通設計は`docs/development/internals-session-state-common.md`にあり、各主題の資料はそれを前提にする。
複数の状態を扱う変更では、それぞれに該当する資料の和を全文で読む。
フラグを追加・変更する時は、該当する主題別資料を同じ変更で直す。

| 変更・調査する対象 | 読む資料 |
| --- | --- |
| 状態ファイルの設計、状態の継承 | `docs/development/internals-session-state-common.md` |
| 活動・WI投入作業の記録 | `docs/development/internals-session-state-activity.md` |
| managed-tempの状態、完了報告からの状態解析 | `docs/development/internals-session-state-temporary-and-results.md` |
| Function hooksの終了要求、定期再確認や利用上限の再開予約、背景待機の共有状態 | `docs/development/internals-session-state-function-hooks.md` |
| 作業契約の状態、plan・セッション名の状態 | `docs/development/internals-session-state-planning.md` |
| 振り返り・モード・無進捗停止の状態、進捗点検の状態 | `docs/development/internals-session-state-progress.md` |
| 検証失敗後のbugfixの起動、操作の直前のスキルの案内、終了工程の証拠 | `docs/development/internals-session-state-operation-skills.md` |
| hookの診断計測、通知の反復抑止・配送状態 | `docs/development/internals-session-state-notifications.md` |
| agents_serverの呼出・所有・待機の観測状態 | `docs/development/internals-session-state-agents-server.md` |
| バックグラウンドタスクの所有・通知・停止の状態 | `docs/development/internals-session-state-background-tasks.md` |
| UWI回答の検出状態 | `docs/development/internals-session-state-user-responses.md` |
| Antigravityのイベント間状態 | `docs/development/internals-session-state-antigravity.md` |

## 委譲先の調査

`agents_server`が起動した委譲先が動作しない事象（起動失敗、初期化未到達、委譲先の無応答、結果の欠落）は、`docs/development/internals-agents-server-investigation.md`の順に記録を確かめる。
WindowsのCodex境界試験の記録と、pytestの回収失敗時の標準エラー`resource-diagnostics:`は、`docs/development/internals-agents-server-investigation-resource-diagnostics.md`で読む。

## 環境変数

環境変数を新設・変更する時と、既存の環境変数の名前、用途、参照主体を確かめる時は、`docs/development/internals-environment-variables.md`の一覧を読む。
