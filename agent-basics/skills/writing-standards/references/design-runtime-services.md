# 実行と状態変更の設計：常駐サービス設計

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 状態や業務ルールに関わる排他・トランザクションの単位を導く前 | `design-time-state.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-time-state.md` |
| 処理自体の失敗を異常終了で表すかを判断する前 | `../../bugfix/references/root-cause-prevention-selection.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-prevention-selection.md` |

## 常駐サービス設計

発火条件: 常駐サービスの設計・変更・レビュー、または子プロセス、委譲先、非同期ジョブなど配下を持つ機能の停止・再開・切替を要件化・設計・変更・レビューする場面。

- 停止範囲: 実行主体、所有関係、実行環境の寿命、子・孫の階層、異なるホスト・接続、停止と完了の競合
- 配下の状態: 稼働中、停止済みで再開可能、完了済みで結果未回収、状態や作用が未確定などから、継続・中断からの再開・結果回収・重複実行防止を判断する。
  列挙外も配下の実際の状態を確かめて扱いを決める
- 常駐サービスでは、安全な停止（graceful shutdown）と処理中のリクエスト・ジョブ、稼働状態と受け付け可否を確認するヘルスチェック

## 負荷制御と外部呼び出しの設計

発火条件: 公開APIの負荷制御や外部サービス呼び出しを設計・変更・レビューする場面。

- 公開APIの過負荷を抑えるレート制限とバックプレッシャー
- 外部呼び出しの障害波及を抑えるサーキットブレーカー
- 通信の時間予算: `implementation-time-errors.md`「セキュリティ・ロギング・エラー処理」の各待ち時間と操作全体の予算を選び、再試行は`design-runtime-jobs.md`「非同期ジョブ・リトライ設計」の安全性と上限に従う
- 並行呼び出しの接続プール: ピーク並行数、接続保持時間、プール容量、取得待ちを比較し、容量不足を観測してから、保持時間や並行数の削減と増量を比べる

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 実装時のコーディング品質：セキュリティ・ロギング・エラー処理の基準を適用する前 | `implementation-time-errors.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/implementation-time-errors.md` |
| 実行と状態変更の設計：非同期ジョブ・リトライ設計の基準を適用する前 | `design-runtime-jobs.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-runtime-jobs.md` |
