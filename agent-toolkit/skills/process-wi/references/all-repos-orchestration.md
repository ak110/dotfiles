# 登録全体の統括と対象別担当への受渡し

`atk wi process-loop`は登録集合を取得し、常駐cwdで1つの対話CLIを起動する。
統括担当は[process-wi all-repos](../../process-wi/references/all-repos.md)を完遂し、対象集合が1件の場合と複数対象中の少数候補を自ら処理する。
常駐プログラムは巡回ごとの1回の起動、対象別の所有と準備、結果の検収、返却保存と回収を担当する。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 基準資料を選択・取得する時 | `../../process-wi/references/all-repos.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/all-repos.md` |
| 基準資料を選択・取得する時 | `../../report-completion/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/report-completion/SKILL.md` |
| 基準資料を選択・取得する時 | `../../review-session/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/review-session/SKILL.md` |
| 基準資料を選択・取得する時 | `selection-procedure.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/selection-procedure.md` |
| 基準資料を選択・取得する時 | `selection-format.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/selection-format.md` |
| 基準資料を選択・取得する時 | `run-lanes.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/run-lanes.md` |
| 基準資料を選択・取得する時 | `../../../share/exec.parent.md` | 判断 | `read:agent-toolkit:*/share/exec.parent.md` |
| 基準資料を選択・取得する時 | `finish-session.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/finish-session.md` |
| 不良・是正要求・改善点を観測または受領した時と、ユーザーが時機の指示として「即時対応」や同義の表現を使った時 | `agent-toolkit:process-wi/references/immediate-fix.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/immediate-fix.md` |
| 配下担当の進捗を点検する時 | `../../delegation/references/waiting-and-monitoring-progress.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring-progress.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 担当の役割に関する判断・作業の前 | `all-repos-orchestration-roles.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/all-repos-orchestration-roles.md` |
| 取得済み候補の受領に関する判断・作業の前 | `all-repos-orchestration-inputs.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/all-repos-orchestration-inputs.md` |
| 登録集合・worktreeと資源・所有・継続・回収に関する判断・作業の前 | `all-repos-orchestration-resources.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/all-repos-orchestration-resources.md` |
