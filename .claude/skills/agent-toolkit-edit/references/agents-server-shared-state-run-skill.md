# agents_serverの共有状態と読み書きの担当：run-skillの再開入力

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 共有状態の共通設計・実行主体・保存と回収の条件を判断する前 | `agents-server-shared-state.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/agents-server-shared-state.md` |
| セッション状態と内部フラグの参照範囲を選ぶ前 | `session-state-and-flags.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/session-state-and-flags.md` |

## run-skillの再開入力

`_atk/run_skill_state.py`が`state_dir/run-skill/runs/<run_id>.json`を検証・更新する。
元のSessionStart入力、会話ID、cwd、モデルとeffort、接続の所在、ログ、残時間、実行中のdeadlineとCLIのPID世代を保持する。
認証値は保存しない。
`_atk/run_skill.py`は公開`--resume`で同じ入力を復元し、元と同じスキル・モード・cwdの排他を取得してから旧CLIの終了を確認する。
CLI出力readerによる会話ID更新は、同じPID世代の実行中に限定して起動側の更新と直列化する。

`running`はCLI実行中、`interrupted`は未完了、`completed`は同期済み報告までの完了を表す。
batchでは`_atk/run_skill_batch.py`が保存済みの準備範囲を確定した後に`completed`へ移す。
通常の実行終了で残時間を確定し、利用上限の待機とprobeの間は予算を消費しない。
実行中に所有者が消失して終了時刻を確定できない場合は、保存済みdeadlineを上限として残時間を算出し、予算を増やさない。
未完了の入力とログを保持し、完了済みの再開記録とログは最終更新から30日経過後の起動時に回収する。
