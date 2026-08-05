# 計画ファイル機械チェック・修正タスク

## 入力

本節を機械チェック・修正系へ渡す資料契約の正本とする。

- `plan-review-delegation.md`、`plan-codex-review.md`、本ファイル、`plan-codex-implementation-task.md`の絶対パスを受け取り、着手時に全文を読む
- 計画ファイル、品質規範、対象固有スキル、プロジェクト規範の絶対パスを受け取り、着手時に全文を読む
- 作業ディレクトリ、条件付き複製元、初回機械検査、レビュー前退避、指摘反映、
  反映後検収、レビュー後検収、承認済み復旧、復旧後検収、後始末の別を受け取る
- 後始末では、呼び出し元が検収済みの管理対象一時ディレクトリ絶対パスを受け取る
- 反映後検収、レビュー後検収、復旧後検収では、管理対象一時ディレクトリ、計画全文バックアップ、
  サンドボックス計画ファイル、対象worktreeと条件付き複製元のsnapshotの絶対パスを受け取る
- 指摘反映では指摘全文、区分、呼び出し元が確定した採否、承認状態を受け取る
- 承認済み復旧では、利用者の明示確認結果、変更対象、退避先、記録済みHEAD、具体的な復旧手順を受け取る
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
| 初回機械検査 | 管理対象一時ディレクトリ、全文バックアップ、サンドボックス計画、snapshotを作成する。サンドボックスへ3検査、違反修正、再検査を実施する | 管理対象一時ディレクトリだけ |
| レビュー前退避 | 管理対象一時ディレクトリ、全文バックアップ、無変更のサンドボックス計画、snapshotを作成する | 管理対象一時ディレクトリだけ |
| 指摘反映 | 初回機械検査と同じ退避を作成し、採用済み指摘だけをサンドボックスへ反映して3検査を実施する | 管理対象一時ディレクトリだけ |
| 反映後検収・レビュー後検収・復旧後検収 | 受領した退避を使い、SHA-256照合、統一差分取得、snapshot比較を再実行する | 受領した管理対象一時ディレクトリ内の生出力だけ |
| 承認済み復旧 | 利用者が明示確認した復旧手順だけを実行し、続けて復旧後検収を実施する | 確認済み手順が列挙する対象worktreeまたは複製元のパスと、生出力 |
| 後始末 | 受領した管理対象一時ディレクトリへ`_managed_temp.py cleanup`だけを実行し、対象パスの不在を確認する | cleanup対象だけ |

初回機械検査と指摘反映の3検査は、`plan-codex-review.md`「機械チェック委譲」節に従う。
承認済み復旧以外では、正規計画ファイル、対象worktree、条件付き複製元を変更しない。
承認済み復旧は利用者の明示確認結果を欠く場合に実行せず、`escalation_points`へ不足を返す。

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

- `managed_temp_dir`、`plan_backup_path`、`sandbox_plan_path`、`snapshot_paths`
- `plan_sha256_before`、`plan_sha256_after`、`plan_file_change`、`sandbox_plan_change`
- `worktree_check_result`。`target`と条件付き`source`ごとに`exit_code`、compareのJSON全体を保持する
  `compare_json`、`repository_changed`、`worktrees_changed`、`classification`を記載する。
  `classification`は`unchanged`、`repository_changed`、`worktree_state_only`、`error`、
  `source`が無い場合の`not_applicable`から選び、`changed|unchanged`の二値へ縮約しない。
  終了コード2は`error`、`repository_changed=true`は`repository_changed`、
  `repository_changed=false`かつ`worktrees_changed=true`は`worktree_state_only`、両方falseは`unchanged`とする
- `plan_file_diff`。差分なしは`none`とし、差分の各変更を機械違反または採用済み指摘へ対応づける
- 初回機械検査と指摘反映では、各検査の初回と最終の終了コード、error件数、warning件数を`check_results`へ記載する

反映後検収、レビュー後検収、復旧後検収は次を返す。

- SHA-256照合、snapshot比較、統一差分取得の標準出力、標準エラー出力、終了コード
- 同じ生出力を保存した`raw_output_paths`と、`worktree_check_result`、`plan_file_change`

承認済み復旧は、実行した確認済み手順、変更パス、復旧後検収の全出力を返す。
後始末は`managed_temp_dir`、`cleanup_exit_code`、`cleanup_target_absent: true|false`だけを返す。
削除済みディレクトリ内の`raw_output_paths`は返さない。
