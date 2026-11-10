# 起草完了の条件：重複判定

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 報告用UWIの確認・保存手順へ切り替える前 | `../../report-completion/references/report-formats-uwi.md` | 判断 | `read:agent-toolkit:*/skills/report-completion/references/report-formats-uwi.md` |
| 複数リポジトリの共通本文を参照するAWIを読む前 | `cross-repository-submission.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/cross-repository-submission.md` |
| 観測事象を契機とする要求の採用を確定する前 | `agent-basics:bugfix/references/root-cause-analysis-evidence.md`、`agent-basics:bugfix/references/root-cause-actions-selection.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-analysis-evidence.md`、`read:agent-basics:*/skills/bugfix/references/root-cause-actions-selection.md` |
| 一連の処理全体の成立を判定する前 | `agent-basics:bugfix/references/root-cause-actions-selection.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-actions-selection.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../add-awi-by-user/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/add-awi-by-user/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `wi-show-bulk.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/wi-show-bulk.md` |
| 類似見直しの限定探索と是正範囲を判定する前 | `agent-basics:bugfix/references/root-cause-severity-impact.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-severity-impact.md` |
| 原因分析の記録が推定だけに依拠していないことを判定する前 | `agent-basics:bugfix/references/root-cause-analysis-evidence.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-analysis-evidence.md` |

## 重複判定

`agent-toolkit:add-awi-by-user`の新規主題と類似見直しで発見した独立別件では、未知の既存項目を検索せず、具体的に分かっている既存AWIがある場合にその項目と要求を比較する。
この省略は要求の起点で選ぶ。
それらの作業中に補助コマンドの失敗などから独立して観測した運用不具合は、手動の新規主題に含めず、登録前に未終了・採用済みの項目を検索して比較する。
既存AWIの更新依頼では指定項目を取得して比較し、振り返りからの登録では登録前の重複判定を行う。
他の起点の新規通常AWIも、下記の除外対象を除き登録前に既存項目を検索して比較する。
比較する場合は、対象、期待される結果、観測事象、破られた不変条件、根本原因の仕組み、必要な処置について両項目の被覆を確認する。
ファイル名・フック名・コマンド名の違いだけで別件とせず、同じ根本原因でも直接原因と必要な処置が独立する場合は要求の同一性基準で分ける。
既存項目が要求を完全に網羅する場合は再利用し、部分的な網羅では残りを投入する。
比較結果は投入時の報告に記載する。
検索が必要な場合は、まず`atk wi grep --summary-only --state=active --target-repo=<対象リポジトリの絶対パス> --skip-pull -F -- '<語>'`を実行する。
作業中の運用不具合では`--state=all`を使い、採用済みの候補について、適用条件・修正内容・実際の利用版と配布状況も比較する。
未配布の旧版で同じ事象が起きた場合は既存成果の再利用と更新後の確認へ接続し、修正後も同条件で再発する場合や処置が未被覆の場合は是正要求を残す。
単語一致や採用済みという状態だけで解消済みとは判断しない。
該当する項目とタイトルを取得し、その本文を`atk wi show`で確認する。
正規表現検索する場合のみ`-F`を外し、`'<Pythonの正規表現>'`を指定する。
終了コード1は一致する項目が0件であった正常な結果であり、検索エラーとは区別する。
単語の一致はあくまで候補の絞り込みにのみ用い、要求が網羅されているかどうかは取得した項目の本文を精読して判断する。
複数件の本文を一括取得する手順は`references/wi-show-bulk.md`に従う。
ユーザー自身が要求単位を記述した原稿の整形投入、UWI、および移行・復元作業は重複判定の対象外とする。
ただし、回答済みUWIとの整合性確認（`wi-standards-operations.md`「投入と取得」）は、種別を問わず必ず実施する。
検索して比較する場合は、起草前の照会条件と比較対象を証拠に残し、`atk wi add`の直前に同一条件で`--skip-pull`を付けずに再検索する。
既知項目のみとの比較ではその項目の状態を保存直前に確認する。
既存項目が完全に網羅している場合は、既存項目を再利用する。
部分的な網羅、要求同士の衝突、あるいは網羅元項目の終了など、比較結果に変化が生じた場合は保存を中断し、本文の修正および意味の検収プロセスへ戻る。
専念型のエージェントは完全網羅の場合に`reused`として結果を返し、付随型のエージェントも既存項目を投入結果として扱う。
部分網羅、衝突、網羅元項目の終了が発生した場合、付随型のエージェントは保存処理を進めず、生じた変化と必要な本文修正の内容を「続行できない理由」として委譲元へ報告する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| AWIとUWIの共通規範の基準を適用する前 | `../SKILL.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/SKILL.md` |
| AWIとUWIの共通規範：投入と取得の基準を適用する前 | `wi-standards-operations.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/wi-standards-operations.md` |
