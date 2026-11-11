# process-loopのworktree隔離の設計記録

[設計記録の索引](design.md)と[継続処理の起動・隔離・制御の設計記録](design-process-loop.md)から参照する主題別の記録である。

## process-loopのworktree隔離

`atk wi process-loop`は影響範囲の大きい主作業ツリーを直接編集せずにセッションを起動する。
`--worktree[=NAME]`を指定すると任意の対象リポジトリでこの隔離を有効にする。
dotfilesリポジトリでは、オプションを指定しない場合も従来どおり隔離を有効にする。

worktreeは対象リポジトリ配下の`.claude/worktrees/<NAME>`へ配置し、専用ブランチ`worktree-<NAME>`を割り当てる。
`NAME`を省略した場合は`process-loop`を使用する。
worktreeの作成前と再利用前に、`.claude/worktrees/`がGitの無視対象であることを確認する。
無視されていない場合は、対象リポジトリの`info/exclude`へ`/.claude/worktrees/`の完全一致行を独立して追加する。
追加後の`git check-ignore`が成功した場合だけ、worktreeの作成または再利用へ進む。
除外判定の失敗、設定の更新失敗、再判定の失敗では、worktreeとセッションを起動せず待機へ戻る。

既存worktreeは、Gitの照会がすべて非空の成功値を返すことを確認してから再利用する。
`--git-common-dir`を対象リポジトリと一致させ、`--show-toplevel`をworktreeの配置先と一致させる。
現在のブランチが`worktree-<NAME>`であり、worktreeがcleanであることも再利用の条件とする。
条件のいずれかを確認できない場合はfetchとrebaseを実行せず、セッションを起動しない。
条件を満たしたworktreeは、現在のブランチの追跡先を優先し、解決できない場合だけ`origin/HEAD`へ後退して得た上流ブランチへfetchとrebaseで追随させる。
解決した上流ブランチはfetch・rebaseの内部だけで使用し、対象リポジトリごとに異なる公開先をセッションのプロンプトへ推測注入しない。
公開操作と公開先の判断は、その運用を所有する主体へ委ねる。

並行worktreeの退避は`atk worktree-stash save --label <退避ラベル>`へ集約する。
ヘルパーはGit共通ディレクトリ直下の固定`agent-toolkit-stash.lock`を`agent-toolkit/agent_toolkit/_common/file_lock.py`で排他する。
ロック中にstashを生成し、`refs/worktree/<退避ラベル>`とworktree・label別の共有保護refを記録してから生成分だけのstashをdropする。
固有refだけでは別worktreeのGCから到達できないため、共有する`refs/atk/worktree-stash/`配下へ同じOIDを保護する。
既存の`refs/stash`先頭OIDは維持し、途中失敗時は復旧識別子と保護または共有stashを保持する。
固定ロックファイルを削除しないのは、次回も同じinodeを排他対象として再利用するためである。
未追跡ファイルを含む退避を実現できない`git stash create`方式は採用しない。

`update-dotfiles`は同じPython環境で専用ヘルパーを1回起動し、旧退避の分類と保護をGitの一括処理へ渡す。
保存した作業内容・index・未追跡内容がrootの`mise.lock`だけであり、退避元commitがbranch・remoteから到達できる旧退避は回収する。
その他の退避と、ローカルcommitの唯一の参照になっている退避は保持し、共有保護が無いものだけ保護する。
固定ロック内で読み取ったOIDを条件に、回収と保護を`git update-ref --stdin`のトランザクションで確定する。
履歴を保持するbranch・remoteのOIDも同じトランザクションで検証し、到達性判定の後に参照が移動した場合は回収を中止する。
保護済みの退避を1件ずつ再起動・再表示する処理と、移行済みを表す専用の永続フラグは置かない。
画面は整理が発生した場合の件数要約と、失敗した場合の原因・次の操作を示す。
個別の識別子と処理の詳細は永続ログが保持する。
欠損や保護値の不整合ではデータを保持してpullへ進まない。

今回退避は`stash apply --index`の成功後だけ`drop <ref>`で回収する。
pull失敗後の復元成功も回収し、復元競合とrebase競合では保持する。
回収失敗は復元失敗と分け、復元済み内容をresetせず再試行を案内する。
dropは固定ロック内で読取OIDを条件として固有ref、共有保護refの順に削除し、片側だけ残っても同じ識別子で再試行できる。
他worktreeの同label退避と既存共有stashは回収しない。
`git pull --autostash`への単純置換は、未追跡内容・indexの保全と競合後の回復を満たさないため採用しない。
退避の作成・復元・回収は既存のGit操作を使い、旧退避の整理のために新しい管理サービスや台帳は追加しない。

Claude Codeの`--worktree`へ置き換える案は、worktree隔離ガードがシェル構文を拒否するため採用しない。
`atk`側でworktreeを準備し、セッションのcwdを準備済みworktreeへ設定する。
対象にWorktreeCreate/Removeがあれば共通のhook呼出しを使い、対象設定・cwd・イベント入力・環境を渡す。
返却先とGit登録・branch・共通ディレクトリ・資源所有を一致させ、設定済みhookの失敗では途中資源と診断を保持する。
通常Git作成への暗黙の切替は、対象が指定した準備・撤去の対応を失うため採らない。
hookが無い対象は固定配置と対象固有のprepareを使い、hook指定先も反復ごとに別名へ回避しない。
常駐が起動前にworktreeを準備し、統括は対象の規範と準備結果を利用する。
