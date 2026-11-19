# HEADの更新と再レビューの要否

レビュー修正かmergeの競合の解消でHEADが変わったら、影響を受ける計画を含むレビューだけを再レビューする。
前回のレビューHEADを`R`、最新のHEADを`H`、開始時のHEAD（rebase後はrebase先のOID）を`<基点>`とし、次のどちらかに当たる計画を「影響を受ける計画」とする。

- `git range-diff <開始時のHEAD>..<R> <基点>..<H>`で、その計画の判定対象commit（`review_contract`で判定対象外としたcommitを除く）のどれかが`=`以外で対応している。
- `git diff --name-only <R> <H>`の変更ファイルが、その計画の判定対象commitで変更されたファイルと重なる。

1. 対象worktreeで上のコマンドを直接実行する。
   判定対象commitは、`atk run-script plan-commits -- <計画の絶対パス> --worktree <対象worktreeの絶対パス>`が返す一覧から判定対象外を除いたものである。
   変更ファイルは`git show --name-only --format= <commit>`で確かめ、終了コードが0でなければ原因を解消して判定し直す。
2. 影響を受けない計画は再レビューを省き、計画名、`R`と`H`の完全なOID、実行したコマンド、対応行の要点、省いた理由を引き継ぎ記録へ残す。
   rebaseでcommitのOIDだけが変わった場合も再レビューは要らない。
3. 影響を受ける計画でも、差分が`agent-basics:review-standards/references/reviewee.md`「方式見直しと再レビュー」の再レビューを省ける修正に当たれば省き、理由を同じ引き継ぎ記録へ残す。
