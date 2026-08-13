# feedbacks-plannerの起動と受領

Claude Codeホストで通常型feedbackを処理する場合に、メインが調査から計画レビューまでを委譲するsender契約を定める。
メインはqueue操作と検収を担当し、planner固有の実行手順を起動文へ複製しない。

## 起動

active一覧を取得した時点のreadyな通常型feedbackを、対象リポジトリごとの1 waveとして
`agent-toolkit:feedbacks-planner`へ1回だけ渡す。
filename昇順の本文一覧を渡し、blocked項目、未回答TBD、スナップショット後に追加された項目は含めない。
plannerは採用項目を1つの統合計画へまとめ、項目ごとの原文、採否、対象、完了条件、実装単位を識別可能に保つ。

起動文には次の絶対パスと値だけを渡す。

- feedback filename一覧と、メインが`atk mq show`で一度だけ取得したfilename昇順の本文一覧
- 対象worktreeとプロジェクト規範
- `process-feedbacks/references/`配下の`explore-template.md`、`decision-format.md`、`content-adjustment.md`、`review-checklists.md`
- `plan-mode/references/plan-review-task.md`
- `agent-toolkit:plan-mode`などのauthor skillと、バグ対応時は`agent-toolkit:bugfix`
- 既存ファイルと衝突しない乱数サフィックス付きで、委譲元が確定した計画ファイルの絶対パス

これらはplannerがauthorへ元の提示素材、確定した採否と合意、対象、規範、author用taskを欠落なく渡せる形で指定する。
TBD候補は、技術調査と明文化済み方針で確定できず、かつ採用済み本文が要求しない選択肢に限定する。
採用済み本文が明示する変更自体を確認事項又は実装前提にしない。

plannerへqueue変異、push、フィードバック投入、worktree作成と回収の権限を渡さない。

## 受領

完了報告を次の実体へ照合する。

- 採否記録と`decision-format.md`
- 採用時の計画ファイルの実在、分量、機械検査結果、レビュー収束状態
- 計画の提示素材と合意表が渡したfeedback本文、確定した採否、利用者合意に対応していること
- planner起動前後のGit状態と`write_status`
- TBD候補と利用者判断事項

計画全文をplannerの完了報告へ要求しない。
採用時は各filenameへ`atk mq convert-to-plan <filename> --plan-file=<計画絶対パス> --target-repo=<repo>`を実行し、
保存結果の`plan_file`を同じ実在する計画パスへ照合する。
終端工程の一覧、対象及び認可根拠となる本文の逐語引用を照合し、本文にない操作は差し戻す。
実装変更がない終端工程専用項目は計画なしであることを検収し、終端待機集合へ登録する。
ユーザー判断の保留時はTBD候補を`agent-toolkit:add-feedback`へ渡す。
`hold-with-tbd-inject.md`の`保留と再開`に従い、既存の有効依存とTBD filenameを登録してから
通常の`atk mq return-to-inbox`でinboxへ戻し、active一覧で`blocked`を確認する。
filenameで表せない外部条件待ちは、観測方法、現在値、解除条件、再開工程を本文へ記録し、
`atk mq return-to-inbox <filename> --cooldown-days=3`で戻す。別feedback待ちは`depends_on`を使う。
