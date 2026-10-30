# 登録全体の統括と対象別担当への受渡し

`atk wi process-loop`は登録集合を取得し、常駐cwdで1つの対話CLIを起動する。
統括担当は[process-wi all-repos](../../process-wi/references/all-repos.md)を完遂し、少数候補の対象を自ら処理する。
常駐プログラムは起動、対象別の所有と準備、完遂判定、続行指示、返却保存と回収を担当する。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../process-wi/references/all-repos.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/all-repos.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../completion-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/completion-report/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../session-review/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/session-review/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `selection-procedure.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/selection-procedure.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `selection-format.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/selection-format.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `run-lanes.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/run-lanes.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/exec.parent.md` | 判断 | `read:agent-toolkit:*/share/exec.parent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `finish-session.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/finish-session.md` |

## 担当の役割

統括担当が最上位のメインとして、全対象の判断・確認・完了報告を所有する。
常駐から起動した対話CLIには通常の最上位セッションの契約を適用する。
候補数Nに対する対象別の`floor((N+9)/10)`が2以上のリポジトリは、統括から対象worktreeをcwdとする通常delegateへ委譲する。
その担当は対象repoのprocess-wiを完遂し、統括の配下として結果と未完了工程を返す。
配下担当の確認事項・担当外の発見・改善点は統括へ届け、報告用UWIは統括がprocess-wiの実行全体で1回作成する。
配下担当は祖先の対話CLIを終了する`atk agents-exit-session`を呼ばず、返却と終了観測を統括へ渡す。
統括自身は対象別結果を保存した後、最上位の終了手順で統括CLIを終了する。

## 登録集合

登録集合は`process_loop_repositories`のローカルworktreeの絶対パスのJSON配列で定める。
空集合は通常起動で待機し、`--once`では終了する。
不正な登録は診断の対象を修正してから再開する。
同じoriginは登録順の先頭へまとめ、Git共通ディレクトリと処理worktreeの対応を維持する。
登録変更は次回取得から反映し、現在処理している集合は取得時点のまま保持する。
候補・診断・監査条件を満たす対象に対し、開始猶予と未回答UWIの開始抑止を一括判定する。
開始前の共有更新は全体で1回行い、監査・CI・worktree・所有・失敗・再開の状態は対象別に保持する。

## 取得済み候補の受領

集合JSONの`repositories`は対象別のオブジェクトの配列である。
各要素は`target_repo`、登録元`registered_worktree`、準備済み`worktree`、`common_dir`を持つ。
候補・選定用の項目は`candidate_file`、`readiness`、`lane_limit`である。
所有・再開用の項目は`ownership_record`、`ownership_token`、`lane_session_id`、`previous`である。
開始時候補は本文・鮮度を含む`candidate_file`の原本をそのまま使う。
`pick-wi-check --candidate-file`にはその絶対パスを渡し、着手可否は受領したreadinessの診断も含めて適用する。
不足する本文と依存先だけを通常キューCLIで補う。
回答済みUWI、候補の分類・開始時CI点検は[selection-procedure.md](selection-procedure.md)を使う。
選定YAMLとNを対象ごとに分け、レーン上限も対象別のNから計算する。
詳細な生成・合流の契約は[selection-format.md](selection-format.md)を使う。

## worktreeと資源

常駐は統括の起動前に対象worktreeを準備する。
明示の`--worktree`、無指定のdotfiles自動隔離、対象のWorktreeCreate/Removeを既存atk処理へ接続する。
作成hookの返却実体とGit登録・branch・common_dirの対応を確認する。
hook失敗時は途中資源と診断を保持して、その対象の準備を停止する。
resumeは既存の準備済み実体と未完了成果をそのまま再利用する。
配下担当のレーンは[run-lanes.md](run-lanes.md)の公開atk操作で作成し、対象別lane_session_idを維持する。
準備・統合・実行レビューは[run-lanes.md](run-lanes.md)と`${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md`へ、公開とCI検収は[finish-session.md](finish-session.md)へ接続する。

## 所有・継続・回収

短時間の排他とPID・起動時刻・担当sessionの生存照会でorigin単位の所有を維持する。
所有待ちや準備失敗の対象を除いて、独立した対象の処理を継続する。
異常終了では成果とlane_session_idをpreviousとして保持し、明示resumeは登録全体の統括IDだけを受け付ける。
通常会話と旧リポジトリ担当のIDは入力エラーとして返す。

統括が結果を保存してCLIを終了した後、常駐は起動候補のinbox・processing、対象全体のprocessing、未統合laneとHEADの変化を確認する。
未完了対象は同じ統括会話のresumeへまとめて渡す。
対象別の観測が続行後2回連続で一致した場合、またはその対象が続行不能を返した場合だけ、その対象の続行を打ち切る。
起動時と終了時に共通する候補・診断・未判定アラートをホストの無進捗記録へ保存する。
同じ入力では起動を見送り、新WI・本文/状態/依存/冷却期限/回答の変化・新アラート・明示resumeを再開の契機とする。
無進捗記録の対象は起動時点の候補と実行ホストに限定する。

統括と配下writerの終了を確認した対象だけ所有を解放し、既存のlaneを回収する。
停止未確認は所有をholdし、未統合・dirty・使用中・回収失敗の資源を保持する。
回収結果と理由をログへ残し、理由を解消して`atk lane delete --session-id <lane_session_id>`で再判定する。
候補原本と再開情報は検収まで保持する。

`abort`、`abort-cancel`、`status`、`instruct`、`instruct-cancel`はOSアカウント単位で維持する。
abort後は現在のセッション終了時に常駐を終了する。
instructは次の統括起動へ1回渡し、統括が対象に関係する部分を適用する。
対象別の正常完了、続行不能、未完了工程、再開位置は集合JSONのresult_fileへ保存する。
返却形式は`agent-toolkit:process-wi all-repos`の「結果と終了」に従う。
