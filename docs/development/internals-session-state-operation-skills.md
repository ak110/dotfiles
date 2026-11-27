# セッション状態ファイルとフラグ：確認・操作スキル系

各状態の共通の設計、生存期間、排他、継承は`docs/development/internals-session-state-common.md`にある。

## 確認・操作スキル系

`pretooluse/operation_skills.py`の`written_paths`は、終了工程の成果作業とWI受付の編集境界の観測で共有する。
`is_verification_operation`は検証・CIの実際の失敗を観測する対象を選ぶ。
`full_reads.py`の全文取得の判定は退避出力の回収とレビュー返却の受領で共有し、スキル起動の台帳は持たない。

- `verification_bugfix`: 検証・CIの実失敗を観測した主体ごとに、最後の失敗のtool_use_idと案内待ちの印を保持する。
  PostToolUseが失敗を記録し、次のPreToolUseで案内待ちを排他消費して一度だけ原因分析を促す。
  起動成功、分類、報告書式、読取方式は追跡せず、送達やStopを遮断しない。
- `termination_evidence`: `agent-toolkit/agent_toolkit/_hooks/termination_evidence.py`が、作業の対象と判断の根拠を作業単位で保持する。
  PreToolUse、PostToolUse、UserPromptSubmitが、成果作業、未完了の呼び出し、人間の原入力を更新する。
  完了手順書の全文取得・digest・圧縮後の読込資格・省略資格は保持しない。
  解決済みの呼び出しや入力は更新のたびに除外し、版や形式が合わない値は診断を残す。
  別の会話から継承した値は`session_id`の一致を確認してから使う。
  - 成果作業: 成功した対象スキルの開始、自身のリポジトリ編集、計画・WIの保存、commit・pushを`deliverables`へ記録する。
  - 作業の対象: 観測したリポジトリのルートを`repositories`へ、成功した編集の相対パスを`edits`へ記録する。
    Gitの`-C`、シェルの移動先と静的に確定した書込先を使い、所属不明は`null`で保持する。
  - 待機: `async_targets`と所有者を確かめ、有効なCLI待機と観測義務を区別する。
    待機が解消したら`pending_work`は残る作業へ戻す。
  - 判断: 中止・置換・開始・再開は人間の原入力、技術的不成立は同じ作業の実失敗、待機は所有する委譲先か未回答UWIを根拠にする。
    `publish_scope`は元の指示入力または適用済み認可から確定した公開範囲を保持する。
    記録手順は`agent-toolkit:atk-mode/references/termination-evidence.md`と`agent-toolkit:atk-mode/playbooks/confirmation.md`に従う。
  - 元の入力: `last_human_input`は機械通知後も人間の原入力を保持する。
    同じターンIDを持つ別の通知で本文と由来を上書きしない。
    ターン途中の`queued_command`は`transcript_path`の`queued_offset`から取り込み、`origin.kind=human`だけを人間入力にする。
    `--context`は現在の`session_id`と`work_id`を返し、人間入力を観測した場合だけ`input_id`と`quote`へ値を入れる。
    状態が無い、別会話や旧版である、現在のメインでない、作業も原入力も無い場合は取得を拒否する。
