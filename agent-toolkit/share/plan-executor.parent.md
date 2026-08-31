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

## 実装レビュー後の計画保存commit

全実装単位の実装レビューが指摘0件で収束するか、修正後に再レビュー不要として収束した後、計画ファイルを所有する呼び出し元は
実装レビューで確定した変更と実装時の進捗を計画へ反映し、`atk plans commit yyyy/MM/dd-{名称}-{小文字16進数4桁}.md`を1回実行する。
引数は`plans` rootからの相対メイン計画パスとし、portable接頭辞や絶対パスを渡さない。
このcommitは指定stemのメイン、detail、付属素材及びレビュー表だけを対象とし、対象リポジトリの実装commitや他計画を含めない。
旧rootの既存計画を改訂する場合は新root用のこの経路を適用しない。保存commitの完了後に、通常の統合・キュー終端へ進む。

## pushとCI

レーンはpushとCIを実行しない。
全レーン完了後のpush、CI、CI修正及び固有の終端工程は`agent-toolkit:process-feedbacks`の終了工程を正本とする。
