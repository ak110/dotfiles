# セッション状態フラグ

配布物のhook間で共有する状態の記録元、利用先、寿命を定める。
状態ファイルは`{tempdir}/claude-agent-toolkit-{session_id}.json`とする。

フックがセッション状態の不足を理由にブロックした場合は、状態ファイルを`Read`して
当該状態と他の記録済み状態を実測する。
記録契機が発生していない場合は、同じ委譲の再実行以外の対処を選ぶ（努力目標）。

## 検証・git状態系

- `test_executed`: PostToolUseがBashの検証コマンドまたはpyfltr MCPの`run_for_agent`成功時に記録し、
  `git commit`未検証警告の抑制に使う。セッション終了まで保持する
- `git_log_checked`: PostToolUse(Bash)が`git log`観測時に記録する
- `amend_pending_status_check`: cwd別辞書としてamendまたはfixup成功時に記録し、push前のdirty検査に使う
- `sleep_poll_detected`: PreToolUse(Bash)がsleep直後の状態確認連結を検出した場合に記録する
- `session_edited_files`: PostToolUseが成功した編集の対象パスを重複なく記録し、
  PreToolUse(Bash)の一括stage警告が自セッション編集済み集合として読む。セッション終了まで保持し、
  リセット経路は設けない。Claude CodeではWrite・Edit・MultiEditが、Codexでは成功した`apply_patch`が
  記録元となる。Codexの移動では移動元と移動先の双方を記録する。
  CodexのBashは終了コードを取得できないため、`test_executed`・`git_log_checked`・
  `amend_pending_status_check`と同様に本フラグの記録元にならない

## plan系

- `plan_mode_skill_invoked`: plan-mode起動を記録し、計画ファイル検査の適用判定に使う
- `plan_impl_executor_active_subagent_sessions`: SubagentStartが`plan-impl-executor`と`feedbacks-planner`の`agent_id`を
  Agent識別子別に記録し、SubagentStopが完了報告を検査する。記録した要素は、正常報告、SendMessage再開、
  plan-mode起動のいずれでも削除されず、別の調整役の要素と併存する。状態JSON全体の寿命は末尾の規定に従う
- `current_plan_file_path`: 計画ファイル編集時のパスを記録する
- `last_hook_session_title`: Claude CodeのUserPromptSubmitが計画ファイルのstemを`sessionTitle`へ実際に出力した値を記録する。
  値が存在する間は同一セッションで再出力しない。sessionTitle出力時だけ更新し、既存の状態JSONクリア又は保持期限を過ぎた回収で除去する
- `plan_file_written`・`direct_agent_toolkit_edit_count`・`last_agent_toolkit_edit_path`:
  計画ファイル作成前の直接編集を検知する

## 振り返り・モード系

- `session_review_invoked`: 振り返りスキルの起動を記録し、Stop hookの重複を抑止する。
  Claude CodeではPostToolUseとUserPromptSubmitが記録し、EnterPlanModeで解除する。
  CodexではUserPromptSubmitと振り返りスキルが記録し、同一セッション内の解除経路を設けない。
  Codexの状態が期限回収された場合は、transcript内の手動起動または起動確定標識から起動済み判定を復元する
- `agent_toolkit_edit_skill_invoked`: dotfilesリポジトリ固有の
  `scripts/claude_hook_pretooluse.py`などがagent-toolkit-edit起動を記録し、編集警告の抑制に使う
- `dotfiles_reference_docs_read`: dotfilesの個人PostToolUseフックが参照文書へのReadを解決済み絶対パスの一覧として記録し、
  個人PreToolUseフックが同じチェックアウト内のコーディングエージェント向け文書の編集警告を抑制する。
  セッション終了まで保持し、リセット経路は設けない
- `process_feedbacks_skill_invoked`・`plan_and_add_feedback_skill_invoked`・`add_feedback_skill_invoked`:
  それぞれ対応するフィードバック処理スキルの起動を記録する。Stop hookの自動session-review起動条件に使う。
  PostToolUse(Skill)とUserPromptSubmitが記録し、`agent-toolkit:exit-session`起動時に3フラグをまとめて偽へ戻す。
  手動session-review起動はこれらのフラグを必要としない。セッション終了まで保持する
- `delegation_skill_invoked`: メインセッションでSkillツールが`agent-toolkit:delegation`または
  `delegation`を起動した場合にPostToolUseが真化する。
  メインセッションからcodex MCPまたはAgent／Taskで新規委譲を開始する前の経路検査に使い、
  セッション終了まで保持する。sidechainのSkill起動は記録しない

## codex連携系

- `codex_remote_snapshot_by_key`: codex呼び出し直前のリモートrefを記録し、呼び出し後に比較して削除する
- `codex_remote_cwd_by_key`: 呼び出し元ごとの直近`cwd`を記録し、同じ呼び出し元からの継続時に使う
- `claude-agent-toolkit-codex-thread-cwd-<threadIdのSHA-256>.json`: codexの`threadId`ごとの`cwd`を
  疑似セッション状態として記録し、オーケストレーターをまたぐ継続呼び出し時の比較対象を解決する

## TBD系

- `tbd_answered_by_repo`: エージェント識別子ごと・対象リポジトリIDごとの回答済みTBDファイル名を
  PostToolUseが記録する。値は`{エージェント識別子: {対象リポジトリID: ファイル名一覧}}`の2段辞書とし、
  `transcript_path`から抽出できないメイン会話は`main`をキーとする。
  cwdが空・保存先未解決・後述の指紋が前回と同一・リポジトリID未解決・走査不完全のいずれかの場合は
  記録を更新しない。前回の一覧にない回答済みファイル名の通知判定に使い、セッション終了まで保持する。
  リセット経路は設けず、一覧の記録更新のみで状態を進める。
  メインとサブエージェントのフック呼び出しは同一`session_id`で届くため、
  エージェント識別子で分けないと一方の呼び出しが回答差分を消費し他方へ通知が届かない

- `tbd_fingerprint_by_repo`: active状態ディレクトリ（`inbox`・`processing`）の内容変化指紋を
  PostToolUseが記録する。値は`{エージェント識別子: {作業ディレクトリ: 指紋文字列}}`の2段辞書とする。
  前回観測時の指紋と同一の場合は走査と回答差分の検出をいずれも省略する用途に使う。
  回答済みファイル名一覧と同じ更新で記録し、セッション終了まで保持する

サブエージェント起動の判定は`tool_name in ("Agent", "Task")`をSSOTとする。
新規フラグには記録元、利用先、寿命、リセット経路を併記する。
状態JSONはセッション終了イベントを契機に削除しない。同イベントは同じセッションへ後から戻る場合にも発火し、
`--continue`・`--resume`・`/resume`で戻ると同じ`session_id`で会話が続くため、削除すると再開後の記録が失われる。
回収は更新から一定期間が経過した状態JSONに限り、対のロックファイルも同時に破棄する。
対応する状態JSONが無いロックファイルは、ロック自身の更新時刻が同じ期間を超えた場合だけ破棄する。
例外は終了理由が`clear`の場合とし、会話が破棄されたことが確定するため排他ロック下で当該セッションの状態JSONを削除する。
サブエージェント側で記録される状態は呼び出し元へ自動伝播しないため、
親側で必要な値は完了報告の構造化欄から厳格に解析する。
