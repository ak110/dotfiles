# 対象リポジトリとprivate-notesの区別

対象リポジトリと、AWI、UWIおよび計画を保管するprivate-notes（`atk config get private_notes`が返すリポジトリ）は別のリポジトリとして扱う。報告でリポジトリのパスと状態を書く場合は、どちらを指すかを明示する。

- 変更対象の特定、差分の確認、commit、退避および巻き戻しは、依頼が指す対象リポジトリの作業ツリーで行う
- エージェントはprivate-notesを`atk wi`、`atk plans`、`atk serve`を通じて変更する。`atk`がGit履歴の分岐かrebaseの失敗を報告し、再実行でも解消しない場合に限り、その出力が示す回復手順の範囲で`git`を直接実行してよい。前後の状態と実行した操作を報告へ含める。private-notesは常駐の`atk wi process-loop`・`atk serve`と別セッションが同時に更新するため、`atk worktree-stash`、`git stash`、未コミット差分の破棄は回復手順の範囲でも行わない
- AWIとUWIの投入は`atk config get private_notes`が返す作業ツリーへ直接行い、別の作業ツリーを作成しない。別の作業ツリーのローカルcommitはユーザーと後続セッションに届かない
- private-notesの未コミット差分（キューの状態ディレクトリと計画ファイルを含む）は`atk wi commit`で確定する。cleanでないためにコマンドが失敗した場合も退避せず、同コマンドで確定するか、差分を所有する主体の終端を待つ
- 委譲先へ渡す`push不可`は対象リポジトリの`git push`を指し、`atk`がprivate-notesへ内部で行うcommitとpushは含まない
- ユーザーが外部のエディターでキューのファイルを直接編集することは制限しない。その未コミット変更は`atk wi commit`で確定する
