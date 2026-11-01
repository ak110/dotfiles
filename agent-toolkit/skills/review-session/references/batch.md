# 複数セッションの非同期振り返り

記録済みの非活動セッションをまとめて振り返り、共通する原因と対策を整理してAWIを登録する。
分析と参照資料は`session-analysis.md`を共用する。

## 名指し参照の読込方法

本文で名指しした資料と読込表の資料は、条件が成立したらその判断や操作の前に自分で全文読み、指定箇所に注目してから、その基準で判断する。読込表の列の使い方は`agent-basics:writing-standards`の`references/quality-principles.md`「読込表の読み方」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../wi-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/SKILL.md` |
| 対象の分析を始める前 | `session-analysis.md` | 判断 | `read:agent-toolkit:*/skills/review-session/references/session-analysis.md` |
| AWIを投入担当へ渡す前 | `${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md` | 判断 | `read:agent-toolkit:*/share/add-wi.parent.md` |
| 結果報告を保存する前 | `../../report-completion/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/report-completion/SKILL.md` |
| 読込表と本文の名指し参照を読む前 | `agent-basics:writing-standards/references/quality-principles.md`の「読込表の読み方」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/quality-principles.md#「読込表の読み方」` |

## 起動

環境変数 `AGENT_TOOLKIT_SESSION_REVIEW_BATCH` が無い手動呼出しでは、次の公開操作を実行する。

```sh
atk run-skill --args "batch <追加の着眼点>" agent-toolkit:review-session
```

追加の着眼点が無い場合は`--args batch`とする。先頭のbatchと残りの着眼点を1つの文字列引数で渡す。
実際の分析はrun-skillが起動する別セッションで行う。
進行中の担当がある場合は、分析をその実行へ引き続き委ねる。
自動起動は前回batch開始から6時間以上経過した場合に限る。
手動起動は間隔制限、自動停止設定と未完了の失敗報告AWIによる抑止を適用しないが、活動判定とアカウント共通排他は維持する。
batch自身のセッションも振り返り対象に含める。
非活動の選定対象がbatch自身の記録だけの場合は見送り、通常セッションと混在する場合にまとめて分析する。

## 分析と保存

1. `AGENT_TOOLKIT_SESSION_REVIEW_BATCH` が指すmanifest JSONを読む。
   `targets` が今回の固定対象、`prepared` が対象別の準備結果である。
   `target_repo` は今回1件の結果報告UWIの登録先であり、各対象の分析先と区別する。
   準備済みのbundleと文書を再利用し、対象集合と処理範囲をmanifestに固定して分析する。
2. `session-analysis.md`の分析基準・参照資料を読み、対象ごとの `conversation_path` を通読し、候補・統計・比較材料を評価する。
   各対象の `reference_document` があれば読む。
   同じ原因をセッション横断で整理し、対象IDと候補IDの組で出典を保持する。
   未確認の対象を成功とせず、既存の失敗対応に沿って原因と未完了範囲を報告する。
3. 原因と対策を確定したAWIを `agent-toolkit:wi-standards` と `${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md` の契約で登録する。
   sourceは `session-review` とし、エージェント由来の提案として処理担当が根拠と採否を懐疑的に評価する。
   所要時間目標と超過分析は対象1件あたり300秒として適用する。
4. `agent-toolkit:report-completion` の振り返り結果報告書式で、今回の結果を事後承認型UWIへ1件保存する。
   対象一覧、登録AWI・見送り・費用確認を対象IDと対応付ける。
   sourceは `run-skill`、scopeは `agent-toolkit:review-session batch`、本文frontmatterは `run_skill_report: true` とし、manifestの `target_repo` を `--target-repo` へ明示する。
   同一実行の報告があれば同じUWIを更新して1件に保つ。
   再度report-completionを実行する場合もこの報告を再利用する。
5. AWIと報告の保存を検収し、次の操作で分析の完了を記録する。

   ```sh
   atk run-script session-review-state -- finish
   ```

   分析完了の記録を保存した後、batchセッションを終了する。

`finish` は分析完了の記録だけを行う。
起動元run-skillがbatchセッションの終了後に報告の同期を検収し、今回準備した範囲を処理済みにする。
この検収と処理済みの確定はbatchセッション自身の完了条件に含めず、分析完了の記録後に終了を要求する。
次の対象は自動起動の間隔制限を満たした次回に処理し、手動起動では続けて処理できる。
失敗時はfinishを実行せず、原因と未完了対象を最終応答に残す。
待ち記録、失敗報告AWIの重複確認、AWIが未完了の間の自動抑止は起動元が所有する。

## 状態定義

`_common/session_review.py`はhook・活動状態の削除から独立したOSアカウント共通の`{state_dir}/session-review/`を管理する。

- `pending/<host>-<session_id>.json`: 最上位SessionStartがhost・会話ID・記録パス・cwd・リポジトリ・開始更新時刻・登録世代・batch由来を保存し、処理済み会話のUserPromptSubmitも再登録する。委譲先・サブエージェントを除外し、成功確定か取消しまで保持する。batch由来のセッションのhookでは再帰的に自動起動しない
- `reviewed/<host>-<session_id>.json`: prepareが増分を選ぶ記録別の最終完全行と同一性を保持し、旧結果が返っても確定範囲を維持する。元記録がすべて無く、pending・未解決失敗・未確定manifestから参照されない場合だけ回収する
- `runs/`: 実行ID別manifestに開始日時・固定対象・報告先・準備範囲・分析結果・プロセス・失敗を保持し、中断後の報告同期と未確定範囲を検収する。開始日時のない旧manifestはmtimeを自動起動間隔の基準にする。成功確定済み、または報告同期済み失敗の全対象がpendingから除かれたmanifestとログはrun-skillと同じ30日保持とし、未確定・報告未達は確定まで保持する
- `state.lock`: モデル起動・利用上限待機・WI保存を除く、登録・状態更新・成功確定を短時間排他する
- `batch.lock`: 自動・手動の全リポジトリで対象選定から報告同期検収・成功確定まで共有する。親中断時もプロセス識別と子の起動引数に含めた固有manifestパスで生存を確認し、二重起動を防ぐ
- 失敗は配布元リポジトリへの是正AWI（`source: session-review`）として報告し、処理担当が是正する。UWIでの回答は待たない。pendingとmanifestの失敗キーにより、同原因のAWIが未完了（inbox・processing・hold）の対象を自動選定から外し、AWIの終了後の次回起動で再試行する。手動再試行時も未完了の既存AWIを再利用する。準備に失敗した対象だけを切り離し、残りの対象はモデル起動へ進める

batchはrun-skillの報告同期検収後、手動振り返りは全体分析後の`atk run-script session-review-state -- complete --prepared <準備結果JSON>`で準備済み範囲を確定し、新世代・準備後の追記はpendingに残す。
`AGENT_TOOLKIT_SESSION_REVIEW_BATCH`はmanifest、`AGENT_TOOLKIT_RUN_SKILL_TARGET_REPO`は報告先を子へ渡す。
`session_review_auto_start`がfalseでも待ちを登録し、SessionStartで件数と手動操作を通知する。
batch起動時の回収では、共有の起動診断`launch.log`も、pending・未確定manifestが無く最終更新から30日経過した場合だけ削除する。
