# 計画実行担当の起動と受領

呼び出し元は1計画1レーンの専用worktreeで`plan-executor`を起動し、checkpointとエスカレーションを受領する。
実装担当の内部手順は`${CLAUDE_PLUGIN_ROOT}/share/implementation.subagent.md`、レビューラウンドの受領は`${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`を正本とする。

## 起動

同じ計画ファイルへ書き込む計画担当の終端を確認し、各レーンへ専用managed-tempと専用git worktreeを作成する。
現在worktreeの借用、統合branch及び統合worktreeを使わない。複数の計画ファイルを同じレーンへまとめない。

`plan-executor`へ次を渡す。

- 計画ファイル（メイン・詳細）、プロジェクト規範、作成規範スキル及びタスク文書の絶対パス
- レーン専用worktree及びレーンmanaged-tempの絶対パス
- pickerが確定した順序を保持したフィードバックファイル名一覧
- フィードバック固有の処理順、公開、確認又は検証指示
- 複製元、対象外worktree、commit可、ffマージ可、所有資源の回収可、push不可という権限

計画レビューの指摘管理表は渡さない。同表は計画の確定までに用いる記録であり、実装担当の入力ではない。
実装担当が扱うのは実装レビューの指摘管理表だけとし、その受領条件は`${CLAUDE_PLUGIN_ROOT}/share/implementation.subagent.md`が定める。
計画レビューの経緯が必要になる事象が生じた場合だけ、当該事象を示して同表の絶対パスを追送する。

## checkpointの受領

`review_round`は`${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`に従って受領する。

`merge_request`は1レーンずつ許可し、許可中は他レーンのベース更新を許可しない。
許可メッセージには、マージ先worktreeの絶対パス、マージ先branch名及び今回のレーンへ入っている計画ファイルの絶対パス一覧を含める。
マージ先HEAD完全OIDは渡さない。
1回の許可は当該レーンが`計画実行完了`を返すまで有効とする。許可後にrebase競合・再レビュー・再収束を経ても失効せず、同じレーンへ同じ許可を再送しない。

認可範囲外、前提差異又はユーザー判断を`needs_escalation`で受領した場合は、回答だけを同じexecutorへ返す。
`計画実行完了`を受領したら追加検収を行わず、レーン完了として扱う。

## 実装レビュー後の計画最終化

保存済み計画を実装するレーンでは、実装の着手前に計画ファイルを所有する呼び出し元が`atk plans checkout <plans rootからの相対メイン計画パス>`を1回実行し、計画ファイルとレビュー指摘管理表を作業rootへ取得する。
最終化では取得した計画ファイルへ実装時の進捗を反映し、同じ`atk plans commit`で取得元と同じ保存先へ戻す。
競合の通知を受領した場合は同じ操作を繰り返さず、保存元の現在の内容と作業側の差分を確認してから反映内容を確定する。

全実装単位の実装レビューが指摘0件で収束するか、修正後に再レビュー不要として収束した後に最終化する。
計画ファイルを所有する呼び出し元が実装レビューで確定した変更と実装時の進捗を作業rootの計画へ反映し、
`atk plans commit dd-{名称}-{小文字16進数4桁}.md`を1回実行する。
`plan-executor`と実装担当はこの操作へ着手せず、呼び出し元へ差し戻す。
`agent-toolkit:process-feedbacks`の経路では、レーンの`merge_request`を受領したメインがマージ許可を与える前に本節を完了する。
引数は`plans` rootからの相対メイン計画パスとし、portable接頭辞や絶対パスを渡さない。
この操作は指定stemのメイン、detail、付属素材及びレビュー表をprivate-notesへ移動してcommit・pushする。
対象リポジトリの実装commitや他計画は含めない。
失敗時は作業rootの計画を保持して同じ操作から再開し、成功後に保存実体と作業側の消失を確認する。
旧root直下の既存計画を改訂する場合はこの移動経路を適用しない。最終化の完了後に、通常の統合・キュー終端へ進む。

## pushとCI

レーンはpushとCIを実行しない。
全レーン完了後のpush、CI、CI修正及び固有の終端工程は`agent-toolkit:process-feedbacks`の終了工程を正本とする。
