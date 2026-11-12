# セッション状態ファイルとフラグ

本書はhook間の共有状態ファイルの設計、生存期間、排他制御、各フラグの記録元・参照先を定める。
状態ファイルの共通設計は`session-state-and-flags-common.md`に従う。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 呼び出し主体の判別方法を確かめる前 | `agent-basics:writing-standards/references/claude-hooks.md` |
| run-skillの再開入力と寿命を変更・調査する前 | `agents-server-shared-state-run-skill.md` |

## セッション状態資料の選択

読み書きする状態、更新主体、消費側との利用関係を基準に、次の表から共通資料と主題別資料を選ぶ。
複数の状態を扱う変更では、それぞれに該当する資料の和を全文で読む。
ファイル名だけで対象を限定せず、判断に必要な前提・例外が別の資料にある場合も、その資料を加える。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| セッション状態や内部フラグを変更・調査する前、状態ファイルの設計・状態の継承に関する判断・作業の前 | `session-state-and-flags-common.md` |
| 活動・WI投入作業の記録を変更・調査する前 | `session-state-and-flags-activity.md` |
| managed-tempの状態を変更・調査する前 | `session-state-and-flags-temporary-and-results.md` |
| 完了報告からの状態解析を変更・調査する前 | `session-state-and-flags-temporary-and-results.md` |
| Function hooksの終了要求、定期再確認や利用上限の再開予約を変更・調査する前 | `session-state-and-flags-function-hooks.md` |
| 作業契約の状態を変更・調査する前 | `session-state-and-flags-planning.md` |
| plan・セッション名の状態を変更・調査する前 | `session-state-and-flags-planning.md` |
| 振り返り・モード・無進捗停止の状態を変更・調査する前 | `session-state-and-flags-progress.md` |
| 進捗点検の状態を変更・調査する前 | `session-state-and-flags-progress.md` |
| 確認・操作スキルの起動、資料取得証拠、読込の選択・案内や終了工程の証拠を変更・調査する前 | `session-state-and-flags-document-receipts.md` |
| hookの診断計測、通知の反復抑止・配送状態を変更・調査する前 | `session-state-and-flags-notifications.md` |
| agents_serverの呼出・所有・待機の観測状態を変更・調査する前 | `session-state-and-flags-agents-server.md` |
| バックグラウンドタスクの所有・通知・停止の状態を変更・調査する前 | `session-state-and-flags-background-tasks.md` |
| UWI回答の検出状態を変更・調査する前 | `session-state-and-flags-user-responses.md` |
| Antigravityのイベント間状態を変更・調査する前 | `session-state-and-flags-antigravity.md` |
