# セッション状態ファイルとフラグ：[振り返り](../../agent-toolkit/skills/atk-mode/playbooks/review-session-batch.md)・モード系

各状態の共通の設計、生存期間、排他、継承は`docs/development/internals-session-state-common.md`にある。

## [振り返り](../../agent-toolkit/skills/atk-mode/playbooks/review-session-batch.md)・モード系

### 振り返りとモードの状態

- batch準備結果の`partial_analysis`: 欠測を明示した部分分析。
  省略時false。
  既存manifestとreviewedへ欠測・回復情報を保持し、`observed_through_line`は末尾欠測を含む物理境界（省略時`through_line`）を表す。
  `bundle_scope_path`は永続scopeへ移管する前の生成元（省略時`scope_path`）を表し、同期後の既存回収に使う。
  確定条件と単発の厳格契約は`docs/development/design-session-review-startup.md`「batchの共有状態」に集約する
- `add_awi_skill_invoked`: add-awi-by-userを起動した会話の履歴。
  `session_title.observe_skill`、両ホストの明示的なスキル入力、メインのスキル本文の全文取得が、既存のhook状態へ真を記録する。
  投入完了や表示タイトルの変更では保持し、`session_review.auto_start_due`が生存する会話の履歴を読んで起動を判定する。
  生存する相談会話だけが自動起動の抑止対象で、手動batchは従来の条件を使う。
  生存する相談会話の履歴は期限切れ回収でも保持し、会話が閉じれば通常の回収条件へ戻す。
  状態ファイルの回収・clear・継承は共通設計に従う
- `AGENT_TOOLKIT_SESSION_REVIEW_LAUNCH`: hook→run-skill試行ID。
  `session-review/launches/`のID別JSON/logは起動元・プロセス同一性・診断・状態を持つ。
  `starting`=未受付、`accepted`=manifest移管、`settled`=正常終了/見送り、`notified`=次SessionStart通知済み。
  受付前終了を一度通知。
  実行中・未報告失敗以外は30日後sweepで回収
- `process_wi_skill_invoked`: process-wi起動フラグ。
  PostToolUse(Skill)・UserPromptSubmitが記録、`atk agents-exit-session`の機械可読応答で偽へ戻す。
  セッション終了まで保持する
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
