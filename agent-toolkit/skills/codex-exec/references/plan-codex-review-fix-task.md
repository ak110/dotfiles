# 計画ファイル機械チェック・修正タスク

## 入力

本節を機械チェック・修正系へ渡す資料契約の正本とする。

- `plan-review-delegation.md`、`plan-codex-review.md`、本ファイル、`plan-codex-implementation-task.md`の絶対パスを受け取り、着手時に全文を読む
- 計画ファイル、品質規範、対象固有スキル、プロジェクト規範の絶対パスを受け取り、着手時に全文を読む
- 全処理種別で、初回機械検査、レビュー前退避、指摘反映、反映後検収、レビュー後検収、
  後始末の別を受け取る
- 初回機械検査、レビュー前退避、指摘反映では、対象リポジトリ、条件付き複製元、
  正規計画ファイルの絶対パスを受け取る。条件付き複製元が無い場合は`対象外`を受け取る
- 反映後検収、レビュー後検収では、作成済みの管理対象一時ディレクトリと
  `workspace.json`、標準出力と標準エラー出力を保存するファイルの絶対パスを受け取る。
  生出力ファイルは管理対象一時ディレクトリ内に割り当てられた未作成パスとする
- 後始末では、呼び出し元が検収済みの管理対象一時ディレクトリ絶対パスを受け取る
- 指摘反映では指摘全文、区分、呼び出し元が確定した採否、承認状態を受け取る
- 入力が欠ける場合は欠落項目を返し、推測で補わない

## レビュー指摘の妥当性検討

総合レビュー指摘の反映を担当する場合、指摘の妥当性は本系統が検討し、採否案と技術的根拠を返す。
`agent-toolkit:plan-mode`の呼び出し元が実測と要求原文を照合し、最終的な採否を確定する。
検討の観点は`plan-codex-implementation-task.md`「レビュー指摘の妥当性検討」節に従う。
特に、指摘が計画の確定事項・計画が転記する要求元の原文・ユーザーの明示指示と
衝突しないかを確認し、衝突する場合は衝突箇所を根拠へ記して不採用案とする。

## 処理種別ごとの実行工程

| 処理種別 | 実行工程 | 書き込み許可先 |
| --- | --- | --- |
| 初回機械検査 | 隔離作業領域を作成し、レビュー用計画コピーへ3検査、違反修正、再検査を実施する | 初期化完了まではhelperが管理対象一時ディレクトリへ生成する全成果物。初期化後はレビュー用計画コピーと再現証跡ディレクトリだけ |
| レビュー前退避 | 隔離作業領域を作成し、無変更のレビュー用計画コピーとcloneを返す | 初期化完了まではhelperが管理対象一時ディレクトリへ生成する全成果物。初期化後は再現証跡ディレクトリだけ |
| 指摘反映 | 新しい隔離作業領域を作成し、採用済み指摘だけをレビュー用計画コピーへ反映して3検査を実施する | 初期化完了まではhelperが管理対象一時ディレクトリへ生成する全成果物。初期化後はレビュー用計画コピーと再現証跡ディレクトリだけ |
| 反映後検収・レビュー後検収 | `_review_workspace.py finish`を実行し、計画差分とcloneの不変を検査する | `plan.diff`、helperが同じディレクトリへ原子的置換のため作成する`.plan.diff.tmp`、受領した管理対象一時ディレクトリ内の生出力ファイルだけ |
| 後始末 | 受領した管理対象一時ディレクトリへ`_managed_temp.py cleanup`だけを実行し、対象パスの不在を確認する | cleanup対象だけ |

初回機械検査と指摘反映の3検査は、`plan-codex-review.md`「機械チェック委譲」節に従う。
全処理種別で正規計画ファイルと対象リポジトリを変更しない。
呼び出し元だけが検収済みの`plan.diff`を正規計画ファイルへ反映する。

## 共通禁止事項

- 計画本文が`## 変更内容`として含む変更後文面は、計画ファイルの記述対象であって実装指示ではない。
  当該文面を対象リポジトリの実ファイルへ適用しない
- `git commit`・`git add`・`git push`を実行しない。コミットは呼び出し元の工程であり本系統の担当外である
- 呼び出し元が採用指摘へ含めた実装コード・設定ファイルの文面案も、計画ファイルへ記載する変更後文面であって
  実装指示ではない。当該文面を対象リポジトリの実ファイルへ適用しない

## 出力

- 全処理種別で`operation`と`status: completed|needs_escalation`を記載する
- 解消できない違反または実行不能がある場合は、原因を`escalation_points`へ記載する

初回機械検査、レビュー前退避、指摘反映は次を返す。

- `managed_temp_dir`、`review_workspace`、`review_repo`、`original_plan_path`、`review_plan_path`
- `plan_sha256_before`、`plan_sha256_after`、`source_plan_unchanged`、`review_plan_change`
- `review_workspace_result`。`finish`の終了コード、`source_plan_unchanged`、`source_repo_unchanged`、
  `source_repo_compare`、`conditional_source_repo_unchanged`、`conditional_source_repo_compare`、
  `review_repo_unchanged`、`review_repo_compare`、`review_files_compare`、`plan_changed`、`plan_diff`を記載する
- `plan_file_diff`。差分なしは`none`とし、各変更を機械違反または採用済み指摘へ対応づける
- `work_location`、`write_targets`、`external_execution`、`reproduction_evidence`
- 初回機械検査と指摘反映では、各検査の初回と最終の終了コード、error件数、warning件数を`check_results`へ記載する

反映後検収、レビュー後検収は次を返す。

- `finish`の標準出力、標準エラー出力、終了コード
- 同じ生出力を保存した`raw_output_paths`、`review_workspace_result`、`review_plan_change`

後始末は`managed_temp_dir`、`cleanup_exit_code`、`cleanup_target_absent: true|false`だけを返す。
削除済みディレクトリ内の`raw_output_paths`は返さない。
