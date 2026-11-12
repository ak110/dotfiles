# セッション状態ファイルとフラグ：Antigravityのイベント間状態

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 各状態の共通設計・生存期間・排他・継承を適用する前 | `session-state-and-flags-common.md` |

## Antigravityのイベント間状態

Antigravity向けの6つのイベントアダプターは、共通のセッション状態ファイルへ以下の値を保存する。
値の更新および一括消費は`update_state`の排他ロック区間内で実行し、不要になったデータは既存のTTLスイープ処理によって回収される。

| キー | 型 | 書き込みと消費の契機 |
| --- | --- | --- |
| `antigravity_pending_contexts` | `list[str \| {text, receipts}]` | `SessionStart`、`PreToolUse`、`PostToolUse`が本文と配送予約の識別情報を蓄積する。旧文字列も読める。`PostInvocation`と初回の`PreInvocation`で一括消費し、最終応答の`injectSteps`へ実際に採用した時点で配送を記録する。保留時と内部handler出力時は記録しない |
| `antigravity_last_user_step` | `int` | `PreInvocation`が処理済みとなった`USER_INPUT`の`step_index`を記録し、同一発話に対する二重処理を防止する |
| `antigravity_stop_continuing` | `bool` | 直前の`Stop`が`continue`を要求したという事実を保存し、共通入力における`stop_hook_active`の値へ正規化する |
| `agy_cwd` | `str` | `SessionStart`が正規化されたワークスペースのcwdを保存し、`session_records`が記録ファイルのパス解決に使用する |
| `agy_transcript_path` | `str` | `SessionStart`が`transcriptPath`を保存し、`session_records`が記録の読み取りに使用する |
| `antigravity_subagent_ids` | `list[str]` | 親エージェントの`PostToolUse`が、`invoke_subagent`の返却値から作成済みの`conversationId`を登録する。件数が一致した`manage_subagents`のactive一覧、または`Stop`における`fullyIdle=true`によって終了を観測する |

自身が所有するsubagentの起動については、共通の`background_task_ids`にも登録する。
`background_task_ids`は起動したタスクの所有履歴としてTTL満了まで保持し、終了の観測時にも削除しない。
active一覧の照会結果や`fullyIdle`による状態の更新は、`antigravity_subagent_ids`へ反映する。
なお、Antigravityには存在しないイベントである`SubagentStart`や`SubagentStop`の直接購読は行わない。
