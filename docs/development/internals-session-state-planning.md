# セッション状態ファイルとフラグ：作業契約系

各状態の共通の設計、生存期間、排他、継承は`docs/development/internals-session-state-common.md`にある。
run-skillの再開入力と寿命は`docs/development/internals-agents-server-state-run-skill.md`にある。

## 作業契約系

run-skillが保持する元の実行入力・会話・時間予算は、[共有状態資料のrun-skillの再開入力](internals-agents-server-state-run-skill.md)に従う。
通常のhook状態JSONへ複製しない。

- `whole_contract_notified`: 委譲先の主体ごとに、全体作業契約の更新を案内した版（本文のSHA-256）の一覧を保持する。
  PreToolUseの`work_contract.delegated_notice`が`whole_contract_launch`の版と原本の現在の版を比べ、異なる版を主体ごとに1回だけ非遮断で案内する。
  案内は最終出力へ採用した時に記録し、本文は配送しない。
  compactのSessionStartがこの記録を削除する。
  セッション状態と同じ寿命で保持する。
- `exec_review_receipt_sources`: 実行レビューの終了行が指定した取得資料と、主体が取得した版を保持する。
  `atk review-table respond`の直前に資料の取得を確認する。
  compactのSessionStartでは取得結果を捨て、clearでは対象の資料指定も捨てる。
- `whole_contract_launch`: 委譲先が起動時に受け取った全体作業契約の所在と版（起動の本文の`kind="startup-document"`の要素が示す本文識別子）。
  最初の判定で`agents_server`の登録簿の起動本文から求めて保持し、登録簿にまだ無いsessionは記録せず次の操作で求め直す。
  起動の本文に契約の全文が無い起動では空の値を記録し、更新を案内しない。
  セッション状態と同じ寿命で保持する。
- `work_contract_input_sequence`: UserPromptSubmitが実ユーザー入力ごとに進める整数。
  初期値は0で、機械注入の間は値を保つ。
  PreToolUseが未作成通知の入力区切りとして参照する。
  セッション状態と同じ寿命で保持する。
  排他ロック下で比較・更新し、同じ入力では1回だけ通知する。
  SessionStartの`compact`時に削除して、圧縮後の最初の操作で再通知する。
  他はセッション状態と同じ寿命で保持する。

## plan系

- `plan_mode_skill_invoked`: `atk run-script plan-create`の成功を記録し、計画ファイルの構造に対する自動チェックを適用するかどうかの判定に使用する
- `current_plan_file_path`: PostToolUseが、計画ファイルの編集時や公開された計画作成コマンドの出力からファイルパスを記録する。
  `atk run-script plan-create`の成功時は、その出力が示す新しい計画のパスで置き換える。
  UserPromptSubmitのコマンドhookがセッション名を選択する際にこのパスを参照する
- `last_hook_session_title`: UserPromptSubmitのコマンドhookが設定したセッション名。
  現在のセッション名が空であるか、この値と一致している場合に限り、新しい種別や計画名への更新を許可する。
  同hookが通常状態ファイルへ排他ロック下で保存する。
  通常状態ファイルと同様の保持期限および`clear`のタイミングで消去される
- `session_title_kind`: PostToolUse(Skill)、およびスキルの手動起動を受け取ったUserPromptSubmitが記録する命名用の種別（`process-wi`、`single-lane`、`all-repos`）。
  UserPromptSubmitのコマンドhookがセッション名を選択する際に参照する。
  種別が`all-repos`の場合と、process-loopの環境識別印と会話IDが一致している場合（記録された種別を問わない）は、計画名を連結しない統括名`process-wi all-repos`を使う。
  生存期間は通常状態ファイルと同一とする
