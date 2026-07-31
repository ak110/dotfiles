# 計画ファイル機械チェック・修正タスク

## 入力

- `plan-codex-review.md`と`plan-codex-review-fix-task.md`の絶対パスを受け取り、
  着手時に各ファイルの全文を読む
- 計画ファイル、品質規範、対象固有スキル、プロジェクト規範の絶対パスを受け取り、
  着手時に各ファイルの全文を読む
- 作業ディレクトリの絶対パスは、pyfltrの`--work-dir`へ渡す対象リポジトリとして使う
- 作業ディレクトリは対象worktreeの唯一の入力とし、条件付きで複製元の絶対パスまたは`対象外`を受け取る
- 入力が欠ける場合は欠落項目を返し、推測で補わない

## 機械チェック・修正

- `plan-codex-review.md`「機械チェック委譲」節の検査、違反修正、再検査を全て実施する
- 受領した計画ファイルと、所有者限定の専用一時ディレクトリ内の退避だけを書き込み許可先とする
- 対象worktreeと条件付き複製元を変更せず、検査後に計画ファイルの実体と両リポジトリの検査結果を再確認する

## 出力

- 各検査の初回と最終の終了コード、error件数、warning件数を`check_results`へ記載する
- 解消できない違反または実行不能がある場合は、原因を`escalation_points`へ記載する
- `worktree_check_result: target=unchanged|changed, source=not_applicable|unchanged|changed`を記載する
