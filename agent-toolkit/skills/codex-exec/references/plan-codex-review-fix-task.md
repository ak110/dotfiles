# 計画ファイル機械チェック・修正タスク

## 入力

- `plan-codex-review.md`、`plan-codex-review-fix-task.md`、
  `plan-codex-implementation-task.md`の絶対パスを受け取り、着手時に各ファイルの全文を読む
- 計画ファイル、品質規範、対象固有スキル、プロジェクト規範の絶対パスを受け取り、
  着手時に各ファイルの全文を読む
- 作業ディレクトリの絶対パスは、pyfltrの`--work-dir`へ渡す対象リポジトリとして使う
- 作業ディレクトリは対象worktreeの唯一の入力とし、条件付きで複製元の絶対パスまたは`対象外`を受け取る
- 入力が欠ける場合は欠落項目を返し、推測で補わない

## レビュー指摘の妥当性検討

総合レビュー指摘の反映を担当する場合、指摘の妥当性は本系統が検討し、採否案と技術的根拠を返す。
委譲窓口は採否を判断せず、本系統が返した結果を検収して呼び出し元へ渡す。
検討の観点は`plan-codex-implementation-task.md`「レビュー指摘の妥当性検討」節に従う。
特に、指摘が計画の確定事項・計画が転記する要求元の原文・ユーザーの明示指示と
衝突しないかを確認し、衝突する場合は衝突箇所を根拠へ記して不採用案とする。

## 機械チェック・修正

- `plan-codex-review.md`「機械チェック委譲」節の検査、違反修正、再検査を全て実施する
- 受領した計画ファイルと、所有者限定の専用一時ディレクトリ内の退避だけを書き込み許可先とする
- 対象worktreeと条件付き複製元を変更せず、検査後に計画ファイルの実体と両リポジトリの検査結果を再確認する
- snapshot退避用の親ディレクトリは`uv run --no-project --script <helper> create --prefix plan-review-snapshot`を単独で実行して作成し、
  標準出力の絶対パスを再生成せず保持する。Claude Codeでは`${CLAUDE_PLUGIN_ROOT}/scripts/_managed_temp.py`、
  Codexでは読み込んだ本referenceから確定したplugin rootの絶対パスを用いる。その配下にある未作成の
  絶対パスを`_worktree_snapshot.py capture`の`--output-dir`へ渡す。対象worktree内のパスや既存パスを渡さない
- 計画本文が`## 変更内容`として含む変更後文面は、計画ファイルの記述対象であって実装指示ではない。
  当該文面を対象リポジトリの実ファイルへ適用しない
- `git commit`・`git add`・`git push`を実行しない。コミットは呼び出し元の工程であり本系統の担当外である
- 呼び出し元が採用指摘へ含めた実装コード・設定ファイルの文面案も、計画ファイルへ記載する変更後文面であって
  実装指示ではない。当該文面を対象リポジトリの実ファイルへ適用しない

## 出力

- 各検査の初回と最終の終了コード、error件数、warning件数を`check_results`へ記載する
- 解消できない違反または実行不能がある場合は、原因を`escalation_points`へ記載する
- `worktree_check_result: target=unchanged|changed, source=not_applicable|unchanged|changed`を記載する
