# レーンworktreeの作成・保持・回収

本書は、`agent-toolkit:process-wi`のレーンworktreeを`atk lane`で扱う手順を定める。
一時ファイルの置き場所の一般則は`agent-toolkit:managed-temp`が定める。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## レーンworktreeの扱い

1. `atk lane create`で、主作業ツリー配下の`.claude/worktrees/process-wi-<レーン識別子>`に作成し、worktree・branch・所有セッションを紐付けて登録する。
   メインエージェントによる所有資源の手作業での再登録は禁止する。
2. 統合後も追加変更に備えて保持し、全工程と外部プロセスの終了後に`atk lane delete`で回収する。
   回収順は`atk lane create --commands-file`に渡したdeleteコマンド、worktree、branch、以前の構成の個別managed-tempとする。
   通常終了時の`atk agents-exit-session`とprocess-loopの子セッション終了後の処理も同じ回収処理を呼ぶ。
3. CLIが未統合・dirty・稼働中・状態不明・一部失敗の資源を保持したら、メインが共通判断で理由を調べる。
   理由を解消した対象は同じ操作で再判定する。
   保護する成果、現在の担当、残るユーザー判断と再開条件がある対象は保持する。
4. 登録情報と回収結果への参照は、回収対象のworktree・branch・個別managed-tempとは別の状態ディレクトリに保存する。
   回収結果は通常の7日保持に任せる。
   再開に必要な選定情報・rebase後の対応表・検証記録は`atk lane`が所有登録と同じ状態ディレクトリに保持するため、managed-tempの7日保持を前提としない。
5. 選定原本も所有登録の選定情報も無い古い登録の作業ツリーは、`atk lane recover --session-id <所有session>`で解放する。
   次回開始時は`atk lane delete --list`で残存理由と再開操作を確認する。
