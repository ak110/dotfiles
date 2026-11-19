# process-loopのworktree隔離の設計記録

[設計記録の索引](design.md)から参照する主題別の記録である。

## process-loopのworktree隔離

`atk wi process-loop`は影響範囲の大きい主作業ツリーを直接編集せずにセッションを起動する。

worktreeの作成前と再利用前に、`.claude/worktrees/`がGitの無視対象であることを確認する。

既存worktreeは、Gitの照会がすべて非空の成功値を返すことを確認してから再利用する。
解決した上流ブランチはfetch・rebaseの内部だけで使用し、対象リポジトリごとに異なる公開先をセッションのプロンプトへ推測注入しない。

並行worktreeの退避は`atk worktree-stash save --label <退避ラベル>`へ集約する。
固有refだけでは別worktreeのGCから到達できないため、共有する`refs/atk/worktree-stash/`配下へ同じOIDを保護する。
未追跡ファイルを含む退避を実現できない`git stash create`方式は採用しない。

`update-dotfiles`は同じPython環境で専用ヘルパーを1回起動し、旧退避の分類と保護をGitの一括処理へ渡す。
保護済みの退避を1件ずつ再起動・再表示する処理と、移行済みを表す専用の永続フラグは置かない。
欠損や保護値の不整合ではデータを保持してpullへ進まない。

`git pull --autostash`への単純置換は、未追跡内容・indexの保全と競合後の回復を満たさないため採用しない。

Claude Codeの`--worktree`へ置き換える案は、worktree隔離ガードがシェル構文を拒否するため採用しない。
`atk`側でworktreeを準備し、セッションのcwdを準備済みworktreeへ設定する。
通常Git作成への暗黙の切替は、対象が指定した準備・撤去の対応を失うため採らない。
常駐が起動前にworktreeを準備し、統括は対象の規範と準備結果を利用する。
