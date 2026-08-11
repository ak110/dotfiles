# feedbacks-plannerの起動と受領

Claude Codeホストで通常型feedbackを処理する場合に、メインが調査から計画レビューまでを委譲するsender契約を定める。
メインはqueue操作と検収を担当し、planner固有の実行手順を起動文へ複製しない。

## 起動

readyな通常型feedbackごとに`agent-toolkit:feedbacks-planner`を別識別子で起動する。
利用できる実行枠内で並列起動し、同じ対象worktreeを読み取り専用で共有する。
計画ファイルの保存先は項目ごとに別パスとする。

起動文には次の絶対パスと値だけを渡す。

- feedback filenameと、メインが`atk mq show`で一度だけ取得した本文
- 対象worktreeとプロジェクト規範
- `explore-template.md`、`plan-review-task.md`、`decision-format.md`、`content-adjustment.md`、`review-checklists.md`
- `agent-toolkit:plan-mode`などのauthor skillと、バグ対応時は`agent-toolkit:bugfix`
- 計画ファイルの保存先ディレクトリ

これらはplannerがauthorへ元の提示素材、確定した採否と合意、対象、規範、author用taskを欠落なく渡せる形で指定する。

plannerへqueue変異、push、フィードバック投入、worktree作成と回収の権限を渡さない。

## 受領

完了報告を次の実体へ照合する。

- 採否記録と`decision-format.md`
- 採用時の計画ファイルの実在、分量、機械検査結果、レビュー収束状態
- 計画の提示素材と合意表が渡したfeedback本文、確定した採否、利用者合意に対応していること
- planner起動前後のGit状態と`write_status`
- TBD候補と利用者判断事項

計画全文をplannerの完了報告へ要求しない。
採用時は`atk mq convert-to-plan <filename> --plan-file=<計画絶対パス> --target-repo=<repo>`を実行し、
保存結果の`plan_file`を実在する計画パスへ照合する。
ユーザー判断の保留時はTBD候補を`agent-toolkit:add-feedback`へ渡し、通常の`atk mq return-to-inbox`でinboxへ戻す。
filenameで表せない外部条件待ちは、観測方法、現在値、解除条件、再開工程を本文へ記録し、
`atk mq return-to-inbox <filename> --cooldown-days=3`で戻す。別feedback待ちは`depends_on`を使う。
