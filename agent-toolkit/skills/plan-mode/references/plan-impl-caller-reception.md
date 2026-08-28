# 計画実装担当の起動と受領

呼び出し元は1計画1レーンの専用worktreeで`plan-impl-executor`を起動し、checkpointとエスカレーションを受領する。
実装担当の内部手順は`implementation-task.md`、レビューラウンドの受領は`review-loop-coordination.md`を正本とする。

## 起動

同じ計画ファイルへ書き込む計画担当の終端を確認し、各レーンへ専用managed-tempと専用git worktreeを作成する。
現在worktreeの借用、統合branch及び統合worktreeを使わない。複数の計画ファイルを同じレーンへまとめない。

`plan-impl-executor`へ次を渡す。

- 計画ファイル（メイン・詳細）、プロジェクト規範、作成規範スキル及びタスク文書の絶対パス
- レーン専用worktree、レーンmanaged-temp及び実装レビュー用managed-tempの絶対パス
- pickerが確定した順序を保持したフィードバックファイル名一覧
- フィードバック固有の処理順、公開、確認又は検証指示
- 複製元、対象外worktree、commit可、ffマージ可、所有資源の回収可、push不可という権限

## checkpointの受領

`review_round`は`review-loop-coordination.md`に従って受領する。

`merge_request`は1レーンずつ許可し、許可中は他レーンのベース更新を許可しない。
許可メッセージには、マージ先worktreeの絶対パス、マージ先branch名及び今回のレーンへ入っている計画ファイルの絶対パス一覧を含める。
マージ先HEAD完全OIDは渡さない。

認可範囲外、前提差異又はユーザー判断を`needs_escalation`で受領した場合は、回答だけを同じexecutorへ返す。
`計画実行完了`を受領したら追加検収を行わず、レーン完了として扱う。

## pushとCI

レーンはpushとCIを実行しない。
全レーン完了後のpush、CI、CI修正及び固有の終端工程は`agent-toolkit:process-feedbacks`の終了工程を正本とする。
