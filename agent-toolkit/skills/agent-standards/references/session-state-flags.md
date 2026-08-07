# セッション状態フラグ

配布物のhook間で共有する状態の記録元、利用先、寿命を定める。
状態ファイルは`{tempdir}/claude-agent-toolkit-{session_id}.json`とする。

- `test_executed`: PostToolUseがBashの検証コマンドまたはpyfltr MCPの`run_for_agent`成功時に記録し、
  `git commit`未検証警告の抑制に使う。セッション終了まで保持する
- `git_status_checked`: PostToolUse(Bash)が`git status`・`git log`・`git diff`観測時に記録する
- `git_log_checked`: PostToolUse(Bash)が`git log`観測時に記録する
- `amend_pending_status_check`: cwd別辞書としてamendまたはfixup成功時に記録し、push前のdirty検査に使う
- `sleep_poll_detected`: PreToolUse(Bash)がsleep直後の状態確認連結を検出した場合に記録する
- `plan_mode_skill_invoked`: plan-mode起動を記録し、計画ファイル検査の適用判定に使う
- `session_review_invoked`: 振り返りスキルの起動を記録し、Stop hookの重複を抑止する
- `session_review_extension_pending`: 振り返り拡張が誘導を担うことを示す。
  配布物外の拡張フックがPostToolUseで真化する。
  配布物のStop hookが参照し、真の場合は自身の振り返り誘導を抑止する。
  寿命はセッション終了まで
- `agent_toolkit_edit_skill_invoked`: agent-toolkit-edit起動を記録し、編集警告の抑制に使う
- `process_feedbacks_skill_invoked`: process-feedbacks起動中の自律モード判定に使う
- `plan_and_add_entries_skill_invoked`: plan-and-add-feedback起動を記録する
- `codex_exec_skill_invoked`: メインセッションでSkillツールが`agent-toolkit:codex-exec`または
  `codex-exec`を起動した場合にPostToolUseが真化する。
  メインセッションからcodex MCPを呼び出す前の経路検査に使い、セッション終了まで保持する
- `plan_impl_executor_active_subagent_sessions`: SubagentStartが`plan-impl-executor`の`agent_id`を
  Agent識別子別に記録し、SubagentStopが完了報告を検査する。正常報告、SendMessage再開、
  plan-mode起動では削除せず、別executorの要素と併存させる。親SessionEndで状態JSON全体を削除する
- `codex_remote_snapshot_by_key`: codex呼び出し直前のリモートrefを記録し、呼び出し後に比較して削除する
- `codex_remote_cwd_by_key`: 呼び出し元ごとの直近`cwd`を記録し、同じ呼び出し元からの継続時に使う
- `claude-agent-toolkit-codex-thread-cwd-<threadIdのSHA-256>.json`: codexの`threadId`ごとの`cwd`を
  疑似セッション状態として記録し、オーケストレーターをまたぐ継続呼び出し時の比較対象を解決する
- `current_plan_file_path`: 計画ファイル編集時のパスを記録する
- `plan_impl_executor_verified_plan_path`: 実在する計画を参照したexecutor起動時の正規化済みパスをPreToolUseが記録し、遡及スキャン対象の解決に使う。plan-mode起動時に削除する
- `plan_file_written`・`direct_agent_toolkit_edit_count`・`last_agent_toolkit_edit_path`:
  計画ファイル作成前の直接編集を検知する
- `tbd_unanswered_by_repo`: エージェント識別子ごと・対象リポジトリIDごとの直近の未回答TBD件数を
  PostToolUseが記録する。値は`{エージェント識別子: {対象リポジトリID: 件数}}`の2段辞書とし、
  `transcript_path`から抽出できないメイン会話は`main`をキーとする。
  cwdが空・保存先未解決・後述の指紋が前回と同一・リポジトリID未解決・走査不完全のいずれかの場合は
  記録を更新しない。件数が1件以上から0件へ遷移した時点の通知判定に使い、セッション終了まで保持する。
  リセット経路は設けず、件数の記録更新のみで状態を進める。
  メインとサブエージェントのフック呼び出しは同一`session_id`で届くため、
  エージェント識別子で分けないと一方の呼び出しが遷移を消費し他方へ通知が届かない

- `tbd_fingerprint_by_repo`: active状態ディレクトリ（`inbox`・`processing`）の内容変化指紋を
  PostToolUseが記録する。値は`{エージェント識別子: {作業ディレクトリ: 指紋文字列}}`の2段辞書とする。
  指紋は`.md`ファイルごとの相対パス・`st_mtime_ns`・サイズを連結したSHA-256であり、
  `stat`のみで算出する。前回観測時の指紋と同一の場合は走査と`git remote get-url origin`を
  いずれも実行せず、遷移検出も行わない。
  最大`st_mtime_ns`だけでは時刻の分解能より短い間隔の書き換えを検出できないため、サイズを併せる。
  未回答件数と同じ更新で記録し、セッション終了まで保持する

サブエージェント起動の判定は`tool_name in ("Agent", "Task")`をSSOTとする。
新規フラグには記録元、利用先、寿命、リセット経路を併記する。
状態JSONは親セッションのSessionEndで排他ロック下から削除する。並行プロセスが共有するロックファイルは保持する。
サブエージェント側で記録される状態は呼び出し元へ自動伝播しないため、
親側で必要な値は完了報告の構造化欄から厳格に解析する。
