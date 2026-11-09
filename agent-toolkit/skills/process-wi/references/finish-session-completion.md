# 公開工程とセッション終了：完了報告と終了

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 合流した公開対象を判断する前 | `selection-procedure-addition.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/selection-procedure-addition.md` |
| 目標起点の公開対象を判断する前 | `lanes-goals.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/lanes-goals.md` |
| 未達条件の延期とユーザー確認・冷却を選ぶ前 | `../../wi-standards/references/states-and-dependencies-dependencies.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/states-and-dependencies-dependencies.md` |
| 基準資料を選択・取得する時 | `agent-toolkit:report-completion/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/report-completion/SKILL.md` |
| 統合後の即時修正の担当と時機を決める前 | `agent-toolkit:process-wi/references/immediate-fix.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/immediate-fix.md` |
| 公開後にも独立して成立するWI投入を別実行へ引き継ぐ前 | `../../../share/wi-submission.md` | 判断 | `read:agent-toolkit:*/share/wi-submission.md` |
| 基準資料を選択・取得する時 | `../../report-completion/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/report-completion/SKILL.md` |
| 基準資料を選択・取得する時 | `run-lanes-integration.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/run-lanes-integration.md` |

## 完了報告と終了

**25.** 単独のメインは公開工程の検収後に`agent-toolkit:report-completion`へ進み、成果・検証・公開結果・未達を同じ作業完了報告へ集約する。
自律モードでは報告用UWIの保存成功と警告を検収し、元の要求と実際の送達、残工程、新たな指摘の処置を確認してから、同スキル「工程」の終了処理へ進む。
最終報告には公開結果と元の要求に対する到達点を含める。

 統括からのリポジトリ委譲は、冒頭の返却契約に従って統括へ成果を返す。
単独・統括・委譲先のいずれも、作業の終了と公開では同期のreview-sessionを工程の対象外とする。
作業完了報告は非同期振り返りの結果とは独立して送達する。

**26.** 通常終了時に呼び出される`atk agents-exit-session`は、所有セッションに属するレーンリソースを`atk lane delete`と同等の条件で回収する。
process-loopにおいては、子セッションが終了した後、正常・異常の判定や再起動の判断前に同一条件でリソースを回収する。
メインは終了要求前にも既知の残存資源と誤変更を`agent-toolkit/share/rules/common/02-agent-operations.md`「プロセス終了の安全規定」で調べ、解消できる保持理由を是正して既存の回収操作へ戻す。
返却結果に未回収の対象があれば、`run-lanes-integration.md`「未回収レーンの再回収」に従う。
保持が必要な対象は、保護する成果や現在の担当、残るユーザー判断と再開条件を報告へ記録する。
回収の失敗をホストの終了要求成功で覆い隠さず、次回の`atk lane delete --list`へ引き継ぐ。

 統括から委譲されたリポジトリ担当は`atk agents-exit-session`を実行せず、統括への返却で担当工程を終える。
統括は各担当の終了結果と続行予定、保持すべき成果を検収し、既存のレーン回収条件に従う。
常駐起動の統括と単独メインのホスト終了はreport-completionが所有する。
 対象worktreeを保持している終了済みの委譲セッションについては、所有側の`agents_server`に対して結果を保持した上での解放を要求し、その後に現在使用中であるかを判定する。
 自動終了時の回収処理は`lane delete`と同じ適格判定を用い、終了セッションからの解放応答を待たずに、未確認のセッションを保持理由と結果保存先の情報付きで残す。
 対話型の`atk lane delete`は、所有側の解放応答と実行プロセスの終了をタイムアウトまで待機し、解放後の独立した使用状態を1回で判定する。
 実行中のセッション、所有側が応答しないセッション、解放後も外部プロセスが利用しているworktreeは回収せずに保持する。
 未解放のセッションIDが返却された場合は、`run-lanes-integration.md`「未回収レーンの再回収」に従い、終了結果を回収した後に公開MCPの`stop`へ渡し、同一の所有セッションに対して`atk lane delete`を再実行する。
