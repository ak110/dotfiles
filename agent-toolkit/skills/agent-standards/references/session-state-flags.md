# セッション状態フラグ

本ファイルは`agent-toolkit:agent-standards`スキル本体（SKILL.md）「セッション状態フラグ」節から分離したフラグ一覧・寿命明示ルールの詳細である。

- `test_executed`: PostToolUse(Bash)が記録。`git commit`未検証警告の抑制に使う
- `git_status_checked`: PostToolUse(Bash)が`git status`/`git log`/`git diff`観測時に記録
- `git_log_checked`: PostToolUse(Bash)が`git log`観測時に記録。commit/rebase/push/編集時リセット、amend/rebase前確認に使う
- `amend_pending_status_check`: cwd別辞書。PostToolUse(Bash)がamend/fixup成功時に記録し、実送出push成功時にリセットする。
  PreToolUse(Bash)側もpush前dirty検査clean通過時にリセットする（`--dry-run`・`-n`除く）
- `plan_mode_skill_invoked`: PostToolUse(Skill)／UserPromptSubmit（スラッシュ）で`agent-toolkit:plan-mode`呼び出しを記録する。plan file検査と最初ツール警告抑制に使う
- `session_review_invoked`: PostToolUse(Skill)／UserPromptSubmit（スラッシュ）で振り返りスキルを記録。対象は配布物`agent-toolkit:session-review`・個人フック`session-review-dotfiles`。Stop hook重複抑止・辞書リセットに使う
- `agent_toolkit_edit_skill_invoked`: dotfiles個人フックが`agent-toolkit-edit`呼び出しを記録する。未起動時のPreToolUse警告抑制と配布物`pretooluse.py`のRead隔離ブロックの例外判定に使う
- dotfiles個人フック管理: `session_review_extension_pending`（`session-review-dotfiles`使用記録、配布物Stop hook重複送出抑制）、
  `autonomous_exit_invoked`（`agent-toolkit:exit-session`呼び出し記録、`DOTFILES_AUTONOMOUS_EXIT_REQUIRED=1`下のStop hook未呼び出し判定用）
- `user_prompt_counter`・`norm_inquiry_last_injected`: `user_prompt_submit.py`が`update_state`で毎回加算・記録し同ファイル内のクールダウン判定に使う。前者は単調増加カウンター、後者は直近の規範照会リマインダー注入時カウンター値
- `process_feedbacks_skill_invoked`: PostToolUse(Skill)／UserPromptSubmit（スラッシュ）で`agent-toolkit:process-feedbacks`・各短縮スラッシュを記録。
  Stop hookの拡張照合カテゴリ有効化判定と、PreToolUse(EnterPlanMode)ブロック判定に使う。
  PostToolUseは`process-feedbacks`起動検知時に真へ強制上書きし、`exit-session`スキル起動検知時に偽へ戻す
  （`process-feedbacks/SKILL.md`「ステップ8」がexit-sessionで終端するため）
- `plan_and_add_entries_skill_invoked`: PostToolUse(Skill)／UserPromptSubmit（スラッシュ）で
  `agent-toolkit:plan-and-add-feedback`・各短縮スラッシュを記録。
  読み取り元はPreToolUse（`EnterPlanMode`発行ブロック）。
  寿命は`agent-toolkit:process-feedbacks`起動検知（plan-and-add-feedbackの終端工程が参照呼び出しする先）でリセット
- サブエージェント起動を検知する判定は`tool_name in ("Agent", "Task")`をSSOTとする
  （pretooluse・posttooluseとも同一。コード追加・改訂時は`grep -rn`で確認して同一集合を使う）
- plan-file-creatorの整合性チェック完遂判定フラグ群は、PreToolUse(Agent/Task)がサブエージェントの
  起動要求検知時点で前倒しして記録し、PostToolUse(Agent/Task)も完了時点で記録する:
  `plan_reviewer_invoked`・`codex_review_invoked`
  （PreToolUseゲートの必須対象は`codex_review_invoked`のみ。`plan_reviewer_invoked`は`codex-review.md`
  「codex利用可否の2段階判定」節でMCP利用不可時の代替起動を記録する用途に残り、ゲート必須対象ではない）。
  状態更新は既に真の場合に何も書き込まないため、起動時点と完了時点で二重に記録しても結果は同一となる。
  前倒し記録により、完了通知前でも起動要求が受理された時点からフラグは真となる。
  `codex_review_invoked`は`plan-codex-delegate`を用途`計画レビュー`・`実装差分レビュー`で起動した時、
  または`isSidechain`が偽の`mcp__codex__codex`完了時に記録する。
  用途`実装`での起動は記録対象から除く。実装用途の起動を記録するとレビュー未実施のまま
  計画作成工程の完遂判定を通過できるためである。
  用途は起動プロンプト本文の`用途: <値>`行から判定し、記述が無い場合は記録側へ倒す
  （レビュー起動を実装起動と誤判定すると正当な進行がブロックされるため）。
  `_reset_process7_completion_flags`のリセット対象タプルは`_PROCESS7_COMPLETION_FLAGS`と別に
  `plan_reviewer_invoked`を明示追加し、新計画着手時に前計画のフォールバック起動記録を持ち越さない
  （`_PROCESS7_COMPLETION_FLAGS`縮小に伴うリセット対象脱落の是正、plan-reviewer指摘反映）。
  `agent-toolkit:plan-file-creator`配下から起動された場合、各フラグはplan-file-creator自身のセッション状態に
  記録され起動元（親）へは反映されない。
  親への反映は、plan-file-creator完了報告本文の`invoked_subagents:`行をAgent/Task完了ハンドラがパースし、
  対応するフラグを親自身のセッション状態へ設定する経路で行う
- `plan_impl_executor_active_subagent_sessions`: PostToolUse(Agent/Task)が`plan-impl-executor`系起動時に記録する。
  サブセッションID（`agentId`）をキーとする辞書形式で保持する。
  SubagentStop側の`_inspect_plan_impl_executor_report_format`は、`transcript_path`のファイル名
  （`agent-<agentId>.jsonl`形式）から抽出した`agentId`が本辞書のキーと一致する場合に限り
  書式検査を発火する。検査で欠落ラベル・矛盾のいずれも検出しなかった場合のみ一致したエントリを
  辞書から削除し、検出した場合（block時）はエントリを保持して再試行時の再検査に備える。
  抽出・突合に失敗した場合は検査を発火しない（安全側）
  （寿命: 当該サブエージェントの完了検知まで）
- `plan_codex_delegate_invoked`: `plan-codex-delegate`サブエージェント起動検知時点で前倒しして真化する。
  PreToolUse(Agent/Task)が記録し、サイドチェーン内からも参照できる。
  `mcp__codex__codex`直接呼び出し前の経路遵守検査に使う
- `plan_codex_delegate_blocked`: `plan-codex-delegate`起動失敗時（auto mode下のブロック等）に真化する。
  PostToolUseFailure・PermissionDenied（Agent/Task限定）が検出し、`mcp__codex__codex`直接呼び出しのauto mode例外条件に使う
- `recorded_codex_thread_id`: `mcp__codex__codex`成功時のPostToolUseが`tool_response.threadId`を記録する。
  `mcp__codex__codex-reply`のPreToolUseがthreadId一致検査で参照する（`plan_codex_delegate_invoked`・
  `plan_codex_delegate_blocked`・`recorded_codex_thread_id`の3件は新計画着手時に
  plan-file-creatorの整合性チェック完了フラグと共にリセットされる）
  並列`plan-codex-delegate`インスタンスはいずれも`isSidechain`真のため、本フラグのthreadId厳密一致検査は
  スキップされ`_check_codex_mcp_via_plan_codex_delegate`の起動履歴検査のみが適用される。
  並列時に本フラグの単一値設計が競合する事象は生じない
  （メインセッション直接の`mcp__codex__codex`エスカレーション呼び出しのみ本フラグを参照し、当該呼び出しは逐次的である）
- `current_plan_file_path`: PostToolUse(Write/Edit/MultiEdit)が計画ファイル編集時のパスを記録。
  ExitPlanMode時の再読込と、`plan-impl-executor`系Agent起動時の起動プロンプト参照先パス一致判定に使う
- `plan_impl_executor_verified_plan_path`: PreToolUseが`plan-impl-executor`系Agent/Task起動時に記録する。
  参照パスが`current_plan_file_path`と異なりかつ実在する場合
  （別の既存計画を参照した正当な起動と判定した場合）に当該パスを記録する。
  `_check_plan_file_retroactive_scan_recorded`が遡及スキャン対象の計画ファイルを解決する際、
  本フラグが設定されていれば`current_plan_file_path`より優先して参照する。
  `current_plan_file_path`を直接更新しないのは、完遂ゲートの一致比較（`referenced == current`）が
  別計画の切替検出のたびに書き換わる事態を避けるためである。
  書き換わると、当該計画への再起動時に本来不要な完遂フラグ要求へ誤って該当してしまう
  （寿命: `agent-toolkit:plan-mode`起動時に消去する）
- 計画ファイル未作成時の直接編集検知フラグ群（`plan_file_written`・`direct_agent_toolkit_edit_count`・`last_agent_toolkit_edit_path`）はPreToolUseが更新する。agent-toolkit配下編集連続を検知し、2件目warn・3件目blockとする
- 新規フラグ追加時は当該フラグの寿命（セッション終了まで維持・新計画着手時にリセット・
  特定イベントで消去等）を明示する。寿命が「新計画着手時にリセット」に該当する場合は
  既存のリセット関数（`pretooluse.py`の`_reset_process7_completion_flags`等）へ追加する
- Agent tool経由のサブエージェントで記録されるフラグは呼び出し元セッションに閉じる。
  親側での判定は完了報告本文の機械パース経路を要する。
  詳細は本バレット上部のplan-file-creator関連フラグ項を参照する
