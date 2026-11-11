# 登録全体の統括と対象別担当への受渡し：登録集合

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 基準資料を選択・取得する時 | `../../process-wi/references/all-repos.md` |
| 基準資料を選択・取得する時 | `run-lanes.md` |
| 基準資料を選択・取得する時 | `../../../share/exec.parent.md` |
| 基準資料を選択・取得する時 | `finish-session.md` |
| 不良・是正要求・改善点を観測または受領した時と、ユーザーが時機の指示として「即時対応」や同義の表現を使った時 | `agent-toolkit:process-wi/references/immediate-fix.md` |
| 配下担当の進捗を点検する時 | `../../delegation/references/waiting-and-monitoring-progress.md` |

## 登録集合

登録集合は`process_loop_repositories`のローカルworktreeの絶対パスのJSON配列で定める。
空集合は通常起動で待機し、`--once`では終了する。
不正な登録は診断の対象を修正してから再開する。
同じoriginは登録順の先頭へまとめ、Git共通ディレクトリと処理worktreeの対応を維持する。
登録変更は次回取得から反映し、現在処理している集合は取得時点のまま保持する。
候補・診断・監査条件を満たす対象に対し、開始猶予と未回答UWIの開始抑止を一括判定する。
開始前の共有更新は全体で1回行い、監査・CI・worktree・所有・失敗・再開の状態は対象別に保持する。

通常の巡回では、常駐が統括の会話を起動する前に`update-dotfiles`を1回実行する。
統括の会話の途中では実行せず、`--no-update`による明示停止と、更新直後の自己再起動による重複抑止を除く。
会話終了後は対象別の結果、キューとレーンの状態を検収し、残件があれば次の巡回が新しい統括を起動する。
`--resume`と`--auto-resume`を明示した起動は保存済みの会話を再開する場合があり、この指定は最初の巡回で消費される。

結果を保存せずに終了した対象は完了扱いにならない。
次の統括は、対象別の所有、準備済みworktree、lane_session_idと前回の結果を`previous`から引き継ぐ。
統括CLIの終了だけで配下担当の停止を仮定せず、常駐が実プロセスと登録済みwriterの生存を確認する。
停止未確認の担当、未統合レーンとdirtyなworktreeは保持する。
例外や利用上限で統括CLIを止める経路は所有するプロセスの子孫も回収し、残存を検知した場合は所有を保持する。
利用上限時の同じ会話の再開と、予告を受けた統括が先に保存する内容は、`../../user-confirmation-and-report/references/usage-limit.md`「常駐から起動された統括の保存と再開」に従う。
寿命・更新・停止の確認と再検証は、`docs/development/audit-records-usage-limit.md`にある。
同記録の「agent-toolkit/skills/process-wi/references/all-repos-orchestration-resources.md：登録集合：2026年11月12日」を参照する。

## worktreeと資源

**6.** 常駐は統括の起動前に対象worktreeを準備する。

明示の`--worktree`、無指定のdotfiles自動隔離、対象のWorktreeCreate/Removeを既存atk処理へ接続する。
作成hookの返却実体とGit登録・branch・common_dirの対応を確認する。
hook失敗時は途中資源と診断を保持して、その対象の準備を停止する。
resumeは既存の準備済み実体と未完了成果をそのまま再利用する。
配下担当のレーンは[run-lanes.md](run-lanes.md)の公開atk操作で作成し、対象別lane_session_idを維持する。
準備・統合・実行レビューは[run-lanes.md](run-lanes.md)と`${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md`へ、公開とCI検収は[finish-session.md](finish-session.md)へ接続する。

## 所有・継続・回収

担当を起動する前に、受領した`previous`と前回の結果ファイルから、ユーザーだけが提供できる入力の不足で`blocked`となったAWIの現状を確かめる。
未回答UWIへの依存が未登録なら、`run-lanes-completion.md`「採否の確定と終了区分」3のUWI保存・依存登録・`inbox`への差戻しを先に行う。
自分で調達できる入力の不足はこの回答待ちに含めず、通常の調査・再開へ渡す。

**7.** 短時間の排他とPID・起動時刻・担当sessionの生存照会でorigin単位の所有を維持する。

所有待ちや準備失敗の対象を除いて、独立した対象の処理を継続する。
異常終了では成果とlane_session_idをpreviousとして保持する。
明示resumeは台帳に元の対象集合があれば再利用し、無ければ現在の登録集合で指定会話を再開する。
会話の再開条件は既存の会話解決へ委ね、実行中writerの所有は対象別の生存判定で保護する。

**8.** 統括が結果を保存してCLIを終了した後、常駐は開始側と同じ現行の着手可否を使い、当初候補の今回進められる作業、対象全体のprocessing、対処を要する不正metadata、未統合laneを確認する。

必要な判断が済んだ有効な未来のcooldown、未回答UWIへの依存、外部の先行項目への依存で進められない項目は、今回の再実行が必要な残件から除く。
同じ実行内で解消できる内部依存は残件に含め、再開記録の文章だけで作業を残件から除かない。
処理済み部分の公開・CI検収が終わり、残りが正当に先送りされていれば、WI自体は未完了でも今回の処理結果としてcompletedを受理する。
先送りする前の判断と解決の見込みは、既存の確認・依存・冷却の契約で確定し、常駐は意味判定や追加の申告フラグを持たない。
統括の結果statusにかかわらず、その巡回の起動は1回で終える。
残った候補・processing・未統合レーンは次の巡回の新しい統括へ渡し、previousと準備済みworktree・lane_session_idを再利用する。
続行不能を返した対象は、起動時と終了時に共通する候補・診断・未判定アラートをホストの無進捗記録へ保存する。
同じ入力では起動を見送り、新WI・本文/状態/依存/冷却期限/回答の変化・新アラート・明示resumeを再開の契機とする。
無進捗記録の対象は起動時点の候補と実行ホストに限定する。

**9.** 統括と配下writerの終了を確認した対象だけ所有を解放し、既存のlaneを回収する。

停止未確認は所有をholdし、未統合・dirty・使用中・回収失敗の資源を保持する。
回収結果と理由をログへ残し、理由を解消して`atk lane delete --session-id <lane_session_id>`で再判定する。
候補原本と再開情報は検収まで保持する。

**10.** `abort`、`abort-cancel`、`status`、`instruct`、`instruct-cancel`はOSアカウント単位で維持する。

 abort後は現在のセッション終了時に常駐を終了する。
 instructは次の統括起動へ1回渡し、統括が対象に関係する部分を適用する。
 対象別の正常完了、続行不能、未完了工程、再開位置は集合JSONのresult_fileへ保存する。
 返却形式は`agent-toolkit:process-wi all-repos`の「結果と終了」に従う。
