# セッション状態ファイルとフラグ：[振り返り](../../../../agent-toolkit/skills/review-session/references/batch.md)・モード系

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 各状態の共通設計・生存期間・排他・継承を適用する前 | `session-state-and-flags-common.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/session-state-and-flags-common.md` |
| 呼び出し主体の判別方法を確かめる前 | `agent-basics:writing-standards/references/claude-hooks.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks.md` |
| run-skillの再開入力と寿命を変更・調査する前 | `agents-server-shared-state-run-skill.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/agents-server-shared-state-run-skill.md` |

## [振り返り](../../../../agent-toolkit/skills/review-session/references/batch.md)・モード系

### 振り返りとモードの状態

- `AGENT_TOOLKIT_SESSION_REVIEW_LAUNCH`: hook→run-skill試行ID。
  `session-review/launches/`のID別JSON/logは起動元・プロセス同一性・診断・状態を持つ。
  `starting`=未受付、`accepted`=manifest移管、`settled`=正常終了/見送り、`notified`=次SessionStart通知済み。
  受付前終了を一度通知。
  実行中・未報告失敗以外は30日後sweepで回収
- `process_wi_skill_invoked`: process-wi起動フラグ。
  PostToolUse(Skill)・UserPromptSubmitが記録、`atk agents-exit-session`の機械可読応答で偽へ戻す。
  セッション終了まで保持する
- `autonomous_exit_invoked`: `_hooks/posttooluse.py`が`atk agents-exit-session`の実行・機械可読応答を記録。
  `_hooks/autonomous_exit.py`がprocess-loopのStop判定で参照し、未完了バックグラウンドタスクによる終了取消で偽、再実行で真とする。
  セッション中だけ保持し、スキル完了では再利用しない
- `stop_no_tool_turn_count`: `agent-toolkit/agent_toolkit/_hooks/busy_loop_guard.py`が、自セッションでツール呼び出しをしなかったターンの連続回数を記録し、常駐ループの停止判定に使用する。
  ツール呼び出しを観測したターン、委譲先やバックグラウンドタスクの完了を待つターン、停止工程の実行時は0に戻す。
  セッション終了まで保持する
- `stop_observed_entry_count`: 同フックが、Stop判定時点で観測済みだったセッション記録のエントリ数を記録し、次のターンで差分配信分のみを走査する起点として参照する。
  セッション終了まで保持する

## 進捗の点検系

- `progress_self_check`: `_hooks/progress_self_check.py`が、呼出主体（`agent_id`、無ければ`main`）ごとに次の3値をepoch秒で保持する。
  `started_at`は初回観測、`last_notified_at`は通知が最終出力へ採用された最後の時刻、`reserved_at`は期日到達で通知を生成した時刻である。
  PostToolUseとUserPromptSubmitが`update_state`で読み書きし、初回観測は記録だけにする。
  実発話・機械注入・PostToolUseを区別せず、`last_notified_at`から1800秒以上たった最初の観測で`source="progress-self-check"`の通知を生成し、`reserved_at`を記録する。
  生成しただけでは期日を消費しない。
  `notice.record_delivery`が最終出力へ採用した通知の生成時刻を`progress_self_check_delivered`へ記録し、次の観測がその最大値を`last_notified_at`へ畳み込む。
  `reserved_at`は60秒の間だけ同じ主体の並列な生成を抑止し、hook.pyがPostToolUseとUserPromptSubmitの処理の終了時に未採用の予約を解除する。
  Antigravityの保留通知は後の注入で配送記録を残すため、予約は失効時間に任せる。
  通知時刻はhookが通知を採用した証拠に限り、点検を実施した証拠の対象からは除く。
  リセットは無く、生存期間はセッション状態ファイルと同一とする。
