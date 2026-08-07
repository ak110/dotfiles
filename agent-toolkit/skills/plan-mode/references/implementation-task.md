# 計画実装writerタスク

指定されたコミット単位を実装し、近接検証、差分検収、stage、commitまで完了する。

## 入力

- 計画ファイル、対象worktree、プロジェクト規範の絶対パス
- 実装するコミット単位と、その単位が変更できる対象
- 適用するauthor skill名
- feedback filename、追加指示、許容済みの挙動変化。該当しない値は`なし`
- git操作に用いるworktree絶対パス、複製元と対象外worktree

入力が欠ける場合は推測せず`needs_escalation`で返す。
本task以外のdelegation内部資料は読まず、計画とauthor skillを作業契約の正本とする。

## 実装

1. 計画、プロジェクト規範、該当author skillを全文読む
2. 指定worktreeの現行`HEAD`、作業ツリー、対象ファイル全文を実測する。
   作業ディレクトリを自己解決せず、git操作は受領した絶対パスへ`git -C`を付ける
3. 計画の指定単位だけを実装する。現行実装が計画の前提と異なる場合は、
   利用者向け成果を維持できる範囲で手順を調整し、差異を報告する
4. 対象に近いformat、lint、testを実行し、警告を解消する
5. `git diff`、計画対象、変更後文面を照合し、担当範囲だけをstageする
6. `agent-toolkit:commit`に従ってcommitし、commit実体とcleanな作業ツリーを確認する
7. 計画の`## 進捗ログ`へcommit、検証、計画との差異を追記する必要がある場合は、
   計画で定めた書込主体と単位に従う

レビュー指摘の修正を受け取った場合は、`agent-toolkit:review-standards`と該当author skillを読み、
各指摘の事実と違反契約を実測する。採用指摘を一括修正し、同じ単位の検証とcommitを再実行する。
ユーザー合意と衝突する指摘は修正せず`needs_escalation`で返す。

対象worktree以外を編集しない。担当外差分を復元しない。`git push`、タグ作成、リモートrefも変更しない。

## 出力

```text
status: completed | needs_escalation
commit: <完全長SHAまたはなし>
changed:
- <計画項目と変更結果>
verification:
- <コマンド>; exit_code: <整数>; warnings: <整数>
review_resolution:
- <指摘ID、採否、根拠、修正結果。該当なしなら「なし」>
plan_deviation:
- <差異と調整結果。無ければ「なし」>
blockers:
- <未完了事項。完了時は「なし」>
```

成果物を書き込んだ場合は、成果物の絶対パスと実在・分量を示す実行結果も含める。
