# 登録全体の統括と対象別担当への受渡し

`atk wi process-loop`は登録集合を取得し、今回の全対象を担当する1つの統括会話を起動する。
統括は[process-wi all-repos](../../process-wi/references/all-repos.md)を完遂する。
常駐プログラムは共有の同期・開始判定と統括会話の起動、対象別の所有・準備・結果検収・回収を担当する。
未完了の会話を再開する場合は、その会話の対象集合と責務を維持する。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| リポジトリ担当として統括の全体工程と対象集合の扱いを確認する時 | `../../process-wi/references/all-repos.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 担当の役割に関する判断・作業の前 | `all-repos-orchestration-roles.md` |
| 取得済み候補の受領に関する判断・作業の前（操作対象外: 役割の開始入力） | `all-repos-orchestration-inputs.md` |
| 登録集合・worktreeと資源・所有・継続・回収に関する判断・作業の前 | `all-repos-orchestration-resources.md` |
