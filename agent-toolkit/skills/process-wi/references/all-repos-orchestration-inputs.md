# 登録全体の統括と対象別担当への受渡し：取得済み候補の受領

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 基準資料を選択・取得する時 | `selection-procedure.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/selection-procedure.md` |
| 基準資料を選択・取得する時 | `selection-format.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/selection-format.md` |
| 不良・是正要求・改善点を観測または受領した時と、ユーザーが時機の指示として「即時対応」や同義の表現を使った時 | `agent-toolkit:process-wi/references/immediate-fix.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/immediate-fix.md` |
| 配下担当の進捗を点検する時 | `../../delegation/references/waiting-and-monitoring-progress.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring-progress.md` |

## 取得済み候補の受領

**3.** 集合JSONの`repositories`は対象別のオブジェクトの配列である。

各要素は`target_repo`、登録元`registered_worktree`、準備済み`worktree`、`common_dir`を持つ。
候補・選定用の項目は`candidate_file`、`snapshot_file`、`readiness`、`lane_limit`である。
所有・再開用の項目は`ownership_record`、`ownership_token`、`lane_session_id`、`previous`である。

**4.** 開始時候補は本文・鮮度を含む`candidate_file`の原本をそのまま使う。

リポジトリ担当は受領した全体作業契約から巡回開始時刻とタイムゾーンを取得し、自身の作業記録へ残す。
新着の確認には受領した`snapshot_file`を使い、巡回開始時刻は経過時間の記録へ使う。
`snapshot_file`は初回取得と同じ全状態・全種別の識別子集合であり、本文を持つ候補一覧とは別に保持する。
再開時も初回の集合を引き継ぎ、欠落・破損時は原本を回復する。
具体的な新着の取得と判定は`immediate-fix.md`「開始後に追加されたWIの確認」に従う。

`pick-wi-check --candidate-file`にはその絶対パスを渡し、着手可否は受領したreadinessの診断も含めて適用する。
不足する本文と依存先だけを通常キューCLIで補う。
回答済みUWI、候補の分類・開始時CI点検は[selection-procedure.md](selection-procedure.md)を使う。
選定YAMLとNを対象ごとに分け、レーン上限も対象別のNから計算する。
詳細な生成・合流の契約は[selection-format.md](selection-format.md)を使う。

**5.** 統括自身が直接処理する場合も、受領候補・readiness・対象別の選定・`lane_session_id`の対応を保つ。

候補が1件であることと、その候補に着手できることは別に確認する。
直接処理は`lane_limit`が2以上ならlanes、1以下ならsingle-laneへ接続し、どちらも本節の受領確認と所定選定を適用する。
直接起動したレーン担当とwriterも対象別の`writer_sessions`へ記録し、常駐による終了確認・所有解放・回収と、`previous`を用いた再開へ引き渡す。
省略を選ぶ場合は、元工程が保護する結果を保てる根拠を比較し、`agent-toolkit/share/rules/common/01-agent.md`「QCDと3段判定」と既存の逸脱契約に従って判断・報告する。
